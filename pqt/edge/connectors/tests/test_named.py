"""X1 milestone B5e: ready profiles for named systems (ADR-037). Fixtures follow each vendor's public documentation
(see profiles/README.md for what was verified); data is invented."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from pqt_connectors import push, tally
from pqt_connectors.importer import Importer, State
from pqt_connectors.profile import load
from pqt_connectors.refs import Refs

from test_file_connector import KEY, SCHEMAS
from test_push import call

HERE = Path(__file__).resolve().parent
PROFILES = HERE.parent / "profiles"
FIX = HERE / "fixtures"


def test_every_ready_profile_loads():
    for p in sorted(PROFILES.glob("*.yaml")):
        load(p)


# -- Razorpay webhooks ---------------------------------------------------------------------------------------------

@pytest.fixture
def razorpay(tmp_path, monkeypatch):
    monkeypatch.setenv("PQT_TEST_RZP_SECRET", "rzp-test-webhook-secret")
    clients = tmp_path / "clients.yaml"
    push.add_client(clients, client_id="razorpay", profile=str(PROFILES / "razorpay-webhook.yaml"), webhook=True,
                    kind="payments", signature={"header": "X-Razorpay-Signature", "secret": "env:PQT_TEST_RZP_SECRET"},
                    only={"event": ["payment.captured"]})
    sent: list[dict] = []
    srv = push.make_server(push.load_clients(clients), store="store-001", key=KEY, state_path=tmp_path / "s.db",
                           sink=sent.extend, schemas=SCHEMAS, bind="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", sent
    srv.shutdown()
    srv.server_close()


def sign(raw: bytes) -> str:
    return hmac.new(b"rzp-test-webhook-secret", raw, hashlib.sha256).hexdigest()


def test_a_razorpay_payment_captured_webhook_becomes_a_payment(razorpay):
    base, sent = razorpay
    raw = (FIX / "razorpay" / "payment.captured.json").read_bytes()
    st, _, body = call(base, "/v1/webhooks/razorpay", raw=raw, token=None, headers={"X-Razorpay-Signature": sign(raw)})
    assert st == 200 and body["events"] == 1, body
    (ev,) = sent
    d = ev["data"]
    assert ev["type"] == "com.pqt.pos.payment" and d["tender"] == "upi" and d["amountMinor"] == 54000 and d["direction"] == "in"
    assert ev["time"] == "2026-01-01T04:45:40.000Z", "created_at is epoch seconds"
    assert ev["source"] == "urn:pqt:edge:store-001:pos-razorpay"
    text = json.dumps(sent)
    for v in ("someone@upi", "someone@example.com", "+919000000000", "pay_TESTPAYMENT01", "order_TESTORDER001"):
        assert v not in text
    st, _, again = call(base, "/v1/webhooks/razorpay", raw=raw, token=None, headers={"X-Razorpay-Signature": sign(raw)})
    assert st == 200 and again["events"] == 0, "Razorpay retries deliveries: a repeat adds nothing"


def test_other_razorpay_events_are_acknowledged_and_ignored(razorpay):
    base, sent = razorpay
    doc = json.loads((FIX / "razorpay" / "payment.captured.json").read_text(encoding="utf-8"))
    doc["event"] = "payment.failed"
    doc["payload"]["payment"]["entity"]["status"] = "failed"
    raw = json.dumps(doc).encode()
    st, _, body = call(base, "/v1/webhooks/razorpay", raw=raw, token=None, headers={"X-Razorpay-Signature": sign(raw)})
    assert st == 200 and body["ignored"] is True and body["events"] == 0 and sent == []


def test_a_tampered_razorpay_body_is_refused(razorpay):
    base, sent = razorpay
    raw = (FIX / "razorpay" / "payment.captured.json").read_bytes()
    st, _, _ = call(base, "/v1/webhooks/razorpay", raw=raw.replace(b"54000", b"5400000"), token=None,
                    headers={"X-Razorpay-Signature": sign(raw)})
    assert st == 401 and sent == []


# -- TallyPrime ------------------------------------------------------------------------------------------------------

class FakeTally:
    def __init__(self, reply: bytes):
        self.reply, self.requests = reply, []
        tally_ = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                tally_.requests.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(tally_.reply)))
                self.end_headers()
                self.wfile.write(tally_.reply)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def tally_server():
    t = FakeTally((FIX / "tally" / "daybook.xml").read_bytes())
    yield t
    t.close()


def shop_profile(tmp: Path) -> Path:
    """The shop's copy of the TallyPrime profile: its own cash and bank ledgers named under maps.tender."""
    doc = yaml.safe_load((PROFILES / "tallyprime.yaml").read_text(encoding="utf-8"))
    doc["maps"]["tender"]["upi"] = list(doc["maps"]["tender"].get("upi") or []) + ["HDFC Bank UPI"]
    p = tmp / "tally-shop.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


def tally_poll(tmp: Path, t: FakeTally, sent: list, *, today=date(2026, 1, 2), **extra):
    cfg = {"id": "shop-tally", "type": "tally", "profile": str(shop_profile(tmp)), "url": t.url,
           "company": "Example Stores & Co", "start": "2026-01-01", "lookbackDays": 3, **extra}
    src = tally.TallySource.from_config(cfg, base=tmp, today=lambda: today)
    state = State(tmp / "state.db")
    try:
        imp = Importer(src.profile, store="store-001", refs=Refs(KEY, src.profile.system), state=state, schemas=SCHEMAS)
        return src.poll(imp, sent.extend)
    finally:
        state.close()


def test_tally_day_book_becomes_bills_payments_returns_and_purchases(tmp_path, tally_server):
    sent: list[dict] = []
    r = tally_poll(tmp_path, tally_server, sent)
    assert r["errors"] == [], r["errors"]
    by = {}
    for e in sent:
        by.setdefault(e["type"], []).append(e["data"])
    lines = sorted((d["sku"], d["qtyMilli"], d["unit"], d["amountMinor"]) for d in by["com.pqt.pos.sale.line"])
    assert lines == [("Atta-5-Kg", 2000, "pcs", 49000), ("Milk-1-Ltr", 2000, "l", 6600), ("Onion-Loose", 1250, "kg", 5000)]
    pays = sorted((d["tender"], d["direction"], d["amountMinor"]) for d in by["com.pqt.pos.payment"])
    assert pays == [("cash", "in", 54000), ("cash", "out", 24500), ("upi", "in", 6600)]
    moves = sorted((d["sku"], d["direction"], d["reason"], d["qtyMilli"]) for d in by["com.pqt.stock.movement"])
    assert ("Atta-5-Kg", "in", "purchase_receipt", 20000) in moves and ("Atta-5-Kg", "in", "customer_return", 1000) in moves
    assert r["vouchers"] == {"Sales": 2, "Credit Note": 1, "Purchase": 1, "cancelled": 1, "other": 1}


def test_the_request_asks_for_the_day_book_of_the_company_over_the_window(tmp_path, tally_server):
    tally_poll(tmp_path, tally_server, [])
    req = tally_server.requests[0]
    assert "<TALLYREQUEST>Export</TALLYREQUEST>" in req and "<ID>Day Book</ID>" in req
    assert "<SVFROMDATE>20251229</SVFROMDATE>" in req and "<SVTODATE>20260102</SVTODATE>" in req, "start less lookback"
    assert "<SVCURRENTCOMPANY>Example Stores &amp; Co</SVCURRENTCOMPANY>" in req, "the company name is XML-escaped"


def test_re_reading_the_window_adds_nothing_and_the_cursor_moves_to_today(tmp_path, tally_server):
    first: list[dict] = []
    r = tally_poll(tmp_path, tally_server, first)
    assert r["cursor"] == "2026-01-02"
    again: list[dict] = []
    tally_poll(tmp_path, tally_server, again, today=date(2026, 1, 3))
    assert again == []
    assert "<SVFROMDATE>20251230</SVFROMDATE>" in tally_server.requests[-1], "cursor less lookback"


def test_party_names_from_tally_never_leave(tmp_path, tally_server):
    sent: list[dict] = []
    r = tally_poll(tmp_path, tally_server, sent)
    text = json.dumps(sent) + json.dumps(r)
    for v in ("Asha Example", "Supplier Traders", "S-101", "CN-7", "P-55"):
        assert v not in text


def test_an_xml_bomb_from_the_tally_port_is_refused(tmp_path):
    bomb = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">' \
           b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><ENVELOPE>&c;</ENVELOPE>'
    t = FakeTally(bomb)
    try:
        sent: list[dict] = []
        r = tally_poll(tmp_path, t, sent)
        assert sent == [] and r["errors"] and "XML" in r["errors"][0]["error"] and r["cursor"] is None
    finally:
        t.close()


@pytest.mark.parametrize("url", ["http://8.8.8.8:9000", "ftp://127.0.0.1:9000", "http://"])
def test_tally_is_reached_over_the_shop_network_only(tmp_path, url):
    with pytest.raises(ValueError):
        tally.TallySource.from_config({"id": "t", "type": "tally", "profile": str(PROFILES / "tallyprime.yaml"),
                                       "url": url, "company": "X", "start": "2026-01-01"}, base=tmp_path)
