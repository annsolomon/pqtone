"""X1 milestone B5b: the push API on the edge box (ADR-037). Invented data only."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from pqt_connectors import push
from pqt_connectors.secrets import SecretError, resolve

from test_file_connector import KEY, PII, SCHEMAS

HERE = Path(__file__).resolve().parent
PROFILES = HERE.parent / "profiles"
OPENAPI = HERE.parent / "openapi.yaml"
TOKEN = "t0k-" + "a" * 40
SIG_SECRET = secrets.token_hex(16)  # made per run: no secret-looking literal in the repository

BILL = [
    {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:02+05:30", "till": "Counter 1", "lineNo": 1, "sku": "ATTA-5KG",
     "qty": 2, "unit": "pcs", "amount": "490.00", "tax": "23.33"},
    {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:02+05:30", "till": "Counter 1", "lineNo": 2, "sku": "ONION",
     "qty": 1.25, "unit": "kg", "amount": 50},
]
PAYMENT = [{"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:40+05:30", "tender": "upi", "amount": "540.00",
            "direction": "in", "reference": "UPI-77881"}]

GATEWAY_PROFILE = {
    "apiVersion": "connector.pqt/v1", "name": "test-gateway", "system": "gw-test", "timezone": "Asia/Kolkata",
    "timestampFormat": "epoch_s", "currency": "INR", "moneyUnit": "minor",
    "files": {"payments": {"path": "payload.payment",
                           "columns": {"billNo": "notes.bill_no", "timestamp": "created_at", "tender": "method",
                                       "amount": "amount", "reference": "id"}}},
    "maps": {"tender": {"upi": [], "card": [], "wallet": [], "other": ["netbanking", "emi"]}},
}


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("PQT_TEST_SIG", SIG_SECRET)
    (tmp_path / "gw.yaml").write_text(yaml.safe_dump(GATEWAY_PROFILE), encoding="utf-8")
    clients = {"apiVersion": "connector.pqt/v1", "clients": [
        {"id": "my-pos", "tokenSha256": sha(TOKEN), "profile": str(PROFILES / "canonical.yaml")},
        {"id": "signed-pos", "tokenSha256": sha("other-" + TOKEN), "profile": str(PROFILES / "canonical.yaml"),
         "kinds": ["bills"], "signature": {"header": "X-PQT-Signature", "secret": "env:PQT_TEST_SIG", "prefix": "sha256="}},
        {"id": "gateway", "webhook": True, "kind": "payments", "profile": str(tmp_path / "gw.yaml"),
         "signature": {"header": "X-Gateway-Signature", "secret": "env:PQT_TEST_SIG"}},
    ]}
    cfg = tmp_path / "clients.yaml"
    cfg.write_text(yaml.safe_dump(clients), encoding="utf-8")
    sent: list[dict] = []
    lock = threading.Lock()

    def sink(events):
        with lock:
            sent.extend(events)
    srv = push.make_server(push.load_clients(cfg), store="store-001", key=KEY, state_path=tmp_path / "state.db",
                           sink=sink, schemas=SCHEMAS, bind="127.0.0.1", port=0, max_body=64 * 1024,
                           rate=(5.0, 10))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, sent, srv
    srv.shutdown()
    srv.server_close()


def call(base, path, body=None, *, token=TOKEN, headers=None, method=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"null")


def test_canonical_records_are_imported_and_a_retry_adds_nothing(server):
    base, sent, _ = server
    st, _, body = call(base, "/v1/records", {"bills": BILL, "payments": PAYMENT})
    assert st == 200, body
    assert body["events"] == 2 + 2 + 1 and body["errors"] == [] and body["records"] == {"bills": 2, "payments": 1}
    assert {e["source"] for e in sent} == {"urn:pqt:edge:store-001:pos-custom"}
    st, _, again = call(base, "/v1/records", {"bills": BILL, "payments": PAYMENT})
    # The unchanged bill is recognised as a whole (no events rebuilt); the payment by its id.
    assert st == 200 and again["events"] == 0 and again["bills"]["unchanged"] == 1 and again["alreadySent"] == 1
    assert len(sent) == 5


def test_an_edited_bill_posted_again_becomes_the_next_revision(server):
    base, sent, _ = server
    call(base, "/v1/records", {"bills": BILL})
    edited = [dict(BILL[0]), dict(BILL[1], amount="55.00")]
    st, _, body = call(base, "/v1/records", {"bills": edited})
    assert st == 200 and body["bills"]["revised"] == 1
    assert any(e["type"] == "com.pqt.pos.till.event" and e["data"]["kind"] == "void" for e in sent)


@pytest.mark.parametrize("token, headers", [(None, {}), ("wrong-token", {}), (TOKEN[:-1], {}),
                                            (None, {"Authorization": "Basic Zm9vOmJhcg=="})])
def test_without_the_right_token_nothing_happens(server, token, headers):
    base, sent, _ = server
    st, h, body = call(base, "/v1/records", {"bills": BILL}, token=token, headers=headers)
    assert st == 401 and h.get("WWW-Authenticate", "").startswith("Bearer") and sent == []
    assert body["code"] == "unauthorized"


def test_a_client_that_signs_must_sign_correctly(server):
    base, sent, _ = server
    raw = json.dumps({"bills": BILL}).encode()
    good = "sha256=" + hmac.new(SIG_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    st, _, _ = call(base, "/v1/records", raw=raw, token="other-" + TOKEN, headers={"X-PQT-Signature": "sha256=" + "0" * 64})
    assert st == 401 and sent == []
    st, _, _ = call(base, "/v1/records", raw=raw, token="other-" + TOKEN)
    assert st == 401 and sent == []
    st, _, body = call(base, "/v1/records", raw=raw, token="other-" + TOKEN, headers={"X-PQT-Signature": good})
    assert st == 200 and body["events"] == 4


def test_a_client_may_only_send_the_kinds_it_was_given(server):
    base, sent, _ = server
    raw = json.dumps({"payments": PAYMENT}).encode()
    sig = "sha256=" + hmac.new(SIG_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    st, _, body = call(base, "/v1/records", raw=raw, token="other-" + TOKEN, headers={"X-PQT-Signature": sig})
    assert st == 403 and body["code"] == "kind-not-allowed" and sent == []


def test_canonical_documents_are_checked_against_the_schema_before_anything_is_imported(server):
    base, sent, _ = server
    bad = {"bills": BILL, "payments": [dict(PAYMENT[0], tender="bitcoin")]}
    st, _, body = call(base, "/v1/records", bad)
    assert st == 422 and body["code"] == "invalid-records" and sent == []
    assert any("payments/0/tender" in e for e in body["errors"])


@pytest.mark.parametrize("raw, ctype, status, code", [
    (b"{not json", "application/json", 400, "malformed-json"),
    (b"[1,2]", "application/json", 422, "invalid-records"),
    (json.dumps({"bills": BILL}).encode(), "text/csv", 415, "unsupported-media-type"),
])
def test_bad_requests_are_refused_with_a_problem(server, raw, ctype, status, code):
    base, sent, _ = server
    st, h, body = call(base, "/v1/records", raw=raw, headers={"Content-Type": ctype})
    assert (st, body["code"]) == (status, code) and sent == []
    assert h["Content-Type"].startswith("application/problem+json")


def test_an_oversized_body_is_refused_unread(server):
    base, sent, _ = server
    st, _, body = call(base, "/v1/records", raw=b" " * (64 * 1024 + 1))
    assert st == 413 and body["code"] == "too-large" and sent == []


def test_a_client_sending_too_fast_is_told_to_wait(server):
    base, _, _ = server
    codes = [call(base, "/v1/records", {"bills": BILL})[0] for _ in range(14)]
    assert 429 in codes and codes[0] == 200
    st, h, _ = call(base, "/v1/records", {"bills": BILL})
    if st == 429:
        assert int(h["Retry-After"]) >= 1


def test_a_gateway_webhook_is_checked_by_signature_and_mapped_by_its_profile(server):
    base, sent, _ = server
    hook = {"event": "payment.captured", "payload": {"payment": {
        "id": "pay_TEST123", "amount": 54000, "currency": "INR", "method": "upi", "created_at": 1767242740,
        "notes": {"bill_no": "INV-1001"}, "email": "someone@example.com", "contact": PII["Phone"]}}}
    raw = json.dumps(hook).encode()
    sig = hmac.new(SIG_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    st, _, _ = call(base, "/v1/webhooks/gateway", raw=raw, token=None, headers={"X-Gateway-Signature": "f" * 64})
    assert st == 401 and sent == []
    st, _, body = call(base, "/v1/webhooks/gateway", raw=raw, token=None, headers={"X-Gateway-Signature": sig})
    assert st == 200 and body["events"] == 1
    (ev,) = sent
    assert ev["type"] == "com.pqt.pos.payment" and ev["data"]["amountMinor"] == 54000 and ev["data"]["tender"] == "upi"
    assert ev["source"] == "urn:pqt:edge:store-001:pos-gw-test"
    text = json.dumps(sent) + json.dumps(body)
    for v in ("someone@example.com", PII["Phone"], "pay_TEST123", "INV-1001"):
        assert v not in text
    st, _, _ = call(base, "/v1/webhooks/my-pos", raw=raw, token=None, headers={"X-Gateway-Signature": sig})
    assert st == 404, "a push client is not a webhook"


def test_personal_data_posted_by_mistake_leaves_nothing(server):
    base, sent, _ = server
    prof_doc = yaml.safe_load((PROFILES / "canonical.yaml").read_text(encoding="utf-8"))
    assert prof_doc["system"] == "custom"
    leaky = {"bills": [dict(BILL[0], customerName=PII["Customer Name"])]}
    st, _, body = call(base, "/v1/records", leaky)
    assert st == 422 and sent == [], "unknown fields are refused by the canonical schema"
    assert PII["Customer Name"] not in json.dumps(body)


def test_health_schema_and_unknown_routes(server):
    base, _, _ = server
    assert call(base, "/v1/health", token=None)[0:3:2] == (200, {"status": "ok"})
    st, _, schema = call(base, "/v1/schema", token=None)
    assert st == 200 and schema["title"] == "PQT canonical billing records"
    assert call(base, "/v1/nothing", token=None)[0] == 404
    assert call(base, "/v1/records", method="GET")[0] == 405


def test_concurrent_posts_are_imported_once_each(server):
    base, sent, _ = server
    bills = [[dict(line, billNo=f"INV-{n}") for line in BILL] for n in range(8)]
    results = []

    def post(b):
        results.append(call(base, "/v1/records", {"bills": b})[0])
    threads = [threading.Thread(target=post, args=(b,)) for b in bills]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(set(results)) in ([200], [200, 429])
    assert len({e["id"] for e in sent}) == len(sent)


def test_every_route_and_status_the_server_uses_is_in_the_openapi_file():
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    for (method, path), statuses in push.ROUTES.items():
        op = doc["paths"][path][method.lower()]
        assert set(map(str, statuses)) <= set(op["responses"]), (method, path)


def test_the_lan_needs_tls(tmp_path):
    clients = push.Clients([])
    with pytest.raises(ValueError, match="TLS"):
        push.make_server(clients, store="store-001", key=KEY, state_path=tmp_path / "s.db", sink=lambda e: None,
                         bind="0.0.0.0", port=0)


def test_adding_a_client_prints_the_token_once_and_stores_only_its_hash(tmp_path, capsys):
    from pqt_connectors import cli

    cfg = tmp_path / "clients.yaml"
    assert cli.main(["push-client", "add", "--clients", str(cfg), "--id", "shop-pos", "--profile",
                     str(PROFILES / "canonical.yaml")]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    stored = cfg.read_text(encoding="utf-8")
    assert len(token) >= 40 and token not in stored and sha(token) in stored
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"
    assert push.load_clients(cfg).by_token(token).id == "shop-pos"
    assert cli.main(["push-client", "add", "--clients", str(cfg), "--id", "shop-pos", "--profile",
                     str(PROFILES / "canonical.yaml")]) == 1, "ids are unique"
    assert cli.main(["push-client", "remove", "--clients", str(cfg), "--id", "shop-pos"]) == 0
    assert push.load_clients(cfg).by_token(token) is None


def test_secrets_come_from_the_environment_or_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PQT_X", "from-env")
    f = tmp_path / "s"
    f.write_text("from-file\n", encoding="utf-8")
    f.chmod(0o600)
    assert resolve("env:PQT_X") == "from-env" and resolve(f"file:{f}") == "from-file"
    for bad in ("plain-text-secret", "env:PQT_NOT_SET_ANYWHERE"):
        with pytest.raises(SecretError):
            resolve(bad)
    f.chmod(0o644)
    with pytest.raises(SecretError, match="600"):
        resolve(f"file:{f}")


def test_tls_on_the_shop_network(tmp_path):
    import shutil
    import ssl
    import subprocess

    if not shutil.which("openssl"):
        pytest.skip("openssl binary not available to make a test certificate")
    cert, key_file = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key_file), "-out", str(cert),
                    "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True)
    clients_file = tmp_path / "clients.yaml"
    from pqt_connectors.push import add_client

    token = add_client(clients_file, client_id="lan-pos", profile=str(PROFILES / "canonical.yaml"))
    sent: list[dict] = []
    srv = push.make_server(push.load_clients(clients_file), store="store-001", key=KEY, state_path=tmp_path / "s.db",
                           sink=sent.extend, bind="127.0.0.1", port=0, tls=(str(cert), str(key_file)))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        port = srv.server_address[1]
        ctx = ssl.create_default_context(cafile=str(cert))
        req = urllib.request.Request(f"https://127.0.0.1:{port}/v1/records", data=json.dumps({"bills": BILL}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            assert r.status == 200 and json.loads(r.read())["events"] == 4
        with pytest.raises(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=5).read()
    finally:
        srv.shutdown()
        srv.server_close()
