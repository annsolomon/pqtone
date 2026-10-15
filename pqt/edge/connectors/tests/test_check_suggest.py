"""X1 milestone B5a: `pqt-connectors check` (dry run) and `suggest` (a starting profile). Invented data only."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from pqt_connectors import cli
from pqt_connectors.profile import load

from test_file_connector import PROFILE, SCHEMAS, bill_rows, payment_rows, write_csv


def test_check_dry_runs_a_profile_without_state_key_or_outbox(tmp_path, capsys):
    bills = write_csv(tmp_path / "bills.csv", bill_rows())
    pays = write_csv(tmp_path / "payments.csv", payment_rows())
    before = sorted(p.name for p in tmp_path.iterdir())
    rc = cli.main(["check", "--profile", str(PROFILE), "--store", "store-001", "--schemas", str(SCHEMAS),
                   "--bills", str(bills), "--payments", str(pays), "--show", "2"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["events"] == {"com.pqt.pos.payment": 2, "com.pqt.pos.sale.line": 2, "com.pqt.stock.movement": 2}
    assert len(out["sample"]) == 2 and out["errors"] == []
    assert out["records"]["bills"] == 2 and out["records"]["payments"] == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == before, "no state, key or outbox file was written"


def test_check_reports_every_bad_row_and_exits_2(tmp_path, capsys):
    rows = bill_rows()
    rows[0]["Amount"] = "about 500"
    bills = write_csv(tmp_path / "bills.csv", rows)
    rc = cli.main(["check", "--profile", str(PROFILE), "--store", "store-001", "--bills", str(bills)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert any(e["row"] == 2 and "amount" in e["error"] for e in out["errors"])


def test_suggest_maps_familiar_column_names_and_leaves_personal_data_unmapped(tmp_path, capsys):
    sample = tmp_path / "SalesRegister.csv"
    with open(sample, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Invoice No", "Invoice Date", "Time", "Counter", "Item Code", "Item Name", "Quantity", "UOM",
                    "Net Amount", "GST Amount", "Customer Name", "Mobile"])
        w.writerow(["A-1", "01/01/2026", "10:15", "C1", "ATTA", "Atta 5kg", "2", "Nos", "490.00", "23.33", "x", "y"])
    rc = cli.main(["suggest", "--kind", "bills", "--sample", str(sample), "--system", "my-pos"])
    text = capsys.readouterr().out
    assert rc == 0
    doc = yaml.safe_load(text)
    cols = doc["files"]["bills"]["columns"]
    assert cols == {"billNo": "Invoice No", "date": "Invoice Date", "time": "Time", "till": "Counter",
                    "sku": "Item Code", "qty": "Quantity", "unit": "UOM", "amount": "Net Amount", "tax": "GST Amount"}
    assert doc["system"] == "my-pos"
    assert "Customer Name" in text and "Mobile" in text, "listed as not mapped, so the reader sees what is left out"
    assert "Customer Name" not in cols.values() and "Mobile" not in cols.values()


def test_suggest_on_nested_json_proposes_each_and_parent_paths(tmp_path, capsys):
    sample = tmp_path / "invoices.json"
    sample.write_text(json.dumps({"invoices": [{"invoice_number": "A-1", "created_at": "2026-01-01T10:15:02+05:30",
                                                "customer": {"name": "x"},
                                                "items": [{"item_code": "ATTA", "qty": 2, "amount": 490}]}]}),
                      encoding="utf-8")
    rc = cli.main(["suggest", "--kind", "bills", "--sample", str(sample)])
    doc = yaml.safe_load(capsys.readouterr().out)
    spec = doc["files"]["bills"]
    assert rc == 0
    assert spec["path"] == "invoices" and spec["each"] == "items"
    assert spec["columns"]["billNo"] == "$.invoice_number" and spec["columns"]["timestamp"] == "$.created_at"
    assert spec["columns"]["sku"] == "item_code" and spec["columns"]["amount"] == "amount"


def test_a_suggested_profile_is_complete_enough_to_load_after_review(tmp_path, capsys):
    sample = write_csv(tmp_path / "bills.csv", bill_rows())
    assert cli.main(["suggest", "--kind", "bills", "--sample", str(sample)]) == 0
    p = tmp_path / "p.yaml"
    p.write_text(capsys.readouterr().out, encoding="utf-8")
    prof = load(p)
    assert set(prof.columns["bills"]) >= {"billNo", "date", "time", "sku", "qty", "amount"}
