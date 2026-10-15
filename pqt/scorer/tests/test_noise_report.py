"""N3 noise comparison report."""
import json

from pqt_scorer import cli
from pqt_scorer.noise import compare, render


def score(p, r, failures=()):
    return {"precision": p, "recall": r, "tp": 10, "fp": 0, "fn": 0, "latencyMs": {"p95": None}}, list(failures)


def doc(*rows):
    return {"results": [{"scenario": sc, "ruleId": rule, "mode": mode, "score": s, "failures": f}
                        for sc, rule, mode, (s, f) in rows]}


def test_drops_and_gate():
    runs = [("clean", doc(("rush_hour", "R-QUEUE-001", "enforce", score(1.0, 1.0)),
                          ("rush_hour", "R-ABS-001", "shadow", score(1.0, 1.0)))),
            ("measured", doc(("rush_hour", "R-QUEUE-001", "enforce", score(0.97, 0.96)),
                             ("rush_hour", "R-ABS-001", "shadow", score(0.5, 0.5, ["recall 0.500 < 0.95"])))),
            ("measured-x2", doc(("rush_hour", "R-QUEUE-001", "enforce", score(0.90, 0.80, ["recall 0.800 < 0.95"])),
                                ("rush_hour", "R-ABS-001", "shadow", score(0.4, 0.4))))]
    d = compare(runs)
    q = next(r for r in d["rows"] if r["ruleId"] == "R-QUEUE-001")
    assert q["by"]["measured"]["recallDropPp"] == 4.0 and q["by"]["measured-x2"]["precisionDropPp"] == 10.0
    assert d["gate"]["passed"] is True  # shadow rules and the 2x profile do not gate
    md = render(d)
    assert "PASS" in md and "-3 / -4 pp" in md
    runs[1] = ("measured", doc(("rush_hour", "R-QUEUE-001", "enforce", score(0.9, 0.9, ["precision 0.900 < 0.95"]))))
    assert compare(runs)["gate"]["passed"] is False


def test_cli(tmp_path, capsys):
    for label, p in (("clean", 1.0), ("measured", 0.96)):
        (tmp_path / label).mkdir()
        (tmp_path / label / "score.json").write_text(json.dumps(doc(("baseline", "R-DWELL-001", "enforce", score(p, p)))))
    args = ["noise-report", "--run", f"clean={tmp_path / 'clean'}", "--run", f"measured={tmp_path / 'measured'}",
            "--out", str(tmp_path / "out"), "--gate"]
    assert cli.main(args) == 0
    assert (tmp_path / "out" / "noise.md").read_text().startswith("## Rule quality under vision noise")
