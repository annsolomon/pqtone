"""X1 milestone B3: billing events through the gateway into event-core, and the console's billing reads.

A real producer may send a bill up to 35 days old (day-end exports, ADR-036) while camera events keep the
24-hour bound; an edge source writes only the store it names; the sales, payments and stock reads show what
was ingested for that store and nothing else. Every reference is made-up hex; no real data.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import requests

from conftest import BASE, edge_event, login, now_iso

CE = {"Content-Type": "application/cloudevents+json"}
STORE = "store-001"


def post(event, token):
    return requests.post(f"{BASE}/v1/events", data=json.dumps(event),
                         headers={**CE, "Authorization": f"Bearer {token}"}, timeout=15)


def billing_event(short: str, schema_path: str, subject: str, time: str, data: dict, store: str = STORE) -> dict:
    return {
        # Its own source: the file connector's (pos-file) belongs to the B8 cable test, which checks every event in it.
        "specversion": "1.0", "id": f"e2e-{uuid.uuid4()}", "source": f"urn:pqt:edge:{STORE}:pos-e2e",
        "type": f"com.pqt.{short}", "time": time, "subject": subject,
        "dataschema": f"https://schemas.pqt.local/{schema_path}/1.0.0", "datacontenttype": "application/json",
        "storeid": store, "partitionkey": store, "data": {"storeId": store, **data},
    }


def sale_line(till: str, bill: str, amount: int, time: str, sku: str = "E2E-ITEM") -> dict:
    return billing_event("pos.sale.line", "pos/sale.line", f"till:{till}", time, {
        "tillId": till, "billRef": f"bill-{bill}", "revision": 0, "lineNo": 1, "sku": sku, "qtyMilli": 1000,
        "unit": "pcs", "amountMinor": amount, "currency": "INR"})


def payment(till: str, bill: str, amount: int, time: str, tender: str = "upi", direction: str = "in") -> dict:
    return billing_event("pos.payment", "pos/payment", f"till:{till}", time, {
        "tillId": till, "billRef": f"bill-{bill}", "paymentNo": 1, "tender": tender, "direction": direction,
        "amountMinor": amount, "currency": "INR"})


def window(days_back: int = 4) -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    f = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"from={f}&to={t}"


def test_a_three_day_old_bill_is_accepted_and_a_three_day_old_camera_event_is_refused(edge_token):
    three_days_ago = now_iso(-3 * 86400)
    bill = payment("till-e2e-age", uuid.uuid4().hex, 1500, three_days_ago)
    r = post(bill, edge_token)
    assert r.status_code == 202, r.text
    camera = edge_event(time=three_days_ago)
    r = post(camera, edge_token)
    assert r.status_code == 400
    assert "time: older than PT24H" in r.json()["errors"]
    ancient = payment("till-e2e-age", uuid.uuid4().hex, 1500, now_iso(-36 * 86400))
    r = post(ancient, edge_token)
    assert r.status_code == 400 and "time: older than PT840H" in r.json()["errors"]


def test_an_edge_source_cannot_write_another_store(edge_token):
    other = edge_event(storeid="store-002", partitionkey="store-002")
    r = post(other, edge_token)
    assert r.status_code == 403 and r.json()["code"] == "store-not-allowed", r.text
    mismatched = sale_line("till-e2e-x", uuid.uuid4().hex, 100, now_iso(-60))
    mismatched["data"]["storeId"] = "store-002"
    r = post(mismatched, edge_token)
    assert r.status_code == 400 and "data.storeId: must equal storeid" in r.json()["errors"]


def test_billing_reads_show_what_was_ingested_for_that_store_only(edge_token):
    till = f"till-e2e-{uuid.uuid4().hex[:8]}"
    sku = f"E2E-{uuid.uuid4().hex[:8].upper()}"
    bill = uuid.uuid4().hex
    t = now_iso(-2 * 86400)
    for ev in (sale_line(till, bill, 24900, t, sku), payment(till, bill, 24900, now_iso(-2 * 86400 + 1))):
        r = post(ev, edge_token)
        assert r.status_code == 202, r.text
    movement = billing_event("stock.movement", "stock/movement", f"sku:{sku}", now_iso(-2 * 86400 + 2), {
        "sku": sku, "direction": "out", "reason": "sale", "qtyMilli": 1000, "unit": "pcs"})
    assert post(movement, edge_token).status_code == 202

    v = login("viewer")
    sales = v.get(f"/api/stores/{STORE}/sales?{window()}&bucket=day&tz=UTC&till={till}")
    assert sales.status_code == 200, sales.text
    body = sales.json()
    assert body["simRunId"] is None and body["tillId"] == till
    assert [(b["bills"], b["salesMinor"], b["currency"]) for b in body["buckets"]] == [(1, 24900, "INR")]

    pays = v.get(f"/api/stores/{STORE}/payments?{window()}&till={till}").json()
    assert pays["timeZone"] == "Asia/Kolkata"
    assert [(b["tender"], b["inMinor"], b["outMinor"], b["netMinor"]) for b in pays["buckets"]] == [("upi", 24900, 0, 24900)]

    stock = v.get(f"/api/stores/{STORE}/stock?{window()}&after={sku[:-1]}&limit=1000").json()
    mine = [s for s in stock["skus"] if s["sku"] == sku]
    assert len(mine) == 1 and mine[0]["outMilli"] == 1000 and mine[0]["outByReason"] == {"sale": 1000}

    elsewhere = v.get(f"/api/stores/store-002/sales?{window()}&till={till}").json()
    assert elsewhere["buckets"] == [], "store-001's bill is not in store-002's sales"


def test_billing_read_parameters_are_checked():
    v = login("viewer")
    assert v.get(f"/api/stores/{STORE}/sales?{window()}&bucket=week").json()["code"] == "bad-bucket"
    assert v.get(f"/api/stores/{STORE}/sales?{window(40)}").json()["code"] == "window-too-long"
    assert v.get(f"/api/stores/{STORE}/sales?{window(8)}&bucket=hour").json()["code"] == "window-too-long"
    assert v.get(f"/api/stores/{STORE}/sales?{window(3)}&bucket=15m").json()["code"] == "window-too-long"
    assert v.get(f"/api/stores/{STORE}/sales?{window(1)}&bucket=15m").status_code == 200
    assert v.get(f"/api/stores/{STORE}/payments?{window()}&tz=Mars/Olympus").json()["code"] == "bad-time-zone"
    assert v.get(f"/api/stores/{STORE}/stock?{window()}&limit=0").json()["code"] == "bad-limit"
    assert v.get(f"/api/stores/{STORE}/sales?to=2026-01-01T00:00:00Z").status_code == 400
    assert v.get(f"/api/stores/Store-1/sales?{window()}").status_code == 400


def test_billing_reads_need_a_session():
    assert requests.get(f"{BASE}/api/stores/{STORE}/sales?{window()}", timeout=10).status_code == 401


def test_traffic_counts_visitors_and_queues_in_the_same_buckets():
    """X1 B7: the traffic read lines up with the sales buckets, names the entrance zones and returns counts only."""
    v = login("viewer")
    r = v.get(f"/api/stores/{STORE}/traffic?{window(1)}&bucket=15m&tz=UTC")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entranceZones"] == ["entrance"]
    assert len(body["buckets"]) in (100, 101), "25 hours of 15-minute buckets, every one present"
    assert all(set(b) == {"start", "visitors", "queues"} for b in body["buckets"])
    assert "trk-" not in r.text, "no track id leaves the server"
    assert v.get(f"/api/stores/store-999/traffic?{window(1)}").status_code == 404
