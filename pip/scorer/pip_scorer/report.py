"""score.json, score.md (PR comment) and junit.xml."""
from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape


def write_reports(out_dir: str | Path, results: list[dict], extra_failures: list[str],
                  metrics: dict | None = None) -> None:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    passed = all(not r["failures"] for r in results) and not extra_failures
    doc = {"passed": passed, "results": results, "otherFailures": extra_failures}
    if metrics:
        doc["metrics"] = metrics
    (d / "score.json").write_text(json.dumps(doc, indent=2) + "\n")

    lines = ["## Rule quality report", "",
             f"**Result: {'PASS' if passed else 'FAIL'}**", "",
             "| Scenario | Rule | Mode | TP | FP | FN | Precision | Recall | Latency p95 | Gate |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in results:
        s = r["score"]
        p95 = s["latencyMs"]["p95"]
        lines.append(f"| {r['scenario']} | {r['ruleId']} | {r.get('mode', '')} | {s['tp']} | {s['fp']} | {s['fn']} | "
                     f"{s['precision']:.3f} | {s['recall']:.3f} | {'-' if p95 is None else f'{p95/1000:.1f}s'} | "
                     f"{'ok' if not r['failures'] else '; '.join(r['failures'])} |")
    lat = (metrics or {}).get("processingLatencyMs")
    if lat and lat.get("n"):
        sec = lambda ms: f"{ms / 1000:.1f}s"
        lines += ["", f"Processing latency (ingest -> incident row): p50 {sec(lat['p50'])}, p95 {sec(lat['p95'])}, "
                      f"max {sec(lat['max'])} over {lat['n']} incidents"]
    if extra_failures:
        lines += ["", "### Other failures", ""] + [f"- {f}" for f in extra_failures]
    (d / "score.md").write_text("\n".join(lines) + "\n")

    cases = []
    for r in results:
        name = escape(f"{r['scenario']}::{r['ruleId']}")
        if r["failures"]:
            msg = escape("; ".join(r["failures"]))
            cases.append(f'  <testcase classname="rule-quality" name="{name}"><failure message="{msg}"/></testcase>')
        else:
            cases.append(f'  <testcase classname="rule-quality" name="{name}"/>')
    for f in extra_failures:
        cases.append(f'  <testcase classname="rule-quality" name="{escape(f)}"><failure message="{escape(f)}"/></testcase>')
    fails = sum(1 for r in results if r["failures"]) + len(extra_failures)
    (d / "junit.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="pip-scorer" tests="{len(cases)}" '
        f'failures="{fails}">\n' + "\n".join(cases) + "\n</testsuite>\n")
