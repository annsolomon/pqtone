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
        # Kept next to the manifest so `pqt-scorer stored` can check every item was answered.
        (Path(a.out) / "http_results.json").write_text(json.dumps(totals, sort_keys=True) + "\n")
        print(json.dumps({"http": totals}))
    return 0


def _noisy_scenarios(a, scenarios: list[str]) -> dict[str, str]:
    """Tier 2 N3: copies of the scenarios with faults.vision pointing at the (optionally scaled) profile."""
    from . import vision_noise

    work = Path(a.out) / "_noise"
    work.mkdir(parents=True, exist_ok=True)
    profile = yaml.safe_load(Path(a.noise_profile).read_text())
    if a.noise_scale != 1.0:
        profile = vision_noise.scale(profile, a.noise_scale)
    errors = vision_noise.validate(profile)
    if errors:
        raise SystemExit(f"noise profile invalid: {errors}")
    prof_path = work / f"{profile['name']}.yaml"
    prof_path.write_text(yaml.safe_dump(profile, sort_keys=False))
    out = {}
    for sc in scenarios:
        doc = yaml.safe_load(Path(f"{a.scenarios_dir}/{sc}.yaml").read_text())
        doc.setdefault("faults", {})["vision"] = {"profile": str(prof_path.resolve())}
        path = work / f"{sc}.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
        out[sc] = str(path)
    return out


def cmd_matrix(a) -> int:
    matrix = yaml.safe_load(Path(a.matrix).read_text())
    cases = []
    paths = {sc: f"{a.scenarios_dir}/{sc}.yaml" for sc in matrix["scenarios"]}
    if a.noise_profile:
        paths = _noisy_scenarios(a, matrix["scenarios"])
    for sc in matrix["scenarios"]:
        for seed in matrix["seeds"]:
            case_dir = Path(a.out) / f"{sc}-{seed}"
            out = simulate(seed=seed, layout_path=a.layout, scenario_path=paths[sc], rules_path=a.rules)
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


def cmd_export_billing(a) -> int:
    """X1: a run's billing as the CSV exports a billing system would write (edge/connectors generic profile)."""
    from .billing_export import export

    events = [json.loads(line) for line in (Path(a.run) / "events.jsonl").read_text().splitlines() if line.strip()]
    print(json.dumps(export(events, a.out, store=a.store)))
    return 0


def cmd_noise_validate(a) -> int:
    from .noise_validate import render_md, validate_profile

    v = validate_profile(a.profile, scenarios_dir=a.scenarios_dir, layout=a.layout, rules=a.rules,
                         **({"scenarios": tuple(a.scenarios.split(","))} if a.scenarios else {}),
                         **({"seeds": tuple(int(x) for x in a.seeds.split(","))} if a.seeds else {}))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation.json").write_text(json.dumps(v.as_dict(), indent=2, sort_keys=True) + "\n")
    md = render_md(v, profile_path=a.profile)
    (out / "validation.md").write_text(md)
    print(md)
    return 0


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
    m.add_argument("--noise-profile", help="apply this vision noise profile to every scenario (Tier 2 N3)")
    m.add_argument("--noise-scale", type=float, default=1.0, help="multiply the profile's error rates (N3: 2)")
    m.set_defaults(fn=cmd_matrix)

    d = sub.add_parser("verify-determinism", help="run the same seed twice and compare hashes")
    _common(d)
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--scenario", default="sim/scenarios/baseline.yaml")
    d.set_defaults(fn=cmd_determinism)

    xb = sub.add_parser("export-billing", help="X1: write a run's billing as billing-system CSV exports")
    xb.add_argument("--run", required=True, help="a run directory (events.jsonl)")
    xb.add_argument("--out", required=True)
    xb.add_argument("--store", help="only this store (multi-store runs)")
    xb.set_defaults(fn=cmd_export_billing)
    nv = sub.add_parser("noise-validate", help="Tier 2 N5: KS test of simulated vs measured count errors")
    _common(nv)
    nv.add_argument("--profile", required=True, help="a measured noise profile with queueCountError")
    nv.add_argument("--scenarios-dir", default="sim/scenarios")
    nv.add_argument("--scenarios", help="comma-separated (default: the queue scenarios)")
    nv.add_argument("--seeds", help="comma-separated (default: 11,42,1337)")
    nv.add_argument("--out", required=True, help="writes validation.json and validation.md here")
    nv.set_defaults(fn=cmd_noise_validate)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
