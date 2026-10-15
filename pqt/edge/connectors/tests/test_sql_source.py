"""X1 milestone B5c: the database source (ADR-037). Invented data only; SQLite stands in for the shop's database,
PostgreSQL is exercised when PQT_TEST_PG_DSN points at a throw-away database."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml

from pqt_connectors import sqlsource
from pqt_connectors.importer import Importer, State
from pqt_connectors.profile import load
from pqt_connectors.refs import Refs

from test_file_connector import KEY, PII, SCHEMAS

HERE = Path(__file__).resolve().parent

PROFILE = {
    "apiVersion": "connector.pqt/v1", "name": "shop-db", "system": "shop-db", "timezone": "Asia/Kolkata",
    "timestampFormat": "%Y-%m-%d %H:%M:%S", "currency": "INR",
    "files": {
        "bills": {"columns": {"billNo": "bill_no", "timestamp": "billed_at", "till": "counter", "lineNo": "line_no",
                              "sku": "item_code", "qty": "qty", "unit": "uom", "amount": "net"}},
        "payments": {"columns": {"billNo": "bill_no", "timestamp": "paid_at", "tender": "mode", "amount": "amount"}},
    },
    "maps": {"tender": {"cash": [], "upi": ["gpay", "phonepe"], "card": []}, "unit": {"pcs": ["nos"], "kg": []}},
}

BILLS_SQL = """
    SELECT b.bill_no, b.billed_at, b.counter, l.line_no, l.item_code, l.qty, l.uom, l.net, b.modified_at,
           b.customer_name, b.customer_phone            -- selected by mistake: never read (not mapped)
      FROM bills b JOIN bill_lines l ON l.bill_no = b.bill_no
     WHERE b.modified_at > :cursor
     ORDER BY b.modified_at, b.bill_no, l.line_no
"""
PAYMENTS_SQL = "SELECT bill_no, paid_at, mode, amount, id FROM payments WHERE id > :cursor ORDER BY id"


def make_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE bills (bill_no TEXT PRIMARY KEY, billed_at TEXT, counter TEXT, modified_at TEXT,
                            customer_name TEXT, customer_phone TEXT);
        CREATE TABLE bill_lines (bill_no TEXT, line_no INTEGER, item_code TEXT, qty TEXT, uom TEXT, net TEXT);
        CREATE TABLE payments (id INTEGER PRIMARY KEY, bill_no TEXT, paid_at TEXT, mode TEXT, amount TEXT);
    """)
    return db


def add_bill(db, no, at, modified, lines, counter="C1"):
    db.execute("INSERT OR REPLACE INTO bills VALUES (?, ?, ?, ?, ?, ?)",
               (no, at, counter, modified, PII["Customer Name"], PII["Phone"]))
    db.execute("DELETE FROM bill_lines WHERE bill_no = ?", (no,))
    for n, (sku, qty, net) in enumerate(lines, 1):
        db.execute("INSERT INTO bill_lines VALUES (?, ?, ?, ?, 'Nos', ?)", (no, n, sku, qty, net))
    db.commit()


@pytest.fixture
def shop(tmp_path):
    db_path = tmp_path / "pos.db"
    db = make_db(db_path)
    add_bill(db, "B-1", "2026-01-01 10:00:00", "2026-01-01 10:00:05", [("ATTA", "2", "490.00"), ("DAL", "1", "120.00")])
    add_bill(db, "B-2", "2026-01-01 10:05:00", "2026-01-01 10:05:03", [("MILK", "1", "33.00")])
    db.execute("INSERT INTO payments (bill_no, paid_at, mode, amount) VALUES ('B-1', '2026-01-01 10:00:30', 'GPay', '610.00')")
    db.commit()
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
    cfg = {"id": "shop-db", "type": "sql", "profile": "profile.yaml", "driver": "sqlite", "dsn": str(db_path),
           "queries": {
               "bills": {"sql": BILLS_SQL, "cursor": "modified_at", "start": "2026-01-01 00:00:00", "lookback": "PT10M"},
               "payments": {"sql": PAYMENTS_SQL, "cursor": "id", "start": 0},
           }}
    return tmp_path, db, cfg


def poll(tmp, cfg, sent):
    src = sqlsource.SqlSource.from_config(cfg, base=tmp)
    prof = src.profile
    state = State(tmp / "state.db")
    try:
        imp = Importer(prof, store="store-001", refs=Refs(KEY, prof.system), state=state, schemas=SCHEMAS)
        return src.poll(imp, sent.extend)
    finally:
        state.close()


def test_the_first_poll_reads_from_start_and_the_next_finds_nothing_new(shop):
    tmp, _db, cfg = shop
    sent: list[dict] = []
    r = poll(tmp, cfg, sent)
    assert r["records"] == {"bills": 3, "payments": 1} and r["errors"] == []
    assert sum(e["type"] == "com.pqt.pos.sale.line" for e in sent) == 3
    again: list[dict] = []
    r2 = poll(tmp, cfg, again)
    assert again == [], "lookback re-reads B-2 but an unchanged bill sends nothing"
    assert r2["records"]["payments"] == 0


def test_new_and_edited_bills_after_a_restart_have_no_gap_and_no_duplicate(shop):
    tmp, db, cfg = shop
    poll(tmp, cfg, [])
    add_bill(db, "B-3", "2026-01-01 11:00:00", "2026-01-01 11:00:02", [("SALT", "1", "20.00")])
    add_bill(db, "B-1", "2026-01-01 10:00:00", "2026-01-01 11:01:00", [("ATTA", "2", "490.00")])   # DAL removed
    sent: list[dict] = []
    poll(tmp, cfg, sent)  # a fresh SqlSource: the cursor comes from the state database
    lines = [e for e in sent if e["type"] == "com.pqt.pos.sale.line"]
    voids = [e for e in sent if e["type"] == "com.pqt.pos.till.event"]
    assert len(voids) == 1 and voids[0]["data"]["revision"] == 0
    assert sorted((e["data"]["revision"], e["data"]["sku"]) for e in lines) == [(0, "SALT"), (1, "ATTA")]


def test_a_late_commit_inside_the_lookback_is_still_read(shop):
    tmp, db, cfg = shop
    poll(tmp, cfg, [])
    # Committed after the last poll but stamped 2 minutes before its cursor (a long transaction).
    add_bill(db, "B-4", "2026-01-01 10:03:00", "2026-01-01 10:03:00", [("OIL", "1", "180.00")])
    sent: list[dict] = []
    poll(tmp, cfg, sent)
    assert {e["data"]["sku"] for e in sent if e["type"] == "com.pqt.pos.sale.line"} == {"OIL"}


def test_personal_columns_in_the_query_never_leave(shop):
    tmp, _db, cfg = shop
    sent: list[dict] = []
    r = poll(tmp, cfg, sent)
    text = json.dumps(sent) + json.dumps(r)
    for v in (PII["Customer Name"], PII["Phone"], "B-1", "B-2"):
        assert v not in text


def test_a_page_never_splits_rows_that_share_a_cursor_value(shop):
    tmp, db, cfg = shop
    for n in range(3):
        add_bill(db, f"P-{n}", f"2026-01-02 09:0{n}:00", f"2026-01-02 09:0{n}:00", [("A", "1", "1.00"), ("B", "1", "1.00")])
    cfg["queries"]["bills"].update(maxRows=5, start="2026-01-02 00:00:00", lookback="PT0S")
    del cfg["queries"]["payments"]
    seen: list[list[str]] = []
    for _ in range(4):
        sent: list[dict] = []
        poll(tmp, cfg, sent)
        seen.append(sorted({e["data"]["billRef"] for e in sent if e["type"] == "com.pqt.pos.sale.line"}))
    assert [len(s) for s in seen] == [2, 1, 0, 0], "5 rows max: two whole bills, then the third, then nothing"


def test_a_bad_row_holds_the_cursor_until_it_is_fixed(shop):
    tmp, db, cfg = shop
    add_bill(db, "B-5", "2026-01-01 12:00:00", "2026-01-01 12:00:00", [("RICE", "lots", "70.00")])   # unreadable qty
    add_bill(db, "B-6", "2026-01-01 12:10:00", "2026-01-01 12:10:00", [("SUGAR", "1", "45.00")])
    cfg["queries"]["bills"]["lookback"] = "PT0S"
    sent: list[dict] = []
    r = poll(tmp, cfg, sent)
    assert any("qty" in e["error"] for e in r["errors"])
    skus = {e["data"]["sku"] for e in sent if e["type"] == "com.pqt.pos.sale.line"}
    assert "RICE" not in skus and "SUGAR" in skus, "the bills after the bad one are sent"
    assert r["cursors"]["bills"] == {"str": "2026-01-01 10:05:03"}, "but the cursor stops before the bad bill"
    again: list[dict] = []
    assert poll(tmp, cfg, again)["errors"], "still reported on the next poll, never skipped"
    assert again == [], "B-6 is not sent twice"
    add_bill(db, "B-5", "2026-01-01 12:00:00", "2026-01-01 12:00:00", [("RICE", "1", "70.00")])      # fixed in place
    fixed: list[dict] = []
    r3 = poll(tmp, cfg, fixed)
    assert r3["errors"] == [] and {e["data"]["sku"] for e in fixed if e["type"] == "com.pqt.pos.sale.line"} == {"RICE"}
    assert r3["cursors"]["bills"] == {"str": "2026-01-01 12:10:00"}


@pytest.mark.parametrize("sql", [
    "UPDATE bills SET counter = 'x' WHERE modified_at > :cursor",
    "DELETE FROM bills WHERE modified_at > :cursor",
    "SELECT * FROM bills WHERE modified_at > :cursor; DROP TABLE bills",
    "SELECT * INTO copy FROM bills WHERE modified_at > :cursor",
    "WITH x AS (DELETE FROM bills RETURNING *) SELECT * FROM x WHERE modified_at > :cursor",
    "SELECT * FROM bills",                                 # no cursor parameter: would re-read everything forever
    "CALL refresh(:cursor)",
])
def test_only_a_single_select_with_the_cursor_is_accepted(shop, sql):
    tmp, _db, cfg = shop
    cfg["queries"]["bills"]["sql"] = sql
    with pytest.raises(ValueError):
        sqlsource.SqlSource.from_config(cfg, base=tmp)


def test_keywords_inside_strings_and_comments_do_not_trip_the_check(shop):
    tmp, _db, cfg = shop
    cfg["queries"]["bills"]["sql"] = BILLS_SQL.replace("WHERE", "-- delete from x;\n WHERE counter <> 'DROP; UPDATE' AND")
    sqlsource.SqlSource.from_config(cfg, base=tmp)


def test_the_sqlite_connection_cannot_write(shop):
    tmp, _db, cfg = shop
    src = sqlsource.SqlSource.from_config(cfg, base=tmp)
    conn = src.connect()
    try:
        with pytest.raises(Exception):
            conn.execute("DELETE FROM bills")
    finally:
        conn.close()


@pytest.mark.parametrize("style, expected", [
    ("qmark", ("SELECT a FROM t WHERE m > ? AND note <> ':cursor'", ("V",))),
    ("named", ("SELECT a FROM t WHERE m > :cursor AND note <> ':cursor'", {"cursor": "V"})),
    ("pyformat", ("SELECT a FROM t WHERE m > %(cursor)s AND note <> ':cursor'", {"cursor": "V"})),
    ("format", ("SELECT a FROM t WHERE m > %s AND note <> ':cursor'", ("V",))),
])
def test_the_cursor_parameter_is_bound_in_each_drivers_style(style, expected):
    assert sqlsource.bind("SELECT a FROM t WHERE m > :cursor AND note <> ':cursor'", "V", style) == expected


def test_the_dsn_is_a_secret_reference_for_server_databases(shop, monkeypatch):
    tmp, _db, cfg = shop
    cfg.update(driver="postgresql", dsn="postgresql://pos:hunter2@db/pos")
    with pytest.raises(ValueError, match="env:"):
        sqlsource.SqlSource.from_config(cfg, base=tmp)


@pytest.mark.skipif(not os.environ.get("PQT_TEST_PG_DSN"), reason="set PQT_TEST_PG_DSN to a throw-away PostgreSQL database")
def test_postgresql_end_to_end_read_only(tmp_path, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ["PQT_TEST_PG_DSN"]
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS pqt_b5c_payments")
        c.execute("CREATE TABLE pqt_b5c_payments (id serial PRIMARY KEY, bill_no text, paid_at timestamptz, mode text,"
                  " amount numeric(12,2), customer_phone text)")
        c.execute("INSERT INTO pqt_b5c_payments (bill_no, paid_at, mode, amount, customer_phone) VALUES "
                  "('PG-1', '2026-01-01 10:00:30+05:30', 'cash', 610.50, %s), ('PG-2', '2026-01-01 10:02:00+05:30', 'gpay', 33, %s)",
                  (PII["Phone"], PII["Phone"]))
    monkeypatch.setenv("PQT_TEST_PG_DSN_REF", dsn)
    prof = dict(PROFILE, files={"payments": PROFILE["files"]["payments"]})
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(prof), encoding="utf-8")
    cfg = {"id": "pg", "type": "sql", "profile": "profile.yaml", "driver": "postgresql", "dsn": "env:PQT_TEST_PG_DSN_REF",
           "queries": {"payments": {"sql": "SELECT * FROM pqt_b5c_payments WHERE id > :cursor ORDER BY id",
                                    "cursor": "id", "start": 0}}}
    sent: list[dict] = []
    r = poll(tmp_path, cfg, sent)
    assert r["errors"] == [] and [e["data"]["amountMinor"] for e in sent] == [61050, 3300]
    assert PII["Phone"] not in json.dumps(sent)
    src = sqlsource.SqlSource.from_config(cfg, base=tmp_path)
    conn = src.connect()
    try:
        with pytest.raises(Exception):
            conn.cursor().execute("DELETE FROM pqt_b5c_payments")
    finally:
        conn.close()
    again: list[dict] = []
    assert poll(tmp_path, cfg, again)["records"] == {"payments": 0} and again == []
