"""Milestone S2: a second store layout with two queues, and several stores in one run."""
import json
from collections import Counter
from pathlib import Path

import jsonschema
import pytest
import yaml

from store_sim.generate import simulate, store_seed, write_files

LAYOUT = "config/layouts/store-001.json"
RULES = "config/rules.yaml"
SCHEMAS = Path("schemas")


def run(scenario: str, seed: int = 42, scenario_path: str | None = None):
    return simulate(seed=seed, layout_path=LAYOUT, scenario_path=scenario_path or f"sim/scenarios/{scenario}.yaml",
                    rules_path=RULES)


def test_store_002_lines_are_served_only_by_their_own_registers():
    layout = json.loads(Path("config/layouts/store-002.json").read_text())
    regs = {r["id"]: r["queueId"] for r in layout["registers"]}
    zone_of = {q["id"]: q["zoneId"] for q in layout["queues"]}
    out = run("two_queues")
    lengths = [e["data"] for e in out.clean if e["type"].endswith("queue.length")]
    assert {d["queueId"] for d in lengths} == {"checkout-1", "checkout-2"}
    per_queue = Counter(regs.values())
    for d in lengths:
        assert 0 <= d["openRegisters"] <= per_queue[d["queueId"]], d
    # A shopper's checkout zone is the zone of the line they joined.
    joined = {e["data"]["trackId"]: e["data"]["queueId"] for e in out.clean if e["type"].endswith("queue.joined")}
    assert set(joined.values()) == {"checkout-1", "checkout-2"}, "both lines get used"
    for e in out.clean:
        if e["type"].endswith("zone.entered") and e["data"]["zoneId"].startswith("checkout-"):
            assert e["data"]["zoneId"] == zone_of[joined[e["data"]["trackId"]]]


def test_two_queues_breach_independently_in_every_matrix_seed():
    for seed in (11, 42, 1337):
        keys = {g["key"] for g in run("two_queues", seed).ground_truth if g["ruleId"] == "R-QUEUE-001"}
        assert keys == {"checkout-1", "checkout-2"}, (seed, keys)


def test_multi_store_run_interleaves_both_stores_in_time_order_with_unique_ids():
    out = run("multi_store")
    stores = Counter(e["storeid"] for e in out.clean)
    assert set(stores) == {"store-001", "store-002"}
    times = [e["time"] for e in out.clean]
    assert times == sorted(times)
    assert [int(e["sequence"]) for e in out.clean] == list(range(len(out.clean)))
    assert len({e["id"] for e in out.clean}) == len(out.clean)
    for e in out.clean:
        assert e["source"].endswith(e["storeid"]) and e["partitionkey"] == e["storeid"]
    assert {g["storeId"] for g in out.ground_truth} == {"store-001", "store-002"}
    assert [m["storeId"] for m in out.manifest["stores"]] == ["store-001", "store-002"]
    assert out.manifest["counts"]["shoppers"] == sum(m["shoppers"] for m in out.manifest["stores"])


def test_first_store_in_a_multi_store_run_behaves_exactly_as_alone(tmp_path):
    """Rules keep state per (store, run): a neighbour store must not change a store's events or truth."""
    sc = yaml.safe_load(Path("sim/scenarios/multi_store.yaml").read_text())
    sc["stores"] = ["store-001"]
    solo_path = tmp_path / "solo.yaml"
    solo_path.write_text(yaml.safe_dump(sc))
    multi, solo = run("multi_store"), run("x", scenario_path=str(solo_path))
    shape = lambda evs: [(e["time"], e["type"], e["subject"], json.dumps(e["data"], sort_keys=True))
                         for e in evs if e["storeid"] == "store-001"]
    assert shape(multi.clean) == shape(solo.clean)
    truth = lambda gt: [(g["ruleId"], g["key"], g["onsetMs"], g["atMs"]) for g in gt if g["storeId"] == "store-001"]
    assert truth(multi.ground_truth) == truth(solo.ground_truth)


def test_multi_store_is_deterministic_and_store_seeds_differ(tmp_path):
    m1 = write_files(run("multi_store"), tmp_path / "a")
    m2 = write_files(run("multi_store"), tmp_path / "b")
    assert m1["sha256"] == m2["sha256"]
    assert store_seed(42, 0) == 42
    assert len({store_seed(42, i) for i in range(5)}) == 5


def test_every_multi_store_event_matches_the_contract():
    catalog = json.loads((SCHEMAS / "catalog.json").read_text())
    envelope = jsonschema.Draft202012Validator(json.loads((SCHEMAS / "envelope.schema.json").read_text()))
    data = {(t["type"], v["dataschema"]): jsonschema.Draft202012Validator(json.loads((SCHEMAS / v["file"]).read_text()))
            for t in catalog["types"] for v in t["versions"]}
    for ev in run("multi_store").clean:
        envelope.validate(ev)
        data[(ev["type"], ev["dataschema"])].validate(ev["data"])


@pytest.mark.parametrize("stores, message", [
    (["store-001", "store-001"], "each store may appear once"),
    (["store-999"], "no layout"),
    (["../etc/passwd"], "invalid store id"),
    ([], "a non-empty list of store ids"),
])
def test_bad_store_lists_are_rejected(stores, message, tmp_path):
    sc = {"name": "x", "duration": "PT1H", "arrivals": [{"from": "PT0S", "to": "PT1H", "perHour": 60}], "stores": stores}
    p = tmp_path / "x.yaml"
    p.write_text(yaml.safe_dump(sc))
    with pytest.raises(ValueError, match=message):
        run("x", scenario_path=str(p))


def test_register_serving_an_unknown_queue_is_rejected(tmp_path):
    from store_sim.model import StoreModel
    layout = json.loads(Path("config/layouts/store-002.json").read_text())
    layout["registers"][0]["queueId"] = "nope"
    with pytest.raises(ValueError, match="unknown queue"):
        StoreModel(layout, yaml.safe_load(Path("sim/scenarios/two_queues.yaml").read_text()), 1)
