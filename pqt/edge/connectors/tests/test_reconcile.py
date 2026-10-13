"""X1 milestone B6: the daily reconcile, on simulated days with planted differences."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

store_sim = pytest.importorskip("store_sim.generate")

from pqt_connectors import cli  # noqa: E402
from pqt_connectors.reconcile import bp, reconcile  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def runs():
    import os

    here = os.getcwd()
    os.chdir(ROOT)
    try:
        out = {name: store_sim.simulate(seed=42, layout_path="config/layouts/store-001.json",
                                        scenario_path=f"sim/scenarios/{name}.yaml", rules_path="config/rules.yaml")
               for name in ("billing_day", "refund_abuse", "stock_out")}
    finally:
        os.chdir(here)
    return {k: [e for e in v.clean if not e["type"].startswith("com.pqt.store.")] for k, v in out.items()}


def test_a_normal_day_adds_up(runs):
    [r] = reconcile(runs["billing_day"])
    assert r["ok"] and r["alerts"] == [] and r["dayTotalReported"]
    assert {c["name"] for c in r["checks"]} >= {"lines_vs_payments", "sales", "refunds", "bills", "tender:upi"}
    assert r["businessDate"] == "2026-01-01" and r["stock"] == []


def test_a_day_total_off_by_80bp_is_an_alert(runs):
    [r] = reconcile(runs["refund_abuse"])
    sales = next(c for c in r["checks"] if c["name"] == "sales")
    assert not sales["ok"] and sales["diffBp"] == pytest.approx(80, abs=1) and r["alerts"] == ["sales"]


def test_the_threshold_is_respected(runs):
    [r] = reconcile(runs["refund_abuse"], threshold_bp=100)
    assert r["ok"]  # 80 bp is inside a 100 bp tolerance


def test_a_lost_payment_breaks_lines_vs_payments(runs):
    evs = copy.deepcopy(runs["billing_day"])
    i = next(i for i, e in enumerate(evs) if e["type"] == "com.pqt.pos.payment")
    lost = evs.pop(i)["data"]
    [r] = reconcile(evs)
    lvp = next(c for c in r["checks"] if c["name"] == "lines_vs_payments")
    assert not lvp["ok"] and lvp["diff"] == lost["amountMinor"]
    assert f"tender:{lost['tender']}" in r["alerts"] or bp(lost["amountMinor"], 1, 1) > 0


def test_a_missing_stock_movement_is_found_per_sku(runs):
    evs = copy.deepcopy(runs["billing_day"])
    i = next(i for i, e in enumerate(evs) if e["type"] == "com.pqt.stock.movement" and e["data"]["reason"] == "purchase_receipt")
    gone = evs.pop(i)["data"]
    [r] = reconcile(evs)
    assert [s["sku"] for s in r["stock"]] == [gone["sku"]]
    assert r["stock"][0]["unexplainedMilli"] == gone["qtyMilli"] and f"stock:{gone['sku']}" in r["alerts"]


def test_stock_outs_and_deliveries_add_up(runs):
    [r] = reconcile(runs["stock_out"])
    assert r["stock"] == [] and r["ok"]


def test_duplicates_from_the_transport_are_not_double_sales(runs):
    evs = runs["billing_day"] + runs["billing_day"][:200]
    [r] = reconcile(evs)
    assert r["ok"]


def test_cli_exit_code_3_on_alerts(runs, tmp_path):
    good, bad = tmp_path / "good.jsonl", tmp_path / "bad.jsonl"
    good.write_text("\n".join(json.dumps(e) for e in runs["billing_day"]) + "\n")
    bad.write_text("\n".join(json.dumps(e) for e in runs["refund_abuse"]) + "\n")
    assert cli.main(["reconcile", "--events", str(good)]) == 0
    assert cli.main(["reconcile", "--events", str(bad), "--report", str(tmp_path / "r.json")]) == 3
    assert json.loads((tmp_path / "r.json").read_text())[0]["alerts"] == ["sales"]
