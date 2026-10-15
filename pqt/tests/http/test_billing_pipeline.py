"""X1 milestone B8: the console shows what the billing system sold, after the whole trip.

store-sim's trading day -> billing-system CSV exports (as yesterday) -> file connector on the edge box ->
edge outbox (through a cable cut) -> event-core -> the Sales page's reads. Run by scripts/chaos-edge.sh after
scripts/billing_e2e_check.py, which wrote the expected figures to $PQT_BILLING_E2E; skipped in the ordinary
e2e-http run, where no such day has been imported yet.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from conftest import login

REPORT = os.environ.get("PQT_BILLING_E2E")
pytestmark = pytest.mark.skipif(not REPORT, reason="runs inside make chaos-edge (X1 B8, B5f), after the billing import")
IST = timezone(timedelta(hours=5, minutes=30))


def expected() -> dict:
    with open(REPORT, encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["pass"], f"the billing check failed first: {doc['problems']}"
    return doc


def day_window(day: str) -> str:
    start = datetime.fromisoformat(day).replace(tzinfo=IST)
    f = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t = (start + timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"from={f}&to={t}&tz=Asia/Kolkata"


def test_the_consoles_sales_for_the_imported_day_match_the_billing_system():
    doc = expected()
    v = login("viewer")
    r = v.get(f"/api/stores/{doc['storeId']}/sales?{day_window(doc['day'])}&bucket=day")
    assert r.status_code == 200, r.text
    totals = {t["currency"]: t for t in r.json()["totals"]}
    mine = totals[doc["currency"]]
    assert mine["salesMinor"] == doc["truth"]["salesMinor"]
    assert mine["bills"] == doc["truth"]["bills"]


def test_the_consoles_payments_for_the_imported_day_match_the_billing_system():
    doc = expected()
    v = login("viewer")
    r = v.get(f"/api/stores/{doc['storeId']}/payments?{day_window(doc['day'])}&bucket=day")
    assert r.status_code == 200, r.text
    net = {t["tender"]: t["netMinor"] for t in r.json()["totals"] if t["currency"] == doc["currency"]}
    out = sum(t["outMinor"] for t in r.json()["totals"] if t["currency"] == doc["currency"])
    assert net == doc["truth"]["paymentsByTender"]
    assert out == doc["truth"]["refundsMinor"]


def test_the_consoles_stock_for_the_imported_day_has_every_sku_that_moved():
    doc = expected()
    v = login("viewer")
    skus, after = [], None
    while True:
        r = v.get(f"/api/stores/{doc['storeId']}/stock?{day_window(doc['day']).rsplit('&tz=', 1)[0]}&limit=1000"
                  + (f"&after={after}" if after else ""))
        assert r.status_code == 200, r.text
        body = r.json()
        skus += body["skus"]
        after = body["next"]
        if not after:
            break
    sold = [s for s in skus if s["outByReason"].get("sale")]
    assert sold, "bills take stock off the shelf"
    assert all(s["level"] is not None for s in sold), "every SKU sold has a reported level from the billing system"


def test_the_edge_boxs_billing_sources_are_reported_healthy_on_the_console():
    """X1 B5f: the connector service's health, carried by the agent's heartbeat (edge.heartbeat 1.1.0)."""
    v = login("viewer")
    r = v.get("/api/stores/store-001/edge")
    assert r.status_code == 200, r.text
    agents = {a["agentId"]: a for a in r.json()["agents"]}
    assert "edge-chaos" in agents, list(agents)
    conns = {c["id"]: c for c in agents["edge-chaos"]["connectors"]}
    assert set(conns) == {"push", "chaos-db"}, conns
    assert conns["chaos-db"]["ok"] and conns["chaos-db"]["events"] > 0 and conns["chaos-db"]["failures"] == 0
    assert conns["push"]["ok"] and conns["push"]["type"] == "push"
