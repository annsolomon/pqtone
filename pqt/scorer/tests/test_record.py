"""Milestone R6: rows recorded in pqt.rule_score after a live run."""
from pqt_scorer.gates import Confidence
from pqt_scorer.record import INSERT, rows, rule_versions, write


def result(rule, tp, fp, fn, p95=120):
    return {"ruleId": rule, "score": {"tp": tp, "fp": fp, "fn": fn, "latencyMs": {"p95": p95}}}


def test_rule_versions_come_from_the_rules_file():
    v = rule_versions("config/rules.yaml")
    assert v["R-ABS-001"][1] == "shadow" and v["R-QUEUE-001"][1] == "enforce"
    assert all(len(ver.split(".")) == 3 for ver, _ in v.values())


def test_one_row_per_running_rule_with_the_gate_that_applied():
    versions = {"R-QUEUE-001": ("1.0.0", "enforce"), "R-ABS-001": ("1.0.0", "shadow"), "R-OFF-001": ("1.0.0", "off")}
    gates = {"R-QUEUE-001": (0.95, 0.95), "R-ABS-001": (0.90, 0.95), "R-OFF-001": (0.5, 0.5)}
    conf = Confidence(min_n=20, z=1.96, min_lower_bound=0.8)
    out = rows([result("R-QUEUE-001", 3, 0, 0), result("R-ABS-001", 1, 1, 0, None), result("R-OFF-001", 0, 0, 0)],
               run_id="run-abc", scenario="register_delay", versions=versions, gates=gates, conf=conf)
    assert out == [
        ("run-abc", "register_delay", "R-QUEUE-001", "1.0.0", "enforce", 3, 0, 0, 120, 0.95, 0.95, 20, 0.8),
        ("run-abc", "register_delay", "R-ABS-001", "1.0.0", "shadow", 1, 1, 0, None, 0.90, 0.95, 20, 0.8),
    ]


def test_write_is_one_parameterised_idempotent_insert():
    class Cur:
        def __init__(self):
            self.calls = []

        def executemany(self, sql, data):
            self.calls.append((sql, data))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    cur = Cur()

    class Conn:
        def cursor(self):
            return cur

    data = [("run-abc", "s", "R-QUEUE-001", "1.0.0", "enforce", 1, 0, 0, None, 0.95, 0.95, None, None)]
    assert write(Conn(), data) == 1
    sql, sent = cur.calls[0]
    assert sent == data and "ON CONFLICT (sim_run_id, rule_id) DO NOTHING" in sql and sql == INSERT
    assert "%s" in sql and "run-abc" not in sql, "values are bound, never formatted into SQL"
