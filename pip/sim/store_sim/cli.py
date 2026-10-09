"""store-sim command line interface."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

import yaml

from .generate import simulate, write_files
from .sinks import http_sink, kafka_sink


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--layout", default="config/layouts/store-001.json")
    p.add_argument("--rules", default="config/rules.yaml")


def cmd_run(a) -> int:
    out = simulate(seed=a.seed, layout_path=a.layout, scenario_path=a.scenario, rules_path=a.rules)
    manifest = write_files(out, a.out)
    print(json.dumps({"runId": out.run_id, "counts": manifest["counts"], "out": a.out}))
    if a.sink in ("kafka", "both"):
        kafka_sink(out.emitted, a.speed)
    if a.sink in ("http", "both"):
        totals = http_sink(out.emitted, a.speed)
        # Kept next to the manifest so `pip-scorer stored` can check every item was answered.
        (Path(a.out) / "http_results.json").write_text(json.dumps(totals, sort_keys=True) + "\n")
        print(json.dumps({"http": totals}))
    return 0


def cmd_matrix(a) -> int:
    matrix = yaml.safe_load(Path(a.matrix).read_text())
    cases = []
    for sc in matrix["scenarios"]:
        for seed in matrix["seeds"]:
            case_dir = Path(a.out) / f"{sc}-{seed}"
            out = simulate(seed=seed, layout_path=a.layout, scenario_path=f"{a.scenarios_dir}/{sc}.yaml",
                           rules_path=a.rules)
            write_files(out, case_dir)
            cases.append({"scenario": sc, "seed": seed, "dir": str(case_dir), "runId": out.run_id,
                          "groundTruth": len(out.ground_truth)})
    (Path(a.out) / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
    print(f"generated {len(cases)} cases into {a.out}")
    return 0


def cmd_determinism(a) -> int:
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        m1 = write_files(simulate(seed=a.seed, layout_path=a.layout, scenario_path=a.scenario, rules_path=a.rules), d1)
        m2 = write_files(simulate(seed=a.seed, layout_path=a.layout, scenario_path=a.scenario, rules_path=a.rules), d2)
    same = m1["sha256"] == m2["sha256"]
    print(json.dumps({"seed": a.seed, "identical": same, "sha256": m1["sha256"]}))
    return 0 if same else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    p = argparse.ArgumentParser(prog="store-sim")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="simulate one scenario and emit events")
    _common(r)
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--scenario", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--sink", choices=["file", "kafka", "http", "both"], default="file")
    r.add_argument("--speed", type=float, default=0.0, help="0 = as fast as possible; N = N x real time")
    r.set_defaults(fn=cmd_run)

    m = sub.add_parser("matrix", help="generate every (scenario, seed) case to files")
    _common(m)
    m.add_argument("--matrix", default="scorer/matrix.yaml")
    m.add_argument("--scenarios-dir", default="sim/scenarios")
    m.add_argument("--out", required=True)
    m.set_defaults(fn=cmd_matrix)

    d = sub.add_parser("verify-determinism", help="run the same seed twice and compare hashes")
    _common(d)
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--scenario", default="sim/scenarios/baseline.yaml")
    d.set_defaults(fn=cmd_determinism)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
