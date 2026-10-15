"""X1 milestone B5a: the watched drop folder. Invented data only."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from pqt_connectors import watch
from pqt_connectors.importer import Importer, State
from pqt_connectors.profile import load
from pqt_connectors.refs import Refs

from test_file_connector import KEY, PROFILE, SCHEMAS, bill_rows, payment_rows, write_csv

NOW = 1_800_000_000.0


def aged(path: Path, seconds: float = 60) -> Path:
    os.utime(path, (NOW - seconds, NOW - seconds))
    return path


def factory(tmp: Path):
    prof = load(PROFILE)

    def make() -> Importer:
        return Importer(prof, store="store-001", refs=Refs(KEY, prof.system), state=State(tmp / "state.db"), schemas=SCHEMAS)
    return prof, make


def run(drop: Path, tmp: Path, sent: list) -> dict:
    prof, make = factory(tmp)
    return watch.run_once(drop, prof, make, sent.extend, min_age_s=10, now=lambda: NOW)


def test_dropped_files_are_imported_once_and_moved_to_done(tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    aged(write_csv(drop / "bills-2026-01-01.csv", bill_rows()))
    aged(write_csv(drop / "payments-2026-01-01.csv", payment_rows()))
    sent: list[dict] = []
    summary = run(drop, tmp_path, sent)
    assert summary["imported"] == ["bills-2026-01-01.csv", "payments-2026-01-01.csv"] and summary["failed"] == []
    assert len(sent) == 2 + 2 + 2, "two lines, two stock movements, two payments"
    assert sorted(p.name for p in (drop / "done").rglob("*.csv")) == ["bills-2026-01-01.csv", "payments-2026-01-01.csv"]
    assert not list(drop.glob("*.csv"))
    again: list[dict] = []
    assert run(drop, tmp_path, again)["imported"] == [] and again == []


def test_a_file_with_a_bad_row_goes_to_failed_with_its_report_and_the_rest_is_sent(tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    good = bill_rows("INV-2001")
    bad = bill_rows("INV-2002")
    bad[1]["Qty"] = "lots"
    aged(write_csv(drop / "bills.csv", good + bad))
    sent: list[dict] = []
    summary = run(drop, tmp_path, sent)
    assert summary["failed"] == ["bills.csv"]
    assert {e["data"]["billRef"] for e in sent if e["type"] == "com.pqt.pos.sale.line"}.__len__() == 1
    report = json.loads((drop / "failed" / "bills.csv.report.json").read_text(encoding="utf-8"))
    assert report["errors"] and all(e["file"] == "bills.csv" for e in report["errors"])
    assert (drop / "failed" / "bills.csv").exists()
    # The shop fixes the row and drops the file again: only the held-back bill is new.
    bad[1]["Qty"] = "1"
    aged(write_csv(drop / "bills.csv", good + bad))
    later: list[dict] = []
    assert run(drop, tmp_path, later)["imported"] == ["bills.csv"]
    assert {e["data"]["billRef"] for e in later if e["type"] == "com.pqt.pos.sale.line"}.__len__() == 1


def test_files_still_being_written_or_temporary_or_unknown_are_left_alone(tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    write_csv(drop / "bills.csv", bill_rows())                       # modified just now: may still be written
    os.utime(drop / "bills.csv", (NOW - 2, NOW - 2))
    aged(write_csv(drop / "payments.csv.part", payment_rows()))      # a temporary name
    aged(write_csv(drop / ".payments.csv", payment_rows()))          # hidden
    aged(write_csv(drop / "notes.csv", payment_rows()))              # matches no kind
    sent: list[dict] = []
    summary = run(drop, tmp_path, sent)
    assert summary["imported"] == [] and sent == []
    assert summary["unknown"] == ["notes.csv"]
    assert sorted(p.name for p in drop.iterdir() if p.is_file()) == [".payments.csv", "bills.csv", "notes.csv",
                                                                     "payments.csv.part"]


def test_profile_patterns_choose_which_files_are_which(tmp_path):
    import yaml

    doc = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    doc["files"]["bills"]["match"] = ["SalesRegister*.csv"]
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    prof = load(p)
    drop = tmp_path / "drop"
    drop.mkdir()
    aged(write_csv(drop / "SalesRegister_Jan.csv", bill_rows()))
    aged(write_csv(drop / "bills.csv", bill_rows("INV-9")))
    sent: list[dict] = []

    def make() -> Importer:
        return Importer(prof, store="store-001", refs=Refs(KEY, prof.system), state=State(tmp_path / "s.db"), schemas=SCHEMAS)
    summary = watch.run_once(drop, prof, make, sent.extend, min_age_s=10, now=lambda: NOW)
    assert summary["imported"] == ["SalesRegister_Jan.csv"] and summary["unknown"] == ["bills.csv"]


def test_a_sink_failure_leaves_the_files_for_the_next_poll(tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    aged(write_csv(drop / "bills.csv", bill_rows()))
    prof, make = factory(tmp_path)

    def broken(_events):
        raise OSError("outbox full")
    try:
        watch.run_once(drop, prof, make, broken, min_age_s=10, now=lambda: NOW)
    except OSError:
        pass
    assert (drop / "bills.csv").exists() and not (drop / "done").exists()
    sent: list[dict] = []
    assert run(drop, tmp_path, sent)["imported"] == ["bills.csv"] and len(sent) == 4


def test_now_defaults_to_the_wall_clock():
    assert abs(watch._wall() - time.time()) < 5


def test_symbolic_links_are_never_followed(tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    secret = aged(write_csv(tmp_path / "elsewhere.csv", bill_rows()))
    (drop / "bills.csv").symlink_to(secret)
    sent: list[dict] = []
    summary = run(drop, tmp_path, sent)
    assert summary["imported"] == [] and sent == [] and (drop / "bills.csv").is_symlink()
