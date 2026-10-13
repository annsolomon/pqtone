#!/usr/bin/env python3
"""X1 B8 verdict: a simulated trading day, exported as yesterday's billing-system CSVs, imported by the file
connector on the edge box during the cable test, arrived in event-core and adds up.

  1. Every billing event the connector put in the outbox is in pqt.event exactly once (the cable test checks
     the sequences; this checks the business content).
  2. The daily reconcile (B6, ADR-034) over the stored events finds nothing: lines vs payments exact, our
     sums vs the billing system's day totals within 50 bp, stock movements vs level changes.
  3. Our sums equal the simulator's ground truth: sales, bills, refunds, net by tender.
  4. Each event has its typed row in event-core's billing tables (B3), one for one.

Read-only, as pqt_read. Writes the figures to --out for the console check (tests/http/test_billing_pipeline.py).

Usage (tools container): python scripts/billing_e2e_check.py --run /work/edge/billing-run --day 2026-10-12
    --source urn:pqt:edge:store-001:pos-file --import-report /work/edge/state/billing-import.json
    --out /work/edge/reports/billing-e2e.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pqt_connectors.reconcile import reconcile

TABLES = {"com.pqt.pos.sale.line": "pos_sale_line", "com.pqt.pos.payment": "pos_payment",
          "com.pqt.pos.till.event": "pos_till_event", "com.pqt.stock.movement": "stock_movement",
          "com.pqt.stock.level": "stock_level", "com.pqt.pos.day.total": "pos_day_total"}


def _iso(t: datetime) -> str:
    u = t.astimezone(timezone.utc)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True, help="the store-sim run directory (billing_truth.json)")
    p.add_argument("--day", required=True, help="the trading day the export was moved to (YYYY-MM-DD)")
    p.add_argument("--source", required=True, help="the connector's CloudEvents source")
    p.add_argument("--import-report", required=True, help="the connector's import report (first import)")
    p.add_argument("--reimport-report", help="the second import of the same files: must add nothing")
    p.add_argument("--tz", default="Asia/Kolkata")
    p.add_argument("--out", required=True)
    a = p.parse_args()

    truth_doc = json.loads((Path(a.run) / "billing_truth.json").read_text())
    truth = truth_doc[0]
    imported = json.loads(Path(a.import_report).read_text())
    problems: list[str] = []

    import psycopg

    with psycopg.connect(os.environ["PQT_SCORER_DSN"]) as conn:
        rows = conn.execute(
            "SELECT id, source, type, event_time, data::text FROM pqt.event WHERE source = %s ORDER BY event_time, event_seq",
            (a.source,)).fetchall()
        events = [{"id": r[0], "source": r[1], "type": r[2], "time": _iso(r[3]),
                   "data": json.loads(r[4])} for r in rows]
        ids = [e["id"] for e in events]
        if len(ids) != len(set(ids)):
            problems.append(f"{len(ids) - len(set(ids))} billing events stored more than once")
        by_type: dict[str, int] = {}
        for e in events:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        typed = {}
        for t, table in TABLES.items():
            # Table names come from the fixed map above, never from input.
            typed[t] = conn.execute(f"SELECT count(*) FROM pqt.{table} WHERE source = %s", (a.source,)).fetchone()[0]
            if typed[t] != by_type.get(t, 0):
                problems.append(f"{table}: {typed[t]} rows for {by_type.get(t, 0)} events")

    if len(events) != imported["events"]:
        problems.append(f"the connector sent {imported['events']} events, event-core holds {len(events)}")
    if imported.get("errors"):
        problems.append(f"the connector reported {len(imported['errors'])} rows")
    if a.reimport_report:
        again = json.loads(Path(a.reimport_report).read_text())
        if again["events"] != 0:
            problems.append(f"re-importing the same files added {again['events']} events")

    reports = reconcile(events, tz=a.tz)
    days = [r for r in reports if r["businessDate"] == a.day]
    if len(reports) != 1 or not days:
        problems.append(f"expected one reconciled day {a.day}, got {[r['businessDate'] for r in reports]}")
    day = days[0] if days else None
    if day and not day["ok"]:
        problems.append(f"the reconcile raised {day['alerts']}")
    if day and not day["dayTotalReported"]:
        problems.append("the billing system's day totals did not arrive")
    expected = truth["dayTotals"]
    ours = day["ours"] if day else {}
    for k in ("salesMinor", "refundsMinor", "bills"):
        if ours.get(k) != expected[k]:
            problems.append(f"{k}: ours {ours.get(k)}, truth {expected[k]}")
    if day and dict(sorted(ours["paymentsByTender"].items())) != dict(sorted(expected["paymentsByTender"].items())):
        problems.append(f"net by tender: ours {ours['paymentsByTender']}, truth {expected['paymentsByTender']}")

    result = {"day": a.day, "storeId": truth["storeId"], "currency": truth["currency"], "source": a.source,
              "events": len(events), "byType": by_type, "typedRows": typed, "truth": expected, "ours": ours,
              "reconcile": day, "problems": problems, "pass": not problems}
    Path(a.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"billing: {len(events)} events from {a.source} for {a.day}; "
          f"sales {ours.get('salesMinor')} (truth {expected['salesMinor']}), bills {ours.get('bills')} (truth {expected['bills']}), "
          f"reconcile {'ok' if day and day['ok'] else 'ALERT'}")
    print("| Type | Events | Typed rows |\n|---|---|---|")
    for t in TABLES:
        print(f"| {t} | {by_type.get(t, 0)} | {typed[t]} |")
    print("PASS" if not problems else "FAIL:\n- " + "\n- ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
