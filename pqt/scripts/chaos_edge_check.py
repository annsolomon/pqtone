#!/usr/bin/env python3
"""A4 verdict: every event in the edge outbox reached PostgreSQL exactly once, sequences are gap-free per
source (each camera, and the billing connector, X1 B8), nothing is left pending or rejected, and the camera came
back within 10 s. Read-only on both sides.

Usage (tools container): python scripts/chaos_edge_check.py --outbox /work/edge/state/outbox.db
                          --run /work/edge/reports/run.json --out /work/edge/reports/chaos-edge.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

CAMERA_RECOVERY_LIMIT_S = 10.0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--outbox", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--require-source", action="append", default=[],
                   help="a source that must be in the outbox (X1 B8: the billing connector's)")
    a = p.parse_args()
    run = json.load(open(a.run))
    ob = sqlite3.connect(f"file:{a.outbox}?mode=ro", uri=True)
    rows = ob.execute("SELECT source, seq, event_id, state FROM outbox ORDER BY source, seq").fetchall()
    problems: list[str] = []
    by_source: dict[str, list[tuple[int, str, str]]] = {}
    for src, seq, eid, state in rows:
        by_source.setdefault(src, []).append((seq, eid, state))

    import psycopg

    table = []
    with psycopg.connect(os.environ["PQT_SCORER_DSN"]) as conn:
        for src, items in sorted(by_source.items()):
            seqs = [s for s, _, _ in items]
            gaps = sorted(set(range(1, max(seqs) + 1)) - set(seqs))
            ids = [e for _, e, _ in items]
            counts = dict(conn.execute("SELECT id, count(*) FROM pqt.event WHERE source = %s AND id = ANY(%s) GROUP BY id",
                                       (src, ids)).fetchall())
            missing = [e for e in ids if counts.get(e, 0) == 0]
            dups = [e for e in ids if counts.get(e, 0) > 1]
            pending = sum(1 for *_, st in items if st == "pending")
            rejected = sum(1 for *_, st in items if st == "rejected")
            table.append({"source": src, "firstSeq": min(seqs), "lastSeq": max(seqs), "events": len(ids),
                          "stored": sum(1 for e in ids if counts.get(e, 0) >= 1), "missing": len(missing),
                          "duplicate": len(dups), "pending": pending, "rejected": rejected, "gaps": len(gaps)})
            if gaps:
                problems.append(f"{src}: {len(gaps)} gaps in the outbox sequence (first {gaps[:5]})")
            if missing:
                problems.append(f"{src}: {len(missing)} events never reached PostgreSQL")
            if dups:
                problems.append(f"{src}: {len(dups)} events stored more than once")
            if pending or rejected:
                problems.append(f"{src}: {pending} pending and {rejected} rejected in the outbox")
    if not table:
        problems.append("the outbox is empty: the agent produced nothing")
    for src in a.require_source:
        if src not in by_source:
            problems.append(f"{src}: nothing from this source in the outbox")
    rec = run.get("cameraRecoverySeconds")
    if rec is None or rec > CAMERA_RECOVERY_LIMIT_S:
        problems.append(f"camera recovery {rec} s is not within {CAMERA_RECOVERY_LIMIT_S} s")
    result = {"run": run, "sources": table, "problems": problems, "pass": not problems}
    with open(a.out, "w") as f:
        json.dump(result, f, indent=2)
    print("| Source | Sequences | Events | Stored | Missing | Duplicate | Pending | Rejected |")
    print("|---|---|---|---|---|---|---|---|")
    for t in table:
        print(f"| {t['source']} | {t['firstSeq']}..{t['lastSeq']} | {t['events']} | {t['stored']} | {t['missing']} | "
              f"{t['duplicate']} | {t['pending']} | {t['rejected']} |")
    print(f"\ncut {run.get('cutSeconds')} s, agent killed with SIGKILL mid-stream, camera back after {rec} s")
    print("PASS" if not problems else "FAIL:\n- " + "\n- ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
