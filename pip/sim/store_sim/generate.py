"""Run one simulation: model -> CloudEvents -> ground truth -> faults -> manifest."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import SIM_VERSION
from .config import RuleSet, load_layout, load_rules, load_scenario, parse_duration_ms, sha256_bytes, sha256_file
from .events import canonical, iso_ms, make_event, parse_iso_ms
from .faults import inject
from .model import StoreModel
from .reference import RefEngine

DEFAULT_EPOCH = "2026-01-01T09:00:00.000Z"


@dataclass
class RunOutput:
    run_id: str
    clean: list[dict]
    emitted: list[dict]
    ground_truth: list[dict]
    manifest: dict


def run_id_for(seed: int, scenario_bytes: bytes, layout_bytes: bytes, rules_bytes: bytes) -> str:
    h = sha256_bytes(b"|".join([str(seed).encode(), scenario_bytes, layout_bytes, rules_bytes, SIM_VERSION.encode()]))
    return f"run-{h[:12]}"


def simulate(*, seed: int, layout_path: str, scenario_path: str, rules_path: str) -> RunOutput:
    layout = load_layout(layout_path)
    scenario = load_scenario(scenario_path)
    rules: RuleSet = load_rules(rules_path)
    run_id = run_id_for(seed, Path(scenario_path).read_bytes(), Path(layout_path).read_bytes(),
                        Path(rules_path).read_bytes())
    epoch_ms = parse_iso_ms(scenario.get("epoch", DEFAULT_EPOCH))

    model = StoreModel(layout, scenario, seed)
    records = model.run()
    store_id = layout["storeId"]
    clean = [make_event(order=r.order, short_type=r.short_type, t_ms=epoch_ms + r.t_ms, store_id=store_id,
                        run_id=run_id, subject=r.subject, data=r.data) for r in records]

    # Ground truth: reference rules over the clean, in-order stream.
    ref = RefEngine(rules)
    for ev in clean:
        ref.process({"t": parse_iso_ms(ev["time"]), "type": ev["type"], "storeId": store_id,
                     "simRunId": run_id, "id": ev["id"], "data": ev["data"]})
    horizon = epoch_ms + model.end_ms - rules.grace_ms
    ref.finish(horizon)
    gt = []
    for inc in ref.out:
        if inc["kind"] != "OPENED":
            continue
        gt.append({"gtId": f"gt-{len(gt):05d}", "ruleId": inc["ruleId"], "mode": inc["mode"],
                   "storeId": store_id, "simRunId": run_id, "key": inc["key"],
                   "onsetMs": inc["onsetMs"], "atMs": inc["detectedMs"], "at": iso_ms(inc["detectedMs"])})

    fault_rng = np.random.default_rng(np.random.SeedSequence([seed, 0xFA017]))
    emitted, counts = inject(clean, scenario.get("faults", {}), fault_rng)

    manifest = {
        "simVersion": SIM_VERSION, "seed": seed, "runId": run_id, "storeId": store_id,
        "scenario": scenario.get("name", Path(scenario_path).stem),
        "layoutSha256": sha256_file(layout_path), "scenarioSha256": sha256_file(scenario_path),
        "rulesSha256": sha256_file(rules_path),
        "epoch": iso_ms(epoch_ms), "durationMs": parse_duration_ms(scenario["duration"]),
        "endMs": epoch_ms + model.end_ms, "horizonMs": horizon, "graceMs": rules.grace_ms,
        "counts": {"cleanEvents": len(clean), "emittedEvents": len(emitted), "shoppers": model.n_shoppers,
                   "groundTruth": len(gt), **counts},
    }
    return RunOutput(run_id, clean, emitted, gt, manifest)


def write_files(out: RunOutput, out_dir: str | Path) -> dict:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    ev_bytes = ("\n".join(canonical(e) for e in out.emitted) + "\n").encode()
    gt_bytes = ("\n".join(canonical(g) for g in out.ground_truth) + ("\n" if out.ground_truth else "")).encode()
    (d / "events.jsonl").write_bytes(ev_bytes)
    (d / "ground_truth.jsonl").write_bytes(gt_bytes)
    manifest = dict(out.manifest)
    manifest["sha256"] = {"events": sha256_bytes(ev_bytes), "groundTruth": sha256_bytes(gt_bytes)}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
