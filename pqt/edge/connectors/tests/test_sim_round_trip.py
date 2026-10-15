"""X1 B4 end to end without Docker: store-sim bills -> billing-system CSV exports -> file connector ->
X1 events whose totals equal the simulator's ground truth. Re-importing adds nothing."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

store_sim = pytest.importorskip("store_sim.generate")
from store_sim.billing_export import export  # noqa: E402

from pqt_connectors.importer import Importer, State  # noqa: E402
from pqt_connectors.profile import load  # noqa: E402
from pqt_connectors.refs import Refs  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PROFILE = ROOT / "edge/connectors/profiles/generic-csv.yaml"


def import_dir(d: Path, state: Path) -> tuple[list[dict], dict]:
    imp = Importer(load(PROFILE), store="store-001", refs=Refs(bytes(32), "file"), state=State(state),
                   schemas=ROOT / "schemas")
    imp.bills(d / "bills.csv")
    imp.payments(d / "payments.csv")
    imp.movements(d / "movements.csv")
    imp.stock(d / "stock.csv")
    imp.day_totals(d / "day.csv")
    sent: list[dict] = []
    report = imp.commit(sent.extend).as_dict()
    imp.state.close()
    return sent, report


@pytest.mark.parametrize("scenario", ["billing_day", "refund_abuse"])
def test_simulated_exports_arrive_with_the_simulators_totals(tmp_path, monkeypatch, scenario):
    monkeypatch.chdir(ROOT)
    out = store_sim.simulate(seed=42, layout_path="config/layouts/store-001.json",
                             scenario_path=f"sim/scenarios/{scenario}.yaml", rules_path="config/rules.yaml")
    truth = out.billing_truth[0]["dayTotals"]
    export(out.clean, tmp_path / "x")
    sent, report = import_dir(tmp_path / "x", tmp_path / "state.db")
    assert report["errors"] == []
    lines = [e["data"] for e in sent if e["type"] == "com.pqt.pos.sale.line"]
    assert sum(ln["amountMinor"] for ln in lines) == truth["salesMinor"]
    assert len({ln["billRef"] for ln in lines}) == truth["bills"]
    net = defaultdict(int)
    refunds = 0
    for e in sent:
        if e["type"] == "com.pqt.pos.payment":
            d = e["data"]
            net[d["tender"]] += d["amountMinor"] if d["direction"] == "in" else -d["amountMinor"]
            refunds += d["amountMinor"] if d["direction"] == "out" else 0
    assert dict(sorted(net.items())) == truth["paymentsByTender"] and refunds == truth["refundsMinor"]
    day = next(e["data"] for e in sent if e["type"] == "com.pqt.pos.day.total")
    if scenario == "billing_day":
        assert day["salesMinor"] == truth["salesMinor"]
    else:  # the planted 0.8% disagreement survives the round trip, for the reconcile job (B6) to find
        assert day["salesMinor"] != truth["salesMinor"]
    again, report2 = import_dir(tmp_path / "x", tmp_path / "state.db")
    assert again == [] and report2["bills"]["unchanged"] == truth["bills"]


def test_the_imported_day_reconciles_including_stock(tmp_path, monkeypatch):
    """X1 B8: what the connector makes of a normal simulated day passes the daily reconcile, stock included
    (sales leave the shelf through the bills, which the exports do not list as movements)."""
    from pqt_connectors.reconcile import reconcile

    monkeypatch.chdir(ROOT)
    out = store_sim.simulate(seed=7, layout_path="config/layouts/store-001.json",
                             scenario_path="sim/scenarios/billing_day.yaml", rules_path="config/rules.yaml")
    export(out.clean, tmp_path / "x", shift_days=30)
    sent, report = import_dir(tmp_path / "x", tmp_path / "state.db")
    assert report["errors"] == []
    days = reconcile(sent)
    assert [d["businessDate"] for d in days] == ["2026-01-31"]
    assert days[0]["ok"], days[0]["alerts"]
    assert days[0]["ours"]["salesMinor"] == out.billing_truth[0]["dayTotals"]["salesMinor"]
