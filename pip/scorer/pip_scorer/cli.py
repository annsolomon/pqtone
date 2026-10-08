"""pip-scorer CLI: offline matrix scoring and end-to-end scoring against PostgreSQL."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

from .gates import dur_ms, evaluate, gate_for, regressions
from .match import RuleScore, score
from .report import write_reports

RULES = ["R-QUEUE-001", "R-DWELL-001", "R-ABS-001", "R-FOOT-001"]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _rule_modes(gt: list[dict], incidents: list[dict]) -> dict[str, str]:
    modes = {}
    for x in gt + incidents:
        modes.setdefault(x["ruleId"], x.get("mode", ""))
    return modes


def cmd_offline(a) -> int:
    thresholds = yaml.safe_load(Path(a.thresholds).read_text())
    m = thresholds["matching"]
    before, max_lat = dur_ms(m["toleranceBefore"]), dur_ms(m["maxLatency"])
    cases = json.loads((Path(a.matrix_dir) / "cases.json").read_text())

    agg: dict[str, dict[str, RuleScore]] = {}
    modes: dict[str, str] = {}
    for c in cases:
        d = Path(c["dir"])
        gt = read_jsonl(d / "ground_truth.jsonl")
        inc = read_jsonl(d / "incidents.jsonl")
        if not (d / "incidents.jsonl").exists():
            print(f"missing incidents for {d}; run the rules-engine offline runner first", file=sys.stderr)
            return 2
        modes.update(_rule_modes(gt, inc))
        per = score(gt, inc, tolerance_before_ms=before, max_latency_ms=max_lat, rule_ids=RULES)
        bucket = agg.setdefault(c["scenario"], {r: RuleScore(r) for r in RULES})
        for r, s in per.items():
            bucket.setdefault(r, RuleScore(r)).merge(s)

    results, current = [], {}
    for scenario, rules in agg.items():
        for rule, s in rules.items():
            sd = s.to_dict()
            failures = evaluate(scenario, sd, gate_for(thresholds, scenario, rule))
            results.append({"scenario": scenario, "ruleId": rule, "mode": modes.get(rule, ""), "score": sd,
                            "failures": failures,
                            "examples": {"missed": s.unmatched_gt[:3], "spurious": s.unmatched_incidents[:3]}})
            current.setdefault(scenario, {})[rule] = {"precision": sd["precision"], "recall": sd["recall"]}

    extra = []
    if a.baseline and Path(a.baseline).exists():
        extra = regressions(current, json.loads(Path(a.baseline).read_text()),
                            float(thresholds.get("regression", {}).get("maxDropPp", 2.0)))
    write_reports(a.out, results, extra)
    (Path(a.out) / "baseline.candidate.json").write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    print((Path(a.out) / "score.md").read_text())
    return 0 if all(not r["failures"] for r in results) and not extra else 1


def _connect():
    import psycopg

    return psycopg.connect(os.environ["PIP_SCORER_DSN"], autocommit=True)


def cmd_e2e(a) -> int:
    thresholds = yaml.safe_load(Path(a.thresholds).read_text())
    m = thresholds["matching"]
    case = Path(a.case_dir)
    manifest = json.loads((case / "manifest.json").read_text())
    run_id, store, horizon = manifest["runId"], manifest["storeId"], manifest["horizonMs"]
    state_key = f"{store}|{run_id}"
    deadline = time.monotonic() + a.timeout

    with _connect() as conn:
        print(f"waiting for rules-engine watermark on {state_key} to reach {horizon}")
        while True:
            rows = conn.execute("SELECT payload FROM pip.pipeline_heartbeat").fetchall()
            wm = max((k.get("watermarkMs", 0) for (p,) in rows for k in p.get("keys", [])
                      if k.get("key") == state_key), default=None)
            if wm is not None and wm >= horizon:
                break
            if time.monotonic() > deadline:
                print(f"timed out; last watermark={wm}", file=sys.stderr)
                return 3
            time.sleep(2)
        last = -1
        while True:
            n = conn.execute("SELECT count(*) FROM pip.incident WHERE sim_run_id = %s", (run_id,)).fetchone()[0]
            if n == last:
                break
            last = n
            time.sleep(4)
        rows = conn.execute(
            "SELECT rule_id, mode, store_id, incident_key, "
            "(extract(epoch from detected_at) * 1000)::bigint "
            "FROM pip.incident WHERE sim_run_id = %s", (run_id,)).fetchall()
        stored = conn.execute("SELECT count(*) FROM pip.event WHERE sim_run_id = %s", (run_id,)).fetchone()[0]

    incidents = [{"ruleId": r, "mode": mo, "storeId": s, "key": k, "detectedMs": int(d), "kind": "OPENED"}
                 for r, mo, s, k, d in rows]
    gt = read_jsonl(case / "ground_truth.jsonl")
    per = score(gt, incidents, tolerance_before_ms=dur_ms(m["toleranceBefore"]),
                max_latency_ms=dur_ms(m["maxLatency"]), rule_ids=RULES)
    scenario = manifest["scenario"]
    modes = _rule_modes(gt, incidents)
    results = []
    for rule, s in per.items():
        sd = s.to_dict()
        results.append({"scenario": f"e2e:{scenario}", "ruleId": rule, "mode": modes.get(rule, ""), "score": sd,
                        "failures": evaluate(scenario, sd, gate_for(thresholds, scenario, rule))})
    expected = manifest["counts"]["cleanEvents"]
    extra = [] if stored == expected else [f"stored events {stored} != expected unique valid events {expected}"]
    write_reports(a.out, results, extra)
    print((Path(a.out) / "score.md").read_text())
    print(json.dumps({"runId": run_id, "storedEvents": stored, "expectedEvents": expected,
                      "incidents": len(incidents)}))
    return 0 if all(not r["failures"] for r in results) and not extra else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pip-scorer")
    sub = p.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("offline", help="score a generated matrix (incidents from the offline runner)")
    o.add_argument("--matrix-dir", required=True)
    o.add_argument("--thresholds", default="scorer/thresholds.yaml")
    o.add_argument("--baseline", default="scorer/baseline.json")
    o.add_argument("--out", required=True)
    o.set_defaults(fn=cmd_offline)
    e = sub.add_parser("e2e", help="score a live run from PostgreSQL")
    e.add_argument("--case-dir", required=True)
    e.add_argument("--thresholds", default="scorer/thresholds.yaml")
    e.add_argument("--timeout", type=int, default=600)
    e.add_argument("--out", required=True)
    e.set_defaults(fn=cmd_e2e)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
