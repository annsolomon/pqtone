"""Write a simulated run's billing as the CSV exports a billing system would produce (X1, B4/B8).

The files follow `edge/connectors/profiles/generic-csv.yaml`, so the file connector can import them and
the end-to-end test can compare what arrives with the simulator's ground truth. Like a real export they
list each bill's current version only (voided bills that were never re-issued are left out), payments,
purchases and returns (sales leave stock through the bills), stock snapshots and the day totals. Bill
and document numbers are made up from the simulator's references (`SIM-` + 10 hex digits); India time.

`shift_days` moves every date by whole days (times of day unchanged), so a run simulated at its fixed epoch
can be exported as yesterday's trading day: event-core accepts real billing events up to 35 days old
(ADR-036), and the end-to-end test (B8) needs exactly that.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
UNIT = {"pcs": "Nos", "kg": "Kgs", "g": "Gm", "l": "Ltr", "ml": "Ml", "m": "Mtr", "box": "Box", "pack": "Pkt", "other": "Other"}
TENDER = {"cash": "Cash", "upi": "UPI", "card": "Card", "wallet": "Wallet", "credit": "Credit Sale", "voucher": "Gift Voucher",
          "other": "Other"}
REASON = {"purchase_receipt": "Purchase", "customer_return": "Sales Return", "supplier_return": "Purchase Return",
          "transfer_in": "Stock Transfer In", "transfer_out": "Stock Transfer Out", "adjustment": "Stock Adjustment",
          "damage": "Damaged"}


def _parse(iso: str) -> datetime:
    return datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")


def _local(iso: str, shift_days: int = 0) -> tuple[str, str]:
    dt = (_parse(iso) + timedelta(days=shift_days)).astimezone(IST)
    return dt.strftime("%d-%m-%Y"), dt.strftime("%H:%M:%S")


def first_local_date(events: list[dict]) -> date | None:
    """The India-time date of a run's first billing event (what `shift_days` is counted from)."""
    times = [_parse(e["time"]) for e in events if not e["type"].startswith("com.pqt.store.")]
    return min(times).astimezone(IST).date() if times else None


def _amount(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{abs(minor) // 100}.{abs(minor) % 100:02d}"


def _qty(milli: int) -> str:
    sign = "-" if milli < 0 else ""
    whole, frac = divmod(abs(milli), 1000)
    return f"{sign}{whole}" + (f".{frac:03d}".rstrip("0") if frac else "")


def _no(ref: str) -> str:
    return "SIM-" + ref.split("-", 1)[1][:10].upper()


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def export(events: list[dict], out: str | Path, *, store: str | None = None, shift_days: int = 0) -> dict[str, int]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    evs = [e for e in events if not e["type"].startswith("com.pqt.store.") and (store is None or e["storeid"] == store)]
    lines: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    voided: dict[str, set[int]] = defaultdict(set)
    for e in evs:
        d = e["data"]
        if e["type"] == "com.pqt.pos.sale.line":
            lines[d["billRef"]][d["revision"]].append(e)
        elif e["type"] == "com.pqt.pos.till.event" and d["kind"] == "void":
            voided[d["billRef"]].add(d["revision"])
    bill_rows = []
    for ref, revs in lines.items():
        top = max(revs)
        if top in voided[ref]:
            continue
        for e in sorted(revs[top], key=lambda x: x["data"]["lineNo"]):
            d = e["data"]
            date, time = _local(e["time"], shift_days)
            bill_rows.append([_no(ref), date, time, d["tillId"], d["lineNo"], d["sku"], _qty(d["qtyMilli"]),
                              UNIT[d["unit"]], _amount(d["amountMinor"]), _amount(d.get("taxMinor", 0))])
    _write(out / "bills.csv", ["Bill No", "Date", "Time", "Counter", "Line", "Item Code", "Qty", "Unit", "Amount", "Tax"],
           bill_rows)
    pay_rows, mv_rows, stock_rows, day_rows = [], [], [], []
    for e in evs:
        d = e["data"]
        date, time = _local(e["time"], shift_days)
        if e["type"] == "com.pqt.pos.payment":
            pay_rows.append([_no(d["billRef"]), date, time, d["tillId"], TENDER[d["tender"]], _amount(d["amountMinor"]),
                             "Payment" if d["direction"] == "in" else "Refund",
                             ("TXN-" + d["providerRef"][4:14].upper()) if d.get("providerRef") else ""])
        elif e["type"] == "com.pqt.stock.movement" and d["reason"] != "sale":
            mv_rows.append([_no(d["docRef"]), date, time, d["sku"], _qty(d["qtyMilli"]), UNIT[d["unit"]],
                            "Inward" if d["direction"] == "in" else "Outward", REASON[d["reason"]], d.get("lineNo", "")])
        elif e["type"] == "com.pqt.stock.level":
            stock_rows.append([d["sku"], _qty(d["qtyMilli"]), UNIT[d["unit"]], date, time])
        elif e["type"] == "com.pqt.pos.day.total":
            t = d["paymentsByTender"]
            business = datetime.strptime(d["businessDate"], "%Y-%m-%d") + timedelta(days=shift_days)
            day_rows.append([business.strftime("%d-%m-%Y"), _amount(d["salesMinor"]),
                             _amount(d["refundsMinor"]), d["bills"], *[_amount(t[k]) if k in t else "" for k in
                                                                       ("cash", "upi", "card", "wallet", "credit")]])
    _write(out / "payments.csv", ["Bill No", "Date", "Time", "Counter", "Mode", "Amount", "Type", "Txn Ref"], pay_rows)
    _write(out / "movements.csv", ["Doc No", "Date", "Time", "Item Code", "Qty", "Unit", "Direction", "Reason", "Line"],
           mv_rows)
    _write(out / "stock.csv", ["Item Code", "Qty", "Unit", "Date", "Time"], stock_rows)
    _write(out / "day.csv", ["Date", "Sales", "Refunds", "Bills", "Cash", "UPI", "Card", "Wallet", "Credit"], day_rows)
    counts = {"bills": len(bill_rows), "payments": len(pay_rows), "movements": len(mv_rows), "stock": len(stock_rows),
              "dayTotals": len(day_rows), "shiftDays": shift_days}
    (out / "export.json").write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
    return counts
