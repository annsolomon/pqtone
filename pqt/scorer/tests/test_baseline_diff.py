"""Milestone Q3: the baseline is changed only by a reviewed PR, with the changes spelled out."""
from __future__ import annotations

import json

from pqt_scorer.baseline_diff import diff, main, markdown

BASE = {"baseline": {"R-QUEUE-001": {"precision": 1.0, "recall": 1.0},
                     "R-DWELL-001": {"precision": 1.0, "recall": 1.0}},
        "late_beyond_grace": {"R-DWELL-001": {"precision": 0.6667, "recall": 1.0}}}


def test_identical_means_nothing_to_accept(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps(BASE))
    (tmp_path / "c.json").write_text(json.dumps(BASE))
    assert main([str(tmp_path / "b.json"), str(tmp_path / "c.json")]) == 0
    assert diff(BASE, BASE) == []
    assert "Nothing to accept" in markdown([])


def test_kinds_and_order_put_drops_first():
    c = json.loads(json.dumps(BASE))
    c["baseline"]["R-QUEUE-001"] = {"precision": 0.9, "recall": 1.0}           # drop
    c["late_beyond_grace"]["R-DWELL-001"] = {"precision": 0.75, "recall": 1.0}  # rise
    del c["baseline"]["R-DWELL-001"]                                            # removed
    c["footfall_spike"] = {"R-FOOT-001": {"precision": 1.0, "recall": 1.0}}    # new
    kinds = [(ch.kind, ch.scenario, ch.rule) for ch in diff(BASE, c)]
    assert kinds == [("drop", "baseline", "R-QUEUE-001"), ("removed", "baseline", "R-DWELL-001"),
                     ("new", "footfall_spike", "R-FOOT-001"), ("rise", "late_beyond_grace", "R-DWELL-001")]


def test_a_drop_in_one_metric_counts_even_if_the_other_rises():
    c = json.loads(json.dumps(BASE))
    c["late_beyond_grace"]["R-DWELL-001"] = {"precision": 0.9, "recall": 0.95}
    (ch,) = diff(BASE, c)
    assert ch.kind == "drop"
    assert round(ch.worst_drop_pp(), 1) == 5.0


def test_markdown_warns_about_drops_and_never_auto_merges():
    c = json.loads(json.dumps(BASE))
    c["baseline"]["R-QUEUE-001"] = {"precision": 0.9, "recall": 1.0}
    md = markdown(diff(BASE, c), source="run 123 on main@abc")
    assert "1 row(s) get worse or disappear" in md
    assert "| drop | baseline | R-QUEUE-001 | 1.0000 → 0.9000 | 1.0000 → 1.0000 |" in md
    assert "run 123 on main@abc" in md
    assert "Never merged automatically" in md


def test_cli_exit_codes_and_pr_body(tmp_path):
    c = json.loads(json.dumps(BASE))
    c["new_scenario"] = {"R-QUEUE-001": {"precision": 1.0, "recall": 1.0}}
    (tmp_path / "b.json").write_text(json.dumps(BASE))
    (tmp_path / "c.json").write_text(json.dumps(c))
    out = tmp_path / "body.md"
    assert main([str(tmp_path / "b.json"), str(tmp_path / "c.json"), "--markdown", str(out)]) == 3
    assert "| new | new_scenario | R-QUEUE-001 |" in out.read_text()


def test_bad_input_is_rejected(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps(BASE))
    (tmp_path / "bad.json").write_text(json.dumps({"baseline": {"R-QUEUE-001": {"precision": 2}}}))
    assert main([str(tmp_path / "b.json"), str(tmp_path / "bad.json")]) == 2
    assert main([str(tmp_path / "b.json"), str(tmp_path / "missing.json")]) == 2
