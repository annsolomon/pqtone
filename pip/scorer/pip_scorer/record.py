"""Milestone R6: record a live (e2e) run's score per rule in pip.rule_score.

The console's shadow page reads these rows to show how a shadow rule performs, accumulated over
runs of the same rule version, before anyone proposes promoting it (docs/runbooks/RB-11). Writes go
through pip_app (insert only; the table is append-only). Re-scoring a run inserts nothing.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .gates import Confidence

INSERT = """
INSERT INTO pip.rule_score (source, sim_run_id, scenario, rule_id, rule_version, mode, tp, fp, fn,
                            latency_p95_ms, precision_min, recall_min, min_n, min_lower_bound)
VALUES ('e2e', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (sim_run_id, rule_id) DO NOTHING
"""


def rule_versions(rules_path: str | Path) -> dict[str, tuple[str, str]]:
    """{ruleId: (version, mode)} from the rules file the run was simulated with."""
    doc = yaml.safe_load(Path(rules_path).read_text())
    return {r["id"]: (str(r["version"]), r["mode"]) for r in doc["rules"]}


def rows(results: list[dict], *, run_id: str, scenario: str, versions: dict[str, tuple[str, str]],
         gates: dict[str, tuple[float, float]], conf: Confidence) -> list[tuple]:
    """One row per rule that is running (enforce or shadow); rules switched off are not recorded."""
    out = []
    for r in results:
        rule = r["ruleId"]
        version, mode = versions.get(rule, (None, None))
        if mode not in ("enforce", "shadow") or version is None:
            continue
        s = r["score"]
        p_min, r_min = gates[rule]
        out.append((run_id, scenario, rule, version, mode, s["tp"], s["fp"], s["fn"], s["latencyMs"]["p95"],
                    p_min, r_min, conf.min_n, conf.min_lower_bound))
    return out


def write(conn, data: list[tuple]) -> int:
    with conn.cursor() as cur:
        cur.executemany(INSERT, data)
    return len(data)
