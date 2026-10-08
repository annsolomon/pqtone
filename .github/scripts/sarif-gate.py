#!/usr/bin/env python3
"""Fail when a SARIF report has findings at or above a CVSS-style security severity.

Usage: sarif-gate.py <dir-with-sarif-files> <min-severity, e.g. 7.0>

CodeQL tags security queries with properties["security-severity"] (0-10). Results whose
rule has no security severity (quality queries) are listed but never fail the gate.
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
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    root, threshold = Path(argv[1]), float(argv[2])
    files = sorted(root.rglob("*.sarif"))
    if not files:
        print(f"no SARIF files under {root}", file=sys.stderr)
        return 2
    rows = [r for f in files for r in findings(f)]
    blocking = [r for r in rows if r[0] is not None and r[0] >= threshold]
    lines = [f"CodeQL: {len(rows)} result(s), {len(blocking)} at security severity >= {threshold}"]
    for sev, rule, loc in sorted(rows, key=lambda r: -(r[0] or 0)):
        lines.append(f"  {'BLOCK' if (sev or 0) >= threshold else 'info '} {sev if sev is not None else '-':>4} {rule} {loc}")
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
