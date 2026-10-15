"""X1 milestone B5a: canonical records from any source (ADR-037). Invented data only.

The same trading morning, written the way different software would write it (the generic CSV export, an
Excel workbook, a custom POS's nested JSON, NDJSON lines, a canonical-records document), must become the
same events; whatever personal data those sources carry must leave nothing behind.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from pqt_connectors.importer import Importer, State
from pqt_connectors.profile import ProfileError, load
from pqt_connectors.refs import Refs

from test_file_connector import KEY, PII, PROFILE, SCHEMAS, bill_rows, payment_rows, write_csv

HERE = Path(__file__).resolve().parent
PROFILES = HERE.parent / "profiles"
RECORDS_SCHEMA = HERE.parent / "schemas" / "records.schema.json"
PII_VALUES = list(PII.values())

INVOICE = {
    "invoice_number": "INV-1001", "created_at": "2026-01-01T10:15:02+05:30", "counter": "Counter 1",
    "customer": {"name": PII["Customer Name"], "phone": PII["Phone"], "gstin": PII["GSTIN"]}, "remarks": PII["Notes"],
    "items": [
        {"line": 1, "code": "ATTA 5KG", "quantity": 2, "uom": "Nos", "net": 490.00, "gst": 23.33},
        {"line": 2, "code": "ONION-LOOSE", "quantity": 1.25, "uom": "Kgs", "net": 50, "gst": 0},
    ],
}
PAYMENTS = [
    {"invoice_number": "INV-1001", "paid_at": "2026-01-01T10:15:40+05:30", "counter": "Counter 1", "mode": "PhonePe",
     "amount": "500.00", "kind": "Payment", "ref": "UPI-77881", "payer": PII["Customer Name"]},
    {"invoice_number": "INV-1001", "paid_at": "2026-01-01T10:15:41+05:30", "counter": "Counter 1", "mode": "Cash",
     "amount": 40.00, "kind": "Payment", "ref": "", "payer": PII["Customer Name"]},
]


def generic() -> dict:
    return yaml.safe_load(PROFILE.read_text(encoding="utf-8"))


def write_profile(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def custom_json_profile() -> dict:
    """A custom POS that keeps invoices with nested items, ISO timestamps and JSON numbers."""
    doc = generic()
    doc.update(name="custom-pos-json", timestampFormat="iso8601")
    doc["files"] = {
        "bills": {"path": "invoices", "each": "items",
                  "columns": {"billNo": "$.invoice_number", "timestamp": "$.created_at", "till": "$.counter",
                              "lineNo": "line", "sku": "code", "qty": "quantity", "unit": "uom", "amount": "net",
                              "tax": "gst"}},
        "payments": {"path": "payments",
                     "columns": {"billNo": "invoice_number", "timestamp": "paid_at", "till": "counter", "tender": "mode",
                                 "amount": "amount", "direction": "kind", "reference": "ref"}},
    }
    return doc


def importer(tmp: Path, profile_path: Path, state: str = "state.db") -> Importer:
    prof = load(profile_path)
    return Importer(prof, store="store-001", refs=Refs(KEY, prof.system), state=State(tmp / state), schemas=SCHEMAS)


def events_of(imp: Importer) -> list[dict]:
    out: list[dict] = []
    rep = imp.commit(out.extend)
    assert rep.errors == [], rep.errors
    return out


def shape(events: list[dict]) -> list[tuple]:
    return sorted((e["id"], e["type"], e["time"], json.dumps(e["data"], sort_keys=True)) for e in events)


@pytest.fixture
def csv_events(tmp_path):
    imp = importer(tmp_path, PROFILE, "csv.db")
    imp.bills(write_csv(tmp_path / "bills.csv", bill_rows()))
    imp.payments(write_csv(tmp_path / "payments.csv", payment_rows()))
    return events_of(imp)


def test_nested_json_from_custom_software_gives_the_same_events_as_the_csv_export(tmp_path, csv_events):
    doc = {"invoices": [INVOICE], "payments": PAYMENTS}
    src = tmp_path / "export.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    imp = importer(tmp_path, write_profile(tmp_path / "custom.yaml", custom_json_profile()))
    imp.bills(src)
    imp.payments(src)
    assert shape(events_of(imp)) == shape(csv_events)


def test_ndjson_lines_give_the_same_events(tmp_path, csv_events):
    prof = custom_json_profile()
    prof["files"]["bills"].pop("path")
    prof["files"]["payments"].pop("path")
    bills = tmp_path / "bills.ndjson"
    bills.write_text(json.dumps(INVOICE) + "\n", encoding="utf-8")
    pays = tmp_path / "payments.jsonl"
    pays.write_text("".join(json.dumps(p) + "\n" for p in PAYMENTS), encoding="utf-8")
    imp = importer(tmp_path, write_profile(tmp_path / "nd.yaml", prof))
    imp.bills(bills)
    imp.payments(pays)
    assert shape(events_of(imp)) == shape(csv_events)


def test_an_excel_workbook_with_typed_cells_gives_the_same_events(tmp_path, csv_events):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Bills"
    rows = bill_rows()
    ws.append(list(rows[0]))
    for r in rows:
        typed = dict(r)
        typed["Date"] = datetime(2026, 1, 1)          # Excel date cell
        typed["Time"] = time(10, 15, 2)               # Excel time cell
        typed["Qty"] = float(r["Qty"]) if "." in r["Qty"] else int(r["Qty"])  # 1.25 is a float cell, 2 an int
        typed["Amount"] = float(r["Amount"].replace("₹", ""))  # 490.0, 50.0: numbers as Excel stores them
        typed["Tax"] = float(r["Tax"])
        typed["Line"] = int(r["Line"])
        ws.append(list(typed.values()))
    pay = wb.create_sheet("Payments")
    prow = payment_rows()
    pay.append(list(prow[0]))
    for r in prow:
        pay.append([*(r[k] for k in list(prow[0])[:5]), float(r["Amount"]), *(r[k] for k in list(prow[0])[6:])])
    book = tmp_path / "day.xlsx"
    wb.save(book)
    prof = generic()
    prof["files"]["bills"]["sheet"] = "Bills"
    prof["files"]["payments"]["sheet"] = "Payments"
    imp = importer(tmp_path, write_profile(tmp_path / "xlsx.yaml", prof))
    imp.bills(book)
    imp.payments(book)
    assert shape(events_of(imp)) == shape(csv_events)


def test_a_canonical_records_document_needs_no_mapping_and_validates_against_the_published_schema(tmp_path, csv_events):
    doc = {
        "bills": [
            {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:02+05:30", "till": "Counter 1", "lineNo": 1,
             "sku": "ATTA 5KG", "qty": 2, "unit": "pcs", "amount": "490.00", "tax": 23.33},
            {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:02+05:30", "till": "Counter 1", "lineNo": 2,
             "sku": "ONION-LOOSE", "qty": 1.25, "unit": "kg", "amount": 50, "tax": 0},
        ],
        "payments": [
            {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:40+05:30", "till": "Counter 1", "tender": "upi",
             "amount": "500.00", "direction": "in", "reference": "UPI-77881"},
            {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:41+05:30", "till": "Counter 1", "tender": "cash",
             "amount": 40, "direction": "in"},
        ],
    }
    schema = json.loads(RECORDS_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(doc)) == []
    bad = copy.deepcopy(doc)
    bad["payments"][0]["tender"] = "bitcoin"
    bad["bills"][0]["customerName"] = "x"
    assert len(list(Draft202012Validator(schema).iter_errors(bad))) == 2, "unknown tender and unknown field refused"

    src = tmp_path / "records.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    prof = yaml.safe_load((PROFILES / "canonical.yaml").read_text(encoding="utf-8"))
    prof["system"] = "file"   # ids include the system: compare with the CSV import's
    imp = importer(tmp_path, write_profile(tmp_path / "canonical.yaml", prof))
    imp.bills(src)
    imp.payments(src)
    assert shape(events_of(imp)) == shape(csv_events)


def test_personal_data_in_any_format_leaves_nothing(tmp_path):
    doc = {"invoices": [INVOICE], "payments": PAYMENTS}
    src = tmp_path / "export.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    imp = importer(tmp_path, write_profile(tmp_path / "custom.yaml", custom_json_profile()))
    imp.bills(src)
    imp.payments(src)
    out = json.dumps(events_of(imp)) + json.dumps(imp.report.as_dict())
    for v in PII_VALUES + ["INV-1001", "UPI-77881"]:
        assert v not in out


def test_epoch_timestamps_and_money_in_minor_units(tmp_path, csv_events):
    prof = custom_json_profile()
    prof.update(timestampFormat="epoch_ms", moneyUnit="minor")
    inv = copy.deepcopy(INVOICE)
    inv["created_at"] = int(datetime.fromisoformat("2026-01-01T10:15:02+05:30").timestamp() * 1000)
    inv["items"][0].update(net=49000, gst=2333)
    inv["items"][1].update(net=5000, gst=0)
    pays = copy.deepcopy(PAYMENTS)
    for p, amount in zip(pays, (50000, 4000)):
        p["paid_at"] = int(datetime.fromisoformat(p["paid_at"]).timestamp() * 1000)
        p["amount"] = amount
    src = tmp_path / "e.json"
    src.write_text(json.dumps({"invoices": [inv], "payments": pays}), encoding="utf-8")
    imp = importer(tmp_path, write_profile(tmp_path / "e.yaml", prof))
    imp.bills(src)
    imp.payments(src)
    assert shape(events_of(imp)) == shape(csv_events)


def test_minor_units_refuse_a_fraction_and_a_missing_field_holds_the_bill_back(tmp_path):
    prof = custom_json_profile()
    prof["moneyUnit"] = "minor"
    inv = copy.deepcopy(INVOICE)
    inv["items"][0]["net"] = 490.5           # half a paisa is not an amount in minor units
    other = copy.deepcopy(INVOICE)
    other["invoice_number"] = "INV-1002"
    del other["items"][1]["code"]            # no item code on the second line
    src = tmp_path / "x.json"
    src.write_text(json.dumps({"invoices": [inv, other], "payments": []}), encoding="utf-8")
    imp = importer(tmp_path, write_profile(tmp_path / "x.yaml", prof))
    imp.bills(src)
    out: list[dict] = []
    rep = imp.commit(out.extend)
    assert out == [], "both bills held back whole"
    text = json.dumps(rep.errors)
    assert "x.json" in text and "minor units" in text and "item code" in text
    assert "INV-100" not in text, "reports name records, never bill numbers"


def test_records_not_where_the_profile_says_are_reported_not_ignored(tmp_path):
    src = tmp_path / "x.json"
    src.write_text(json.dumps({"sales": [INVOICE]}), encoding="utf-8")
    imp = importer(tmp_path, write_profile(tmp_path / "x.yaml", custom_json_profile()))
    imp.bills(src)
    out: list[dict] = []
    rep = imp.commit(out.extend)
    assert out == [] and any("invoices" in e["error"] for e in rep.errors)


def test_feeding_records_directly_is_the_same_pipeline(tmp_path, csv_events):
    """Push, database and REST sources (B5b-B5d) hand records straight to the importer."""
    imp = importer(tmp_path, write_profile(tmp_path / "custom.yaml", custom_json_profile()))
    imp.feed("bills", "push", [json.loads(json.dumps(INVOICE), parse_float=Decimal)])
    imp.feed("payments", "push", json.loads(json.dumps(PAYMENTS), parse_float=Decimal))
    assert shape(events_of(imp)) == shape(csv_events)


@pytest.mark.parametrize("change, message", [
    ({"timestampFormat": "sometimes"}, "timestampFormat"),
    ({"moneyUnit": "lakhs"}, "moneyUnit"),
    ({"files": {"bills": {"columns": {"billNo": "a", "sku": "b", "qty": "c", "amount": "d"}}}}, "date"),
    ({"files": {"bills": {"each": "items", "columns": {"billNo": "a", "timestamp": "t", "sku": "b", "qty": "c",
                                                        "amount": "d"}, "sheet": 3}}}, "sheet"),
])
def test_profiles_are_checked_when_loaded(tmp_path, change, message):
    doc = generic()
    doc.update(change)
    with pytest.raises(ProfileError, match=message):
        load(write_profile(tmp_path / "p.yaml", doc))


def test_an_oversized_file_is_refused_whole(tmp_path, monkeypatch):
    from pqt_connectors import records

    monkeypatch.setattr(records, "MAX_FILE_BYTES", 100)
    src = write_csv(tmp_path / "bills.csv", bill_rows())
    imp = importer(tmp_path, PROFILE)
    imp.bills(src)
    out: list[dict] = []
    rep = imp.commit(out.extend)
    assert out == [] and "more than the 100" in rep.errors[0]["error"]


def test_excel_xml_is_parsed_with_defusedxml():
    import openpyxl.xml

    assert openpyxl.xml.DEFUSEDXML, "defusedxml must be installed so workbook XML cannot expand entities"


# -- hardening (security review of B5a/B5b) ------------------------------------------------------------------------

def test_values_that_break_conversions_are_reported_not_raised(tmp_path):
    inv = copy.deepcopy(INVOICE)
    inv["items"][0]["line"] = "first"                    # int() would raise ValueError
    huge = copy.deepcopy(INVOICE)
    huge["invoice_number"] = "INV-1002"
    src = tmp_path / "x.json"
    # 1e1000000 as a JSON number, and a 50-digit monster: neither may crash or balloon memory.
    second = json.dumps(huge).replace('"net": 50', '"net": 1e1000000', 1).replace('"gst": 0', '"gst": ' + "9" * 60, 1)
    assert "1e1000000" in second and "9" * 60 in second
    text = '{"invoices": [' + json.dumps(inv) + ", " + second + '], "payments": []}'
    src.write_text(text, encoding="utf-8")
    imp = importer(tmp_path, write_profile(tmp_path / "x.yaml", custom_json_profile()))
    imp.bills(src)
    out: list[dict] = []
    rep = imp.commit(out.extend)
    assert out == [] and len(rep.errors) >= 2
    assert all(len(e["error"]) < 400 for e in rep.errors)


def test_deeply_nested_json_is_refused_as_a_whole(tmp_path):
    src = tmp_path / "x.json"
    src.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    imp = importer(tmp_path, write_profile(tmp_path / "x.yaml", custom_json_profile()))
    imp.bills(src)
    out: list[dict] = []
    rep = imp.commit(out.extend)
    assert out == [] and rep.errors and "JSON" in rep.errors[0]["error"]


def test_an_excel_file_that_expands_too_far_is_refused(tmp_path, monkeypatch):
    from openpyxl import Workbook

    from pqt_connectors import records

    wb = Workbook()
    ws = wb.active
    ws.append(["Bill No", "Date", "Time", "Counter", "Line", "Item Code", "Qty", "Unit", "Amount", "Tax"])
    for n in range(200):
        ws.append([f"B{n}", "01-01-2026", "10:00:00", "C1", 1, "X" * 200, "1", "Nos", "1.00", "0"])
    book = tmp_path / "big.xlsx"
    wb.save(book)
    monkeypatch.setattr(records, "MAX_XLSX_UNCOMPRESSED", 10_000)
    imp = importer(tmp_path, PROFILE)
    imp.bills(book)
    out: list[dict] = []
    rep = imp.commit(out.extend)
    assert out == [] and "expands" in rep.errors[0]["error"]


def test_a_symbolic_link_is_never_read_as_a_source(tmp_path):
    real = write_csv(tmp_path / "real.csv", bill_rows())
    link = tmp_path / "bills.csv"
    link.symlink_to(real)
    imp = importer(tmp_path, PROFILE)
    imp.bills(link)
    out: list[dict] = []
    rep = imp.commit(out.extend)
    assert out == [] and "symbolic link" in rep.errors[0]["error"]
