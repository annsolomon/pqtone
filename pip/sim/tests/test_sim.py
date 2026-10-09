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


def test_an_extra_draw_in_one_stream_leaves_the_others_alone():
    """docs/learn/store-sim.md: separate RNG streams confine a model change to what it touches."""
    import yaml as _yaml

    from store_sim.model import StoreModel

    layout = json.loads(Path(LAYOUT).read_text())
    scenario = _yaml.safe_load(Path("sim/scenarios/rush_hour.yaml").read_text())

    class ExtraServiceDraw(StoreModel):
        def _lognormal_ms(self, rng, median_s, sigma):
            if rng is self.r_svc:
                rng.random()  # a new random draw, as if the model had grown a feature at the registers
            return super()._lognormal_ms(rng, median_s, sigma)

    def shopping(records):
        # Everything decided by the arrival and movement streams: who arrives when, where they go.
        return [(r.t_ms, r.short_type, r.data.get("zoneId"), r.data.get("trackId")) for r in records
                if r.short_type in ("zone.entered", "zone.exited") and r.data.get("zoneId") != "checkout"]

    base = StoreModel(layout, scenario, seed=7).run()
    changed = ExtraServiceDraw(layout, scenario, seed=7).run()
    assert shopping(changed) == shopping(base)
    service_exits = lambda recs: [r.t_ms for r in recs if r.short_type == "zone.exited" and r.data["zoneId"] == "checkout"]
    assert service_exits(changed) != service_exits(base), "the change itself must show up at the registers"
