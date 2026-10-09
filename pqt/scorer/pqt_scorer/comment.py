"""Milestone Q1: render the rule-quality score as one sticky pull-request comment.

The comment starts with MARKER so CI can find its own earlier comment and edit it instead of
adding a new one on every push. Precision and recall carry a delta, in percentage points,
against the committed baseline (scorer/baseline.json on the PR's base branch).

  python -m pqt_scorer.comment --offline score.json --baseline baseline.json \
      [--e2e e2e/score.json] [--baseline-ref main@abc1234] [--run-url URL] --out comment.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MARKER = "<!-- pqt-score -->"


def _fmt_delta(cur: float, base: float | None) -> str:
    if base is None:
        return f"{cur:.3f} (new)"
    d = (cur - base) * 100
    if abs(d) < 0.05:
        return f"{cur:.3f} (±0.0)"
    return f"{cur:.3f} ({'▲' if d > 0 else '▼'}{abs(d):.1f}pp)"


def _changed(r: dict, baseline: dict) -> bool:
    b = baseline.get(r["scenario"], {}).get(r["ruleId"])
    if b is None:
        return True
    s = r["score"]
    return abs(s["precision"] - b["precision"]) >= 0.0005 or abs(s["recall"] - b["recall"]) >= 0.0005


def _row(r: dict, baseline: dict) -> str:
    s = r["score"]
    b = baseline.get(r["scenario"], {}).get(r["ruleId"])
    p95 = s["latencyMs"]["p95"]
    gate = "ok" if not r["failures"] else "; ".join(r["failures"])
    return (f"| {r['scenario']} | {r['ruleId']} | {r.get('mode', '')} | {s['tp']}/{s['fp']}/{s['fn']} | "
            f"{_fmt_delta(s['precision'], b and b['precision'])} | {_fmt_delta(s['recall'], b and b['recall'])} | "
            f"{'-' if p95 is None else f'{p95 / 1000:.1f}s'} | {gate} |")


HEADER = ["| Scenario | Rule | Mode | TP/FP/FN | Precision (Δ) | Recall (Δ) | p95 | Gate |",
          "|---|---|---|---:|---:|---:|---:|---|"]


def _section(title: str, doc: dict, baseline: dict, *, changes: bool) -> list[str]:
    out = [f"**{title}: {'PASS' if doc.get('passed') else 'FAIL'}**", ""]
    results = doc.get("results", [])
    if changes:
        diff = [r for r in results if _changed(r, baseline)]
        out += ["### Changes against the baseline", ""]
        out += (HEADER + [_row(r, baseline) for r in diff]) if diff else ["No change against the baseline."]
        out.append("")
    lat = doc.get("metrics", {}).get("processingLatencyMs")
    if lat and lat.get("n"):
        out += [f"Processing latency (ingest -> incident row, milestone Q2): p50 {lat['p50']} ms, "
                f"p95 {lat['p95']} ms, max {lat['max']} ms over {lat['n']} incidents", ""]
    if doc.get("otherFailures"):
        out += ["Other failures:", ""] + [f"- {f}" for f in doc["otherFailures"]] + [""]
    out += [f"<details><summary>All {len(results)} rows</summary>", "", *HEADER,
            *[_row(r, baseline) for r in results], "", "</details>", ""]
    return out


def render(offline: dict | None, baseline: dict, *, e2e: dict | None = None,
           baseline_ref: str | None = None, run_url: str | None = None) -> str:
    lines = [MARKER, "## Rule quality report", ""]
    if offline is None:
        lines += ["No offline score was produced. Check the `build, test, score, e2e` job.", ""]
    else:
        lines += _section("Offline", offline, baseline, changes=True)
    if e2e is not None:
        lines += _section("End to end", e2e, {}, changes=False)
    foot = ["Δ is the change in percentage points against `scorer/baseline.json`"
            + (f" at `{baseline_ref}`" if baseline_ref else "") + "; ▼ is a drop, \"new\" has no baseline row."]
    if run_url:
        foot.append(f"Run: {run_url}")
    lines += [" ".join(foot)]
    return "\n".join(lines) + "\n"


def _read(path: str | None) -> dict | None:
    if not path or not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render the sticky rule-quality PR comment")
    ap.add_argument("--offline", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--e2e")
    ap.add_argument("--baseline-ref")
    ap.add_argument("--run-url")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    md = render(_read(a.offline), _read(a.baseline) or {}, e2e=_read(a.e2e),
                baseline_ref=a.baseline_ref, run_url=a.run_url)
    Path(a.out).write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
