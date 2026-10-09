import json
from collections import Counter
from pathlib import Path

import jsonschema
import pytest

from store_sim.config import load_rules, parse_duration_ms
from store_sim.events import iso_ms, parse_iso_ms
from store_sim.generate import simulate, write_files
from store_sim.reference import RefEngine

LAYOUT = "config/layouts/store-001.json"
RULES = "config/rules.yaml"
SCHEMAS = Path("schemas")


def run(scenario: str, seed: int = 42):
    return simulate(seed=seed, layout_path=LAYOUT, scenario_path=f"sim/scenarios/{scenario}.yaml", rules_path=RULES)


def test_same_seed_is_byte_identical(tmp_path):
    m1 = write_files(run("out_of_order"), tmp_path / "a")
    m2 = write_files(run("out_of_order"), tmp_path / "b")
    assert m1["sha256"] == m2["sha256"]
    assert (tmp_path / "a" / "events.jsonl").read_bytes() == (tmp_path / "b" / "events.jsonl").read_bytes()


def test_different_seed_differs(tmp_path):
    m1 = write_files(run("baseline", 1), tmp_path / "a")
    m2 = write_files(run("baseline", 2), tmp_path / "b")
    assert m1["sha256"]["events"] != m2["sha256"]["events"]


@pytest.fixture(scope="module")
def validators():
    catalog = json.loads((SCHEMAS / "catalog.json").read_text())
    envelope = jsonschema.Draft202012Validator(json.loads((SCHEMAS / "envelope.schema.json").read_text()))
    data = {}
    for t in catalog["types"]:
        for v in t["versions"]:
            data[(t["type"], v["dataschema"])] = jsonschema.Draft202012Validator(json.loads((SCHEMAS / v["file"]).read_text()))
    return envelope, data


def test_every_clean_event_matches_the_contract(validators):
    envelope, data = validators
    out = run("register_delay")
    for ev in out.clean:
        envelope.validate(ev)
        data[(ev["type"], ev["dataschema"])].validate(ev["data"])


def test_malformed_events_violate_the_contract(validators):
    envelope, data = validators
    out = run("malformed")
    bad = [e for e in out.emitted if e["id"].startswith("malformed-")]
    assert bad, "scenario should inject malformed events"
    for ev in bad:
        errors = list(envelope.iter_errors(ev))
        if not errors:
            v = data.get((ev["type"], ev["dataschema"]))
            errors = [] if v is None else list(v.iter_errors(ev["data"]))
            if v is None:
                errors = ["unknown type"]
        assert errors, f"malformed event passed validation: {ev['id']}"


def test_faults_do_not_change_event_times_and_ticks_are_never_faulted():
    out = run("late_beyond_grace")
    clean_by_id = {e["id"]: e for e in out.clean}
    for e in out.emitted:
        assert e["time"] == clean_by_id[e["id"]]["time"]
    ticks = [e for e in out.emitted if e["type"].endswith("clock.tick")]
    assert [t["data"]["seq"] for t in ticks] == sorted(t["data"]["seq"] for t in ticks)


def test_duplicates_are_exact_copies():
    out = run("duplicates")
    counts = Counter(e["id"] for e in out.emitted)
    dup_ids = [i for i, c in counts.items() if c > 1]
    assert dup_ids
    assert len(out.emitted) - len(out.clean) == out.manifest["counts"]["duplicates"]


def test_clean_stream_is_time_ordered_with_monotonic_sequence():
    out = run("baseline")
    keys = [(parse_iso_ms(e["time"]), e["sequence"]) for e in out.clean]
    assert keys == sorted(keys)
    assert [e["sequence"] for e in out.clean] == sorted(e["sequence"] for e in out.clean)


def test_register_delay_produces_absence_ground_truth():
    out = run("register_delay")
    assert any(g["ruleId"] == "R-ABS-001" for g in out.ground_truth)


def test_iso_roundtrip():
    for ms in (0, 1_767_258_000_123, 1_767_258_000_999):
        assert parse_iso_ms(iso_ms(ms)) == ms


def test_duration_parser():
    assert parse_duration_ms("PT1H2M3S") == 3_723_000
    with pytest.raises(ValueError):
        parse_duration_ms("P1D")


def _ev(t, typ, data, i):
    return {"t": t, "type": typ, "storeId": "s", "simRunId": "r", "id": f"e{i}", "data": data}


def test_reference_queue_rule_semantics():
    rules = load_rules(RULES)
    ref = RefEngine(rules)
    n, sustain = rules.queue_threshold, rules.queue_sustain_ms
    ref.process(_ev(0, "com.pip.store.queue.length", {"queueId": "q", "length": n, "openRegisters": 1}, 1))
    ref.process(_ev(sustain - 1, "com.pip.store.queue.length", {"queueId": "q", "length": n + 1, "openRegisters": 1}, 2))
    assert not ref.out
    ref.process(_ev(sustain + 10, "com.pip.store.clock.tick", {"seq": 1}, 3))
    assert [(o["ruleId"], o["kind"], o["detectedMs"]) for o in ref.out] == [("R-QUEUE-001", "OPENED", sustain)]
    # inside the hysteresis band: no clear
    ref.process(_ev(sustain + 20, "com.pip.store.queue.length", {"queueId": "q", "length": n - 1, "openRegisters": 1}, 4))
    ref.finish(sustain + 20 + rules.queue_clear_ms + 5_000)
    assert len(ref.out) == 1


def test_reference_absence_cancelled_by_register_opening():
    rules = load_rules(RULES)
    ref = RefEngine(rules)
    n = rules.queue_threshold
    ref.process(_ev(0, "com.pip.store.queue.length", {"queueId": "q", "length": n, "openRegisters": 1}, 1))
    ref.process(_ev(rules.queue_sustain_ms + 1_000, "com.pip.store.register.opened", {"registerId": "r2"}, 2))
    ref.finish(rules.queue_sustain_ms + rules.abs_within_ms + 60_000)
    assert [o["ruleId"] for o in ref.out] == ["R-QUEUE-001"]


# ---------------------------------------------------------------- R-FOOT-001 (windowed footfall spike)
from store_sim.reference import FootfallRef  # noqa: E402

W = 300_000  # 5-minute windows


def _entries(ref: FootfallRef, window: int, n: int, zone: str = "entrance") -> None:
    """n zone.entered events inside window number `window`, plus a tick at its start."""
    start = window * W
    ref.process(_ev(start, "com.pip.store.clock.tick", {"seq": window}, f"t{window}"))
    for k in range(n):
        ref.process(_ev(start + 1_000 + k, "com.pip.store.zone.entered", {"zoneId": zone, "trackId": f"trk-{k}"}, f"{window}-{k}"))


def _foot(windows: list[int], close_last: bool = True) -> list[tuple[str, int, int]]:
    rules = load_rules(RULES)
    assert rules.foot_zones == ("entrance",) and rules.foot_history_windows == 12
    ref = FootfallRef(rules)
    for i, n in enumerate(windows):
        _entries(ref, i, n)
    if close_last:  # a tick far enough past the last window's end + grace closes it
        last_end = len(windows) * W
        ref.process(_ev(last_end + rules.grace_ms, "com.pip.store.clock.tick", {"seq": 999}, "close"))
    ref.finish()
    return [(o["kind"], o["onsetMs"] // W, o["detectedMs"] // W) for o in ref.out]


def test_footfall_spike_opens_after_a_full_hour_and_resolves_on_the_next_quiet_window():
    assert _foot([5] * 12 + [11, 5]) == [("OPENED", 12, 13), ("RESOLVED", 12, 14)]


def test_footfall_spike_needs_a_full_hour_of_history():
    assert _foot([5] * 11 + [40]) == []


def test_footfall_spike_is_strictly_more_than_factor_times_the_mean_and_at_least_min_count():
    assert _foot([5] * 12 + [10]) == []          # exactly 2x the mean is not a spike
    assert _foot([2] * 12 + [9]) == []           # 4.5x the mean but below minCount 10
    assert _foot([2] * 12 + [10]) == [("OPENED", 12, 13)]


def test_footfall_consecutive_spike_windows_are_one_incident():
    out = _foot([5] * 12 + [20, 30, 5])
    assert out == [("OPENED", 12, 13), ("RESOLVED", 12, 15)]


def test_footfall_window_is_only_final_once_stream_time_passes_end_plus_grace():
    assert _foot([5] * 12 + [20], close_last=False) == []
    assert _foot([5] * 12 + [20], close_last=True) == [("OPENED", 12, 13)]


def test_footfall_counts_only_configured_zones():
    rules = load_rules(RULES)
    ref = FootfallRef(rules)
    for i in range(13):
        _entries(ref, i, 5)
    for k in range(50):
        ref.process(_ev(12 * W + 2_000 + k, "com.pip.store.zone.entered", {"zoneId": "produce", "trackId": f"p{k}"}, f"p{k}"))
    ref.process(_ev(14 * W, "com.pip.store.clock.tick", {"seq": 99}, "close"))
    ref.finish()
    assert ref.out == []


def test_footfall_spike_scenario_has_one_entrance_spike_per_seed():
    for seed in (11, 42, 1337):
        gt = [g for g in run("footfall_spike", seed).ground_truth if g["ruleId"] == "R-FOOT-001"]
        assert [g["key"] for g in gt] == ["entrance"], (seed, gt)


@pytest.mark.parametrize("scenario, expected", [
    ("staff_shortage", {"R-QUEUE-001", "R-ABS-001"}),
    ("flash_sale", {"R-QUEUE-001", "R-FOOT-001"}),
    ("closing_time", {"R-QUEUE-001"}),
])
def test_new_scenarios_produce_the_ground_truth_they_describe(scenario, expected):
    """Milestone S3. Every matrix seed must exercise the rules the scenario is about."""
    for seed in (11, 42, 1337):
        out = run(scenario, seed)
        rules = {g["ruleId"] for g in out.ground_truth}
        assert expected <= rules, (scenario, seed, rules)


def test_staff_shortage_never_opens_a_register():
    out = run("staff_shortage")
    assert not [e for e in out.clean if e["type"].endswith("register.opened")]
    assert {e["data"]["openRegisters"] for e in out.clean if e["type"].endswith("queue.length")} == {1}


def test_closing_time_ends_empty_with_one_register_and_nothing_open():
    out = run("closing_time")
    last = [e["data"] for e in out.clean if e["type"].endswith("queue.length")][-1]
    assert last == {"queueId": "checkout-1", "length": 0, "openRegisters": 1}
    rules = load_rules(RULES)
    ref = RefEngine(rules)
    for ev in out.clean:
        ref.process({"t": parse_iso_ms(ev["time"]), "type": ev["type"], "storeId": "store-001",
                     "simRunId": out.run_id, "id": ev["id"], "data": ev["data"]})
    ref.finish(out.manifest["horizonMs"])
    opened = Counter(i["ruleId"] for i in ref.out if i["kind"] == "OPENED")
    resolved = Counter(i["ruleId"] for i in ref.out if i["kind"] == "RESOLVED")
    assert opened == resolved, "every incident opened before closing resolves"


@pytest.mark.parametrize("bad, message", [
    ({"staffing": {"reactonMax": "PT60S"}}, "unknown key staffing.reactonMax"),
    ({"arrival": []}, "unknown key 'arrival'"),
    ({"faults": {"dupes": {"rate": 0.1}}}, "unknown key faults.dupes"),
    ({"arrivals": [{"from": "PT0S", "to": "PT1H"}]}, "arrivals[0] needs from, to and a perHour >= 0"),
    ({"arrivals": [{"from": "PT0S", "to": "PT1H", "perHour": 0}]}, "at least one segment needs perHour > 0"),
])
def test_scenario_typos_are_rejected(bad, message, tmp_path):
    from store_sim.config import load_scenario
    import yaml as _yaml
    sc = {"name": "x", "duration": "PT1H", "arrivals": [{"from": "PT0S", "to": "PT1H", "perHour": 60}]}
    sc.update(bad)
    p = tmp_path / "x.yaml"
    p.write_text(_yaml.safe_dump(sc))
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        load_scenario(p)


def test_every_shipped_scenario_passes_the_key_check():
    from store_sim.config import load_scenario
    for p in sorted(Path("sim/scenarios").glob("*.yaml")):
        load_scenario(p)
