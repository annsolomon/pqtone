"""X1 milestone B1: the billing contracts. Every sample validates; money is never a float; no personal data fits."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
CATALOG = json.loads((SCHEMAS / "catalog.json").read_text())
BILLING = ["pos.sale.line", "pos.payment", "pos.till.event", "stock.movement", "stock.level", "pos.day.total"]
MONEY = {"pos.sale.line": ["amountMinor", "taxMinor"], "pos.payment": ["amountMinor"], "pos.till.event": ["amountMinor"],
         "pos.day.total": ["salesMinor", "refundsMinor"]}


def entry(short: str) -> dict:
    return next(t for t in CATALOG["types"] if t["type"] == f"com.pqt.{short}")


def data_validator(short: str) -> Draft202012Validator:
    v = entry(short)["versions"][-1]
    return Draft202012Validator(json.loads((SCHEMAS / v["file"]).read_text()))


def sample(short: str) -> dict:
    return json.loads((SCHEMAS / "samples" / f"{short}.json").read_text())


ENVELOPE = Draft202012Validator(json.loads((SCHEMAS / "envelope.schema.json").read_text()))


@pytest.mark.parametrize("short", BILLING)
def test_every_billing_type_is_in_the_catalog_with_a_valid_sample(short):
    e = entry(short)
    ev = sample(short)
    assert ev["type"] == e["type"] and ev["dataschema"] == e["versions"][-1]["dataschema"]
    assert list(ENVELOPE.iter_errors(ev)) == []
    assert list(data_validator(short).iter_errors(ev["data"])) == []
    assert re.fullmatch(e["subjectPattern"], ev["subject"])
    assert ev["source"].startswith("urn:pqt:edge:")  # connectors run on the edge box and use its outbox


@pytest.mark.parametrize("short", BILLING)
def test_no_personal_or_free_text_field_fits(short):
    v = data_validator(short)
    for field, value in [("customerName", "A Person"), ("phone", "+910000000000"), ("cardNumber", "4111111111111111"),
                         ("customerGstin", "00AAAAA0000A0Z0"), ("note", "anything")]:
        d = copy.deepcopy(sample(short)["data"])
        d[field] = value
        assert list(v.iter_errors(d)), f"{short} accepted {field}"


@pytest.mark.parametrize("short, field", [(s, f) for s, fs in MONEY.items() for f in fs])
def test_money_is_an_integer_of_minor_units(short, field):
    v = data_validator(short)
    d = copy.deepcopy(sample(short)["data"])
    d[field] = 490.5
    assert list(v.iter_errors(d)), f"{short}.{field} accepted a float"
    d[field] = "490.00"
    assert list(v.iter_errors(d))


def test_references_must_be_hmacs_not_real_numbers():
    v = data_validator("pos.sale.line")
    d = copy.deepcopy(sample("pos.sale.line")["data"])
    d["billRef"] = "INV-2026-000123"
    assert list(v.iter_errors(d))
    p = copy.deepcopy(sample("pos.payment")["data"])
    p["providerRef"] = "UPI-0000001"
    assert list(data_validator("pos.payment").iter_errors(p))


def test_payment_and_movement_signs_come_from_direction():
    p = copy.deepcopy(sample("pos.payment")["data"])
    p["amountMinor"] = -100
    assert list(data_validator("pos.payment").iter_errors(p))
    m = copy.deepcopy(sample("stock.movement")["data"])
    m["qtyMilli"] = 0
    assert list(data_validator("stock.movement").iter_errors(m))


def test_a_till_amount_needs_its_currency():
    d = copy.deepcopy(sample("pos.till.event")["data"])
    del d["currency"]
    assert list(data_validator("pos.till.event").iter_errors(d))
    del d["amountMinor"]
    assert list(data_validator("pos.till.event").iter_errors(d)) == []


def test_day_totals_only_know_the_tenders():
    d = copy.deepcopy(sample("pos.day.total")["data"])
    d["paymentsByTender"]["bitcoin"] = 1
    assert list(data_validator("pos.day.total").iter_errors(d))


def test_the_sample_day_adds_up():
    d = sample("pos.day.total")["data"]
    assert sum(d["paymentsByTender"].values()) == d["salesMinor"] - d["refundsMinor"]
