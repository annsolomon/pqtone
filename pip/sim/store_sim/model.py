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
    open: bool = False
    closing: bool = False
    busy: str | None = None


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
        self.queue_id = layout["queues"][0]["id"]
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
        # How many registers can ever be staffed (staff_shortage). Default: all of them.
        self.max_open = int(staff_cfg.get("maxOpen", len(layout["registers"])))

        self.registers = [Register(r["id"]) for r in layout["registers"]]
        for r in self.registers[: self.initial_open]:
            r.open = True

        self._heap: list = []
        self._hseq = 0
        self._order = 0
        self.records: list[Record] = []
        self.queue: list[str] = []
        self.pending_open = False
        self.queue_empty_since: int | None = 0
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
        self._emit_queue_length(0)
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
            if zone == "apparel" and float(self.r_move.random()) < self.fitting_prob:
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
    def _open_count(self) -> int:
        return sum(1 for r in self.registers if r.open)

    def _emit_queue_length(self, t: int) -> None:
        self._emit(t, "queue.length", f"queue:{self.queue_id}",
                   {"queueId": self.queue_id, "length": len(self.queue), "openRegisters": self._open_count()})

    def _join_checkout(self, t: int, trk: str) -> None:
        self._emit(t, "zone.entered", f"track:{trk}", {"zoneId": "checkout", "trackId": trk})
        self.queue.append(trk)
        self._emit(t, "queue.joined", f"queue:{self.queue_id}", {"queueId": self.queue_id, "trackId": trk})
        self._emit_queue_length(t)
        self._try_serve(t)

    def _try_serve(self, t: int) -> None:
        for reg in self.registers:
            if reg.open and not reg.closing and reg.busy is None and self.queue:
                trk = self.queue.pop(0)
                reg.busy = trk
                self._emit(t, "queue.left", f"queue:{self.queue_id}",
                           {"queueId": self.queue_id, "trackId": trk, "served": True})
                self._emit_queue_length(t)
                svc = self._lognormal_ms(self.r_svc, self.service_median_s, 0.4)
                self._at(t + svc, self._finish_service, reg.id)

    def _finish_service(self, t: int, reg_id: str) -> None:
        reg = next(r for r in self.registers if r.id == reg_id)
        trk = reg.busy
        reg.busy = None
        self._emit(t, "zone.exited", f"track:{trk}", {"zoneId": "checkout", "trackId": trk})
        if reg.closing:
            self._close(t, reg)
        self._try_serve(t)

    # ---------------------------------------------------------------- staffing
    def _close(self, t: int, reg: Register) -> None:
        reg.open = False
        reg.closing = False
        self._emit(t, "register.closed", f"register:{reg.id}", {"registerId": reg.id})
        self._emit_queue_length(t)

    def _open_register(self, t: int) -> None:
        self.pending_open = False
        reg = next((r for r in self.registers if not r.open), None)
        if reg is None:
            return
        reg.open = True
        self._emit(t, "register.opened", f"register:{reg.id}", {"registerId": reg.id})
        self._emit_queue_length(t)
        self._try_serve(t)

    def _staff_check(self, t: int) -> None:
        q = len(self.queue)
        if q > 0:
            self.queue_empty_since = None
        elif self.queue_empty_since is None:
            self.queue_empty_since = t
        if (q >= self.open_threshold and not self.pending_open and any(not r.open for r in self.registers)
                and self._open_count() < self.max_open):
            self.pending_open = True
            delay = int(float(self.r_staff.uniform(self.react_min_ms, self.react_max_ms)))
            self._at(t + delay, self._open_register)
        active = [r for r in self.registers if r.open and not r.closing]
        if (q == 0 and self.queue_empty_since is not None
                and t - self.queue_empty_since >= self.close_after_ms and len(active) > self.min_open):
            reg = active[-1]
            if reg.busy is None:
                self._close(t, reg)
            else:
                reg.closing = True
            self.queue_empty_since = t
        if t + 30_000 < self.duration_ms:
            self._at(t + 30_000, self._staff_check)
