"""Milestone S5: the faults.vision hook. Validated, recorded and deterministic; applies nothing in Tier 1."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from store_sim import vision_noise
from store_sim.generate import simulate, write_files

LAYOUT = "config/layouts/store-001.json"
RULES = "config/rules.yaml"
NOISE = Path("config/noise").resolve()


def scenario_with(tmp_path: Path, vision: dict | None, name: str = "noisy") -> Path:
    base = yaml.safe_load(Path("sim/scenarios/out_of_order.yaml").read_text())
    if vision is not None:
        base["faults"]["vision"] = vision
    p = tmp_path / f"{name}.yaml"
    p.write_text(yaml.safe_dump(base))
    return p


def run(path: Path, seed: int = 42):
    return simulate(seed=seed, layout_path=LAYOUT, scenario_path=str(path), rules_path=RULES)


@pytest.mark.parametrize("profile", ["none.yaml", "example.yaml"])
def test_shipped_profiles_are_valid(profile):
    assert vision_noise.validate(yaml.safe_load((NOISE / profile).read_text())) == []


@pytest.mark.parametrize("edit, where", [
    (lambda d: d["detection"]["missRate"].update(base=1.5), "detection/missRate/base"),
    (lambda d: d.pop("tracking"), "(root)"),
    (lambda d: d.update(apiVersion="noise.pip/v2"), "apiVersion"),
    (lambda d: d["timing"].update(entryJitterMs={"kind": "normal", "mean": 0}), "timing/entryJitterMs"),
    (lambda d: d.update(embeddings=[0.1, 0.2]), "(root)"),
    (lambda d: d.update(zones={"Checkout Lane": {}}), "zones"),
])
def test_invalid_profiles_are_rejected_with_a_location(edit, where):
    doc = yaml.safe_load((NOISE / "example.yaml").read_text())
    edit(doc)
    errors = vision_noise.validate(doc)
    assert errors and any(e.startswith(where) for e in errors), errors


def test_no_vision_section_means_no_profile(tmp_path):
    assert vision_noise.load(tmp_path / "x.yaml", None) is None
    assert vision_noise.load(tmp_path / "x.yaml", {}) is None


def test_a_missing_or_invalid_profile_stops_the_run(tmp_path):
    with pytest.raises(vision_noise.NoiseProfileError, match="not found"):
        run(scenario_with(tmp_path, {"profile": "nowhere.yaml"}))
    bad = tmp_path / "bad.yaml"
    bad.write_text("apiVersion: noise.pip/v1\nname: bad\n")
    with pytest.raises(vision_noise.NoiseProfileError, match="not a valid noise profile"):
        run(scenario_with(tmp_path, {"profile": "bad.yaml"}))
    with pytest.raises(vision_noise.NoiseProfileError, match="exactly one key"):
        run(scenario_with(tmp_path, {"profile": str(NOISE / "none.yaml"), "rate": 0.1}))


def test_tier1_hook_is_a_no_op_on_events_and_ground_truth(tmp_path):
    plain = run(scenario_with(tmp_path, None, "plain"))
    for profile in ("none.yaml", "example.yaml"):
        noisy = run(scenario_with(tmp_path, {"profile": str(NOISE / profile)}, f"with-{profile[:-5]}"))
        strip = lambda evs: [{k: v for k, v in e.items() if k not in ("id", "simrunid", "source")} for e in evs]
        assert strip(noisy.emitted) == strip(plain.emitted)
        assert [g["onsetMs"] for g in noisy.ground_truth] == [g["onsetMs"] for g in plain.ground_truth]


def test_profile_is_recorded_and_changes_the_run_id(tmp_path):
    none = run(scenario_with(tmp_path, {"profile": str(NOISE / "none.yaml")}, "a"))
    example = run(scenario_with(tmp_path, {"profile": str(NOISE / "example.yaml")}, "a"))
    assert none.manifest["visionNoise"]["profile"] == "none"
    assert example.manifest["visionNoise"]["profile"] == "example"
    assert none.manifest["visionNoise"]["sha256"] != example.manifest["visionNoise"]["sha256"]
    assert none.run_id != example.run_id
    assert none.manifest["counts"]["visionNoiseApplied"] == 0


def test_same_seed_and_profile_is_byte_identical(tmp_path):
    sc = scenario_with(tmp_path, {"profile": str(NOISE / "example.yaml")})
    m1 = write_files(run(sc), tmp_path / "a")
    m2 = write_files(run(sc), tmp_path / "b")
    assert m1["sha256"] == m2["sha256"]


def test_existing_scenarios_keep_their_run_ids():
    out = simulate(seed=42, layout_path=LAYOUT, scenario_path="sim/scenarios/baseline.yaml", rules_path=RULES)
    assert "visionNoise" not in out.manifest
    assert "visionNoiseApplied" not in out.manifest["counts"]


def test_relative_profile_paths_resolve_from_the_scenario_file(tmp_path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "p.yaml").write_text((NOISE / "none.yaml").read_text())
    sc = scenario_with(tmp_path, {"profile": "profiles/p.yaml"})
    assert run(sc).manifest["visionNoise"]["profile"] == "none"
