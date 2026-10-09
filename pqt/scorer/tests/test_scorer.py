from pqt_scorer.gates import Gate, evaluate, gate_for, regressions
from pqt_scorer.match import score

RULES = ["R-QUEUE-001"]


def gt(at, key="q"):
    return {"ruleId": "R-QUEUE-001", "storeId": "s", "key": key, "atMs": at}


def inc(at, key="q", kind="OPENED"):
    return {"ruleId": "R-QUEUE-001", "storeId": "s", "key": key, "detectedMs": at, "kind": kind}


def run(g, i):
    return score(g, i, tolerance_before_ms=30_000, max_latency_ms=120_000, rule_ids=RULES)["R-QUEUE-001"]


def test_exact_match():
    s = run([gt(1000)], [inc(1000)])
    assert (s.tp, s.fp, s.fn) == (1, 0, 0)
    assert s.latencies_ms == [0]


def test_outside_window_is_fp_and_fn():
    s = run([gt(0)], [inc(200_000)])
    assert (s.tp, s.fp, s.fn) == (0, 1, 1)


def test_one_to_one_assignment_prefers_closest():
    s = run([gt(0), gt(100_000)], [inc(95_000)])
    assert (s.tp, s.fp, s.fn) == (1, 0, 1)
    assert s.latencies_ms == [-5_000]


def test_keys_do_not_cross_match():
    s = run([gt(0, "a")], [inc(0, "b")])
    assert (s.tp, s.fp, s.fn) == (0, 1, 1)


def test_resolved_records_are_ignored():
    s = run([gt(0)], [inc(0), inc(50_000, kind="RESOLVED")])
    assert (s.tp, s.fp, s.fn) == (1, 0, 0)


def test_vacuous_case_scores_perfect():
    s = run([], [])
    assert s.precision == 1.0 and s.recall == 1.0


def test_gate_overrides_and_evaluation():
    th = {"defaults": {"R-QUEUE-001": {"precision": 0.9, "recall": 0.9, "latencyP95": "PT5S"}},
          "scenarios": {"late": {"*": {"recall": 0.5}}}}
    g = gate_for(th, "late", "R-QUEUE-001")
    assert g == Gate(0.9, 0.5, 5000)
    d = run([gt(0), gt(500_000)], [inc(0)]).to_dict()
    assert evaluate("late", d, g) == []
    assert evaluate("x", d, gate_for(th, "x", "R-QUEUE-001"))


def test_regression_detection():
    out = regressions({"b": {"R": {"precision": 0.9, "recall": 1.0}}},
                      {"b": {"R": {"precision": 1.0, "recall": 1.0}}}, 2.0)
    assert out and "precision" in out[0]
