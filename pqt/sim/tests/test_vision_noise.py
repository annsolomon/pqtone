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
    (lambda d: d.update(apiVersion="noise.pqt/v2"), "apiVersion"),
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
    bad.write_text("apiVersion: noise.pqt/v1\nname: bad\n")
    with pytest.raises(vision_noise.NoiseProfileError, match="not a valid noise profile"):
        run(scenario_with(tmp_path, {"profile": "bad.yaml"}))
    with pytest.raises(vision_noise.NoiseProfileError, match="exactly one key"):
        run(scenario_with(tmp_path, {"profile": str(NOISE / "none.yaml"), "rate": 0.1}))


def strip(evs):
    return [{k: v for k, v in e.items() if k not in ("id", "simrunid", "source")} for e in evs]


def test_the_control_profile_changes_nothing(tmp_path):
    """Tier 2 N2 replaced the Tier 1 no-op: only the all-zero profile ("none") must leave events alone."""
    plain = run(scenario_with(tmp_path, None, "plain"))
    noisy = run(scenario_with(tmp_path, {"profile": str(NOISE / "none.yaml")}, "with-none"))
    assert strip(noisy.emitted) == strip(plain.emitted)
    assert [g["onsetMs"] for g in noisy.ground_truth] == [g["onsetMs"] for g in plain.ground_truth]


def test_a_noisy_profile_changes_what_is_seen_but_never_the_ground_truth(tmp_path):
    plain = run(scenario_with(tmp_path, None, "plain"))
    noisy = run(scenario_with(tmp_path, {"profile": str(NOISE / "example.yaml")}, "with-example"))
    assert strip(noisy.emitted) != strip(plain.emitted)
    assert [(g["ruleId"], g["onsetMs"]) for g in noisy.ground_truth] == [(g["ruleId"], g["onsetMs"]) for g in plain.ground_truth]
    c = noisy.manifest["counts"]
    assert c["visionNoiseApplied"] > 0 and c["visionDropped"] > 0 and c["visionPhantoms"] > 0


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


# ------------------------------------------------------------------ N2: the injection itself
import json  # noqa: E402

import jsonschema  # noqa: E402
import numpy as np  # noqa: E402

SCHEMAS = Path("schemas")


def _profile(**over) -> vision_noise.NoiseProfile:
    doc = yaml.safe_load((NOISE / "none.yaml").read_text())
    for path, value in over.items():
        node = doc
        keys = path.split("__")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
    assert vision_noise.validate(doc) == [], vision_noise.validate(doc)
    return vision_noise.NoiseProfile(doc["name"], Path("x"), "0" * 64, doc)


_CLEAN: list = []


def _clean():
    if not _CLEAN:  # simulate once for the whole module
        _CLEAN.append(run(Path("sim/scenarios/baseline.yaml")).clean)
    return [dict(e) for e in _CLEAN[0]]


def _apply(profile, seed=7):
    return vision_noise.apply(_clean(), profile, np.random.default_rng(seed))


def _kinds(evs):
    from collections import Counter
    return Counter(e["type"].removeprefix("com.pqt.store.") for e in evs)


def test_missing_everyone_removes_all_visits_and_empties_the_queues():
    out, counts = _apply(_profile(detection__missRate={"base": 1.0}))
    k = _kinds(out)
    assert k["zone.entered"] == k["zone.exited"] == k["queue.joined"] == k["queue.left"] == 0
    assert {e["data"]["length"] for e in out if e["type"].endswith("queue.length")} == {0}
    assert k["clock.tick"] == _kinds(_clean())["clock.tick"]  # untouched event kinds survive
    assert counts["visionDropped"] == _kinds(_clean())["zone.entered"]


def test_fragmentation_splits_every_visit_into_new_track_ids():
    clean = _clean()
    out, counts = _apply(_profile(tracking__fragmentation={"probability": 1.0,
                                                           "extraTracks": {"kind": "constant", "value": 1}}))
    assert _kinds(out)["zone.entered"] == 2 * _kinds(clean)["zone.entered"]
    assert counts["visionFragments"] == _kinds(clean)["zone.entered"]
    new = {e["data"]["trackId"] for e in out if e["type"].endswith("zone.entered")} - \
          {e["data"]["trackId"] for e in clean if e["type"].endswith("zone.entered")}
    assert new and all(t.startswith("trk-n") for t in new)


def test_timing_jitter_shifts_entries():
    clean = _clean()
    out, _ = _apply(_profile(timing__entryJitterMs={"kind": "constant", "value": 700}))
    from store_sim.events import parse_iso_ms
    t_clean = sorted(parse_iso_ms(e["time"]) for e in clean if e["type"].endswith("zone.entered"))
    t_noisy = sorted(parse_iso_ms(e["time"]) for e in out if e["type"].endswith("zone.entered"))
    end = max(parse_iso_ms(e["time"]) for e in clean)
    assert t_noisy == sorted(min(t + 700, end) for t in t_clean)


def test_phantoms_appear_at_the_configured_rate():
    out, counts = _apply(_profile(detection__falsePositivesPerMinute=2.0,
                                  detection__phantomTrackDurationMs={"kind": "constant", "value": 8000}))
    minutes = 60 * 2  # baseline scenario: 2 hours
    assert 0.8 * 2 * minutes < counts["visionPhantoms"] < 1.2 * 2 * minutes
    phantom_joins = [e for e in out if e["type"].endswith("queue.joined") and e["data"]["trackId"].startswith("trk-n")]
    assert phantom_joins  # 8 s phantoms in the checkout zone pass the 5 s minimum dwell


def test_noisy_streams_are_ordered_unique_valid_and_consistent():
    out, _ = _apply(yaml.safe_load((NOISE / "example.yaml").read_text()) and
                    vision_noise.NoiseProfile("example", Path("x"), "0" * 64, yaml.safe_load((NOISE / "example.yaml").read_text())))
    times = [e["time"] for e in out]
    assert times == sorted(times)
    assert len({e["id"] for e in out}) == len(out)
    assert [int(e["sequence"]) for e in out] == list(range(len(out)))
    # queue.length equals joins minus leaves at every point (per queue)
    live: dict = {}
    for e in out:
        k = e["type"].removeprefix("com.pqt.store.")
        if k == "queue.joined":
            live[e["data"]["queueId"]] = live.get(e["data"]["queueId"], 0) + 1
        elif k == "queue.left":
            live[e["data"]["queueId"]] -= 1
        elif k == "queue.length":
            assert e["data"]["length"] == max(0, live.get(e["data"]["queueId"], 0))
    # every event still satisfies the contract
    catalog = json.loads((SCHEMAS / "catalog.json").read_text())
    data_v = {v["dataschema"]: jsonschema.Draft202012Validator(json.loads((SCHEMAS / v["file"]).read_text()))
              for t in catalog["types"] for v in t["versions"]}
    env_v = jsonschema.Draft202012Validator(json.loads((SCHEMAS / "envelope.schema.json").read_text()))
    for e in out:
        env_v.validate(e)
        data_v[e["dataschema"]].validate(e["data"])


def test_same_seed_same_noise_and_scaling():
    prof = vision_noise.NoiseProfile("example", Path("x"), "0" * 64, yaml.safe_load((NOISE / "example.yaml").read_text()))
    a, _ = _apply(prof, seed=3)
    b, _ = _apply(prof, seed=3)
    c, _ = _apply(prof, seed=4)
    assert a == b and a != c
    doubled = vision_noise.scale(prof.doc, 2.0)
    assert vision_noise.validate(doubled) == []
    assert doubled["detection"]["falsePositivesPerMinute"] == 2 * prof.doc["detection"]["falsePositivesPerMinute"]
    assert doubled["timing"]["exitJitterMs"]["sd"] == 2 * prof.doc["timing"]["exitJitterMs"]["sd"]
    assert vision_noise.is_identity(yaml.safe_load((NOISE / "none.yaml").read_text()))
    assert not vision_noise.is_identity(prof.doc)


def test_matrix_applies_a_scaled_profile_to_every_scenario(tmp_path):
    from store_sim import cli as sim_cli

    m = tmp_path / "m.yaml"
    m.write_text("seeds: [11]\nscenarios: [baseline]\n")
    out = tmp_path / "out"
    assert sim_cli.main(["matrix", "--matrix", str(m), "--scenarios-dir", "sim/scenarios", "--out", str(out),
                         "--noise-profile", str(NOISE / "example.yaml"), "--noise-scale", "2"]) == 0
    manifest = json.loads((out / "baseline-11" / "manifest.json").read_text())
    assert manifest["visionNoise"]["profile"] == "example-x2"
    assert manifest["counts"]["visionNoiseApplied"] > 0
    assert json.loads((out / "cases.json").read_text())[0]["scenario"] == "baseline"
