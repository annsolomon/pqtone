"""X1 milestone B2: billing and stock simulation with ground truth."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from store_sim import billing
from store_sim.events import canonical, parse_iso_ms
from store_sim.generate import simulate

LAYOUT = "config/layouts/store-001.json"
RULES = "config/rules.yaml"
SC = Path("sim/scenarios")
SCHEMAS = Path("schemas")


def run(name: str, seed: int = 42):
    return simulate(seed=seed, layout_path=LAYOUT, scenario_path=str(SC / f"{name}.yaml"), rules_path=RULES)


@pytest.fixture(scope="module")
def day():
    return run("billing_day")


def billing_events(out) -> list[dict]:
    return [e for e in out.clean if not e["type"].startswith("com.pqt.store.")]


# -- money ---------------------------------------------------------------------------------------

def test_line_amounts_are_exact_tax_inclusive_paise():
    assert billing.line_amounts(24500, 2000, 500) == (49000, 2333)    # 2 x Rs 245 at 5% GST: tax = 49000 * 5/105
    assert billing.line_amounts(4000, 1250, 0) == (5000, 0)          # 1.25 kg at Rs 40/kg
    assert billing.line_amounts(3000, 250, 0) == (750, 0)
    assert billing.line_amounts(3333, 500, 1800) == (1666, 254)      # 1666.5 rounds half-even to 1666
    assert billing.round_minor(Decimal("2.5")) == 2 and billing.round_minor(Decimal("3.5")) == 4


# -- determinism and isolation -------------------------------------------------------------------

def test_same_seed_same_bills(day):
    again = run("billing_day")
    h = lambda o: hashlib.sha256("\n".join(canonical(e) for e in billing_events(o)).encode()).hexdigest()
    assert h(day) == h(again) and day.billing_truth == again.billing_truth
    assert h(run("billing_day", seed=7)) != h(day)


def test_billing_does_not_change_what_the_store_model_does(day):
    """billing_day is baseline plus `billing:`; the store's own events must be the same (ids aside)."""
    base = run("baseline")
    strip = lambda o: [(e["type"], e["time"], e["subject"], canonical(e["data"])) for e in o.clean
                       if e["type"].startswith("com.pqt.store.") and e["type"] != "com.pqt.store.clock.tick"]
    assert strip(day) == strip(base)
    assert [(g["ruleId"], g["key"], g["onsetMs"]) for g in day.ground_truth] == \
           [(g["ruleId"], g["key"], g["onsetMs"]) for g in base.ground_truth]


# -- contracts -----------------------------------------------------------------------------------

def test_every_billing_event_matches_its_contract(day):
    catalog = json.loads((SCHEMAS / "catalog.json").read_text())
    envelope = Draft202012Validator(json.loads((SCHEMAS / "envelope.schema.json").read_text()))
    by_schema, subjects = {}, {}
    for t in catalog["types"]:
        subjects[t["type"]] = t["subjectPattern"]
        for v in t["versions"]:
            by_schema[v["dataschema"]] = Draft202012Validator(json.loads((SCHEMAS / v["file"]).read_text()))
    evs = billing_events(day)
    assert {e["type"] for e in evs} == {"com.pqt.pos.sale.line", "com.pqt.pos.payment", "com.pqt.pos.till.event",
                                        "com.pqt.stock.movement", "com.pqt.stock.level", "com.pqt.pos.day.total"}
    for e in evs:
        assert list(envelope.iter_errors(e)) == [], e
        assert list(by_schema[e["dataschema"]].iter_errors(e["data"])) == [], e
        assert re.fullmatch(subjects[e["type"]], e["subject"]), e


# -- the books balance ---------------------------------------------------------------------------

def recompute(out) -> dict:
    """Day totals from the emitted events alone, as a consumer would: latest revision per bill, voids
    of the latest revision cancel the bill, payments netted by tender."""
    lines = defaultdict(lambda: defaultdict(int))   # bill -> revision -> amount
    voided = defaultdict(set)
    tender = defaultdict(int)
    refunds = 0
    for e in billing_events(out):
        d = e["data"]
        if e["type"] == "com.pqt.pos.sale.line":
            lines[d["billRef"]][d["revision"]] += d["amountMinor"]
        elif e["type"] == "com.pqt.pos.till.event" and d["kind"] == "void":
            voided[d["billRef"]].add(d["revision"])
        elif e["type"] == "com.pqt.pos.payment":
            sign = 1 if d["direction"] == "in" else -1
            tender[d["tender"]] += sign * d["amountMinor"]
            refunds += d["amountMinor"] if d["direction"] == "out" else 0
    sales = bills = 0
    for ref, revs in lines.items():
        top = max(revs)
        if top in voided[ref]:
            continue
        sales += revs[top]
        bills += 1
    return {"salesMinor": sales, "refundsMinor": refunds, "bills": bills, "paymentsByTender": dict(sorted(tender.items()))}


def test_day_totals_match_the_bills_and_the_payments(day):
    truth = day.billing_truth[0]["dayTotals"]
    assert recompute(day) == truth
    reported = next(e for e in billing_events(day) if e["type"] == "com.pqt.pos.day.total")["data"]
    assert {k: reported[k] for k in truth} == truth  # no planted skew in billing_day
    assert sum(truth["paymentsByTender"].values()) == truth["salesMinor"] - truth["refundsMinor"]
    assert day.billing_truth[0]["anomalies"] == []


def test_every_served_shopper_gets_a_bill_or_a_cancelled_one(day):
    served = sum(1 for e in day.clean if e["type"] == "com.pqt.store.queue.left" and e["data"]["served"])
    revs = defaultdict(set)
    voided = defaultdict(set)
    for e in billing_events(day):
        if e["type"] == "com.pqt.pos.sale.line":
            revs[e["data"]["billRef"]].add(e["data"]["revision"])
        elif e["type"] == "com.pqt.pos.till.event" and e["data"]["kind"] == "void":
            voided[e["data"]["billRef"]].add(e["data"]["revision"])
    cancelled = sum(1 for ref, r in revs.items() if max(r) in voided[ref])
    # Shoppers still being served when the run ends have no bill yet.
    assert served - 4 <= day.billing_truth[0]["dayTotals"]["bills"] + cancelled <= served


@pytest.mark.parametrize("name", ["billing_day", "stock_out"])
def test_stock_moves_with_sales_returns_and_deliveries(name):
    """Every stock snapshot equals the opening stock plus the movements dated before it (stock_out has a
    delivery at the same instant as a snapshot: the case the reconcile job, B6, caught)."""
    day = run(name)
    overrides = (yaml.safe_load((SC / f"{name}.yaml").read_text())["billing"].get("stockOverrides") or {})
    cat = json.loads(Path("config/catalogue/store-001.json").read_text())
    stock = {i["sku"]: int(overrides.get(i["sku"], i["initialStockMilli"])) for i in cat["items"]}
    for e in billing_events(day):
        if e["type"] == "com.pqt.stock.movement":
            d = e["data"]
            stock[d["sku"]] += d["qtyMilli"] if d["direction"] == "in" else -d["qtyMilli"]
        elif e["type"] == "com.pqt.stock.level":
            assert e["data"]["qtyMilli"] == stock[e["data"]["sku"]], e
    assert stock == day.billing_truth[0]["closingStockMilli"]
    assert min(stock.values()) >= 0


def test_billing_events_carry_no_real_numbers_or_people(day):
    text = "\n".join(canonical(e["data"]) for e in billing_events(day))
    assert "PB-1001" not in text  # the delivery's document number appears only as an HMAC
    for ref in re.findall(r'"(?:billRef|docRef|providerRef)":"([^"]+)"', text):
        assert re.fullmatch(r"(bill|doc|pay)-[0-9a-f]{32}", ref)


# -- planted anomalies ---------------------------------------------------------------------------

def test_refund_abuse_plants_empty_till_refunds_and_a_day_total_mismatch():
    out = run("refund_abuse")
    truth = out.billing_truth[0]
    planted = [a for a in truth["anomalies"] if a["kind"] == "empty_till_refund"]
    assert len(planted) == 4
    refunds = {(e["data"]["tillId"], parse_iso_ms(e["time"]), e["data"]["billRef"]) for e in billing_events(out)
               if e["type"] == "com.pqt.pos.till.event" and e["data"]["kind"] == "refund"}
    for a in planted:
        assert (a["tillId"], a["atMs"], a["billRef"]) in refunds
    mm = next(a for a in truth["anomalies"] if a["kind"] == "day_total_mismatch")
    assert mm["reportedSalesMinor"] / mm["trueSalesMinor"] == pytest.approx(1.008, abs=1e-6)
    reported = next(e for e in billing_events(out) if e["type"] == "com.pqt.pos.day.total")["data"]
    assert reported["salesMinor"] == mm["reportedSalesMinor"] != recompute(out)["salesMinor"]


def test_stock_out_stops_sales_until_the_delivery():
    out = run("stock_out")
    outs = {}
    for a in out.billing_truth[0]["anomalies"]:
        if a["kind"] == "stock_out":
            outs.setdefault(a["sku"], a["atMs"])
    assert {"MILK-1L", "BREAD-400G"} <= set(outs)
    delivery = min(parse_iso_ms(e["time"]) for e in billing_events(out)
                   if e["type"] == "com.pqt.stock.movement" and e["data"]["reason"] == "purchase_receipt")
    for sku in ("MILK-1L", "BREAD-400G"):
        sold_between = [e for e in billing_events(out) if e["type"] == "com.pqt.pos.sale.line" and e["data"]["sku"] == sku
                        and outs[sku] < parse_iso_ms(e["time"]) < delivery]
        assert sold_between == [], sku


# -- configuration -------------------------------------------------------------------------------

@pytest.mark.parametrize("edit, msg", [
    (lambda b: b.update(tills=3), "unknown key"),
    (lambda b: b.update(tenders={"cash": 0.5, "upi": 0.4}), "tenders"),
    (lambda b: b.update(tenders={"cash": 0.5, "bitcoin": 0.5}), "tenders"),
    (lambda b: b.update(stockOverrides={"NOPE": 1}), "unknown sku"),
    (lambda b: b.update(anomalies={"ghosts": 1}), "anomalies"),
])
def test_bad_billing_config_is_rejected(tmp_path, edit, msg):
    doc = yaml.safe_load((SC / "billing_day.yaml").read_text())
    edit(doc["billing"])
    doc["billing"]["catalogue"] = str(Path("config/catalogue/store-001.json").resolve())
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError, match=msg):
        simulate(seed=1, layout_path=LAYOUT, scenario_path=str(p), rules_path=RULES)


def test_the_catalogue_changes_the_run_id(tmp_path):
    cat = json.loads(Path("config/catalogue/store-001.json").read_text())
    cat["items"][0]["priceMinor"] += 100
    (tmp_path / "cat.json").write_text(json.dumps(cat))
    doc = yaml.safe_load((SC / "billing_day.yaml").read_text())
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    doc["billing"]["catalogue"] = str(Path("config/catalogue/store-001.json").resolve())
    a.write_text(yaml.safe_dump(doc))
    doc["billing"]["catalogue"] = str((tmp_path / "cat.json").resolve())
    b.write_text(yaml.safe_dump(doc))
    ra = simulate(seed=1, layout_path=LAYOUT, scenario_path=str(a), rules_path=RULES)
    rb = simulate(seed=1, layout_path=LAYOUT, scenario_path=str(b), rules_path=RULES)
    assert ra.run_id != rb.run_id and ra.manifest["billing"]["catalogueSha256"] != rb.manifest["billing"]["catalogueSha256"]


def test_bill_references_never_repeat_even_after_a_whole_bill_is_cancelled():
    """A cancelled bill used to free its number, so the next bill reused its reference (found by the
    connector round trip, B4)."""
    for name in ("billing_day", "refund_abuse", "stock_out"):
        out = run(name)
        first_seen: dict[str, str] = {}
        for e in billing_events(out):
            if e["type"] == "com.pqt.pos.sale.line" and e["data"]["revision"] == 0 and e["data"]["lineNo"] == 1:
                ref = e["data"]["billRef"]
                assert ref not in first_seen, f"{name}: {ref} started twice"
                first_seen[ref] = e["time"]


def test_export_writes_billing_system_style_csvs(day, tmp_path):
    from store_sim.billing_export import export

    counts = export(day.clean, tmp_path)
    truth = day.billing_truth[0]["dayTotals"]
    import csv as _csv

    bills = list(_csv.DictReader(open(tmp_path / "bills.csv", encoding="utf-8")))
    assert len({r["Bill No"] for r in bills}) == truth["bills"] and counts["bills"] == len(bills)
    assert sum(round(float(r["Amount"]) * 100) for r in bills) == truth["salesMinor"]
    day_row = next(_csv.DictReader(open(tmp_path / "day.csv", encoding="utf-8")))
    assert day_row["Date"] == "01-01-2026" and day_row["Bills"] == str(truth["bills"])
    for f in ("payments.csv", "movements.csv", "stock.csv"):
        assert (tmp_path / f).read_text().count("\n") > 1
    assert all(r["Bill No"].startswith("SIM-") for r in bills)  # invented numbers, not the HMAC references


def test_export_can_move_the_trading_day_to_a_recent_date(day, tmp_path):
    """X1 B8: the simulated day (1 January 2026) exported as any later day: dates move by whole days, times of
    day and every amount stay the same, so the ground truth still applies."""
    import csv as _csv

    from store_sim.billing_export import export, first_local_date

    assert str(first_local_date(day.clean)) == "2026-01-01"
    export(day.clean, tmp_path / "plain")
    counts = export(day.clean, tmp_path / "moved", shift_days=284)  # 2026-10-12
    assert counts["shiftDays"] == 284
    for f in ("bills.csv", "payments.csv", "movements.csv", "stock.csv"):
        plain = list(_csv.DictReader(open(tmp_path / "plain" / f, encoding="utf-8")))
        moved = list(_csv.DictReader(open(tmp_path / "moved" / f, encoding="utf-8")))
        assert len(plain) == len(moved) and plain, f
        for a, b in zip(plain, moved):
            assert b["Date"] == "12-10-2026" and a["Date"] == "01-01-2026", f
            assert {k: v for k, v in a.items() if k != "Date"} == {k: v for k, v in b.items() if k != "Date"}, f
    day_row = next(_csv.DictReader(open(tmp_path / "moved" / "day.csv", encoding="utf-8")))
    assert day_row["Date"] == "12-10-2026"


def test_the_export_command_shifts_to_a_named_date(tmp_path):
    import csv as _csv

    from store_sim.cli import main

    assert main(["run", "--seed", "42", "--scenario", "sim/scenarios/billing_day.yaml", "--out", str(tmp_path / "run")]) == 0
    assert main(["export-billing", "--run", str(tmp_path / "run"), "--out", str(tmp_path / "x"), "--shift-to", "2026-10-12"]) == 0
    assert {r["Date"] for r in _csv.DictReader(open(tmp_path / "x" / "bills.csv", encoding="utf-8"))} == {"12-10-2026"}
