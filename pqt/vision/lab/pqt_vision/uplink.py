"""HTTP uplink to event-core's batch ingest with OAuth client credentials (V8 now, the edge agent in A3).

Standard library only (urllib), so the edge image needs no HTTP client dependency. Retries are safe
because event-core deduplicates on (source, id): a resent event comes back as "duplicate".

- 429: wait for Retry-After (seconds), then retry the same batch.
- 5xx and connection errors: exponential back-off with jitter, up to `max_attempts`.
- 4xx other than 429 (and 401 after a token refresh): not retryable; raised.
"""
from __future__ import annotations

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

    def send_batch(self, events: list[dict]) -> list[dict]:
        """POST one batch; returns event-core's per-item results ({id, status, ...}) in order."""
        body = json.dumps(events, sort_keys=True, separators=(",", ":")).encode()
        refreshed = False
        for attempt in range(self.max_attempts):
            req = urllib.request.Request(f"{self.ingest_url}/batch", data=body, method="POST", headers={
                "Authorization": f"Bearer {self.auth.token()}", "Content-Type": CONTENT_TYPE})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
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
                raise UplinkError(f"ingest refused the batch: HTTP {e.code} {e.read()[:300]!r}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                self.sleep(min(2 ** attempt, 30) * (0.5 + random.random() / 2))
        raise UplinkError(f"ingest kept failing after {self.max_attempts} attempts")

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
