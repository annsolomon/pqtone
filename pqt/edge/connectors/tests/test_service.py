"""X1 milestone B5f: the connector service runs every configured source on the edge box (ADR-037). Invented data."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
import yaml

from pqt_connectors import service

from test_file_connector import KEY, PII, PROFILE, SCHEMAS, bill_rows, write_csv
from test_push import BILL, call
from test_sql_source import PROFILE as DB_PROFILE, make_db, add_bill

HERE = Path(__file__).resolve().parent
PROFILES = HERE.parent / "profiles"


@pytest.fixture
def box(tmp_path):
    """A shop: a drop folder, a POS database, a push client; and the sources file that wires them."""
    drop = tmp_path / "drop"
    drop.mkdir()
    db = make_db(tmp_path / "pos.db")
    add_bill(db, "D-1", "2026-01-01 10:00:00", "2026-01-01 10:00:05", [("ATTA", "2", "490.00")])
    (tmp_path / "shop-db.yaml").write_text(yaml.safe_dump(DB_PROFILE), encoding="utf-8")
    from pqt_connectors.push import add_client

    token = add_client(tmp_path / "push-clients.yaml", client_id="shop-pos", profile=str(PROFILES / "canonical.yaml"))
    cfg = {"apiVersion": "connector.pqt/v1", "sources": [
        {"id": "drop", "type": "watch", "dir": "drop", "profile": str(PROFILE), "interval": 1, "minAge": 0},
        {"id": "shop-db", "type": "sql", "profile": "shop-db.yaml", "driver": "sqlite", "dsn": "pos.db", "interval": 1,
         "queries": {"bills": {"sql": "SELECT b.bill_no, b.billed_at, b.counter, l.line_no, l.item_code, l.qty, l.uom, "
                                      "l.net, b.modified_at FROM bills b JOIN bill_lines l ON l.bill_no = b.bill_no "
                                      "WHERE b.modified_at > :cursor ORDER BY b.modified_at",
                               "cursor": "modified_at", "start": "2026-01-01 00:00:00"}}},
        {"id": "push", "type": "push", "clients": "push-clients.yaml", "bind": "127.0.0.1", "port": 0},
    ]}
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path, db, token


def make(tmp: Path, sent: list, **kw) -> service.Service:
    lock = threading.Lock()

    def sink(events):
        with lock:
            sent.extend(events)
    return service.Service.from_file(tmp / "sources.yaml", store="store-001", key=KEY, state_path=tmp / "state.db",
                                     sink=sink, schemas=SCHEMAS, status_path=tmp / "status.json", **kw)


def test_every_source_runs_and_reports_its_health(box):
    tmp, db, token = box
    write_csv(tmp / "drop" / "bills.csv", bill_rows())
    sent: list[dict] = []
    svc = make(tmp, sent)
    stop = threading.Event()
    t = threading.Thread(target=svc.run, args=(stop,), daemon=True)
    t.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not svc.push_url():
            time.sleep(0.05)
        st, _, body = call(svc.push_url(), "/v1/records", {"bills": BILL}, token=token)
        assert st == 200 and body["events"] == 4
        while time.time() < deadline:
            status = json.loads((tmp / "status.json").read_text(encoding="utf-8")) if (tmp / "status.json").exists() else {}
            by = {s["id"]: s for s in status.get("sources", [])}
            if all(by.get(i, {}).get("events", 0) > 0 for i in ("drop", "shop-db", "push")):
                break
            time.sleep(0.1)
    finally:
        stop.set()
        t.join(timeout=10)
    assert not t.is_alive(), "the service stops when asked"
    by = {s["id"]: s for s in json.loads((tmp / "status.json").read_text(encoding="utf-8"))["sources"]}
    assert set(by) == {"drop", "shop-db", "push"}
    for s in by.values():
        assert s["ok"] and s["failures"] == 0 and s["lastSuccessAt"] and s["events"] > 0, s
    sources = {e["source"] for e in sent}
    assert sources == {"urn:pqt:edge:store-001:pos-file", "urn:pqt:edge:store-001:pos-shop-db",
                       "urn:pqt:edge:store-001:pos-custom"}
    assert len({e["id"] for e in sent}) == len(sent), "one state, one writer at a time: nothing twice"


def test_a_failing_source_backs_off_and_says_why_without_the_details(tmp_path, monkeypatch):
    (tmp_path / "p.yaml").write_text(yaml.safe_dump(
        {"apiVersion": "connector.pqt/v1", "name": "x", "system": "x", "timestampFormat": "iso8601",
         "files": {"bills": {"columns": {"billNo": "n", "timestamp": "t", "sku": "s", "qty": "q", "amount": "a"}}}}),
        encoding="utf-8")
    cfg = {"apiVersion": "connector.pqt/v1", "sources": [
        {"id": "down-api", "type": "http", "profile": "p.yaml", "baseUrl": "http://127.0.0.1:9", "interval": 10,
         "auth": {"type": "bearer", "token": "env:PQT_TEST_DOWN_TOKEN"},
         "endpoints": {"bills": {"path": "/x", "records": "data"}}}]}
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("PQT_TEST_DOWN_TOKEN", "tok-not-to-be-logged")
    sent: list[dict] = []
    svc = service.Service.from_file(tmp_path / "sources.yaml", store="store-001", key=KEY, state_path=tmp_path / "s.db",
                                    sink=sent.extend, schemas=SCHEMAS, status_path=tmp_path / "status.json",
                                    sleep=lambda s: None)
    job = svc.jobs[0]
    delays = [svc.cycle(job) for _ in range(4)]
    assert delays == [20, 40, 80, 160], "interval x 2^failures"
    for _ in range(4):
        svc.cycle(job)
    assert svc.cycle(job) == 900, "capped at 15 minutes"
    h = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))["sources"][0]
    assert h["ok"] is False and h["failures"] == 9 and h["lastSuccessAt"] is None and h["stale"] is True
    assert "unreachable" in h["lastError"] and "tok-not-to-be-logged" not in json.dumps(h)


def test_row_errors_are_counted_but_do_not_make_a_source_unhealthy(box):
    tmp, db, _ = box
    add_bill(db, "D-2", "2026-01-01 11:00:00", "2026-01-01 11:00:05", [("RICE", "lots", "70.00")])
    svc = make(tmp, [])
    job = next(j for j in svc.jobs if j.id == "shop-db")
    assert svc.cycle(job) == 1
    h = {s["id"]: s for s in json.loads((tmp / "status.json").read_text(encoding="utf-8"))["sources"]}["shop-db"]
    assert h["ok"] is True and h["rowErrors"] >= 1, "the data needs fixing, the connection is fine"


@pytest.mark.parametrize("change, message", [
    ({"type": "carrier-pigeon"}, "type"),
    ({"id": "drop"}, "twice"),
    ({"interval": 0}, "interval"),
])
def test_sources_files_are_checked_when_loaded(box, change, message):
    tmp, _db, _ = box
    doc = yaml.safe_load((tmp / "sources.yaml").read_text(encoding="utf-8"))
    extra = dict(doc["sources"][1], id="another")
    extra.update(change)
    doc["sources"].append(extra)
    (tmp / "sources.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        make(tmp, [])


def test_the_status_file_is_replaced_whole(box):
    tmp, _db, _ = box
    svc = make(tmp, [])
    job = next(j for j in svc.jobs if j.id == "drop")
    svc.cycle(job)
    doc = json.loads((tmp / "status.json").read_text(encoding="utf-8"))
    assert doc["store"] == "store-001" and doc["updatedAt"].endswith("Z") and len(doc["sources"]) == 3
    assert oct((tmp / "status.json").stat().st_mode & 0o777) == "0o600"
    assert not list(tmp.glob(".status.json.*")), "no temporary file left behind"
