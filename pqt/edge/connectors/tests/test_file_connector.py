"""X1 milestone B4: the file connector. Invented data only: no real shop, bill or person."""
from __future__ import annotations

import csv
import json
import os
import stat
from pathlib import Path

import pytest

from pqt_connectors import cli
from pqt_connectors.importer import Importer, State
from pqt_connectors.money import ParseError, to_milli, to_minor
from pqt_connectors.profile import ProfileError, load
from pqt_connectors.refs import KeyError_, Refs, load_key, new_key

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROFILE = HERE.parent / "profiles" / "generic-csv.yaml"
SCHEMAS = ROOT / "schemas"
KEY = bytes(range(32))
PII = {"Customer Name": "Asha Example", "Phone": "+91 90000 00000", "GSTIN": "29ABCDE1234F1Z5", "Notes": "call back"}


def write_csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


def bill_rows(no="INV-1001", extra_line=False, time="10:15:02"):
    rows = [{"Bill No": no, "Date": "01-01-2026", "Time": time, "Counter": "Counter 1", "Line": "1", "Item Code": "ATTA 5KG",
             "Qty": "2", "Unit": "Nos", "Amount": "₹490.00", "Tax": "23.33", **PII},
            {"Bill No": no, "Date": "01-01-2026", "Time": time, "Counter": "Counter 1", "Line": "2", "Item Code": "ONION-LOOSE",
             "Qty": "1.25", "Unit": "Kgs", "Amount": "50", "Tax": "0", **PII}]
    if extra_line:
        rows.append({**rows[1], "Line": "3", "Item Code": "MILK-1L", "Qty": "1", "Unit": "nos", "Amount": "66.00"})
    return rows


def payment_rows(no="INV-1001"):
    return [{"Bill No": no, "Date": "01-01-2026", "Time": "10:15:40", "Counter": "Counter 1", "Mode": "PhonePe",
             "Amount": "500.00", "Type": "Payment", "Txn Ref": "UPI-77881", **PII},
            {"Bill No": no, "Date": "01-01-2026", "Time": "10:15:41", "Counter": "Counter 1", "Mode": "Cash",
             "Amount": "40.00", "Type": "Payment", "Txn Ref": "", **PII}]


def run_import(tmp: Path, *, bills=None, payments=None, movements=None, stock=None, day=None, state="state.db"):
    imp = Importer(load(PROFILE), store="store-001", refs=Refs(KEY, "file"), state=State(tmp / state), schemas=SCHEMAS)
    if bills:
        imp.bills(write_csv(tmp / "bills.csv", bills))
    if payments:
        imp.payments(write_csv(tmp / "payments.csv", payments))
    if movements:
        imp.movements(write_csv(tmp / "movements.csv", movements))
    if stock:
        imp.stock(write_csv(tmp / "stock.csv", stock))
    if day:
        imp.day_totals(write_csv(tmp / "day.csv", day))
    sent: list[dict] = []
    report = imp.commit(sent.extend)
    imp.state.close()
    return sent, report


# -- parsing ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, minor", [("490", 49000), ("₹1,234.50", 123450), ("Rs. 12.5", 1250), ("-12.00", -1200),
                                         ("(15.00)", -1500), ("0.005", 0), ("0.015", 2), ("INR 7", 700)])
def test_money_is_exact(text, minor):
    assert to_minor(text) == minor


@pytest.mark.parametrize("text", ["", "abc", "1.2.3", "12,50 Dr", "1e3", "₹"])
def test_money_refuses_what_it_cannot_read_exactly(text):
    with pytest.raises(ParseError):
        to_minor(text)


def test_quantities_and_european_separators():
    assert to_milli("1.25") == 1250 and to_milli("2") == 2000 and to_milli("0.0005") == 0
    assert to_minor("1.234,50", decimal_sep=",", thousands_sep=".") == 123450


# -- keys and references ---------------------------------------------------------------------------

def test_the_store_key_is_created_once_and_must_be_private(tmp_path):
    k = tmp_path / "k"
    new_key(k)
    assert stat.S_IMODE(os.stat(k).st_mode) == 0o600 and len(load_key(k)) == 32
    with pytest.raises(KeyError_, match="orphan"):
        new_key(k)
    os.chmod(k, 0o644)
    with pytest.raises(KeyError_, match="chmod 600"):
        load_key(k)


def test_references_are_stable_per_store_and_system_and_unlinkable_across_stores():
    a, b = Refs(KEY, "file"), Refs(bytes(32), "file")
    assert a.bill("INV-1001") == a.bill(" INV-1001 ") and a.bill("INV-1001").startswith("bill-")
    assert a.bill("INV-1001") != b.bill("INV-1001") != Refs(KEY, "tally").bill("INV-1001")
    assert a.bill("INV-1001")[5:] == a.doc("INV-1001")[4:]  # one document, one hash, whatever its kind


# -- profiles ---------------------------------------------------------------------------------------

def test_profiles_are_checked(tmp_path):
    import yaml

    doc = yaml.safe_load(PROFILE.read_text())
    for edit, msg in [(lambda d: d.update(colour="red"), "unknown key"),
                      (lambda d: d["files"]["bills"]["columns"].pop("amount"), "missing"),
                      (lambda d: d["files"]["bills"]["columns"].update(customer="Customer Name"), "unknown"),
                      (lambda d: d["maps"]["tender"].update(bitcoin=["btc"]), "not one of"),
                      (lambda d: d["maps"]["tender"]["card"].append("cash"), "maps to both")]:
        bad = yaml.safe_load(yaml.safe_dump(doc))
        edit(bad)
        p = tmp_path / "p.yaml"
        p.write_text(yaml.safe_dump(bad))
        with pytest.raises(ProfileError, match=msg):
            load(p)


# -- import ------------------------------------------------------------------------------------------

def test_a_bill_and_its_payments_become_valid_events(tmp_path):
    sent, report = run_import(tmp_path, bills=bill_rows(), payments=payment_rows())
    assert report.errors == [] and report.bills_new == 1
    lines = [e for e in sent if e["type"] == "com.pqt.pos.sale.line"]
    pays = [e for e in sent if e["type"] == "com.pqt.pos.payment"]
    assert [ln["data"]["amountMinor"] for ln in lines] == [49000, 5000]
    assert [ln["data"]["qtyMilli"] for ln in lines] == [2000, 1250]
    assert [ln["data"]["unit"] for ln in lines] == ["pcs", "kg"]
    assert lines[0]["data"]["sku"] == "ATTA-5KG" and lines[0]["data"]["tillId"] == "counter-1"
    assert lines[0]["time"] == "2026-01-01T04:45:02.000Z"  # 10:15:02 India time
    assert {p["data"]["tender"] for p in pays} == {"upi", "cash"} and sum(p["data"]["amountMinor"] for p in pays) == 54000
    upi = next(p for p in pays if p["data"]["tender"] == "upi")["data"]
    assert upi["providerRef"].startswith("pay-") and "UPI-77881" not in json.dumps(sent)
    assert all(e["source"] == "urn:pqt:edge:store-001:pos-file" for e in sent)
    assert lines[0]["data"]["billRef"] == pays[0]["data"]["billRef"] == Refs(KEY, "file").bill("INV-1001")


def test_nothing_personal_or_any_real_number_leaves(tmp_path):
    sent, _ = run_import(tmp_path, bills=bill_rows(), payments=payment_rows())
    text = json.dumps(sent)
    for value in [*PII.values(), "INV-1001", "UPI-77881"]:
        assert value not in text


def test_reimporting_the_same_files_adds_nothing(tmp_path):
    first, _ = run_import(tmp_path, bills=bill_rows(), payments=payment_rows())
    again, report = run_import(tmp_path, bills=bill_rows(), payments=payment_rows())
    assert len(first) == 4 and again == []
    assert report.bills_unchanged == 1 and report.already_sent == 2


def test_a_changed_bill_becomes_the_next_revision_after_a_void(tmp_path):
    run_import(tmp_path, bills=bill_rows())
    sent, report = run_import(tmp_path, bills=bill_rows(extra_line=True))
    assert report.bills_revised == 1
    void = next(e for e in sent if e["type"] == "com.pqt.pos.till.event")["data"]
    assert void["kind"] == "void" and void["revision"] == 0 and void["amountMinor"] == 54000
    lines = [e["data"] for e in sent if e["type"] == "com.pqt.pos.sale.line"]
    assert {ln["revision"] for ln in lines} == {1} and len(lines) == 3


def test_a_bad_row_holds_back_its_whole_bill_and_is_reported(tmp_path):
    rows = bill_rows() + bill_rows(no="INV-1002")
    rows[3]["Amount"] = "fifty"
    sent, report = run_import(tmp_path, bills=rows)
    refs = {e["data"]["billRef"] for e in sent}
    assert refs == {Refs(KEY, "file").bill("INV-1001")}
    assert any(e["row"] == 5 and "amount" in e["error"] for e in report.errors)
    assert any("held back" in e["error"] for e in report.errors)
    assert "INV-1002" not in json.dumps(report.as_dict())  # reports name rows, not bill numbers


def test_unknown_tender_is_reported_not_guessed(tmp_path):
    rows = payment_rows()
    rows[1]["Mode"] = "Barter"
    sent, report = run_import(tmp_path, payments=rows)
    assert len(sent) == 1 and "unknown tender" in report.errors[0]["error"]


def test_refunds_payment_numbers_and_signs(tmp_path):
    run_import(tmp_path, payments=payment_rows())
    refund = [{**payment_rows()[0], "Time": "18:00:00", "Type": "Refund", "Amount": "50.00", "Txn Ref": "UPI-99"}]
    sent, _ = run_import(tmp_path, payments=payment_rows() + refund)
    out = [e["data"] for e in sent if e["data"]["direction"] == "out"]
    assert len(out) == 1 and out[0]["amountMinor"] == 5000 and out[0]["paymentNo"] == 3
    assert len(sent) == 1  # the two earlier payments were already sent; they keep numbers 1 and 2


def test_movements_stock_and_day_totals(tmp_path):
    movements = [{"Doc No": "PB-778", "Date": "01-01-2026", "Time": "08:05:00", "Item Code": "MILK-1L", "Qty": "48",
                  "Unit": "nos", "Direction": "Inward", "Reason": "Purchase", "Line": "1"}]
    stock = [{"Item Code": "MILK-1L", "Qty": "-2", "Unit": "nos", "Date": "01-01-2026", "Time": "08:10:00"}]
    day = [{"Date": "01-01-2026", "Sales": "45,123.00", "Refunds": "490.00", "Bills": "212", "Cash": "12,030.00",
            "UPI": "29,503.00", "Card": "3,100.00", "Wallet": "", "Credit": ""}]
    sent, report = run_import(tmp_path, movements=movements, stock=stock, day=day)
    assert report.errors == []
    mv = next(e for e in sent if e["type"] == "com.pqt.stock.movement")["data"]
    assert (mv["direction"], mv["reason"], mv["qtyMilli"]) == ("in", "purchase_receipt", 48000)
    assert mv["docRef"].startswith("doc-") and "PB-778" not in json.dumps(sent)
    lvl = next(e for e in sent if e["type"] == "com.pqt.stock.level")["data"]
    assert lvl["qtyMilli"] == -2000 and lvl["source"] == "file"
    dt = next(e for e in sent if e["type"] == "com.pqt.pos.day.total")
    assert dt["data"]["paymentsByTender"] == {"cash": 1203000, "upi": 2950300, "card": 310000}
    assert dt["data"]["salesMinor"] == 4512300 and dt["subject"] == "store:store-001"


def test_a_file_missing_a_mapped_column_is_reported(tmp_path):
    rows = [{k: v for k, v in r.items() if k != "Amount"} for r in bill_rows()]
    sent, report = run_import(tmp_path, bills=rows)
    assert sent == [] and "missing column" in report.errors[0]["error"]


# -- command line and the edge outbox ---------------------------------------------------------------

def test_cli_imports_into_the_edge_outbox_once(tmp_path):
    pytest.importorskip("pqt_edge.outbox")
    from pqt_edge.outbox import Outbox

    new_key(tmp_path / "key")
    write_csv(tmp_path / "bills.csv", bill_rows())
    write_csv(tmp_path / "pay.csv", payment_rows())
    args = ["import", "--profile", str(PROFILE), "--store", "store-001", "--key", str(tmp_path / "key"),
            "--state", str(tmp_path / "state.db"), "--outbox", str(tmp_path / "outbox.db"), "--schemas", str(SCHEMAS),
            "--bills", str(tmp_path / "bills.csv"), "--payments", str(tmp_path / "pay.csv"),
            "--report", str(tmp_path / "report.json")]
    assert cli.main(args) == 0
    box = Outbox(tmp_path / "outbox.db")
    assert box.stats()["pending"] == 4
    box.close()
    assert cli.main(args) == 0  # the same files again
    box = Outbox(tmp_path / "outbox.db")
    assert box.stats()["pending"] == 4
    box.close()
    assert json.loads((tmp_path / "report.json").read_text())["events"] == 0


def test_cli_reports_bad_rows_with_exit_code_2(tmp_path):
    new_key(tmp_path / "key")
    rows = bill_rows()
    rows[0]["Qty"] = "two"
    write_csv(tmp_path / "bills.csv", rows)
    rc = cli.main(["import", "--profile", str(PROFILE), "--store", "store-001", "--key", str(tmp_path / "key"),
                   "--state", str(tmp_path / "s.db"), "--out", str(tmp_path / "e.jsonl"), "--bills", str(tmp_path / "bills.csv")])
    assert rc == 2
