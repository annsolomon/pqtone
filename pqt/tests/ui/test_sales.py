"""X1 milestone B7: the Sales page in a browser.

A simulated trading morning for store 002 (bills with a revision and a void, a refund, payments by tender,
visitors, two queues, a stock movement) is published through the gateway as a tagged simulation run. The
page opens that run on its own day without being told which, and shows the billing system's figures next to
what the cameras saw: sales, bills, payments by tender, the 15-minute table, stock movements. axe-core finds
no serious or critical accessibility problem on the filled page.

Runs after test_demo.py and test_review_accessibility.py (pytest orders files by name). Synthetic data only.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from axe_report import AxeReport
from test_demo import BASE, PUBLIC, REPORTS, sign_in

pytestmark = pytest.mark.skipif(
    PUBLIC != BASE, reason=f"browser tests need PQT_PUBLIC_URL={BASE} (currently {PUBLIC}); run scripts/mode.sh localhost")

STORE = "store-002"
SOURCE = f"urn:pqt:sim:store-sim:{STORE}"
TOKEN_URL = f"{BASE}/auth/realms/pqt/protocol/openid-connect/token"
SCHEMAS = "https://schemas.pqt.local"


def _event(run: str, short: str, schema: str, subject: str, time: str, data: dict) -> dict:
    return {
        "specversion": "1.0", "id": f"ui-sales-{uuid.uuid4()}", "source": SOURCE, "type": f"com.pqt.{short}", "time": time,
        "subject": subject, "dataschema": f"{SCHEMAS}/{schema}/1.0.0", "datacontenttype": "application/json",
        "storeid": STORE, "partitionkey": STORE, "simrunid": run, "data": data,
    }


def trading_morning(run: str) -> list[dict]:
    """1 January 2026, 09:30-10:50 India time (04:00-05:20 UTC). Expected: 2 bills for 200.00, 1 voided,
    UPI 150.00 in and 30.00 refunded, cash 50.00, 6 visitors."""
    def bill_ref(n: int) -> str:
        return f"bill-{run[4:]}{n:020x}"

    def line(till, n, rev, ln, sku, amount, t):
        return _event(run, "pos.sale.line", "pos/sale.line", f"till:{till}", t, {
            "storeId": STORE, "tillId": till, "billRef": bill_ref(n), "revision": rev, "lineNo": ln, "sku": sku,
            "qtyMilli": 1000, "unit": "pcs", "amountMinor": amount, "currency": "INR"})

    def pay(till, n, no, tender, direction, amount, t):
        return _event(run, "pos.payment", "pos/payment", f"till:{till}", t, {
            "storeId": STORE, "tillId": till, "billRef": bill_ref(n), "paymentNo": no, "tender": tender,
            "direction": direction, "amountMinor": amount, "currency": "INR"})

    def till_event(till, kind, n, rev, amount, t):
        d = {"storeId": STORE, "tillId": till, "kind": kind, "billRef": bill_ref(n), "amountMinor": amount, "currency": "INR"}
        if rev is not None:
            d["revision"] = rev
        return _event(run, "pos.till.event", "pos/till.event", f"till:{till}", t, d)

    def queue(q, length, t):
        return _event(run, "store.queue.length", "store/queue.length", f"queue:{q}", t,
                      {"queueId": q, "length": length, "openRegisters": 1})

    def visitor(i, t):
        track = f"trk-ui{run[4:12]}{i}"
        return _event(run, "store.zone.entered", "store/zone.entered", f"track:{track}", t, {"zoneId": "entrance", "trackId": track})

    events = [
        _event(run, "stock.level", "stock/level", "sku:E2E-RICE", "2026-01-01T04:00:00.000Z",
               {"storeId": STORE, "sku": "E2E-RICE", "qtyMilli": 50000, "unit": "kg", "source": "sim"}),
        queue("checkout-1", 1, "2026-01-01T04:25:00.000Z"),
        queue("checkout-2", 0, "2026-01-01T04:25:00.000Z"),
        *[visitor(i, f"2026-01-01T{4 + (30 + i * 6) // 60:02d}:{(30 + i * 6) % 60:02d}:00.000Z") for i in range(6)],  # 04:30 .. 05:00
        line("reg-1", 1, 0, 1, "E2E-ATTA", 12000, "2026-01-01T04:35:00.000Z"),
        line("reg-1", 1, 0, 2, "E2E-DAL", 3000, "2026-01-01T04:35:00.001Z"),
        pay("reg-1", 1, 1, "upi", "in", 15000, "2026-01-01T04:35:30.000Z"),
        _event(run, "stock.movement", "stock/movement", "sku:E2E-RICE", "2026-01-01T04:36:00.000Z",
               {"storeId": STORE, "sku": "E2E-RICE", "direction": "out", "reason": "sale", "qtyMilli": 2000, "unit": "kg"}),
        queue("checkout-1", 4, "2026-01-01T04:40:00.000Z"),
        queue("checkout-2", 3, "2026-01-01T04:50:00.000Z"),
        line("reg-3", 2, 0, 1, "E2E-OIL", 8000, "2026-01-01T04:52:00.000Z"),
        till_event("reg-3", "void", 2, 0, 8000, "2026-01-01T04:53:00.000Z"),
        line("reg-3", 2, 1, 1, "E2E-OIL-S", 5000, "2026-01-01T04:54:00.000Z"),
        pay("reg-3", 2, 1, "cash", "in", 5000, "2026-01-01T04:54:30.000Z"),
        queue("checkout-1", 2, "2026-01-01T05:00:00.000Z"),
        line("reg-2", 3, 0, 1, "E2E-SALT", 2500, "2026-01-01T05:05:00.000Z"),
        till_event("reg-2", "void", 3, 0, 2500, "2026-01-01T05:05:30.000Z"),
        till_event("reg-1", "refund", 1, None, 3000, "2026-01-01T05:20:00.000Z"),
        pay("reg-1", 1, 2, "upi", "out", 3000, "2026-01-01T05:20:00.001Z"),
    ]
    return events


def test_the_sales_page_opens_a_simulated_day_and_shows_billing_next_to_the_cameras():
    run = f"run-{secrets.token_hex(6)}"
    with sync_playwright() as p:
        api = p.request.new_context()
        try:
            tok = api.post(TOKEN_URL, form={"grant_type": "client_credentials", "client_id": "store-sim",
                                            "client_secret": os.environ["PQT_SIM_CLIENT_SECRET"]})
            assert tok.ok, tok.text()
            r = api.post(f"{BASE}/v1/events/batch", data=json.dumps(trading_morning(run)),
                         headers={"Content-Type": "application/cloudevents-batch+json", "Authorization": f"Bearer {tok.json()['access_token']}"})
            assert r.ok, r.text()
            refused = [x for x in r.json()["results"] if x["status"] != "accepted"]
            assert not refused, "events refused:\n" + "\n".join(json.dumps(x) for x in refused)
        finally:
            api.dispose()

        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        AxeReport.install(context)
        page = context.new_page()
        try:
            sign_in(page, "reviewer", os.environ["PQT_REVIEWER_PASSWORD"])
            page.get_by_role("navigation", name="Main").get_by_role("link", name="Sales").click()
            expect(page.get_by_role("heading", level=1, name="Sales and stock")).to_be_visible()
            page.goto(f"{BASE}/sales?store={STORE}")

            expect(page.get_by_label("Data", exact=True)).to_have_value("sim", timeout=30_000)
            expect(page.get_by_label("Data", exact=True).locator("option:checked")).to_have_text(f"Simulation {run}")
            expect(page.get_by_label("Day", exact=True)).to_have_value("2026-01-01")
            expect(page.get_by_test_id("sales-total")).to_have_text("₹200.00", timeout=30_000)
            expect(page.get_by_test_id("sales-bills")).to_have_text("2")
            facts = page.get_by_role("region", name="The day in figures")
            expect(facts).to_contain_text("Voided bills1")
            expect(facts).to_contain_text("Visitors6")
            expect(facts).to_contain_text("Bills per 100 visitors33.3")

            chart = page.get_by_role("img", name=re.compile(r"^2 bills for ₹200\.00, busiest at 10:00 with ₹150\.00; the longest queue was 4\.$"))
            expect(chart).to_be_visible()

            tenders = page.get_by_role("table", name="Payments by tender")
            upi = tenders.get_by_role("row", name=re.compile("^UPI"))
            expect(upi).to_contain_text("₹150.00")
            expect(upi).to_contain_text("₹30.00")
            expect(upi).to_contain_text("₹120.00")
            expect(tenders.get_by_role("row", name=re.compile("^Cash"))).to_contain_text("₹50.00")

            page.get_by_text("Show the 15-minute figures as a table").click()
            quarter = page.get_by_role("table", name="Sales and queue every 15 minutes")
            first = quarter.get_by_role("row").filter(has_text=re.compile(r"^10:00"))
            expect(first).to_contain_text("₹150.00")

            stock = page.get_by_role("table", name="Stock movements per SKU")
            expect(stock.get_by_role("row", name=re.compile("E2E-RICE"))).to_contain_text("2 kg sold")
            expect(stock.get_by_role("row", name=re.compile("E2E-RICE"))).to_contain_text("50 kg at 09:30")

            # X1 B5f: the billing sources panel (store 002 has no edge box: it says so).
            sources = page.get_by_role("region", name="Billing sources")
            expect(sources).to_contain_text("No edge box has reported for this store")

            report = AxeReport()
            report.scan(page, "sales")
            report.write(Path(REPORTS) / "axe-sales")
            blocking = report.blocking()
            assert not blocking, "serious/critical accessibility violations on the Sales page:\n" + "\n".join(
                f"  {v['id']} ({v['impact']}) x{v['count']} - {v['help']} - {[n['target'] for n in v['nodes']]}" for _, v in blocking)
            page.screenshot(path=str(REPORTS / "b7-sales.png"), full_page=True)
        finally:
            context.close()
            browser.close()
