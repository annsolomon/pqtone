"""X1 milestone B5d: the REST source (ADR-037). A local fake API replays recorded replies; no real service is
called. Invented data only."""
from __future__ import annotations

import json
import threading
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from pqt_connectors import restsource
from pqt_connectors.importer import Importer, State
from pqt_connectors.refs import Refs

from test_file_connector import KEY, PII, SCHEMAS

IST = "+05:30"


def invoice(n: int, updated: str) -> dict:
    return {"id": f"inv_{n}", "number": f"CL-{n:04d}", "issued_at": f"2026-01-01T10:{n:02d}:00{IST}", "updated_at": updated,
            "store": "Counter 2", "customer": {"name": PII["Customer Name"], "mobile": PII["Phone"], "gstin": PII["GSTIN"]},
            "lines": [{"sku": f"SKU-{n}", "quantity": "1", "unit": "pcs", "total": f"{100 + n}.00"}]}


INVOICES = [invoice(n, f"2026-01-01T10:{n:02d}:30{IST}") for n in range(1, 8)]

PROFILE = {
    "apiVersion": "connector.pqt/v1", "name": "cloud-pos", "system": "cloud-pos", "timezone": "Asia/Kolkata",
    "timestampFormat": "iso8601", "currency": "INR",
    "files": {"bills": {"each": "lines", "columns": {"billNo": "$.number", "timestamp": "$.issued_at", "till": "$.store",
                                                     "sku": "sku", "qty": "quantity", "unit": "unit", "amount": "total"}}},
    "maps": {"unit": {"pcs": []}},
}


class FakeAPI:
    """Serves INVOICES the way different APIs page and authenticate; records what it was asked."""

    def __init__(self):
        self.requests: list[dict] = []
        self.mode = "page"
        self.per_page = 3
        self.api_key = "k-123"
        self.tokens_issued = 0
        self.valid_tokens: set[str] = set()
        self.fail_next: list[tuple[int, dict]] = []
        self.refresh_tokens = {"r-1"}
        self.rotations = 1
        self.invoices = list(INVOICES)
        self.redirect_to = None
        self.raw_reply = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, status, body, headers=None):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                api.requests.append({"path": self.path, "form": form})
                grant = form.get("grant_type", [""])[0]
                if self.path != "/oauth/token" or form.get("client_secret") != ["cs-1"]:
                    return self._json(401, {"error": "invalid_client"})
                if grant == "refresh_token":
                    old = form.get("refresh_token", [""])[0]
                    if old not in api.refresh_tokens:
                        return self._json(400, {"error": "invalid_grant"})
                    api.rotations += 1
                    api.refresh_tokens = {f"r-{api.rotations}"}
                api.tokens_issued += 1
                token = f"at-{api.tokens_issued}"
                api.valid_tokens = {token}
                body = {"access_token": token, "token_type": "Bearer", "expires_in": 3600}
                if grant == "refresh_token":
                    body["refresh_token"] = next(iter(api.refresh_tokens))
                return self._json(200, body)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                api.requests.append({"path": u.path, "query": q, "headers": dict(self.headers)})
                if api.fail_next:
                    status, headers = api.fail_next.pop(0)
                    return self._json(status, {"error": "try later"}, headers)
                if api.redirect_to:
                    return self._json(302, {}, {"Location": api.redirect_to})
                if api.raw_reply is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(api.raw_reply)))
                    self.end_headers()
                    self.wfile.write(api.raw_reply)
                    return
                auth = self.headers.get("Authorization", "")
                if self.headers.get("X-Api-Key") != api.api_key and auth[7:] not in api.valid_tokens:
                    return self._json(401, {"error": "unauthorized"})
                rows = [i for i in api.invoices if "updated_since" not in q
                        or datetime.fromisoformat(i["updated_at"]) > datetime.fromisoformat(q["updated_since"])]
                rows.sort(key=lambda i: i["updated_at"])
                n, base = api.per_page, f"{api.base}{u.path}"
                if api.mode == "page":
                    p = int(q.get("page", 1))
                    return self._json(200, {"data": {"invoices": rows[(p - 1) * n:p * n]}, "meta": {"page": p}})
                if api.mode == "offset":
                    off, lim = int(q.get("offset", 0)), int(q.get("limit", n))
                    return self._json(200, {"data": {"invoices": rows[off:off + lim]}})
                if api.mode == "cursor":
                    start = int(q.get("cursor", 0))
                    nxt = start + n
                    return self._json(200, {"data": {"invoices": rows[start:nxt]},
                                            "meta": {"next_cursor": str(nxt) if nxt < len(rows) else None}})
                if api.mode in ("link", "next_url"):
                    start = int(q.get("from", 0))
                    nxt = start + n
                    keep = {k: v for k, v in q.items() if k != "from"}
                    nxt_url = f"{base}?{urllib.parse.urlencode({**keep, 'from': nxt})}" if nxt < len(rows) else None
                    if api.mode == "link":
                        return self._json(200, {"data": {"invoices": rows[start:nxt]}},
                                          {"Link": f'<{nxt_url}>; rel="next"'} if nxt_url else {})
                    return self._json(200, {"data": {"invoices": rows[start:nxt]}, "links": {"next": nxt_url}})
                return self._json(500, {})
        return H


@pytest.fixture
def api():
    a = FakeAPI()
    yield a
    a.close()


def config(tmp: Path, api: FakeAPI, *, pagination=None, auth=None, **extra) -> dict:
    (tmp / "p.yaml").write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
    cfg = {"id": "cloud-pos", "type": "http", "profile": "p.yaml", "baseUrl": api.base,
           "auth": auth or {"type": "apiKey", "header": "X-Api-Key", "key": "env:PQT_TEST_API_KEY"},
           "endpoints": {"bills": {"path": "/v1/invoices", "records": "data.invoices",
                                   "pagination": pagination or {"type": "page", "param": "page", "start": 1},
                                   "since": {"param": "updated_since", "cursor": "updated_at", "format": "iso8601",
                                             "start": "2026-01-01T00:00:00+05:30", "lookback": "PT1M"}}}}
    cfg.update(extra)
    return cfg


def poll(tmp, cfg, sent, sleeps=None):
    src = restsource.RestSource.from_config(cfg, base=tmp, sleep=(sleeps.append if sleeps is not None else (lambda s: None)))
    state = State(tmp / "state.db")
    try:
        imp = Importer(src.profile, store="store-001", refs=Refs(KEY, src.profile.system), state=state, schemas=SCHEMAS)
        return src.poll(imp, sent.extend)
    finally:
        state.close()


@pytest.fixture(autouse=True)
def api_key_env(monkeypatch):
    monkeypatch.setenv("PQT_TEST_API_KEY", "k-123")
    monkeypatch.setenv("PQT_TEST_CLIENT_ID", "ci-1")
    monkeypatch.setenv("PQT_TEST_CLIENT_SECRET", "cs-1")


def lines(sent):
    return sorted(e["data"]["sku"] for e in sent if e["type"] == "com.pqt.pos.sale.line")


def test_pages_are_followed_and_the_next_poll_asks_only_for_changes(tmp_path, api):
    sent: list[dict] = []
    r = poll(tmp_path, config(tmp_path, api), sent)
    assert r["errors"] == [] and lines(sent) == [f"SKU-{n}" for n in range(1, 8)]
    assert [q["query"].get("page") for q in api.requests] == ["1", "2", "3", "4"], "stops at the first empty page"
    assert r["cursors"]["bills"] == "2026-01-01T10:07:30+05:30"
    api.requests.clear()
    again: list[dict] = []
    poll(tmp_path, config(tmp_path, api), again)
    since = api.requests[0]["query"]["updated_since"]
    assert datetime.fromisoformat(since) == datetime.fromisoformat("2026-01-01T10:07:30+05:30") - timedelta(minutes=1)
    assert again == [], "the lookback re-reads invoice 7, which is unchanged"


@pytest.mark.parametrize("pagination, mode", [
    ({"type": "offset", "param": "offset", "limitParam": "limit", "limit": 3}, "offset"),
    ({"type": "cursor", "param": "cursor", "next": "meta.next_cursor"}, "cursor"),
    ({"type": "link"}, "link"),
    ({"type": "nextUrl", "next": "links.next"}, "next_url"),
])
def test_every_pagination_style_reads_everything_once(tmp_path, api, pagination, mode):
    api.mode = mode
    sent: list[dict] = []
    r = poll(tmp_path, config(tmp_path, api, pagination=pagination), sent)
    assert r["errors"] == [] and lines(sent) == [f"SKU-{n}" for n in range(1, 8)]
    assert len({e["id"] for e in sent}) == len(sent)


def test_a_rejected_key_is_reported_and_the_cursor_stays(tmp_path, api, monkeypatch):
    monkeypatch.setenv("PQT_TEST_API_KEY", "wrong")
    sent: list[dict] = []
    r = poll(tmp_path, config(tmp_path, api), sent)
    assert sent == [] and r["cursors"] == {} and any("401" in e["error"] for e in r["errors"])
    assert "wrong" not in json.dumps(r), "the key never appears in a report"


def test_oauth2_client_credentials_fetches_one_token_and_renews_it_when_refused(tmp_path, api):
    auth = {"type": "oauth2ClientCredentials", "tokenUrl": f"{api.base}/oauth/token", "clientId": "env:PQT_TEST_CLIENT_ID",
            "clientSecret": "env:PQT_TEST_CLIENT_SECRET", "scope": "invoices.read"}
    sent: list[dict] = []
    r = poll(tmp_path, config(tmp_path, api, auth=auth), sent)
    assert r["errors"] == [] and len(lines(sent)) == 7 and api.tokens_issued == 1
    token_req = next(q for q in api.requests if q["path"] == "/oauth/token")
    assert token_req["form"]["scope"] == ["invoices.read"] and token_req["form"]["grant_type"] == ["client_credentials"]
    # The service revokes the token: the next request gets 401, the source renews once and carries on.
    api.valid_tokens = set()
    api.invoices.append(invoice(9, f"2026-01-01T11:00:00{IST}"))
    more: list[dict] = []
    src = restsource.RestSource.from_config(config(tmp_path, api, auth=auth), base=tmp_path, sleep=lambda s: None)
    src.auth.token = "at-revoked"
    src.auth.expires = float("inf")
    state = State(tmp_path / "state.db")
    try:
        imp = Importer(src.profile, store="store-001", refs=Refs(KEY, "cloud-pos"), state=state, schemas=SCHEMAS)
        r2 = src.poll(imp, more.extend)
    finally:
        state.close()
    assert r2["errors"] == [] and lines(more) == ["SKU-9"] and api.tokens_issued == 2


def test_a_rotated_refresh_token_is_kept_in_its_secret_file(tmp_path, api):
    secret = tmp_path / "refresh"
    secret.write_text("r-1\n", encoding="utf-8")
    secret.chmod(0o600)
    auth = {"type": "oauth2RefreshToken", "tokenUrl": f"{api.base}/oauth/token", "clientId": "env:PQT_TEST_CLIENT_ID",
            "clientSecret": "env:PQT_TEST_CLIENT_SECRET", "refreshToken": f"file:{secret}"}
    sent: list[dict] = []
    r = poll(tmp_path, config(tmp_path, api, auth=auth), sent)
    assert r["errors"] == [] and len(lines(sent)) == 7
    assert secret.read_text(encoding="utf-8").strip() == "r-2" and oct(secret.stat().st_mode & 0o777) == "0o600"
    poll(tmp_path, config(tmp_path, api, auth=auth), [])
    assert secret.read_text(encoding="utf-8").strip() == "r-3", "the stored refresh token was the one used next"


def test_retry_after_is_honoured_and_a_service_that_stays_down_is_reported(tmp_path, api):
    api.fail_next = [(429, {"Retry-After": "2"}), (503, {})]
    sleeps: list[float] = []
    sent: list[dict] = []
    r = poll(tmp_path, config(tmp_path, api), sent, sleeps)
    assert r["errors"] == [] and len(lines(sent)) == 7
    assert sleeps[0] == 2 and len(sleeps) == 2
    api.fail_next = [(503, {})] * 10
    later: list[dict] = []
    r2 = poll(tmp_path, config(tmp_path, api), later, [])
    assert later == [] and any("503" in e["error"] for e in r2["errors"]) and r2["cursors"] == {}


def test_personal_data_in_api_replies_never_leaves(tmp_path, api):
    sent: list[dict] = []
    r = poll(tmp_path, config(tmp_path, api), sent)
    text = json.dumps(sent) + json.dumps(r, default=str)
    for v in (PII["Customer Name"], PII["Phone"], PII["GSTIN"], "CL-0001", "inv_1"):
        assert v not in text


def test_event_ids_are_the_same_after_a_failed_hand_over(tmp_path, api):
    src = restsource.RestSource.from_config(config(tmp_path, api), base=tmp_path, sleep=lambda s: None)
    state = State(tmp_path / "state.db")
    imp = Importer(src.profile, store="store-001", refs=Refs(KEY, "cloud-pos"), state=state, schemas=SCHEMAS)

    def broken(_events):
        raise OSError("outbox full")
    with pytest.raises(OSError):
        src.poll(imp, broken)
    state.close()
    sent: list[dict] = []
    r = poll(tmp_path, config(tmp_path, api), sent)
    clean = tmp_path / "clean"
    clean.mkdir()
    fresh: list[dict] = []
    poll(clean, config(clean, api), fresh)
    assert r["errors"] == [] and sorted(e["id"] for e in sent) == sorted(e["id"] for e in fresh)


@pytest.mark.parametrize("base_url", ["http://api.example.com", "ftp://127.0.0.1", "https://"])
def test_only_https_or_a_local_http_service(tmp_path, api, base_url):
    with pytest.raises(ValueError):
        restsource.RestSource.from_config(config(tmp_path, api, baseUrl=base_url), base=tmp_path)


def test_credentials_must_be_references(tmp_path, api):
    with pytest.raises(ValueError, match="env:"):
        restsource.RestSource.from_config(config(tmp_path, api, auth={"type": "apiKey", "header": "X-Api-Key", "key": "k-123"}),
                                          base=tmp_path)


def test_max_pages_caps_a_poll_and_the_next_poll_continues(tmp_path, api):
    api.per_page = 2
    cfg = config(tmp_path, api)
    cfg["endpoints"]["bills"]["maxPages"] = 2
    cfg["endpoints"]["bills"]["since"]["lookback"] = "PT0S"
    first: list[dict] = []
    r = poll(tmp_path, cfg, first)
    assert lines(first) == ["SKU-1", "SKU-2", "SKU-3", "SKU-4"] and r["truncated"] == ["bills"]
    second: list[dict] = []
    poll(tmp_path, cfg, second)
    assert lines(second) == ["SKU-5", "SKU-6", "SKU-7"]


def test_a_redirect_is_never_followed_so_credentials_stay_with_the_configured_host(tmp_path, api):
    other = FakeAPI()
    try:
        api.redirect_to = f"{other.base}/v1/invoices"
        sent: list[dict] = []
        r = poll(tmp_path, config(tmp_path, api), sent)
        assert sent == [] and any("redirect" in e["error"] for e in r["errors"])
        assert other.requests == [], "the other host never saw the API key"
    finally:
        other.close()


def test_absurd_numbers_in_a_reply_are_reported_not_expanded(tmp_path, api):
    page = json.dumps({"data": {"invoices": [invoice(20, f"2026-01-01T12:00:00{IST}")]}})
    api.raw_reply = page.replace('"total": "120.00"', '"total": 1e1000000').encode()
    sent: list[dict] = []
    r = poll(tmp_path, config(tmp_path, api, pagination={"type": "none"}), sent)
    assert sent == [] and r["errors"] and all(len(e["error"]) < 400 for e in r["errors"])
