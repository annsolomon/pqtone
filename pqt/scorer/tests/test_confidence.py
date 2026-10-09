"""Milestone Q4: Wilson intervals, so small samples are not read as certainty."""
from __future__ import annotations

import math

import pytest

from pqt_scorer.gates import Confidence, check, confidence_for, gate_for, wilson
from pqt_scorer.match import RuleScore
from pqt_scorer.report import write_reports


@pytest.mark.parametrize("k, n, lo, hi", [
    (20, 20, 0.8389, 1.0),     # perfect on 20 is only "at least ~84%" with 95% confidence
    (49, 49, 0.9273, 1.0),
    (2, 2, 0.3424, 1.0),       # two absence incidents: the interval is almost useless
    (19, 20, 0.7639, 0.9911),
    (0, 10, 0.0, 0.2775),
    (5, 10, 0.2366, 0.7634),
])
def test_wilson_matches_published_values(k, n, lo, hi):
    got = wilson(k, n, 1.96)
    assert math.isclose(got[0], lo, abs_tol=5e-4) and math.isclose(got[1], hi, abs_tol=5e-4), got


def test_wilson_with_no_samples_says_nothing():
    assert wilson(0, 0, 1.96) == (0.0, 1.0)


def test_score_dict_carries_sample_sizes_and_intervals():
    s = RuleScore("R-X-001", tp=19, fp=1, fn=0).to_dict()
    assert s["nPrecision"] == 20 and s["nRecall"] == 19
    assert s["precisionCI"][0] == pytest.approx(0.7639, abs=5e-4)
    assert s["recallCI"] == [pytest.approx(0.8318, abs=5e-4), 1.0]


THRESHOLDS = {
    "defaults": {"R-X-001": {"precision": 0.95, "recall": 0.95}},
    "confidence": {"minN": 20, "z": 1.96, "minLowerBound": 0.80},
}


def run(tp, fp, fn):
    sd = RuleScore("R-X-001", tp=tp, fp=fp, fn=fn).to_dict()
    return check("s", sd, gate_for(THRESHOLDS, "s", "R-X-001"), confidence_for(THRESHOLDS))


def test_large_sample_with_a_good_lower_bound_passes():
    failures, warnings = run(49, 0, 0)
    assert failures == [] and warnings == []


def test_large_sample_whose_lower_bound_is_too_low_fails_even_if_the_point_estimate_passes():
    # 57/60 = 0.95 meets the 0.95 threshold, but the interval reaches down to ~0.86 ... still >= 0.80.
    assert run(57, 3, 0)[0] == []
    # 38/40 = 0.95 passes the point gate; its lower bound 0.835 also passes 0.80.
    assert run(38, 2, 0)[0] == []
    # 19/20 = 0.95: lower bound 0.764 < 0.80, so with n >= 20 the gate fails.
    failures, _ = run(19, 1, 0)
    assert any("precision lower bound 0.764 < 0.80 (n=20)" in f for f in failures), failures


def test_small_samples_warn_instead_of_claiming_certainty_but_never_relax_the_point_gate():
    failures, warnings = run(2, 0, 0)
    assert failures == []
    assert any("precision: n=2" in w and "[0.342, 1.000]" in w for w in warnings), warnings
    # A small sample below the point threshold still fails exactly as before Q4.
    failures, _ = run(1, 1, 0)
    assert any(f.startswith("precision 0.500 < 0.95") for f in failures)


def test_empty_rows_neither_fail_nor_warn():
    assert run(0, 0, 0) == ([], [])


def test_without_a_confidence_section_only_the_point_gate_applies():
    sd = RuleScore("R-X-001", tp=19, fp=1, fn=0).to_dict()
    th = {"defaults": THRESHOLDS["defaults"]}
    assert check("s", sd, gate_for(th, "s", "R-X-001"), confidence_for(th)) == ([], [])
    assert confidence_for(th) == Confidence(min_n=None, z=1.96, min_lower_bound=None)


def test_reports_show_intervals_and_warnings(tmp_path):
    sd = RuleScore("R-X-001", tp=2, fp=0, fn=0).to_dict()
    failures, warnings = check("s", sd, gate_for(THRESHOLDS, "s", "R-X-001"), confidence_for(THRESHOLDS))
    write_reports(tmp_path, [{"scenario": "s", "ruleId": "R-X-001", "mode": "enforce", "score": sd,
                              "failures": failures, "warnings": warnings}], [])
    md = (tmp_path / "score.md").read_text()
    assert "Precision 95% CI" in md and "[0.342, 1.000]" in md
    assert "### Warnings" in md and "n=2" in md
