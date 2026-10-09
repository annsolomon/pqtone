"""Milestone Q1: the sticky PR comment with a delta against the committed baseline."""
from __future__ import annotations

import json

from pip_scorer.comment import MARKER, main, render


def result(scenario, rule, tp, fp, fn, *, mode="enforce", failures=(), p95=0):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"scenario": scenario, "ruleId": rule, "mode": mode, "failures": list(failures),
            "score": {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4),
                      "latencyMs": {"p50": p95, "p95": p95 if tp else None, "max": p95}}}


def doc(*results, passed=True, other=()):
    return {"passed": passed, "results": list(results), "otherFailures": list(other)}


BASE = {"baseline": {"R-QUEUE-001": {"precision": 1.0, "recall": 1.0},
                     "R-DWELL-001": {"precision": 1.0, "recall": 1.0}}}


def test_starts_with_the_marker_so_the_comment_can_be_found_and_updated():
    md = render(doc(result("baseline", "R-QUEUE-001", 9, 0, 0)), BASE)
    assert md.startswith(MARKER)


def test_unchanged_scores_show_no_changes():
    md = render(doc(result("baseline", "R-QUEUE-001", 9, 0, 0), result("baseline", "R-DWELL-001", 1, 0, 0)), BASE)
    assert "**Offline: PASS**" in md
    assert "No change against the baseline." in md
    assert "| baseline | R-QUEUE-001 | enforce | 9/0/0 | 1.000 (±0.0) | 1.000 (±0.0) |" in md


def test_a_drop_is_shown_in_percentage_points_and_listed_as_a_change():
    md = render(doc(result("baseline", "R-QUEUE-001", 9, 1, 0), result("baseline", "R-DWELL-001", 1, 0, 0)), BASE)
    assert "0.900 (▼10.0pp)" in md
    changes = md.split("### Changes against the baseline")[1].split("<details>")[0]
    assert "R-QUEUE-001" in changes and "R-DWELL-001" not in changes


def test_an_improvement_is_marked_up():
    base = {"s": {"R-X-001": {"precision": 0.5, "recall": 1.0}}}
    md = render(doc(result("s", "R-X-001", 2, 0, 0)), base)
    assert "1.000 (▲50.0pp)" in md


def test_rows_missing_from_the_baseline_are_marked_new():
    md = render(doc(result("footfall_spike", "R-FOOT-001", 3, 0, 0)), BASE)
    assert "1.000 (new)" in md
    assert "footfall_spike" in md.split("### Changes against the baseline")[1].split("<details>")[0]


def test_failures_and_other_failures_appear():
    md = render(doc(result("baseline", "R-QUEUE-001", 5, 5, 0, failures=["precision 0.500 < 0.95"]), passed=False,
                    other=["baseline/R-QUEUE-001: precision dropped 50.0pp vs baseline"]), BASE)
    assert "**Offline: FAIL**" in md
    assert "precision 0.500 < 0.95" in md
    assert "precision dropped 50.0pp" in md


def test_e2e_section_is_optional():
    off = doc(result("baseline", "R-QUEUE-001", 9, 0, 0))
    assert "End to end" not in render(off, BASE)
    md = render(off, BASE, e2e=doc(result("e2e:register_delay", "R-QUEUE-001", 3, 0, 0)))
    assert "**End to end: PASS**" in md and "e2e:register_delay" in md


def test_missing_offline_score_says_so():
    md = render(None, BASE)
    assert md.startswith(MARKER)
    assert "No offline score was produced" in md


def test_cli_writes_the_comment(tmp_path):
    (tmp_path / "score.json").write_text(json.dumps(doc(result("baseline", "R-QUEUE-001", 9, 0, 0))))
    (tmp_path / "baseline.json").write_text(json.dumps(BASE))
    out = tmp_path / "comment.md"
    rc = main(["--offline", str(tmp_path / "score.json"), "--baseline", str(tmp_path / "baseline.json"),
               "--e2e", str(tmp_path / "absent.json"), "--baseline-ref", "main@abc1234",
               "--run-url", "https://example.invalid/run/1", "--out", str(out)])
    assert rc == 0
    text = out.read_text()
    assert "main@abc1234" in text and "https://example.invalid/run/1" in text


def test_e2e_processing_latency_is_shown():
    e2e = doc(result("e2e:register_delay", "R-QUEUE-001", 3, 0, 0))
    e2e["metrics"] = {"processingLatencyMs": {"n": 6, "unmeasured": 0, "p50": 420, "p95": 1300, "max": 1500}}
    md = render(doc(result("baseline", "R-QUEUE-001", 9, 0, 0)), BASE, e2e=e2e)
    assert "p50 420 ms, p95 1300 ms, max 1500 ms over 6 incidents" in md
