"""pqt-connectors command line.

    pqt-connectors init-key --key /var/lib/pqt-edge/billing.key
    pqt-connectors import --profile profiles/generic-csv.yaml --store store-001 --key billing.key \\
        --state /var/lib/pqt-edge/connector-state.db --outbox /var/lib/pqt-edge/outbox.db \\
        --bills bills.csv --payments payments.csv --movements purchases.csv --stock stock.csv --day-totals day.csv
    pqt-connectors watch --dir /srv/billing-drop --profile my-pos.yaml --store store-001 --key billing.key \\
        --state connector-state.db --outbox outbox.db          # imports what is saved into the folder
    pqt-connectors check --profile my-pos.yaml --store store-001 --bills sample.json   # dry run, nothing kept
    pqt-connectors suggest --kind bills --sample SalesRegister.xlsx --system my-pos > my-pos.yaml
    pqt-connectors push-client add --clients push-clients.yaml --id shop-pos --profile profiles/canonical.yaml
    pqt-connectors serve --clients push-clients.yaml --store store-001 --key billing.key --state state.db \\
        --outbox outbox.db                                       # the push API on 127.0.0.1:8787 (B5b)

Files may be CSV, XLSX, JSON or NDJSON (ADR-037).

Exit codes (import, check): 0 every row imported; 2 some rows were reported (the rest was imported); 1 usage
or setup error. Exit codes (reconcile): 0 every day adds up; 3 at least one alert. watch runs until stopped
(--once: one poll; exit 2 when a file failed).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import profile as profile_mod
from .importer import Importer, State
from .refs import Refs, load_key, new_key

KIND_ARGS = (("bills", "bills"), ("payments", "payments"), ("movements", "movements"), ("stock", "stock"),
             ("day_totals", "dayTotals"))


def _sink(a: argparse.Namespace):
    """(append, close) for the edge outbox or a JSONL file."""
    if a.outbox:
        from pqt_edge.outbox import Outbox  # the same outbox the camera events use (A3)

        box = Outbox(a.outbox)
        return box.append, box.close

    def write(events: list[dict]) -> None:
        with open(a.out, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n")
    return write, lambda: None


def _import(a: argparse.Namespace) -> int:
    prof = profile_mod.load(a.profile)
    if not any(getattr(a, arg) for arg, _ in KIND_ARGS):
        print("nothing to import: give at least one of --bills --payments --movements --stock --day-totals",
              file=sys.stderr)
        return 1
    if bool(a.outbox) == bool(a.out):
        print("give exactly one of --outbox (the edge outbox) or --out (a JSONL file)", file=sys.stderr)
        return 1
    state = State(a.state)
    imp = Importer(prof, store=a.store, refs=Refs(load_key(a.key), prof.system), state=state, schemas=a.schemas)
    for arg, kind in KIND_ARGS:
        if getattr(a, arg):
            imp.ingest(kind, getattr(a, arg))
    append, close = _sink(a)
    try:
        report = imp.commit(append)
    finally:
        close()
        state.close()
    doc = report.as_dict()
    if a.report:
        Path(a.report).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: doc[k] for k in ("files", "events", "bills")} | {"errors": len(doc["errors"])}))
    for e in doc["errors"][:20]:
        print(f"  {e['file']} row {e['row']}: {e['error']}", file=sys.stderr)
    return 2 if doc["errors"] else 0


def _check(a: argparse.Namespace) -> int:
    """Dry run: an in-memory state and a throw-away key, so nothing is kept and no reference is the real one."""
    import os
    from collections import Counter

    prof = profile_mod.load(a.profile)
    if not any(getattr(a, arg) for arg, _ in KIND_ARGS):
        print("nothing to check: give at least one of --bills --payments --movements --stock --day-totals", file=sys.stderr)
        return 1
    imp = Importer(prof, store=a.store, refs=Refs(os.urandom(32), prof.system), state=State(":memory:"),
                   schemas=a.schemas)
    for arg, kind in KIND_ARGS:
        if getattr(a, arg):
            imp.ingest(kind, getattr(a, arg))
    events: list[dict] = []
    report = imp.commit(events.extend).as_dict()
    imp.state.close()
    kinds = {kind: report["files"].get(Path(getattr(a, arg)).name, 0) for arg, kind in KIND_ARGS if getattr(a, arg)}
    print(json.dumps({"profile": prof.name, "records": kinds, "events": dict(sorted(Counter(e["type"] for e in events).items())),
                      "sample": events[: a.show], "errors": report["errors"],
                      "note": "dry run: a throw-away key, so references differ from a real import; nothing was kept"},
                     indent=2, ensure_ascii=False))
    return 2 if report["errors"] else 0


def _suggest(a: argparse.Namespace) -> int:
    import yaml

    from .suggest import suggest

    candidates = [Path(a.base)] if a.base else [Path(__file__).resolve().parent.parent / "profiles" / "generic-csv.yaml",
                                                Path("/app/profiles/generic-csv.yaml")]
    found = next((p for p in candidates if p.is_file()), None)
    if a.base and found is None:
        print(f"no profile at {a.base}", file=sys.stderr)
        return 1
    base = yaml.safe_load(found.read_text(encoding="utf-8")) if found else {}
    base.pop("files", None)
    print(suggest(a.kind, a.sample, system=a.system, base=base), end="")
    return 0


def _watch(a: argparse.Namespace) -> int:
    import signal
    import time

    from . import watch

    prof = profile_mod.load(a.profile)
    key = load_key(a.key)
    if bool(a.outbox) == bool(a.out):
        print("give exactly one of --outbox (the edge outbox) or --out (a JSONL file)", file=sys.stderr)
        return 1
    if not Path(a.dir).is_dir():
        print(f"{a.dir} is not a folder", file=sys.stderr)
        return 1
    stop = {"now": False}

    def _stop(*_):
        stop["now"] = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    def make() -> Importer:
        return Importer(prof, store=a.store, refs=Refs(key, prof.system), state=State(a.state), schemas=a.schemas)
    failed = False
    while True:
        append, close = _sink(a)
        try:
            summary = watch.run_once(a.dir, prof, make, append, min_age_s=a.min_age)
        finally:
            close()
        if summary["imported"] or summary["failed"]:
            print(json.dumps(summary), flush=True)
        failed = failed or bool(summary["failed"])
        if a.once or stop["now"]:
            return 2 if failed else 0
        for _ in range(int(a.interval * 10)):
            if stop["now"]:
                return 2 if failed else 0
            time.sleep(0.1)


def _serve(a: argparse.Namespace) -> int:
    import signal

    from . import push

    if bool(a.outbox) == bool(a.out):
        print("give exactly one of --outbox (the edge outbox) or --out (a JSONL file)", file=sys.stderr)
        return 1
    if bool(a.tls_cert) != bool(a.tls_key):
        print("give both --tls-cert and --tls-key", file=sys.stderr)
        return 1
    clients = push.load_clients(a.clients)
    append, close = _sink(a)
    srv = push.make_server(clients, store=a.store, key=load_key(a.key), state_path=a.state, sink=append,
                           schemas=a.schemas, bind=a.bind, port=a.port,
                           tls=(a.tls_cert, a.tls_key) if a.tls_cert else None, allow_insecure_lan=a.insecure_lan,
                           max_body=int(a.max_body_mb * 1024 * 1024), rate=(a.rate, a.burst),
                           records_schema=a.records_schema)

    def _stop(*_):
        import threading

        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    scheme = "https" if a.tls_cert else "http"
    print(json.dumps({"listening": f"{scheme}://{a.bind}:{srv.server_address[1]}", "clients": [c.id for c in clients.clients]}),
          flush=True)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
        close()
    return 0


def _push_client(a: argparse.Namespace) -> int:
    from . import push

    if a.action == "list":
        for c in push.load_clients(a.clients).clients:
            print(f"{c.id}\t{'webhook ' + c.kind if c.webhook else 'push'}\t{c.profile_path}"
                  f"\t{','.join(sorted(c.kinds)) if c.kinds else 'all kinds'}\t{'signed' if c.signature else ''}")
        return 0
    if a.action == "remove":
        push.remove_client(a.clients, a.id)
        print(f"removed {a.id}")
        return 0
    if not a.id or not a.profile:
        print("add needs --id and --profile", file=sys.stderr)
        return 1
    sig = None
    if a.sig_header:
        sig = {"header": a.sig_header, "secret": a.sig_secret, "prefix": a.sig_prefix or "", "encoding": a.sig_encoding}
    token = push.add_client(a.clients, client_id=a.id, profile=a.profile,
                            kinds=a.kinds.split(",") if a.kinds else None, webhook=a.webhook, kind=a.kind, signature=sig)
    if token:
        print(f"added {a.id}. Its token, shown only now (the file keeps a hash): give it to the software as\n"
              f"Authorization: Bearer <token>")
        print(token)
    else:
        print(f"added webhook {a.id}: POST /v1/webhooks/{a.id}, signed with {a.sig_header}")
    return 0


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
    i = sub.add_parser("import", help="import billing-system exports (CSV, XLSX, JSON, NDJSON)")
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
    c = sub.add_parser("check", help="dry-run a profile on sample files: records, events, every error; nothing kept")
    c.add_argument("--profile", required=True)
    c.add_argument("--store", default="store-001")
    c.add_argument("--schemas", help="also validate every event against this schemas/ directory")
    c.add_argument("--show", type=int, default=3, help="how many events to print (pseudonymised, as they would leave)")
    for flag in ("--bills", "--payments", "--movements", "--stock", "--day-totals"):
        c.add_argument(flag)
    c.set_defaults(fn=_check)
    s_ = sub.add_parser("suggest", help="print a starting profile for a sample export, to review")
    s_.add_argument("--kind", required=True, choices=["bills", "payments", "movements", "stock", "dayTotals"])
    s_.add_argument("--sample", required=True, help="a CSV, XLSX, JSON or NDJSON sample (fake data is enough)")
    s_.add_argument("--system", default="my-system", help="short name for the software ([a-z0-9-])")
    s_.add_argument("--base", help="copy formats and word maps from this profile (default: the generic profile)")
    s_.set_defaults(fn=_suggest)
    w = sub.add_parser("watch", help="import files saved into a folder, once each; move them to done/ or failed/")
    w.add_argument("--dir", required=True)
    w.add_argument("--profile", required=True)
    w.add_argument("--store", required=True)
    w.add_argument("--key", required=True)
    w.add_argument("--state", required=True)
    w.add_argument("--outbox")
    w.add_argument("--out")
    w.add_argument("--schemas")
    w.add_argument("--interval", type=float, default=30, help="seconds between polls")
    w.add_argument("--min-age", type=float, default=10, help="a file must be unchanged this long before it is read")
    w.add_argument("--once", action="store_true")
    w.set_defaults(fn=_watch)
    sv = sub.add_parser("serve", help="the push API: the shop's software or a webhook posts records (B5b)")
    sv.add_argument("--clients", required=True, help="clients file (pqt-connectors push-client add)")
    sv.add_argument("--store", required=True)
    sv.add_argument("--key", required=True)
    sv.add_argument("--state", required=True)
    sv.add_argument("--outbox")
    sv.add_argument("--out")
    sv.add_argument("--schemas")
    sv.add_argument("--records-schema", help="records.schema.json (default: the one shipped with the package)")
    sv.add_argument("--bind", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)
    sv.add_argument("--tls-cert")
    sv.add_argument("--tls-key")
    sv.add_argument("--insecure-lan", action="store_true", help="allow a non-loopback bind without TLS (closed networks only)")
    sv.add_argument("--max-body-mb", type=float, default=5)
    sv.add_argument("--rate", type=float, default=10, help="requests per second per client")
    sv.add_argument("--burst", type=int, default=20)
    sv.set_defaults(fn=_serve)
    pc = sub.add_parser("push-client", help="add, remove or list push and webhook clients")
    pc.add_argument("action", choices=["add", "remove", "list"])
    pc.add_argument("--clients", required=True)
    pc.add_argument("--id")
    pc.add_argument("--profile")
    pc.add_argument("--kinds", help="comma-separated kinds this client may send (default: all its profile maps)")
    pc.add_argument("--webhook", action="store_true", help="a webhook sender: no token, a signature instead")
    pc.add_argument("--kind", help="webhooks: the kind each body is")
    pc.add_argument("--sig-header", help="header carrying the HMAC-SHA256 of the body")
    pc.add_argument("--sig-secret", help="env:NAME or file:/path of the shared secret")
    pc.add_argument("--sig-prefix", help="text before the signature, e.g. sha256=")
    pc.add_argument("--sig-encoding", choices=["hex", "base64"], default="hex")
    pc.set_defaults(fn=_push_client)
    r = sub.add_parser("reconcile", help="B6: check a day's billing events against each other and the day totals")
    r.add_argument("--events", nargs="+", required=True, help="JSONL files of X1 events")
    r.add_argument("--tz", default="Asia/Kolkata", help="the store's time zone (business dates)")
    r.add_argument("--threshold-bp", type=int, help="alert above this many basis points (default 50, ADR-034)")
    r.add_argument("--report")
    r.set_defaults(fn=_reconcile)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (profile_mod.ProfileError, OSError, ValueError) as e:  # SecretError is a ValueError
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
