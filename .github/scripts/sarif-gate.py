#!/usr/bin/env python3
"""Fail when a SARIF report has findings at or above a CVSS-style security severity.

Usage: sarif-gate.py <dir-with-sarif-files> <min-severity, e.g. 7.0> [accepted.json]

CodeQL tags security queries with properties["security-severity"] (0-10). Results whose
rule has no security severity (quality queries) are listed but never fail the gate.
Reviewed findings can be accepted in a JSON file (rule, path, max, reason); each entry
accepts at most `max` matching results, so a new occurrence still fails.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def rule_severities(run: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    tool = run.get("tool", {})
    for component in [tool.get("driver", {}), *tool.get("extensions", [])]:
        for rule in component.get("rules", []) or []:
            sev = (rule.get("properties") or {}).get("security-severity")
            if sev is not None and rule.get("id"):
                out[rule["id"]] = float(sev)
    return out


def findings(path: Path) -> list[tuple[float | None, str, str]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for run in doc.get("runs", []):
        sev = rule_severities(run)
        for res in run.get("results", []) or []:
            rule = res.get("ruleId") or (res.get("rule") or {}).get("id", "?")
            loc = "?"
            locs = res.get("locations") or []
            if locs:
                pl = locs[0].get("physicalLocation", {})
                loc = f'{pl.get("artifactLocation", {}).get("uri", "?")}:{pl.get("region", {}).get("startLine", "?")}'
            rows.append((sev.get(rule), rule, loc))
    return rows


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    root, threshold = Path(argv[1]), float(argv[2])
    accepted = json.loads(Path(argv[3]).read_text(encoding="utf-8"))["accepted"] if len(argv) == 4 else []
    budget = {(a["rule"], a["path"]): int(a["max"]) for a in accepted}
    files = sorted(root.rglob("*.sarif"))
    if not files:
        print(f"no SARIF files under {root}", file=sys.stderr)
        return 2
    rows = [r for f in files for r in findings(f)]
    blocking, accepted_rows = [], []
    for r in rows:
        if r[0] is None or r[0] < threshold:
            continue
        key = (r[1], r[2].rsplit(":", 1)[0])
        if budget.get(key, 0) > 0:
            budget[key] -= 1
            accepted_rows.append(r)
        else:
            blocking.append(r)
    lines = [f"CodeQL: {len(rows)} result(s), {len(blocking)} blocking and {len(accepted_rows)} accepted "
             f"at security severity >= {threshold}"]
    for sev, rule, loc in sorted(rows, key=lambda r: -(r[0] or 0)):
        tag = "BLOCK" if (sev, rule, loc) in blocking else "accpt" if (sev, rule, loc) in accepted_rows else "info "
        lines.append(f"  {tag} {sev if sev is not None else '-':>4} {rule} {loc}")
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("```\n" + "\n".join(lines) + "\n```\n")
    for sev, rule, loc in blocking[:9]:
        print(f"::error title=codeql {rule}::security-severity {sev} at {loc}")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
