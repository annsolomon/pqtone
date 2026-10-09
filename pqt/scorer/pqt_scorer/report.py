"""score.json, score.md (PR comment) and junit.xml."""
from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape


def _ci(s: dict, metric: str) -> str:
    ci = s.get(f"{metric}CI")
    n = s.get(f"n{metric.capitalize()}")
    if not ci or not n:
        return "-"
    return f"[{ci[0]:.3f}, {ci[1]:.3f}] n={n}"


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
             "| Scenario | Rule | Mode | TP | FP | FN | Precision | Precision 95% CI | Recall | Recall 95% CI "
             "| Latency p95 | Gate |",
             "|---|---|---|---:|---:|---:|---:|---|---:|---|---:|---|"]
    for r in results:
        s = r["score"]
        p95 = s["latencyMs"]["p95"]
        lines.append(f"| {r['scenario']} | {r['ruleId']} | {r.get('mode', '')} | {s['tp']} | {s['fp']} | {s['fn']} | "
                     f"{s['precision']:.3f} | {_ci(s, 'precision')} | {s['recall']:.3f} | {_ci(s, 'recall')} | "
                     f"{'-' if p95 is None else f'{p95/1000:.1f}s'} | "
                     f"{'ok' if not r['failures'] else '; '.join(r['failures'])} |")
    warned = [(r, w) for r in results for w in r.get("warnings", [])]
    if warned:
        lines += ["", "### Warnings", "",
                  "Too few samples to certify the threshold. These never fail the build; they say how much "
                  "the number above can be trusted.", ""]
        lines += [f"- {r['scenario']} / {r['ruleId']}: {w}" for r, w in warned]
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
        f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="pqt-scorer" tests="{len(cases)}" '
        f'failures="{fails}">\n' + "\n".join(cases) + "\n</testsuite>\n")
