#!/usr/bin/env python3
"""Emit the scorer results as notice annotations, one compact line per (scenario, rule).

Usage: score-annotate.py <label> <score.json> [<baseline.candidate.json>]

The job summary already has the full Markdown table; annotations make the same numbers
readable from the PR's checks and through the checks API.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CHUNK = 3500


def esc(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    label, path = argv[1], Path(argv[2])
    if not path.is_file():
        print(f"{path} not found; nothing to annotate")
        return 0
    doc = json.loads(path.read_text(encoding="utf-8"))
    lines = [f"{'PASS' if doc.get('passed') else 'FAIL'}  (scenario rule mode: tp/fp/fn precision recall p95ms gate)"]
    for r in doc.get("results", []):
        s = r["score"]
        p95 = s["latencyMs"]["p95"]
        gate = "ok" if not r["failures"] else "; ".join(r["failures"])
        lines.append(f"{r['scenario']} {r['ruleId']} {r.get('mode', '')}: {s['tp']}/{s['fp']}/{s['fn']} "
                     f"{s['precision']:.3f} {s['recall']:.3f} {'-' if p95 is None else p95} {gate}")
    lines += [f"other: {f}" for f in doc.get("otherFailures", [])]
    chunks, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > CHUNK:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for i, c in enumerate(chunks, 1):
        print(f"::notice title={label} {i}/{len(chunks)}::{esc(c.rstrip())}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
