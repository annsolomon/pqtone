"""HTTP uplink to event-core's batch ingest with OAuth client credentials (V8 now, the edge agent in A3).

Standard library only (urllib), so the edge image needs no HTTP client dependency. Retries are safe
because event-core deduplicates on (source, id): a resent event comes back as "duplicate".

- 429: wait for Retry-After (seconds), then retry the same batch.
- 5xx and connection errors: exponential back-off with jitter, up to `max_attempts`.
- 4xx other than 429 (and 401 after a token refresh): not retryable; raised.
- The token endpoint is part of the same path: unreachable, 429 or 5xx is retried like the ingest; a
  refused token (4xx: wrong client or secret) is `UplinkUnavailable`, never a refused batch, so a
  configuration fault keeps events in the outbox instead of rejecting them one by one.
"""
from __future__ import annotations

import email.utils
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

CONTENT_TYPE = "application/cloudevents-batch+json"


class UplinkError(RuntimeError):
    pass


class UplinkRefused(UplinkError):
    """event-core refused the whole batch (a 4xx other than 429): resending it unchanged will not help."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"ingest refused the batch: HTTP {status} {detail}")
        self.status = status


class UplinkUnavailable(UplinkError):
    """Transient failures (network, 429, 5xx) outlasted the retries; try again later."""


@dataclass
class OAuthClient:
    token_url: str
    client_id: str
    client_secret: str = field(repr=False)
    _token: str | None = field(default=None, repr=False)
    _exp: float = 0.0

    def token(self, now: Callable[[], float] = time.monotonic) -> str:
        if self._token is None or now() > self._exp - 30:
            body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                           "client_secret": self.client_secret}).encode()
            req = urllib.request.Request(self.token_url, data=body, method="POST",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 (configured https/http endpoint)
                doc = json.loads(r.read())
            self._token, self._exp = doc["access_token"], now() + float(doc.get("expires_in", 60))
        return self._token

    def invalidate(self) -> None:
        self._token = None


@dataclass
class Uplink:
    ingest_url: str                  # e.g. http://gateway:8080/v1/events (the batch endpoint is <url>/batch)
    auth: OAuthClient
    max_attempts: int = 8
    sleep: Callable[[float], None] = time.sleep
    wall: Callable[[], float] = time.time
    clock_offset_ms: int | None = None  # our clock minus the server's, from the Date header (A5)

    def _note_date(self, headers) -> None:
        """Estimate clock offset from the response's Date header (1 s resolution: take the middle)."""
        value = headers.get("Date") if headers is not None else None
        if not value:
            return
        try:
            server = email.utils.parsedate_to_datetime(value).timestamp() + 0.5
        except (TypeError, ValueError):
            return
        self.clock_offset_ms = int(round((self.wall() - server) * 1000))

    def send_batch(self, events: list[dict]) -> list[dict]:
        """POST one batch; returns event-core's per-item results ({id, status, ...}) in order."""
        body = json.dumps(events, sort_keys=True, separators=(",", ":")).encode()
        refreshed = False
        token_refused = None
        for attempt in range(self.max_attempts):
            try:
                token = self.auth.token()
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    self.sleep(min(2 ** attempt, 30) * (0.5 + random.random() / 2))
                    continue
                token_refused = e.code  # wrong client or secret: retrying now will not help
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError, KeyError):
                self.sleep(min(2 ** attempt, 30) * (0.5 + random.random() / 2))
                continue
            req = urllib.request.Request(f"{self.ingest_url}/batch", data=body, method="POST", headers={
                "Authorization": f"Bearer {token}", "Content-Type": CONTENT_TYPE})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
                    self._note_date(getattr(r, "headers", None))
                    return json.loads(r.read())["results"]
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.sleep(float(e.headers.get("Retry-After", "1") or 1))
                    continue
                if e.code == 401 and not refreshed:
                    self.auth.invalidate()
                    refreshed = True
                    continue
                if e.code >= 500:
                    self.sleep(min(2 ** attempt, 30) * (0.5 + random.random() / 2))
                    continue
                raise UplinkRefused(e.code, repr(e.read()[:300])) from e
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                self.sleep(min(2 ** attempt, 30) * (0.5 + random.random() / 2))
        if token_refused is not None:
            raise UplinkUnavailable(f"token endpoint refused the client (HTTP {token_refused}): check the client id and secret")
        raise UplinkUnavailable(f"ingest kept failing after {self.max_attempts} attempts")

    def send(self, events: Iterable[dict], *, batch_size: int = 200) -> dict[str, int]:
        totals: dict[str, int] = {}
        batch: list[dict] = []

        def flush() -> None:
            for item in self.send_batch(batch):
                totals[item["status"]] = totals.get(item["status"], 0) + 1
            batch.clear()

        for e in events:
            batch.append(e)
            if len(batch) >= batch_size:
                flush()
        if batch:
            flush()
        return totals
