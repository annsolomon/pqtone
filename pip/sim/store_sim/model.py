"""Discrete-event retail store model.

Determinism contract: the only randomness comes from generators spawned from one
SeedSequence; there are no wall-clock reads; ties in the scheduler are broken by a
monotonically increasing insertion counter.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np

from .config import parse_duration_ms

TICK_MS = 10_000
TRAILING_MS = 120_000
HARD_CAP_MS = 30 * 60_000


@dataclass
class Register:
    id: str
    queue_id: str = ""
    open: bool = False
    closing: bool = False
    busy: str | None = None


@dataclass
class Queue:
    """One checkout line and the registers that serve it (milestone S2: a store may have several)."""
    id: str
    zone_id: str
    waiting: list[str] = field(default_factory=list)
    pending_open: bool = False
    empty_since: int | None = 0


@dataclass
class Record:
    t_ms: int          # offset from epoch, milliseconds
    order: int         # global emission order (causal tie-break)
    short_type: str
    subject: str
    data: dict = field(default_factory=dict)


class StoreModel:
    def __init__(self, layout: dict, scenario: dict, seed: int):
        self.layout = layout
        self.sc = scenario
        self.store_id = layout["storeId"]
        self.duration_ms = parse_duration_ms(scenario["duration"])

        ss = np.random.SeedSequence(seed)
        arr, move, svc, staff = ss.spawn(4)
        self.r_arr = np.random.default_rng(arr)
        self.r_move = np.random.default_rng(move)
        self.r_svc = np.random.default_rng(svc)
        self.r_staff = np.random.default_rng(staff)

        sales = [z for z in layout["zones"] if z.get("kind") == "sales"]
        self.sales_ids = [z["id"] for z in sales]
        w = np.array([z["weight"] for z in sales], dtype=float)
        self.sales_p = w / w.sum()
        self.dwell_median_s = layout["dwellMedianSeconds"]

        shop = scenario.get("shoppers", {})
        self.checkout_prob = float(shop.get("checkoutProbability", 0.85))
        self.fitting_prob = float(shop.get("fittingRoomProbability", 0.35))
        self.fitting_median_s = float(shop.get("fittingRoomMedianSeconds", 240))
        self.fitting_sigma = float(shop.get("fittingRoomSigma", 0.45))
        self.service_median_s = float(shop.get("serviceMedianSeconds", 75))

        staff_cfg = scenario.get("staffing", {})
        self.initial_open = int(staff_cfg.get("initialOpen", 2))
        self.min_open = int(staff_cfg.get("minOpen", 1))
        self.open_threshold = int(staff_cfg.get("openAtQueueLength", 6))
        self.react_min_ms = parse_duration_ms(staff_cfg.get("reactionMin", "PT60S"))
        self.react_max_ms = parse_duration_ms(staff_cfg.get("reactionMax", "PT200S"))
        self.close_after_ms = parse_duration_ms(staff_cfg.get("closeAfterIdle", "PT300S"))
        # How many registers per queue can ever be staffed (staff_shortage). Default: all of them (set below).

        # Each register serves one queue (layout "queueId"; default: the first queue). Staffing
        # parameters apply per queue, so a one-queue layout behaves exactly as before S2.
        self.queues = [Queue(q["id"], q["zoneId"]) for q in layout["queues"]]
        if not self.queues:
            raise ValueError("layout needs at least one queue")
        known = {q.id for q in self.queues}
        self.registers = [Register(r["id"], r.get("queueId", self.queues[0].id)) for r in layout["registers"]]
        for r in self.registers:
            if r.queue_id not in known:
                raise ValueError(f"register {r.id} serves unknown queue {r.queue_id}")
        for q in self.queues:
            for r in self._registers_of(q)[: self.initial_open]:
                r.open = True
        self.max_open = int(staff_cfg.get("maxOpen", max(len(self._registers_of(q)) for q in self.queues)))
        self.zone_ids = {z["id"] for z in layout["zones"]}

        self._heap: list = []
        self._hseq = 0
        self._order = 0
        self.records: list[Record] = []
        self.n_shoppers = 0

    # ---------------------------------------------------------------- scheduler
    def _at(self, t: int, fn, *args) -> None:
        heapq.heappush(self._heap, (t, self._hseq, fn, args))
        self._hseq += 1

    def _emit(self, t: int, short_type: str, subject: str, data: dict) -> None:
        self.records.append(Record(t, self._order, short_type, subject, data))
        self._order += 1

    def run(self) -> list[Record]:
        for t in self._arrival_times():
            self._at(t, self._arrive)
        self._at(0, self._staff_check)
        for q in self.queues:
            self._emit_queue_length(0, q)
        cap = self.duration_ms + HARD_CAP_MS
        last_activity = 0
        while self._heap:
            t, _, fn, args = heapq.heappop(self._heap)
            if t > cap:
                break
            fn(t, *args)
            last_activity = max(last_activity, t)
        self.activity_end_ms = last_activity
        self.end_ms = ((last_activity // TICK_MS) + 1) * TICK_MS + TRAILING_MS
        activity = self.records
        self.records = []
        self._order_ticks_into(activity)
        return self.records

    def _order_ticks_into(self, activity: list[Record]) -> None:
        """Merge heartbeat ticks into the activity stream in time order and renumber."""
        ticks = [Record(t, -1, "clock.tick", "store", {"seq": i})
                 for i, t in enumerate(range(0, self.end_ms + 1, TICK_MS))]
        merged = sorted(activity + ticks, key=lambda r: (r.t_ms, 0 if r.order >= 0 else 1, r.order))
        for i, r in enumerate(merged):
            r.order = i
        self.records = merged

    # ---------------------------------------------------------------- arrivals
    def _rate_per_hour(self, t_ms: int) -> float:
        for seg in self.sc["arrivals"]:
            if parse_duration_ms(seg["from"]) <= t_ms < parse_duration_ms(seg["to"]):
                return float(seg["perHour"])
        return 0.0

    def _arrival_times(self) -> list[int]:
        lam_max = max(float(s["perHour"]) for s in self.sc["arrivals"])
        out, t = [], 0.0
        while True:
            t += float(self.r_arr.exponential(3600.0 / lam_max))
            t_ms = int(t * 1000)
            if t_ms >= self.duration_ms:
                return out
            if float(self.r_arr.random()) < self._rate_per_hour(t_ms) / lam_max:
                out.append(t_ms)

    # ---------------------------------------------------------------- shoppers
    def _lognormal_ms(self, rng, median_s: float, sigma: float) -> int:
        return max(1000, int(float(rng.lognormal(np.log(median_s), sigma)) * 1000))

    def _walk_ms(self) -> int:
        return int(float(self.r_move.uniform(5_000, 20_000)))

    def _arrive(self, t: int) -> None:
        self.n_shoppers += 1
        trk = f"trk-{self.n_shoppers:05d}"
        visits = 1 + min(int(self.r_move.poisson(1.5)), 4)
        plan: list[str] = []
        for _ in range(visits):
            zone = str(self.r_move.choice(self.sales_ids, p=self.sales_p))
            plan.append(zone)
            if zone == "apparel" and "fitting-rooms" in self.zone_ids and float(self.r_move.random()) < self.fitting_prob:
                plan.append("fitting-rooms")
        buys = float(self.r_move.random()) < self.checkout_prob
        self._emit(t, "zone.entered", f"track:{trk}", {"zoneId": "entrance", "trackId": trk})
        self._at(t + int(float(self.r_move.uniform(3_000, 8_000))), self._leave_zone, trk, "entrance", plan, buys)

    def _enter_zone(self, t: int, trk: str, zone: str, plan: list[str], buys: bool) -> None:
        self._emit(t, "zone.entered", f"track:{trk}", {"zoneId": zone, "trackId": trk})
        if zone == "fitting-rooms":
            dwell = self._lognormal_ms(self.r_move, self.fitting_median_s, self.fitting_sigma)
        else:
            dwell = self._lognormal_ms(self.r_move, float(self.dwell_median_s[zone]), 0.5)
        self._at(t + dwell, self._leave_zone, trk, zone, plan, buys)

    def _leave_zone(self, t: int, trk: str, zone: str, plan: list[str], buys: bool) -> None:
        self._emit(t, "zone.exited", f"track:{trk}", {"zoneId": zone, "trackId": trk})
        if plan:
            nxt = plan.pop(0)
            self._at(t + self._walk_ms(), self._enter_zone, trk, nxt, plan, buys)
        elif buys:
            self._at(t + self._walk_ms(), self._join_checkout, trk)

    # ---------------------------------------------------------------- checkout
    def _registers_of(self, q: Queue) -> list[Register]:
        return [r for r in self.registers if r.queue_id == q.id]

    def _queue_of(self, reg: Register) -> Queue:
        return next(q for q in self.queues if q.id == reg.queue_id)

    def _open_count(self, q: Queue) -> int:
        return sum(1 for r in self._registers_of(q) if r.open)

    def _emit_queue_length(self, t: int, q: Queue) -> None:
        self._emit(t, "queue.length", f"queue:{q.id}",
                   {"queueId": q.id, "length": len(q.waiting), "openRegisters": self._open_count(q)})

    def _choose_queue(self) -> Queue:
        """Shoppers join the shortest line with an open register (ties: layout order). No random draw,
        so a one-queue store consumes exactly the same random numbers as before S2."""
        if len(self.queues) == 1:
            return self.queues[0]
        staffed = [q for q in self.queues if self._open_count(q) > 0] or self.queues
        return min(staffed, key=lambda q: (len(q.waiting), self.queues.index(q)))

    def _join_checkout(self, t: int, trk: str) -> None:
        q = self._choose_queue()
        self._emit(t, "zone.entered", f"track:{trk}", {"zoneId": q.zone_id, "trackId": trk})
        q.waiting.append(trk)
        self._emit(t, "queue.joined", f"queue:{q.id}", {"queueId": q.id, "trackId": trk})
        self._emit_queue_length(t, q)
        self._try_serve(t, q)

    def _try_serve(self, t: int, q: Queue) -> None:
        for reg in self._registers_of(q):
            if reg.open and not reg.closing and reg.busy is None and q.waiting:
                trk = q.waiting.pop(0)
                reg.busy = trk
                self._emit(t, "queue.left", f"queue:{q.id}",
                           {"queueId": q.id, "trackId": trk, "served": True})
                self._emit_queue_length(t, q)
                svc = self._lognormal_ms(self.r_svc, self.service_median_s, 0.4)
                self._at(t + svc, self._finish_service, reg.id)

    def _finish_service(self, t: int, reg_id: str) -> None:
        reg = next(r for r in self.registers if r.id == reg_id)
        q = self._queue_of(reg)
        trk = reg.busy
        reg.busy = None
        self._emit(t, "zone.exited", f"track:{trk}", {"zoneId": q.zone_id, "trackId": trk})
        if reg.closing:
            self._close(t, reg)
        self._try_serve(t, q)

    # ---------------------------------------------------------------- staffing
    def _close(self, t: int, reg: Register) -> None:
        reg.open = False
        reg.closing = False
        self._emit(t, "register.closed", f"register:{reg.id}", {"registerId": reg.id})
        self._emit_queue_length(t, self._queue_of(reg))

    def _open_register(self, t: int, queue_id: str) -> None:
        q = next(x for x in self.queues if x.id == queue_id)
        q.pending_open = False
        reg = next((r for r in self._registers_of(q) if not r.open), None)
        if reg is None:
            return
        reg.open = True
        self._emit(t, "register.opened", f"register:{reg.id}", {"registerId": reg.id})
        self._emit_queue_length(t, q)
        self._try_serve(t, q)

    def _staff_check(self, t: int) -> None:
        for q in self.queues:
            self._staff_queue(t, q)
        if t + 30_000 < self.duration_ms:
            self._at(t + 30_000, self._staff_check)

    def _staff_queue(self, t: int, q: Queue) -> None:
        n = len(q.waiting)
        regs = self._registers_of(q)
        if n > 0:
            q.empty_since = None
        elif q.empty_since is None:
            q.empty_since = t
        if (n >= self.open_threshold and not q.pending_open and any(not r.open for r in regs)
                and self._open_count(q) < self.max_open):
            q.pending_open = True
            delay = int(float(self.r_staff.uniform(self.react_min_ms, self.react_max_ms)))
            self._at(t + delay, self._open_register, q.id)
        active = [r for r in regs if r.open and not r.closing]
        if (n == 0 and q.empty_since is not None
                and t - q.empty_since >= self.close_after_ms and len(active) > self.min_open):
            reg = active[-1]
            if reg.busy is None:
                self._close(t, reg)
            else:
                reg.closing = True
            q.empty_since = t
