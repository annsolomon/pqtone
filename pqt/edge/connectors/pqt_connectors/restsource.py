"""The REST source (X1 B5d, ADR-037): poll a billing system's JSON API.

Per record kind, an endpoint is read page by page from a `since` value (the largest change time seen so far,
less a `lookback`), and its records are mapped by the profile like any other source. Pagination styles: `page`
(page numbers until an empty page), `offset` (offset + limit), `cursor` (a token in the body), `link` (RFC 8288
`Link: <...>; rel="next"`) and `nextUrl` (a URL in the body). Next-page URLs must stay on the configured host,
so credentials are never sent elsewhere.

Authentication: `apiKey` (a header or query parameter), `bearer` (a static token), `basic`, `oauth2ClientCredentials`
and `oauth2RefreshToken` (a rotated refresh token is written back to its `file:` secret, mode 600). Every credential
is an `env:` or `file:` reference. A refused token (401) is renewed once.

Redirects are never followed (a redirect could carry the credentials to another host): a 3xx is reported.

Resilience: 429 and 503 honour `Retry-After` (capped at 60 s); 5xx and network errors back off 1, 2, 4, 8 s;
after that the kind is reported and its cursor stays, so the next poll retries the same window. The cursor
moves only after the outbox took the events, and never past a record that was reported. Only HTTPS, or HTTP to
this machine (a local service or a test fake). Responses over 20 MB are refused. URLs are logged without their
query (which may carry an API key).
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

from . import profile as profile_mod
from .importer import Importer
from .profile import KINDS, ORDER, Profile
from .records import MISSING, json_loads, resolve as resolve_path
from .secrets import SecretError, resolve as resolve_secret
from .sqlsource import _duration

MAX_RESPONSE = 20 * 1024 * 1024
MAX_RETRIES = 4
BACKOFF = (1, 2, 4, 8)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class SourceFailure(Exception):
    """The API could not be read for a kind this poll (reported; the cursor stays)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None  # surfaces as HTTPError 3xx: reported, never followed with our headers


_OPENER = urllib.request.build_opener(_NoRedirect)


def _ref(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.startswith(("env:", "file:")):
        raise ValueError(f"{what} must be a reference (env:NAME or file:/path), never the secret itself")
    return value


def _check_url(url: str) -> urllib.parse.ParseResult:
    u = urllib.parse.urlparse(url)
    if not u.hostname or u.scheme not in ("https", "http") or (u.scheme == "http" and u.hostname not in LOCAL_HOSTS):
        raise ValueError(f"only https URLs (or http to this machine) are allowed: {u.scheme}://{u.hostname or ''}")
    return u


@dataclass
class Auth:
    kind: str
    cfg: dict
    token: str | None = None
    expires: float = 0.0
    opener: Callable | None = None

    def apply(self, headers: dict, params: dict) -> None:
        if self.kind == "apiKey":
            key = resolve_secret(self.cfg["key"])
            if self.cfg.get("in", "header") == "query":
                params[self.cfg.get("name", "api_key")] = key
            else:
                headers[self.cfg.get("header", "X-Api-Key")] = key
        elif self.kind == "bearer":
            headers["Authorization"] = f"Bearer {resolve_secret(self.cfg['token'])}"
        elif self.kind == "basic":
            pair = f"{resolve_secret(self.cfg['username'])}:{resolve_secret(self.cfg['password'])}"
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        elif self.kind in ("oauth2ClientCredentials", "oauth2RefreshToken"):
            if not self.token or time.time() >= self.expires:
                self._fetch_token()
            headers["Authorization"] = f"Bearer {self.token}"

    def invalidate(self) -> bool:
        """Forget an access token the API refused; True when renewing can help."""
        if self.kind in ("oauth2ClientCredentials", "oauth2RefreshToken"):
            self.token, self.expires = None, 0.0
            return True
        return False

    def _fetch_token(self) -> None:
        form = {"client_id": resolve_secret(self.cfg["clientId"]), "client_secret": resolve_secret(self.cfg["clientSecret"])}
        if self.kind == "oauth2ClientCredentials":
            form["grant_type"] = "client_credentials"
        else:
            form["grant_type"] = "refresh_token"
            form["refresh_token"] = resolve_secret(self.cfg["refreshToken"])
        if self.cfg.get("scope"):
            form["scope"] = self.cfg["scope"]
        req = urllib.request.Request(self.cfg["tokenUrl"], data=urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        try:
            with (self.opener or _OPENER.open)(req, timeout=30) as r:
                body = json_loads(r.read(1024 * 1024))
        except urllib.error.HTTPError as e:
            raise SourceFailure(f"token endpoint refused the credentials (HTTP {e.code})") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SourceFailure(f"token endpoint unreachable: {type(e).__name__}") from None
        if not body.get("access_token"):
            raise SourceFailure("token endpoint returned no access_token")
        self.token = body["access_token"]
        self.expires = time.time() + max(30, int(body.get("expires_in", 3600)) - 60)
        rotated = body.get("refresh_token")
        ref = self.cfg.get("refreshToken", "")
        if self.kind == "oauth2RefreshToken" and rotated and ref.startswith("file:"):
            p = Path(ref[5:])
            tmp = p.with_name(p.name + ".tmp")
            tmp.touch(mode=0o600, exist_ok=True)
            tmp.chmod(0o600)
            tmp.write_text(rotated + "\n", encoding="utf-8")
            tmp.replace(p)


@dataclass
class Endpoint:
    kind: str
    path: str
    records: str | None
    params: dict
    pagination: dict
    since: dict | None
    max_pages: int = 1000


@dataclass
class RestSource:
    id: str
    profile: Profile
    base_url: str
    auth: Auth
    endpoints: list[Endpoint]
    headers: dict = field(default_factory=dict)
    timeout_s: int = 30
    sleep: Callable[[float], None] = time.sleep
    opener: Callable = _OPENER.open

    @classmethod
    def from_config(cls, cfg: dict, *, base: str | Path = ".", sleep: Callable[[float], None] = time.sleep) -> "RestSource":
        base = Path(base)
        unknown = set(cfg) - {"id", "type", "profile", "baseUrl", "auth", "headers", "endpoints", "interval", "timeoutSeconds"}
        if unknown:
            raise ValueError(f"http source: unknown key(s) {sorted(unknown)}")
        sid = str(cfg.get("id") or "")
        if not re.fullmatch(r"[a-z0-9-]{1,64}", sid):
            raise ValueError("http source: id must match [a-z0-9-]{1,64}")
        base_url = str(cfg.get("baseUrl") or "")
        _check_url(base_url)
        a = dict(cfg.get("auth") or {})
        kind = a.pop("type", None)
        needs = {"apiKey": ["key"], "bearer": ["token"], "basic": ["username", "password"],
                 "oauth2ClientCredentials": ["clientId", "clientSecret"],
                 "oauth2RefreshToken": ["clientId", "clientSecret", "refreshToken"]}
        if kind not in needs:
            raise ValueError(f"http source {sid}: auth.type must be one of {sorted(needs)}")
        for k in needs[kind]:
            _ref(a.get(k), f"http source {sid}: auth.{k}")
        if kind.startswith("oauth2"):
            _check_url(str(a.get("tokenUrl") or ""))
        for k in needs[kind]:  # fail at start-up, not at the first poll, when a secret is missing
            try:
                resolve_secret(a[k])
            except SecretError as e:
                raise ValueError(f"http source {sid}: auth.{k}: {e}") from None
        ppath = Path(cfg.get("profile", ""))
        prof = profile_mod.load(ppath if ppath.is_absolute() else base / ppath)
        endpoints = []
        for k, e in (cfg.get("endpoints") or {}).items():
            if k not in KINDS or k not in prof.columns:
                raise ValueError(f"http source {sid}: endpoint {k} is not a kind the profile maps")
            extra = set(e) - {"path", "records", "params", "pagination", "since", "maxPages"}
            if extra:
                raise ValueError(f"http source {sid}.{k}: unknown key(s) {sorted(extra)}")
            pag = dict(e.get("pagination") or {"type": "none"})
            if pag.get("type") not in ("none", "page", "offset", "cursor", "link", "nextUrl"):
                raise ValueError(f"http source {sid}.{k}: pagination.type must be none, page, offset, cursor, link or nextUrl")
            if pag["type"] in ("cursor", "nextUrl") and not pag.get("next"):
                raise ValueError(f"http source {sid}.{k}: {pag['type']} pagination needs `next` (where it is in the body)")
            since = e.get("since")
            if since is not None:
                if not since.get("param") or not since.get("cursor") or "start" not in since:
                    raise ValueError(f"http source {sid}.{k}: since needs param, cursor and start")
                if since.get("lookback"):
                    _duration(since["lookback"]) if str(since.get("format", "iso8601")) not in ("epoch_ms", "epoch_s") else int(since["lookback"])
            endpoints.append(Endpoint(k, str(e.get("path", "")), e.get("records"), dict(e.get("params") or {}), pag, since,
                                      int(e.get("maxPages", 1000))))
        if not endpoints:
            raise ValueError(f"http source {sid}: no endpoints")
        endpoints.sort(key=lambda x: ORDER.index(x.kind))
        auth = Auth(kind, a)
        return cls(sid, prof, base_url.rstrip("/"), auth, endpoints, dict(cfg.get("headers") or {}),
                   int(cfg.get("timeoutSeconds", 30)), sleep)

    # -- since values ---------------------------------------------------------------------------
    @staticmethod
    def _comparable(value: Any, fmt: str) -> Any:
        if value is None or value is MISSING or value == "":
            return None
        if fmt in ("epoch_ms", "epoch_s"):
            return int(Decimal(str(value)))
        if fmt == "iso8601":
            return datetime.fromisoformat(str(value))
        return datetime.strptime(str(value), fmt)

    @staticmethod
    def _format(value: Any, fmt: str) -> str:
        if fmt in ("epoch_ms", "epoch_s"):
            return str(value)
        if fmt == "iso8601":
            return value.isoformat()
        return value.strftime(fmt)

    def _since_param(self, ep: Endpoint, stored: str | None) -> tuple[Any, str | None]:
        if not ep.since:
            return None, None
        fmt = str(ep.since.get("format", "iso8601"))
        raw = stored if stored is not None else ep.since["start"]
        value = self._comparable(raw, fmt)
        lb = ep.since.get("lookback")
        if lb:
            value = value - (int(lb) if fmt in ("epoch_ms", "epoch_s") else _duration(lb))
        return value, self._format(value, fmt)

    # -- HTTP -----------------------------------------------------------------------------------
    def _get(self, url: str, params: dict) -> tuple[Any, dict]:
        renewed = False
        attempt = 0
        while True:
            headers = {"Accept": "application/json", "User-Agent": "pqt-connectors", **self.headers}
            q = dict(params)
            self.auth.opener = self.opener
            self.auth.apply(headers, q)
            u = urllib.parse.urlparse(url)
            merged = dict(urllib.parse.parse_qsl(u.query))
            merged.update({k: str(v) for k, v in q.items()})
            full = urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(merged)))
            req = urllib.request.Request(full, headers=headers, method="GET")
            where = f"{u.scheme}://{u.netloc}{u.path}"
            try:
                with self.opener(req, timeout=self.timeout_s) as r:
                    raw = r.read(MAX_RESPONSE + 1)
                    if len(raw) > MAX_RESPONSE:
                        raise SourceFailure(f"{where}: response larger than {MAX_RESPONSE} bytes")
                    try:
                        return json_loads(raw), dict(r.headers)
                    except (UnicodeDecodeError, ValueError):
                        raise SourceFailure(f"{where}: response is not JSON (or is nested too deeply)") from None
            except urllib.error.HTTPError as e:
                code = e.code
                retry_after = e.headers.get("Retry-After") if e.headers else None
                e.close()
                if 300 <= code < 400:
                    raise SourceFailure(f"{where}: redirect refused (HTTP {code}); point baseUrl at the final address") from None
                if code == 401 and not renewed and self.auth.invalidate():
                    renewed = True
                    continue
                if code in (429, 503) or 500 <= code < 600:
                    if attempt >= MAX_RETRIES:
                        raise SourceFailure(f"{where}: HTTP {code} after {attempt + 1} attempts") from None
                    self.sleep(self._wait(retry_after, attempt) if code in (429, 503) else BACKOFF[min(attempt, 3)])
                    attempt += 1
                    continue
                raise SourceFailure(f"{where}: HTTP {code}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt >= MAX_RETRIES:
                    raise SourceFailure(f"{where}: unreachable ({type(e).__name__}) after {attempt + 1} attempts") from None
                self.sleep(BACKOFF[min(attempt, 3)])
                attempt += 1

    @staticmethod
    def _wait(retry_after: str | None, attempt: int) -> float:
        if retry_after:
            try:
                return min(60.0, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    return min(60.0, max(0.0, (parsedate_to_datetime(retry_after).timestamp() - time.time())))
                except (TypeError, ValueError):
                    pass
        return float(BACKOFF[min(attempt, 3)])

    def _same_origin(self, url: str) -> str:
        nxt, home = urllib.parse.urlparse(url), urllib.parse.urlparse(self.base_url)
        if (nxt.scheme, nxt.netloc) != (home.scheme, home.netloc):
            raise SourceFailure(f"next page points to another host ({nxt.netloc}); refusing to send credentials there")
        return url

    def _pages(self, ep: Endpoint, since_text: str | None):
        """Yield (records, truncated) per page."""
        pag = ep.pagination
        params = dict(ep.params)
        if ep.since and since_text is not None:
            params[ep.since["param"]] = since_text
        url = self.base_url + ep.path
        page_no = int(pag.get("start", 1))
        offset = 0
        token = None
        for n in range(ep.max_pages):
            p = dict(params)
            if pag["type"] == "page":
                p[pag.get("param", "page")] = page_no
            elif pag["type"] == "offset":
                p[pag.get("param", "offset")] = offset
                p[pag.get("limitParam", "limit")] = int(pag.get("limit", 100))
            elif pag["type"] == "cursor" and token is not None:
                p[pag.get("param", "cursor")] = token
            body, headers = self._get(url, p)
            found = resolve_path(body, ep.records) if ep.records else body
            if not isinstance(found, list):
                raise SourceFailure(f"{ep.path}: no list of records at {ep.records!r}")
            last = n + 1 == ep.max_pages
            if pag["type"] == "none":
                yield found, False
                return
            if pag["type"] in ("page", "offset"):
                if not found:
                    return
                yield found, last
                page_no += 1
                offset += len(found)
                continue
            if pag["type"] == "cursor":
                nxt = resolve_path(body, pag["next"])
                yield found, last and nxt not in (None, MISSING, "")
                if nxt in (None, MISSING, "") or not found:
                    return
                token = str(nxt)
                continue
            nxt_url = None
            if pag["type"] == "link":
                m = re.search(r'<([^>]+)>\s*;\s*rel="?next"?', headers.get("Link", "") or "")
                nxt_url = m.group(1) if m else None
            else:
                v = resolve_path(body, pag["next"])
                nxt_url = None if v in (None, MISSING, "") else str(v)
            yield found, last and nxt_url is not None
            if not nxt_url:
                return
            url, params = self._same_origin(nxt_url), {}  # the next URL carries its own parameters

    # -- polling --------------------------------------------------------------------------------
    def poll(self, imp: Importer, sink: Callable[[list[dict]], None]) -> dict:
        state = imp.state
        fetched: dict[str, tuple[Endpoint, list, Any]] = {}
        failures: list[dict] = []
        truncated: list[str] = []
        for ep in self.endpoints:
            stored = state.cursor(self.id, ep.kind)
            prev, since_text = self._since_param(ep, stored)
            records: list = []
            try:
                for page, cut in self._pages(ep, since_text):
                    records.extend(page)
                    if cut:
                        truncated.append(ep.kind)
            except SourceFailure as e:
                failures.append({"file": ep.kind, "row": None, "error": str(e)})
                continue
            fetched[ep.kind] = (ep, records, stored)
            imp.feed(ep.kind, ep.kind, records)
        report = imp.commit(sink).as_dict()
        new: dict[str, str] = {}
        for kind, (ep, records, stored) in fetched.items():
            if not ep.since:
                continue
            fmt = str(ep.since.get("format", "iso8601"))
            vals = [(self._comparable(resolve_path(r, ep.since["cursor"]), fmt), resolve_path(r, ep.since["cursor"]), i + 1)
                    for i, r in enumerate(records)]
            vals = [v for v in vals if v[0] is not None]
            bad = {e["row"] for e in report["errors"] if e["file"] == kind and isinstance(e["row"], int)}
            if bad:
                first_bad = min(v[0] for v in vals if v[2] in bad) if any(v[2] in bad for v in vals) else None
                vals = [v for v in vals if first_bad is None or v[0] < first_bad] if first_bad is not None else []
            if not vals:
                continue
            best = max(vals, key=lambda v: v[0])
            current = self._comparable(stored, fmt) if stored is not None else self._comparable(ep.since["start"], fmt)
            if best[0] > current:
                new[kind] = str(best[1])
        state.set_cursors(self.id, new)
        return {"source": self.id, "records": {k: len(v[1]) for k, v in fetched.items()}, "events": report["events"],
                "errors": failures + report["errors"], "cursors": new, "truncated": truncated}
