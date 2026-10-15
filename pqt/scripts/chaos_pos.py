#!/usr/bin/env python3
"""X1 B5f, for the cable test: a shop's own POS that sends bills to the edge box's push API, and a POS database the
connector service polls. Deterministic invented data (no people, no real bill numbers); standard library only, so it
runs inside the edge-agent container (`python - push ... < scripts/chaos_pos.py`) as well as in the tools image.

    chaos_pos.py push --url http://127.0.0.1:8787 --token-file /var/lib/pqt-edge/push-token --day 2026-10-13 --count 30
    chaos_pos.py db --path /work/edge/state/pos.db --day 2026-10-13 --count 20

`push` prints the API's summed answer as JSON (events, alreadySent, errors) and exits 1 if any request failed.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ITEMS = [("ATTA-5KG", "245.00"), ("DAL-1KG", "120.00"), ("MILK-1L", "33.00"), ("OIL-1L", "180.00"), ("SALT-1KG", "20.00")]


def bills(day: str, count: int, prefix: str) -> list[list[dict]]:
    """`count` bills on `day` (India time), one to three lines each; the same arguments give the same bills."""
    out = []
    for n in range(count):
        minute = 9 * 60 + 5 * n
        ts = f"{day}T{minute // 60:02d}:{minute % 60:02d}:00+05:30"
        lines = []
        for k in range(1 + n % 3):
            sku, price = ITEMS[(n + k) % len(ITEMS)]
            lines.append({"billNo": f"{prefix}-{n:04d}", "timestamp": ts, "till": "pos-2", "lineNo": k + 1, "sku": sku,
                          "qty": 1, "unit": "pcs", "amount": price})
        out.append(lines)
    return out


def payments_for(bill: list[dict]) -> dict:
    total = sum(int(round(float(line["amount"]) * 100)) for line in bill)
    return {"billNo": bill[0]["billNo"], "timestamp": bill[0]["timestamp"].replace(":00+", ":30+"), "till": "pos-2",
            "tender": "upi" if len(bill) % 2 else "cash", "amount": f"{total // 100}.{total % 100:02d}", "direction": "in"}


def push(a: argparse.Namespace) -> int:
    token = open(a.token_file, encoding="utf-8").read().strip()
    total = {"events": 0, "alreadySent": 0, "errors": 0, "requests": 0, "failed": 0}
    for b in bills(a.day, a.count, a.prefix):
        doc = {"bills": b, "payments": [payments_for(b)]}
        req = urllib.request.Request(a.url.rstrip("/") + "/v1/records", data=json.dumps(doc).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        for attempt in range(5):  # a POS retries; re-sending adds nothing
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    body = json.loads(r.read())
                total["events"] += body["events"]
                total["alreadySent"] += body["alreadySent"]
                total["errors"] += len(body["errors"])
                break
            except (urllib.error.URLError, OSError):
                time.sleep(1 + attempt)
        else:
            total["failed"] += 1
        total["requests"] += 1
    print(json.dumps(total))
    return 1 if total["failed"] else 0


def db(a: argparse.Namespace) -> int:
    con = sqlite3.connect(a.path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS bills (bill_no TEXT PRIMARY KEY, billed_at TEXT NOT NULL, counter TEXT NOT NULL,
                                          modified_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bill_lines (bill_no TEXT NOT NULL, line_no INTEGER NOT NULL, item_code TEXT NOT NULL,
                                               qty TEXT NOT NULL, uom TEXT NOT NULL, net TEXT NOT NULL);
    """)
    added = 0
    for b in bills(a.day, a.count, a.prefix):
        no = b[0]["billNo"]
        if con.execute("SELECT 1 FROM bills WHERE bill_no = ?", (no,)).fetchone():
            continue
        at = b[0]["timestamp"][:19].replace("T", " ")
        con.execute("INSERT INTO bills VALUES (?, ?, 'POS 3', ?)", (no, at, time.strftime("%Y-%m-%d %H:%M:%S")))
        for line in b:
            con.execute("INSERT INTO bill_lines VALUES (?, ?, ?, '1', 'Nos', ?)", (no, line["lineNo"], line["sku"], line["amount"]))
        added += 1
    con.commit()
    con.close()
    print(json.dumps({"bills": added}))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pu = sub.add_parser("push")
    pu.add_argument("--url", required=True)
    pu.add_argument("--token-file", required=True)
    pu.add_argument("--day", required=True)
    pu.add_argument("--count", type=int, default=30)
    pu.add_argument("--prefix", default="PUSH")
    pu.set_defaults(fn=push)
    d = sub.add_parser("db")
    d.add_argument("--path", required=True)
    d.add_argument("--day", required=True)
    d.add_argument("--count", type=int, default=20)
    d.add_argument("--prefix", default="DB")
    d.set_defaults(fn=db)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
