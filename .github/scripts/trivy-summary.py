#!/usr/bin/env python3
"""Condense Trivy JSON reports into one line per finding and surface them as annotations.

Usage: trivy-summary.py <report.json> [<report.json> ...]

Prints every vulnerability, misconfiguration and secret as a single line, writes the
list to the job summary, and emits it as GitHub error annotations (split into chunks,
because annotation messages are truncated at about 4 KB). Exit code is always 0: the
Trivy step itself decides pass or fail; this script only makes the result readable.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CHUNK = 3800
MAX_ANNOTATIONS = 9


def lines_for(report: Path) -> list[str]:
    if not report.is_file() or report.stat().st_size == 0:
        return []
    doc = json.loads(report.read_text(encoding="utf-8"))
    out: list[str] = []
    for res in doc.get("Results", []) or []:
        target = res.get("Target", "?")
        for v in res.get("Vulnerabilities", []) or []:
            out.append(f'{v.get("Severity", "?"):8} {v.get("VulnerabilityID", "?"):16} '
                       f'{v.get("PkgName", "?")} {v.get("InstalledVersion", "?")} -> '
                       f'{v.get("FixedVersion", "") or "no fix"} [{target}]')
        for m in res.get("Misconfigurations", []) or []:
            if m.get("Status") == "FAIL":
                out.append(f'{m.get("Severity", "?"):8} {m.get("ID", "?"):16} {m.get("Title", "")} [{target}]')
        for s in res.get("Secrets", []) or []:
            out.append(f'{s.get("Severity", "?"):8} {s.get("RuleID", "?"):16} {s.get("Title", "")} '
                       f'[{target}:{s.get("StartLine", "?")}]')
    return out


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str]) -> int:
    rows: list[str] = []
    for arg in argv[1:]:
        for line in lines_for(Path(arg)):
            if line not in rows:
                rows.append(line)
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    rows.sort(key=lambda r: (order.get(r.split()[0], 9), r))
    header = f"Trivy: {len(rows)} finding(s) in {', '.join(Path(a).name for a in argv[1:])}"
    print(header)
    print("\n".join(rows))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### {header}\n```\n" + "\n".join(rows) + "\n```\n")
    if rows:
        chunks, cur = [], ""
        for r in rows:
            if len(cur) + len(r) + 1 > CHUNK:
                chunks.append(cur)
                cur = ""
            cur += r + "\n"
        chunks.append(cur)
        for i, c in enumerate(chunks[:MAX_ANNOTATIONS], 1):
            print(f"::error title=trivy {i}/{len(chunks)}::{escape(c.rstrip())}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
