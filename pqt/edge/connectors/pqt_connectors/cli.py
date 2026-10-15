"""pqt-connectors command line.

    pqt-connectors init-key --key /var/lib/pqt-edge/billing.key
    pqt-connectors import --profile profiles/generic-csv.yaml --store store-001 --key billing.key \\
        --state /var/lib/pqt-edge/connector-state.db --outbox /var/lib/pqt-edge/outbox.db \\
        --bills bills.csv --payments payments.csv --movements purchases.csv --stock stock.csv --day-totals day.csv

Exit codes (import): 0 every row imported; 2 some rows were reported (the rest was imported); 1 usage or
setup error. Exit codes (reconcile): 0 every day adds up; 3 at least one alert.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import profile as profile_mod
from .importer import Importer, State
from .refs import Refs, load_key, new_key


def _import(a: argparse.Namespace) -> int:
    prof = profile_mod.load(a.profile)
    if not any([a.bills, a.payments, a.movements, a.stock, a.day_totals]):
        print("nothing to import: give at least one of --bills --payments --movements --stock --day-totals",
              file=sys.stderr)
        return 1
    if bool(a.outbox) == bool(a.out):
        print("give exactly one of --outbox (the edge outbox) or --out (a JSONL file)", file=sys.stderr)
        return 1
    state = State(a.state)
    imp = Importer(prof, store=a.store, refs=Refs(load_key(a.key), prof.system), state=state, schemas=a.schemas)
    for path, fn in ((a.bills, imp.bills), (a.payments, imp.payments), (a.movements, imp.movements),
                     (a.stock, imp.stock), (a.day_totals, imp.day_totals)):
        if path:
            fn(path)
    if a.outbox:
        from pqt_edge.outbox import Outbox  # the same outbox the camera events use (A3)

        box = Outbox(a.outbox)
        report = imp.commit(box.append)
        box.close()
    else:
        def write(events: list[dict]) -> None:
            with open(a.out, "a", encoding="utf-8") as f:
                for e in events:
                    f.write(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n")
        report = imp.commit(write)
    state.close()
    doc = report.as_dict()
    if a.report:
        Path(a.report).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: doc[k] for k in ("files", "events", "bills")} | {"errors": len(doc["errors"])}))
    for e in doc["errors"][:20]:
        print(f"  {e['file']} row {e['row']}: {e['error']}", file=sys.stderr)
    return 2 if doc["errors"] else 0


def _reconcile(a: argparse.Namespace) -> int:
    from .reconcile import THRESHOLD_BP, reconcile

    events = []
    for path in a.events:
        events += [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    events = [e for e in events if e.get("type", "").startswith(("com.pqt.pos.", "com.pqt.stock."))]
    reports = reconcile(events, tz=a.tz, threshold_bp=a.threshold_bp or THRESHOLD_BP)
    if a.report:
        Path(a.report).write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    for r in reports:
        print(f"{r['storeId']} {r['businessDate']}: {'ok' if r['ok'] else 'ALERT ' + ', '.join(r['alerts'])}")
    return 0 if all(r["ok"] for r in reports) else 3


def _init_key(a: argparse.Namespace) -> int:
    new_key(a.key)
    print(f"wrote a new store key to {a.key} (mode 600). Back it up with the box: losing it orphans old references.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pqt-connectors", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("init-key", help="create the store's reference key (once, at install)")
    k.add_argument("--key", required=True)
    k.set_defaults(fn=_init_key)
    i = sub.add_parser("import", help="import billing-system CSV exports")
    i.add_argument("--profile", required=True)
    i.add_argument("--store", required=True)
    i.add_argument("--key", required=True, help="the store key file (mode 600)")
    i.add_argument("--state", required=True, help="connector state (SQLite): bill revisions and payment numbers")
    i.add_argument("--outbox", help="the edge outbox database")
    i.add_argument("--out", help="append events to this JSONL file instead (dry run)")
    i.add_argument("--schemas", help="validate every event against this schemas/ directory before sending")
    i.add_argument("--bills")
    i.add_argument("--payments")
    i.add_argument("--movements")
    i.add_argument("--stock")
    i.add_argument("--day-totals")
    i.add_argument("--report", help="write the import report (files, events, bills, errors by row) here")
    i.set_defaults(fn=_import)
    r = sub.add_parser("reconcile", help="B6: check a day's billing events against each other and the day totals")
    r.add_argument("--events", nargs="+", required=True, help="JSONL files of X1 events")
    r.add_argument("--tz", default="Asia/Kolkata", help="the store's time zone (business dates)")
    r.add_argument("--threshold-bp", type=int, help="alert above this many basis points (default 50, ADR-034)")
    r.add_argument("--report")
    r.set_defaults(fn=_reconcile)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (profile_mod.ProfileError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
