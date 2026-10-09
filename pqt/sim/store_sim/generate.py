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
from . import vision_noise
from .model import StoreModel
from .reference import FootfallRef, RefEngine

DEFAULT_EPOCH = "2026-01-01T09:00:00.000Z"


@dataclass
class RunOutput:
    run_id: str
    clean: list[dict]
    emitted: list[dict]
    ground_truth: list[dict]
    manifest: dict


def run_id_for(seed: int, scenario_bytes: bytes, layout_bytes: bytes, rules_bytes: bytes,
               noise_sha256: str | None = None) -> str:
    parts = [str(seed).encode(), scenario_bytes, layout_bytes, rules_bytes, SIM_VERSION.encode()]
    if noise_sha256:  # only when faults.vision is set, so every existing run id stays the same
        parts.append(noise_sha256.encode())
    h = sha256_bytes(b"|".join(parts))
    return f"run-{h[:12]}"


_STORE_ID = __import__("re").compile(r"^[a-z0-9-]{1,64}$")


def layout_paths_for(scenario: dict, layout_path: str) -> list[str]:
    """The layouts a scenario runs on. `stores:` (milestone S2) names layouts by store id, looked up next to
    --layout; without it, the scenario runs on --layout alone, exactly as before."""
    ids = scenario.get("stores")
    if not ids:
        return [layout_path]
    folder = Path(layout_path).parent
    out = []
    for sid in ids:
        if not isinstance(sid, str) or not _STORE_ID.match(sid):
            raise ValueError(f"stores: invalid store id {sid!r}")
        p = folder / f"{sid}.json"
        if not p.is_file():
            raise ValueError(f"stores: no layout {p}")
        out.append(str(p))
    if len(set(out)) != len(out):
        raise ValueError("stores: each store may appear once")
    return out


def store_seed(seed: int, index: int) -> int:
    """The first store keeps the run's seed, so it behaves exactly as it would alone; the others get
    independent seeds derived from (seed, index)."""
    if index == 0:
        return seed
    return int(np.random.SeedSequence([seed, 0x5702, index]).generate_state(1, dtype=np.uint64)[0])


def simulate(*, seed: int, layout_path: str, scenario_path: str, rules_path: str) -> RunOutput:
    scenario = load_scenario(scenario_path)
    paths = layout_paths_for(scenario, layout_path)
    layouts = [load_layout(p) for p in paths]
    store_ids = [lay["storeId"] for lay in layouts]
    if len(set(store_ids)) != len(store_ids):
        raise ValueError("two layouts share a storeId")
    rules: RuleSet = load_rules(rules_path)
    noise = vision_noise.load(scenario_path, scenario.get("faults", {}).get("vision"))
    layout_bytes = b"|".join(Path(p).read_bytes() for p in paths)
    run_id = run_id_for(seed, Path(scenario_path).read_bytes(), layout_bytes,
                        Path(rules_path).read_bytes(), noise.sha256 if noise else None)
    epoch_ms = parse_iso_ms(scenario.get("epoch", DEFAULT_EPOCH))

    models = [StoreModel(lay, scenario, store_seed(seed, i)) for i, lay in enumerate(layouts)]
    per_store = [m.run() for m in models]
    # One stream for the run: time order, then store order, then each store's own causal order.
    merged = sorted(((r.t_ms, i, r.order, r) for i, recs in enumerate(per_store) for r in recs),
                    key=lambda x: x[:3])
    clean = [make_event(order=n, short_type=r.short_type, t_ms=epoch_ms + r.t_ms, store_id=store_ids[i],
                        run_id=run_id, subject=r.subject, data=r.data) for n, (_, i, _, r) in enumerate(merged)]

    # Ground truth: reference rules per store over the clean, in-order stream. Each store's final
    # watermark is its own last event time minus grace, as in rules-engine's per-(store, run) state.
    gt = []
    horizons = {}
    for i, sid in enumerate(store_ids):
        ref = RefEngine(rules)
        foot = FootfallRef(rules)
        for ev in clean:
            if ev["storeid"] != sid:
                continue
            ref_ev = {"t": parse_iso_ms(ev["time"]), "type": ev["type"], "storeId": sid,
                      "simRunId": run_id, "id": ev["id"], "data": ev["data"]}
            ref.process(ref_ev)
            foot.process(ref_ev)
        horizons[sid] = epoch_ms + models[i].end_ms - rules.grace_ms
        ref.finish(horizons[sid])
        foot.finish()
        for inc in ref.out + foot.out:
            if inc["kind"] != "OPENED":
                continue
            gt.append({"gtId": "", "ruleId": inc["ruleId"], "mode": inc["mode"],
                       "storeId": sid, "simRunId": run_id, "key": inc["key"],
                       "onsetMs": inc["onsetMs"], "atMs": inc["detectedMs"], "at": iso_ms(inc["detectedMs"])})
    if len(store_ids) > 1:
        gt.sort(key=lambda g: (g["atMs"], store_ids.index(g["storeId"]), g["ruleId"], g["key"]))
    for n, g in enumerate(gt):
        g["gtId"] = f"gt-{n:05d}"

    # Perception noise first (what the camera would have reported), then transport faults on top.
    # Its own RNG stream, so adding vision noise never shifts the transport-fault draws.
    vision_rng = np.random.default_rng(np.random.SeedSequence([seed, 0x51D0]))
    perceived, vision_counts = vision_noise.apply(clean, noise, vision_rng)
    fault_rng = np.random.default_rng(np.random.SeedSequence([seed, 0xFA017]))
    emitted, counts = inject(perceived, {k: v for k, v in scenario.get("faults", {}).items() if k != "vision"},
                             fault_rng)
    counts.update(vision_counts)

    end_ms = max(epoch_ms + m.end_ms for m in models)
    manifest = {
        "simVersion": SIM_VERSION, "seed": seed, "runId": run_id, "storeId": store_ids[0],
        "scenario": scenario.get("name", Path(scenario_path).stem),
        "layoutSha256": sha256_file(paths[0]), "scenarioSha256": sha256_file(scenario_path),
        "rulesSha256": sha256_file(rules_path),
        **({"visionNoise": {"profile": noise.name, "sha256": noise.sha256}} if noise else {}),
        "epoch": iso_ms(epoch_ms), "durationMs": parse_duration_ms(scenario["duration"]),
        "endMs": end_ms, "horizonMs": max(horizons.values()), "graceMs": rules.grace_ms,
        "counts": {"cleanEvents": len(clean), "emittedEvents": len(emitted),
                   "shoppers": sum(m.n_shoppers for m in models), "groundTruth": len(gt), **counts},
    }
    if len(store_ids) > 1:
        manifest["stores"] = [{"storeId": sid, "layoutSha256": sha256_file(p), "seed": store_seed(seed, i),
                               "endMs": epoch_ms + models[i].end_ms, "horizonMs": horizons[sid],
                               "shoppers": models[i].n_shoppers}
                              for i, (sid, p) in enumerate(zip(store_ids, paths))]
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
