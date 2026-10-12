"""The daily reconcile (X1, milestone B6): do the billing events add up, and do they agree with the
billing system's own day totals? Threshold: 50 basis points (0.5%), ADR-034.

Per store and business date (the day in the store's time zone):

1. `lines_vs_payments`: sales from the bills (latest revision of each bill, cancelled bills excluded)
   minus refunds paid out must equal the net payments received. Exact: any difference is an alert,
   because both sides come from the same billing system.
2. `sales`, `refunds`, `bills`, `tender:<t>`: our sums against `pos.day.total`. An alert when the
   difference exceeds 50 bp of the larger side, or any difference at all on a count.
3. `stock`: for each SKU, the change between two consecutive `stock.level` reports must equal the
   movements in between. An alert for every SKU that does not add up (the shop's own book stock is
   inconsistent: a movement is missing from the export, or stock was edited without a document).

Input: X1 CloudEvents (JSON objects) from any source: an import, the edge outbox or event-core. Output:
a report with every check and the alerts; nothing personal is involved (references are HMACs).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

THRESHOLD_BP = 50  # ADR-034


def _ms(iso: str) -> int:
    return int(datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").timestamp() * 1000)


def _date(ms: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz).date().isoformat()


def bp(diff: int, a: int, b: int) -> float:
    base = max(abs(a), abs(b))
    return 0.0 if diff == 0 else (float("inf") if base == 0 else abs(diff) * 10000.0 / base)


@dataclass
class Day:
    lines: dict[str, dict[int, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    voided: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    paid_in: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    paid_out: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    totals: dict | None = None


def reconcile(events: list[dict], *, tz: str = "Asia/Kolkata", threshold_bp: int = THRESHOLD_BP) -> list[dict]:
    zone = ZoneInfo(tz)
    days: dict[tuple[str, str], Day] = defaultdict(Day)
    stock: dict[tuple[str, str], list[tuple[int, int, str, int]]] = defaultdict(list)  # (store, sku) -> (t, order, kind, qty)
    seen: set[tuple[str, str]] = set()
    for n, e in enumerate(sorted(events, key=lambda e: _ms(e["time"]))):
        key = (e.get("source", ""), e.get("id", ""))
        if key in seen:  # transport duplicates are not double sales
            continue
        seen.add(key)
        t, d, store = _ms(e["time"]), e["data"], e["data"].get("storeId") or e.get("storeid")
        typ = e["type"]
        if typ == "com.pqt.pos.sale.line":
            days[(store, _date(t, zone))].lines[d["billRef"]][d["revision"]] += d["amountMinor"]
        elif typ == "com.pqt.pos.till.event" and d["kind"] == "void":
            days[(store, _date(t, zone))].voided[d["billRef"]].add(d.get("revision", 0))
        elif typ == "com.pqt.pos.payment":
            book = days[(store, _date(t, zone))].paid_in if d["direction"] == "in" else days[(store, _date(t, zone))].paid_out
            book[d["tender"]] += d["amountMinor"]
        elif typ == "com.pqt.pos.day.total":
            days[(store, d["businessDate"])].totals = d
        elif typ == "com.pqt.stock.level":
            stock[(store, d["sku"])].append((t, n, "level", d["qtyMilli"]))
        elif typ == "com.pqt.stock.movement":
            stock[(store, d["sku"])].append((t, n, "move", d["qtyMilli"] if d["direction"] == "in" else -d["qtyMilli"]))

    reports = []
    for (store, date), day in sorted(days.items()):
        # A bill's revisions can straddle midnight only in theory; the latest revision decides the bill.
        sales = bills = 0
        for ref, revs in day.lines.items():
            top = max(revs)
            if top in day.voided[ref]:
                continue
            sales += revs[top]
            bills += 1
        refunds = sum(day.paid_out.values())
        tenders = sorted(set(day.paid_in) | set(day.paid_out))
        net = {k: day.paid_in.get(k, 0) - day.paid_out.get(k, 0) for k in tenders}
        checks = [_check("lines_vs_payments", sales - refunds, sum(net.values()), exact=True)]
        if day.totals:
            tot = day.totals
            checks += [_check("sales", sales, tot["salesMinor"], threshold_bp=threshold_bp),
                       _check("refunds", refunds, tot["refundsMinor"], threshold_bp=threshold_bp),
                       _check("bills", bills, tot["bills"], exact=True)]
            for k in sorted(set(net) | set(tot["paymentsByTender"])):
                checks.append(_check(f"tender:{k}", net.get(k, 0), tot["paymentsByTender"].get(k, 0), threshold_bp=threshold_bp))
        stock_alerts = _stock(stock, store, date, zone)
        alerts = [c["name"] for c in checks if not c["ok"]] + [f"stock:{s['sku']}" for s in stock_alerts]
        reports.append({"storeId": store, "businessDate": date, "thresholdBp": threshold_bp,
                        "dayTotalReported": day.totals is not None, "ours": {"salesMinor": sales, "refundsMinor": refunds,
                                                                               "bills": bills, "paymentsByTender": net},
                        "checks": checks, "stock": stock_alerts, "alerts": alerts, "ok": not alerts})
    return reports


def _check(name: str, ours: int, theirs: int, *, exact: bool = False, threshold_bp: int = THRESHOLD_BP) -> dict:
    diff = ours - theirs
    b = bp(diff, ours, theirs)
    ok = diff == 0 if exact else b <= threshold_bp
    return {"name": name, "ours": ours, "theirs": theirs, "diff": diff, "diffBp": round(b, 1) if b != float("inf") else None,
            "ok": ok}


def _stock(stock: dict, store: str, date: str, zone: ZoneInfo) -> list[dict]:
    out = []
    for (s, sku), items in sorted(stock.items()):
        if s != store:
            continue
        last_level, moved, last_t = None, 0, None
        for t, _, kind, qty in sorted(items):
            if _date(t, zone) != date:
                continue
            if kind == "move":
                moved += qty
                continue
            if last_level is not None and qty - last_level != moved:
                out.append({"sku": sku, "from": last_level, "to": qty, "moved": moved, "unexplainedMilli": qty - last_level - moved,
                            "betweenMs": [last_t, t]})
            last_level, moved, last_t = qty, 0, t
    return out
