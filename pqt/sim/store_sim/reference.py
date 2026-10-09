"""Reference implementation of the rule specification, used to derive ground truth.

This is deliberately an independent implementation of the same specification that
rules-engine implements in Java (services/rules-engine/.../domain/RuleEngine.java).
It runs over the *clean*, in-order event stream; rules-engine runs over the faulty
stream the pipeline actually delivers. The scorer measures the difference.

Semantics shared by both implementations:
  * events are processed in (time, sequence) order;
  * before an event at time t, every timer with deadline < t fires, earliest first
    (ties: QUEUE_RESOLVE, QUEUE_OPEN, ABSENCE_OPEN, DWELL_OPEN, DWELL_EXPIRE, then key);
  * at the end of input, timers with deadline <= horizon fire, where
    horizon = last event time - grace (the final watermark rules-engine reaches).
"""
from __future__ import annotations

from .config import RuleSet

Q_RESOLVE, Q_OPEN, A_OPEN, D_OPEN, D_EXPIRE = range(5)


class RefEngine:
    def __init__(self, rules: RuleSet):
        self.r = rules
        self.q_on = rules.modes["R-QUEUE-001"] != "off"
        self.d_on = rules.modes["R-DWELL-001"] != "off"
        self.a_on = rules.modes["R-ABS-001"] != "off"
        self.queues: dict[str, dict] = {}
        self.dwell: dict[str, dict] = {}
        self.abs = {"pending": None, "trigger": None, "open": False, "onset": None}
        self.out: list[dict] = []
        self.store_id = None
        self.run_id = None

    # ------------------------------------------------------------------ output
    def _emit(self, rule: str, kind: str, key: str, onset: int, detected: int, evidence: list[str]):
        self.out.append({
            "ruleId": rule, "ruleVersion": self.r.versions[rule], "mode": self.r.modes[rule],
            "kind": kind, "storeId": self.store_id, "simRunId": self.run_id, "key": key,
            "onsetMs": onset, "detectedMs": detected, "evidence": evidence,
        })

    # ------------------------------------------------------------------ timers
    def _timers(self):
        r = self.r
        for qid, q in self.queues.items():
            if q["open"] and q["clear"] is not None:
                yield (q["clear"] + r.queue_clear_ms, Q_RESOLVE, qid)
            if not q["open"] and q["breach"] is not None:
                yield (q["breach"] + r.queue_sustain_ms, Q_OPEN, qid)
        if self.abs["pending"] is not None:
            yield (self.abs["pending"], A_OPEN, self.abs["trigger"])
        for k, s in self.dwell.items():
            if not s["open"]:
                yield (s["entry"] + r.dwell_limit_ms, D_OPEN, k)
            yield (s["entry"] + r.dwell_ttl_ms, D_EXPIRE, k)

    def _fire_until(self, t: int, inclusive: bool) -> None:
        while True:
            due = [x for x in self._timers() if x[0] < t or (inclusive and x[0] <= t)]
            if not due:
                return
            self._fire(*min(due))

    def _fire(self, d: int, kind: int, key: str) -> None:
        r = self.r
        if kind == Q_OPEN:
            q = self.queues[key]
            q["open"], q["clear"], q["onset"] = True, None, q["breach"]
            self._emit("R-QUEUE-001", "OPENED", key, q["breach"], d, [q["breachEv"]])
            if self.a_on and self.abs["pending"] is None and not self.abs["open"]:
                self.abs.update(pending=d + r.abs_within_ms, trigger=key)
        elif kind == Q_RESOLVE:
            q = self.queues[key]
            self._emit("R-QUEUE-001", "RESOLVED", key, q["onset"], d, [])
            q.update(open=False, breach=None, clear=None, onset=None)
            if self.abs["trigger"] == key:
                if self.abs["open"]:
                    self._emit("R-ABS-001", "RESOLVED", key, self.abs["onset"], d, [])
                self.abs = {"pending": None, "trigger": None, "open": False, "onset": None}
        elif kind == A_OPEN:
            self.abs.update(pending=None, open=True, onset=d - r.abs_within_ms)
            self._emit("R-ABS-001", "OPENED", key, d - r.abs_within_ms, d, [])
        elif kind == D_OPEN:
            s = self.dwell[key]
            s["open"] = True
            self._emit("R-DWELL-001", "OPENED", key, s["entry"], d, [s["entryEv"]])
        elif kind == D_EXPIRE:
            s = self.dwell.pop(key)
            if s["open"]:
                self._emit("R-DWELL-001", "RESOLVED", key, s["entry"], d, [])

    # ------------------------------------------------------------------ events
    def process(self, ev: dict) -> None:
        t = ev["t"]
        self.store_id = ev["storeId"]
        self.run_id = ev["simRunId"]
        self._fire_until(t, inclusive=False)
        typ, data = ev["type"], ev["data"]
        if typ == "com.pqt.store.queue.length" and self.q_on:
            q = self.queues.setdefault(data["queueId"], {"breach": None, "breachEv": None, "open": False,
                                                          "clear": None, "onset": None})
            length = data["length"]
            if not q["open"]:
                if length >= self.r.queue_threshold:
                    if q["breach"] is None:
                        q["breach"], q["breachEv"] = t, ev["id"]
                else:
                    q["breach"], q["breachEv"] = None, None
            else:
                if length <= self.r.queue_threshold - self.r.queue_hysteresis:
                    if q["clear"] is None:
                        q["clear"] = t
                else:
                    q["clear"] = None
        elif typ == self.r.abs_expect and self.a_on:
            if self.abs["open"]:
                self._emit("R-ABS-001", "RESOLVED", self.abs["trigger"], self.abs["onset"], t, [ev["id"]])
            self.abs = {"pending": None, "trigger": None, "open": False, "onset": None}
        elif typ == "com.pqt.store.zone.entered" and self.d_on and data["zoneId"] in self.r.dwell_zones:
            key = f"{data['trackId']}|{data['zoneId']}"
            old = self.dwell.get(key)
            if old and old["open"]:
                self._emit("R-DWELL-001", "RESOLVED", key, old["entry"], t, [ev["id"]])
            self.dwell[key] = {"entry": t, "entryEv": ev["id"], "open": False}
        elif typ == "com.pqt.store.zone.exited" and self.d_on:
            key = f"{data['trackId']}|{data['zoneId']}"
            s = self.dwell.pop(key, None)
            if s and s["open"]:
                self._emit("R-DWELL-001", "RESOLVED", key, s["entry"], t, [ev["id"]])

    def finish(self, horizon_ms: int) -> None:
        self._fire_until(horizon_ms, inclusive=True)


class FootfallRef:
    """Reference for R-FOOT-001, the windowed footfall-spike rule.

    rules-engine implements it with Kafka Streams windows (FootfallTopology): tumbling windows
    of `window` aligned to the epoch, counting zone.entered per zone; a window is final once
    stream time reaches window end + grace (suppress untilWindowCloses); final windows are
    evaluated in order per (store, run) by FootfallSpikeDetector:

      * baseline = mean of the previous `history` windows of the run (0 where a zone had no entry);
      * spike    = a full history exists, count >= minCount and count * H > factor * sum(history);
      * a spike opens an incident (onset = window start, detected = window end) unless one is
        already open for the zone; the first non-spike window resolves it.

    This runs over the clean, in-order stream, so every window is complete.
    """

    def __init__(self, rules: RuleSet):
        self.r = rules
        self.counts: dict[int, dict[str, int]] = {}
        self.windows: set[int] = set()
        self.last_t: int | None = None
        self.store_id = None
        self.run_id = None
        self.out: list[dict] = []

    def process(self, ev: dict) -> None:
        if not self.r.foot_on:
            return
        t = ev["t"]
        self.store_id, self.run_id = ev["storeId"], ev["simRunId"]
        start = t - t % self.r.foot_window_ms
        self.windows.add(start)
        self.last_t = t if self.last_t is None else max(self.last_t, t)
        if ev["type"] == "com.pqt.store.zone.entered" and ev["data"]["zoneId"] in self.r.foot_zones:
            zone_counts = self.counts.setdefault(start, {})
            zone_counts[ev["data"]["zoneId"]] = zone_counts.get(ev["data"]["zoneId"], 0) + 1

    def finish(self) -> None:
        """Evaluate every window that closed by the end of input (end + grace <= last event time)."""
        if self.last_t is None:
            return
        r, size, h = self.r, self.r.foot_window_ms, self.r.foot_history_windows
        history: dict[str, list[int]] = {z: [] for z in r.foot_zones}
        open_onset: dict[str, int] = {}
        for start in sorted(self.windows):
            end = start + size
            if end + r.grace_ms > self.last_t:
                break
            counts = self.counts.get(start, {})
            for zone in r.foot_zones:
                c = counts.get(zone, 0)
                past = history[zone]
                spike = len(past) == h and c >= r.foot_min_count and c * h > r.foot_factor * sum(past)
                if spike and zone not in open_onset:
                    open_onset[zone] = start
                    self._emit("OPENED", zone, start, end, c, sum(past) / h)
                elif not spike and zone in open_onset:
                    self._emit("RESOLVED", zone, open_onset.pop(zone), end, c, sum(past) / h if past else 0.0)
                past.append(c)
                if len(past) > h:
                    past.pop(0)

    def _emit(self, kind: str, zone: str, onset: int, detected: int, count: int, mean: float) -> None:
        self.out.append({
            "ruleId": "R-FOOT-001", "ruleVersion": self.r.versions["R-FOOT-001"], "mode": self.r.modes["R-FOOT-001"],
            "kind": kind, "storeId": self.store_id, "simRunId": self.run_id, "key": zone,
            "onsetMs": onset, "detectedMs": detected, "evidence": [], "count": count, "baseline": mean,
        })
