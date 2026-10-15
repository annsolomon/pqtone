"""The cable test's POS simulator (X1 B5f) is deterministic and speaks the canonical records."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("chaos_pos", ROOT / "scripts" / "chaos_pos.py")
chaos_pos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chaos_pos)
SCHEMA = json.loads((ROOT / "edge/connectors/pqt_connectors/schemas/records.schema.json").read_text(encoding="utf-8"))


def test_the_same_day_gives_the_same_bills_in_canonical_form():
    a, b = chaos_pos.bills("2026-10-13", 7, "PUSH"), chaos_pos.bills("2026-10-13", 7, "PUSH")
    assert a == b and len(a) == 7 and [len(x) for x in a] == [1, 2, 3, 1, 2, 3, 1]
    doc = {"bills": [line for bill in a for line in bill], "payments": [chaos_pos.payments_for(x) for x in a]}
    assert list(Draft202012Validator(SCHEMA).iter_errors(doc)) == []
    assert chaos_pos.payments_for(a[1])["amount"] == "153.00"  # DAL 120.00 + MILK 33.00


def test_the_pos_database_gets_each_bill_once(tmp_path):
    db = tmp_path / "pos.db"
    assert chaos_pos.main(["db", "--path", str(db), "--day", "2026-10-13", "--count", "4"]) == 0
    assert chaos_pos.main(["db", "--path", str(db), "--day", "2026-10-13", "--count", "4"]) == 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM bills").fetchone()[0] == 4
    assert con.execute("SELECT count(*) FROM bill_lines").fetchone()[0] == 1 + 2 + 3 + 1
