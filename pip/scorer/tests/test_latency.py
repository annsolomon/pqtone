"""Milestone Q2: wall-clock processing latency in e2e, from the stored event to the incident row."""
from __future__ import annotations

import yaml

from pip_scorer.latency import SQL, processing_gate, summarise
from pip_scorer.report import write_reports


def test_percentiles_use_the_same_nearest_rank_as_rule_latency():
    m = summarise([100, 200, 300, 400, 5000], unmeasured=0)
    assert m == {"n": 5, "unmeasured": 0, "p50": 300, "p95": 5000, "max": 5000}


def test_no_measurements():
    assert summarise([], unmeasured=2) == {"n": 0, "unmeasured": 2, "p50": None, "p95": None, "max": None}


def test_negative_values_are_clamped_to_zero():
    # Clock granularity can put the incident row a few microseconds "before" the event row.
    assert summarise([-3, 10], unmeasured=0)["p50"] == 0


def test_gate_fails_only_above_the_limit():
    ok = summarise([1000, 2000], 0)
    slow = summarise([1000, 12000], 0)
    assert processing_gate(ok, 10_000) == []
    assert processing_gate(slow, 10_000) == ["processing latency p95 12000 ms > 10000 ms (ingest -> incident row)"]
    assert processing_gate(summarise([], 0), 10_000) == []
    assert processing_gate(ok, None) == []


def test_every_incident_must_be_measurable():
    assert processing_gate(summarise([10], unmeasured=1), 10_000) == [
        "processing latency: 1 incident(s) had no decision event to measure from"]


def test_threshold_is_configured():
    th = yaml.safe_load(open("scorer/thresholds.yaml"))
    assert th["e2e"]["processingLatencyP95"] == "PT10S"


def test_sql_is_parameterised_and_measures_from_the_watermark_moving_event():
    assert "%s" in SQL and "{" not in SQL
    assert "event_time >= i.detected_at" in SQL and "min(e.received_at)" in SQL


def test_report_shows_the_processing_latency(tmp_path):
    write_reports(tmp_path, [], [], metrics={"processingLatencyMs": summarise([1500, 2500], 0)})
    md = (tmp_path / "score.md").read_text()
    assert "Processing latency (ingest -> incident row): p50 1.5s, p95 2.5s, max 2.5s over 2 incidents" in md
    assert '"processingLatencyMs"' in (tmp_path / "score.json").read_text()


def test_cli_module_resolves_every_name_it_uses():
    # Regression: a missing import only failed at the end of a 15-minute e2e run.
    import ast
    import builtins
    from pathlib import Path

    tree = ast.parse(Path("scorer/pip_scorer/cli.py").read_text())
    defined = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            defined |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined.add(node.name)
            defined |= {a.arg for a in node.args.args} if isinstance(node, ast.FunctionDef) else set()
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, (ast.comprehension,)):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert used - defined == set()
