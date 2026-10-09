"""Milestone Q3: compare the committed baseline with a candidate from CI, for a human to accept.

  python -m pqt_scorer.baseline_diff scorer/baseline.json baseline.candidate.json [--markdown OUT]

Exit 0 when the two are identical, 3 when they differ (scripts/baseline-accept.sh opens a PR
only then), 2 on bad input. Drops are listed first and flagged: accepting one lowers the bar
the regression gate protects, so the PR must say why.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

METRICS = ("precision", "recall")


@dataclass(frozen=True)
class Change:
    scenario: str
    rule: str
    kind: str                 # "drop", "rise", "new", "removed"
    old: dict | None
    new: dict | None

    def worst_drop_pp(self) -> float:
        if not self.old or not self.new:
            return 0.0
        return max((self.old[m] - self.new[m]) * 100 for m in METRICS)


def diff(baseline: dict, candidate: dict) -> list[Change]:
    out: list[Change] = []
    for sc in sorted(set(baseline) | set(candidate)):
        b, c = baseline.get(sc, {}), candidate.get(sc, {})
        for rule in sorted(set(b) | set(c)):
            old, new = b.get(rule), c.get(rule)
            if old is None:
                out.append(Change(sc, rule, "new", None, new))
            elif new is None:
                out.append(Change(sc, rule, "removed", old, None))
            elif any(new[m] < old[m] for m in METRICS):
                out.append(Change(sc, rule, "drop", old, new))
            elif any(new[m] != old[m] for m in METRICS):
                out.append(Change(sc, rule, "rise", old, new))
    order = {"drop": 0, "removed": 1, "new": 2, "rise": 3}
    return sorted(out, key=lambda ch: (order[ch.kind], -ch.worst_drop_pp(), ch.scenario, ch.rule))


def _cell(d: dict | None, m: str) -> str:
    return "-" if d is None else f"{d[m]:.4f}"


def markdown(changes: list[Change], *, source: str = "") -> str:
    if not changes:
        return "The candidate matches the committed baseline. Nothing to accept.\n"
    drops = [c for c in changes if c.kind in ("drop", "removed")]
    lines = ["## Proposed quality baseline", ""]
    if source:
        lines += [f"Candidate: {source}", ""]
    if drops:
        lines += [f"**{len(drops)} row(s) get worse or disappear.** Accepting them lowers what the regression "
                  "gate protects. Say why in this PR, or don't merge it.", ""]
    lines += ["| Change | Scenario | Rule | Precision (old → new) | Recall (old → new) |", "|---|---|---|---|---|"]
    for c in changes:
        lines.append(f"| {c.kind} | {c.scenario} | {c.rule} | {_cell(c.old, 'precision')} → {_cell(c.new, 'precision')} | "
                     f"{_cell(c.old, 'recall')} → {_cell(c.new, 'recall')} |")
    lines += ["", "Never merged automatically (CLAUDE.md). A maintainer reviews and merges."]
    return "\n".join(lines) + "\n"


def _load(path: str) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not all(isinstance(v, dict) for v in doc.values()):
        raise ValueError(f"{path} is not a baseline file ({{scenario: {{rule: {{precision, recall}}}}}})")
    for sc, rules in doc.items():
        for rule, m in rules.items():
            if not all(isinstance(m.get(k), (int, float)) and 0 <= m[k] <= 1 for k in METRICS):
                raise ValueError(f"{path}: {sc}/{rule} needs precision and recall between 0 and 1")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare the committed baseline with a CI candidate")
    ap.add_argument("baseline")
    ap.add_argument("candidate")
    ap.add_argument("--markdown", help="write the PR body here")
    ap.add_argument("--source", default="", help="where the candidate came from, for the PR body")
    a = ap.parse_args(argv)
    try:
        changes = diff(_load(a.baseline), _load(a.candidate))
    except (OSError, ValueError) as e:
        print(f"baseline-diff: {e}", file=sys.stderr)
        return 2
    md = markdown(changes, source=a.source)
    if a.markdown:
        Path(a.markdown).write_text(md, encoding="utf-8")
    print(md, end="")
    return 3 if changes else 0


if __name__ == "__main__":
    raise SystemExit(main())
