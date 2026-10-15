"""Every state transition of the outbox and the uplinker (A3 'Done when'). Synthetic events only."""
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from pqt_edge.outbox import Outbox, OutboxFull
from pqt_edge.uplinker import Uplinker
from pqt_vision.uplink import UplinkRefused, UplinkUnavailable


def ev(i: int, source: str = "urn:pqt:edge:store-001:cam-1", pad: int = 0) -> dict:
    return {"specversion": "1.0", "id": f"id-{source[-5:]}-{i}", "source": source, "type": "com.pqt.store.clock.tick",
            "time": f"2026-10-12T09:00:{i % 60:02d}.000Z", "subject": "store", "storeid": "store-001",
            "partitionkey": "store-001", "data": {"seq": i, "pad": "x" * pad}}


@pytest.fixture
def box(tmp_path: Path) -> Outbox:
    return Outbox(tmp_path / "outbox.db", quota_bytes=10_000_000)


def test_sequences_are_gap_free_per_source_and_survive_a_restart(tmp_path: Path):
    ob = Outbox(tmp_path / "o.db")
    assert ob.append([ev(1), ev(2)]) == [1, 2]
    assert ob.append([ev(3, "urn:pqt:edge:store-001:cam-2")]) == [1]
    ob.close()
    ob = Outbox(tmp_path / "o.db")
    assert ob.append([ev(4)]) == [3]
    bodies = [json.loads(r.body) for r in ob.next_batch()]
    assert [(b["source"][-5:], b["sequence"]) for b in bodies] == [("cam-1", "1"), ("cam-1", "2"), ("cam-2", "1"), ("cam-1", "3")]


def test_the_same_event_cannot_be_stored_twice(box: Outbox):
    box.append([ev(1)])
    with pytest.raises(Exception):
        box.append([ev(1)])
    assert box.stats()["pending"] == 1  # the failed transaction left nothing behind
    assert box.append([ev(2)]) == [2]   # and did not consume a sequence number


def test_acknowledgements_move_rows_to_sent_or_rejected(box: Outbox):
    box.append([ev(i) for i in range(1, 7)])
    rows = box.next_batch()
    results = [{"id": rows[0].event_id, "status": "accepted"}, {"id": rows[1].event_id, "status": "duplicate"},
               {"id": rows[2].event_id, "status": "invalid", "errors": ["bad"]}, {"id": rows[3].event_id, "status": "forbidden"},
               {"id": "someone-else", "status": "accepted"}]  # misaligned; and the 6th has no result at all
    assert box.mark(rows, results) == {"sent": 2, "rejected": 2, "pending": 2}
    st = box.stats()
    assert (st["sent"], st["rejected"], st["pending"]) == (2, 2, 2)
    assert [r.seq for r in box.next_batch()] == [5, 6]
    err = box.db.execute("SELECT last_error FROM outbox WHERE seq = 3").fetchone()[0]
    assert json.loads(err) == {"status": "invalid", "errors": ["bad"]}


def test_transient_failures_keep_rows_pending_and_count_attempts(box: Outbox):
    box.append([ev(1), ev(2)])
    rows = box.next_batch()
    box.failed(rows, "connection refused")
    box.failed(rows, "connection refused")
    assert box.db.execute("SELECT min(attempts), max(attempts) FROM outbox").fetchone() == (2, 2)
    assert len(box.next_batch()) == 2


def test_eviction_deletes_only_the_oldest_sent_rows(tmp_path: Path):
    ob = Outbox(tmp_path / "o.db", quota_bytes=300_000)
    ob.append([ev(i, pad=900) for i in range(1, 201)])  # ~200 kB
    rows = ob.next_batch(150)
    ob.mark(rows, [{"id": r.event_id, "status": "accepted"} for r in rows])
    ob.append([ev(i, pad=900) for i in range(201, 301)])  # pushes past the quota → evicts sent rows
    st = ob.stats()
    assert st["pending"] == 150 and st["sent"] < 150
    gone = ob.db.execute("SELECT min(seq) FROM outbox WHERE state = 'sent'").fetchone()[0]
    assert gone is None or gone > 1  # oldest first
    assert ob.db.execute("SELECT count(*) FROM outbox WHERE state = 'pending' AND seq > 150").fetchone()[0] == 150


def test_a_full_disk_of_pending_events_stops_production_instead_of_dropping(tmp_path: Path):
    ob = Outbox(tmp_path / "o.db", quota_bytes=120_000)
    ob.append([ev(i, pad=900) for i in range(1, 101)])
    with pytest.raises(OutboxFull):
        ob.append([ev(i, pad=900) for i in range(101, 201)])
    assert ob.stats()["pending"] == 100  # nothing dropped, nothing half-written
    rows = ob.next_batch(100)
    ob.mark(rows, [{"id": r.event_id, "status": "accepted"} for r in rows])
    assert len(ob.append([ev(i, pad=900) for i in range(101, 151)])) == 50  # space is back once sent


def test_a_process_killed_mid_transaction_loses_only_the_uncommitted_part(tmp_path: Path):
    db = tmp_path / "o.db"
    child = f"""
import os, sys
sys.path[:0] = {sys.path!r}
from pqt_edge.outbox import Outbox
ob = Outbox({str(db)!r})
ob.append([{{"id": f"a{{i}}", "source": "urn:pqt:edge:s:c", "time": "2026-10-12T09:00:00.000Z"}} for i in range(5)])
ob.db.execute("BEGIN IMMEDIATE")
ob.db.execute("INSERT INTO outbox (source, seq, event_id, event_time, body, state, created_ms) "
              "VALUES ('urn:pqt:edge:s:c', 6, 'x', 't', '{{}}', 'pending', 0)")
os._exit(9)  # like kill -9: no commit, no close
"""
    assert subprocess.run([sys.executable, "-c", child]).returncode == 9
    ob = Outbox(db)
    assert [r.seq for r in ob.next_batch()] == [1, 2, 3, 4, 5]
    assert ob.append([{"id": "b", "source": "urn:pqt:edge:s:c", "time": "2026-10-12T09:00:01.000Z"}]) == [6]


class FakeUplink:
    def __init__(self, behaviour):
        self.behaviour, self.batches = behaviour, []

    def send_batch(self, events):
        self.batches.append(len(events))
        b = self.behaviour.pop(0) if self.behaviour else "ok"
        if b == "down":
            raise UplinkUnavailable("network unreachable")
        if b == "413":
            raise UplinkRefused(413, "too large")
        return [{"id": e["id"], "status": "accepted"} for e in events]


def test_uplinker_sends_retries_splits_and_isolates(box: Outbox):
    box.append([ev(i) for i in range(1, 5)])
    up = Uplinker(box, FakeUplink(["down", "413", "413", "413"]), batch_size=4, sleep=lambda s: None)
    assert up.step() == "retry" and box.stats()["pending"] == 4
    assert up.step() == "split" and up.batch_size == 2
    assert up.step() == "split" and up.batch_size == 1
    assert up.step() == "rejected" and box.stats()["rejected"] == 1  # the one event the server keeps refusing
    while up.step() == "sent":
        pass
    st = box.stats()
    assert (st["sent"], st["rejected"], st["pending"]) == (3, 1, 0)
    assert up.batch_size == 4  # grows back after successes


def test_uplinker_run_loop_backs_off_while_down_and_drains_when_back(box: Outbox):
    box.append([ev(i) for i in range(1, 11)])
    sleeps = []
    stop = threading.Event()

    def sleep(s):
        sleeps.append(s)
        if box.stats()["pending"] == 0:
            stop.set()

    up = Uplinker(box, FakeUplink(["down"] * 5), batch_size=4, sleep=sleep)
    up.run(stop)
    assert box.stats()["sent"] == 10
    assert sleeps[:5] == sorted(sleeps[:5]) and max(sleeps[:5]) > min(sleeps[:5])  # exponential back-off
    assert up.metrics["transientFailures"] == 5


def test_an_unexpected_error_never_stops_the_uplink_thread(box: Outbox):
    """A4 cable test finding: an exception the uplinker did not expect killed its thread for good. Now it
    is counted, logged and retried with back-off like any other failure."""
    box.append([ev(i) for i in range(1, 4)])

    class Flaky(FakeUplink):
        def __init__(self):
            super().__init__([])
            self.boom = 2

        def send_batch(self, events):
            if self.boom:
                self.boom -= 1
                raise OSError("something nobody planned for")
            return super().send_batch(events)

    stop = threading.Event()

    def sleep(s):
        if box.stats()["pending"] == 0:
            stop.set()

    up = Uplinker(box, Flaky(), batch_size=4, sleep=sleep)
    up.run(stop)
    assert box.stats()["sent"] == 3 and up.metrics["unexpectedErrors"] == 2


def test_draining_at_stop_sends_what_is_pending_while_the_core_answers(box: Outbox):
    """A4 cable test finding: events made in the last moments before a stop stayed pending. A clean stop now
    sends them, bounded in time, and gives up at once when the link is down."""
    box.append([ev(i) for i in range(1, 10)])
    up = Uplinker(box, FakeUplink([]), batch_size=4, sleep=lambda s: None)
    d = up.drain(5.0)
    assert d == {"sent": 9, "outcome": "idle", "pending": 0}

    box.append([ev(i) for i in range(10, 13)])
    down = Uplinker(box, FakeUplink(["down"] * 10), batch_size=4, sleep=lambda s: None)
    d = down.drain(5.0)
    assert d["outcome"] == "retry" and d["pending"] == 3 and down.metrics["transientFailures"] == 1, "one try, then stop"

    t = iter([0.0, 0.0, 99.0])
    slow = Uplinker(box, FakeUplink([]), batch_size=1, sleep=lambda s: None)
    d = slow.drain(5.0, clock=lambda: next(t))
    assert d["sent"] == 1 and d["pending"] == 2, "the deadline ends it"
