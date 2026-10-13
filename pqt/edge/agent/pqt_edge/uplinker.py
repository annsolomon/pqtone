"""Drains the outbox to event-core (A3). Runs forever; a cut cable just means a longer back-off."""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable

from pqt_vision.uplink import Uplink, UplinkRefused, UplinkUnavailable

from .outbox import Outbox

log = logging.getLogger("pqt_edge.uplinker")


class Uplinker:
    def __init__(self, outbox: Outbox, uplink: Uplink, *, batch_size: int = 200, idle_s: float = 0.5,
                 backoff_max_s: float = 60.0, sleep: Callable[[float], None] = time.sleep) -> None:
        self.outbox, self.uplink = outbox, uplink
        self.batch_size, self.max_batch = batch_size, batch_size
        self.idle_s, self.backoff_max_s = idle_s, backoff_max_s
        self.sleep = sleep
        self.backoff = 1.0
        self.metrics = {"batches": 0, "sent": 0, "rejected": 0, "transientFailures": 0, "refused": 0,
                        "unexpectedErrors": 0}

    def step(self) -> str:
        """One batch. Returns idle | sent | retry | split | rejected."""
        rows = self.outbox.next_batch(self.batch_size)
        if not rows:
            return "idle"
        try:
            results = self.uplink.send_batch([json.loads(r.body) for r in rows])
        except UplinkUnavailable as e:
            self.outbox.failed(rows, str(e))
            self.metrics["transientFailures"] += 1
            return "retry"
        except UplinkRefused as e:
            self.metrics["refused"] += 1
            if len(rows) > 1:  # e.g. 413: halve the batch until it fits, or until one event is isolated
                self.batch_size = max(1, len(rows) // 2)
                return "split"
            self.outbox.mark(rows, [{"id": rows[0].event_id, "status": "invalid", "errors": [str(e)]}])
            self.metrics["rejected"] += 1
            return "rejected"
        counts = self.outbox.mark(rows, results)
        self.metrics["batches"] += 1
        self.metrics["sent"] += counts["sent"]
        self.metrics["rejected"] += counts["rejected"]
        self.batch_size = min(self.max_batch, self.batch_size * 2)
        return "sent"

    def run(self, stop: threading.Event) -> None:
        """Forever, until `stop`. Nothing may end this loop early: an error nobody planned for is logged,
        counted and retried with back-off, because a dead uplink thread means a silent backlog (A4)."""
        while not stop.is_set():
            try:
                outcome = self.step()
            except Exception:  # noqa: BLE001 - deliberately broad: see the docstring
                log.exception("uplink step failed unexpectedly; retrying with back-off")
                self.metrics["unexpectedErrors"] += 1
                outcome = "retry"
            if outcome == "retry":
                self.sleep(self.backoff * (0.5 + random.random() / 2))
                self.backoff = min(self.backoff_max_s, self.backoff * 2)
            elif outcome == "idle":
                self.backoff = 1.0
                self.sleep(self.idle_s)
            else:
                self.backoff = 1.0
