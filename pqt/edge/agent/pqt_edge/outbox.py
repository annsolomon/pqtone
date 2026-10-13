"""The edge outbox (A3): every event is written to SQLite before it is sent, so nothing is lost when the
uplink, the power or the process goes away.

States of a row:

    pending ──(accepted | duplicate)──► sent ──(evicted under disk pressure, oldest first)──► gone
       │
       ├──(transient failure: network, 429, 5xx)──► pending (attempts + 1)
       │
       └──(invalid | forbidden | conflict)──► rejected   (kept for inspection, never resent; raises an alarm)

- WAL mode with synchronous=FULL: a committed row survives a power cut.
- Each event source (one per camera) gets a durable, gap-free `sequence`, assigned in the same
  transaction as the insert. The cable test checks every sequence from first to last arrives exactly once.
- Disk quota: when the database grows past `quota_bytes`, the oldest *sent* rows are deleted. Pending
  rows are never deleted: if pending rows alone exceed the quota, `append` raises `OutboxFull` and the
  agent must stop perception (and alarm) rather than drop events.
"""
from __future__ import annotations

import functools
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SENT_STATUSES = frozenset({"accepted", "duplicate"})
REJECTED_STATUSES = frozenset({"invalid", "forbidden", "conflict"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS seqs (source TEXT PRIMARY KEY, next INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
  rowid INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  seq INTEGER NOT NULL,
  event_id TEXT NOT NULL,
  event_time TEXT NOT NULL,
  body TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending', 'sent', 'rejected')),
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_ms INTEGER NOT NULL,
  sent_ms INTEGER,
  UNIQUE (source, seq),
  UNIQUE (source, event_id)
);
CREATE INDEX IF NOT EXISTS outbox_state ON outbox (state, rowid);
"""


class OutboxFull(RuntimeError):
    """Pending events alone exceed the disk quota: stop producing, raise an alarm, never drop."""


@dataclass(frozen=True)
class Row:
    rowid: int
    source: str
    seq: int
    event_id: str
    body: str


def _locked(fn):
    """One SQLite connection shared by the camera and uplink threads: every operation holds the lock."""
    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Outbox:
    def __init__(self, path: str | Path, *, quota_bytes: int = 512 * 1024 * 1024,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.quota = quota_bytes
        self.clock = clock
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    @_locked
    def close(self) -> None:
        self.db.close()

    # -- size --------------------------------------------------------------------------------------
    @_locked
    def size_bytes(self) -> int:
        pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        free = self.db.execute("PRAGMA freelist_count").fetchone()[0]
        return (pages - free) * self.db.execute("PRAGMA page_size").fetchone()[0]

    @_locked
    def _pending_bytes(self) -> int:
        return int(self.db.execute("SELECT coalesce(sum(length(body)), 0) FROM outbox WHERE state = 'pending'").fetchone()[0])

    @_locked
    def evict(self) -> int:
        """Delete the oldest sent rows until the database is under 90% of the quota. Returns rows deleted."""
        deleted = 0
        target = int(self.quota * 0.9)
        while self.size_bytes() > target:
            cur = self.db.execute("DELETE FROM outbox WHERE rowid IN "
                                  "(SELECT rowid FROM outbox WHERE state = 'sent' ORDER BY rowid LIMIT 500)")
            if cur.rowcount == 0:
                break
            deleted += cur.rowcount
        return deleted

    # -- write -------------------------------------------------------------------------------------
    @_locked
    def append(self, events: list[dict]) -> list[int]:
        """Store events (assigning each source's next sequence) in one transaction; returns sequences."""
        if not events:
            return []
        incoming = sum(len(_canonical(e)) for e in events)
        if self.size_bytes() + incoming > self.quota:
            self.evict()
            if self._pending_bytes() + incoming > self.quota:
                raise OutboxFull(f"pending events would exceed the {self.quota} byte quota")
        now = int(self.clock() * 1000)
        seqs = []
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for e in events:
                src = e["source"]
                row = self.db.execute("SELECT next FROM seqs WHERE source = ?", (src,)).fetchone()
                seq = row[0] if row else 1
                self.db.execute("INSERT INTO seqs (source, next) VALUES (?, ?) "
                                "ON CONFLICT (source) DO UPDATE SET next = excluded.next", (src, seq + 1))
                body = _canonical({**e, "sequence": str(seq)})
                self.db.execute("INSERT INTO outbox (source, seq, event_id, event_time, body, state, created_ms) "
                                "VALUES (?, ?, ?, ?, ?, 'pending', ?)", (src, seq, e["id"], e["time"], body, now))
                seqs.append(seq)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return seqs

    # -- read / acknowledge ------------------------------------------------------------------------
    @_locked
    def next_batch(self, limit: int = 200) -> list[Row]:
        return [Row(*r) for r in self.db.execute(
            "SELECT rowid, source, seq, event_id, body FROM outbox WHERE state = 'pending' ORDER BY rowid LIMIT ?",
            (limit,))]

    @_locked
    def mark(self, rows: list[Row], results: list[dict]) -> dict[str, int]:
        """Apply event-core's per-item results (same order as `rows`). Unknown or missing → stays pending."""
        now = int(self.clock() * 1000)
        counts = {"sent": 0, "rejected": 0, "pending": 0}
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for i, row in enumerate(rows):
                res = results[i] if i < len(results) else {}
                status = res.get("status")
                if res.get("id") not in (None, row.event_id):
                    status = None  # misaligned reply: treat as not acknowledged
                if status in SENT_STATUSES:
                    self.db.execute("UPDATE outbox SET state = 'sent', sent_ms = ?, attempts = attempts + 1 "
                                    "WHERE rowid = ?", (now, row.rowid))
                    counts["sent"] += 1
                elif status in REJECTED_STATUSES:
                    err = json.dumps({"status": status, "errors": res.get("errors")})[:2000]
                    self.db.execute("UPDATE outbox SET state = 'rejected', last_error = ?, attempts = attempts + 1 "
                                    "WHERE rowid = ?", (err, row.rowid))
                    counts["rejected"] += 1
                else:
                    self.failed([row], f"no acknowledgement ({status!r})", _in_tx=True)
                    counts["pending"] += 1
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return counts

    @_locked
    def failed(self, rows: list[Row], error: str, *, _in_tx: bool = False) -> None:
        """A transient failure for these rows: they stay pending, with the attempt counted."""
        sql = "UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE rowid = ? AND state = 'pending'"
        if _in_tx:
            for r in rows:
                self.db.execute(sql, (error[:2000], r.rowid))
            return
        self.db.executemany(sql, [(error[:2000], r.rowid) for r in rows])

    # -- observability -----------------------------------------------------------------------------
    @_locked
    def stats(self) -> dict:
        by_state = dict(self.db.execute("SELECT state, count(*) FROM outbox GROUP BY state").fetchall())
        oldest = self.db.execute("SELECT min(created_ms) FROM outbox WHERE state = 'pending'").fetchone()[0]
        return {"pending": by_state.get("pending", 0), "sent": by_state.get("sent", 0),
                "rejected": by_state.get("rejected", 0), "bytes": self.size_bytes(), "quotaBytes": self.quota,
                "oldestPendingAgeMs": (int(self.clock() * 1000) - oldest) if oldest else 0}
