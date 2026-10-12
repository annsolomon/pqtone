"""Zones and queue membership with hysteresis (V4).

Everything here works on a track's *foot point in floor coordinates* (metres for a calibrated camera,
pixels on the open-dataset track) and on event time in milliseconds. Nothing reads a wall clock.

Zone membership, per (track, zone), is a four-state machine:

    OUTSIDE --inside--> ENTERING --inside for enter_s--> INSIDE --outside--> EXITING --outside for exit_s--> OUTSIDE
               ^            |                                ^                  |
               +--outside---+                                +-----inside-------+

"inside" means the point is at least `margin` inside the polygon and "outside" at least `margin`
outside it; inside the band between, the machine keeps its current state. So a person standing on the
boundary, whose foot point jitters by a few centimetres, never toggles. A confirmed transition is dated
at the moment it *started* (the first inside/outside observation), so event times stay accurate even
though the decision is taken `enter_s` / `exit_s` later.

Queue membership adds two conditions for a queue zone: the person has been INSIDE for at least
`queue_min_dwell_s`, and their foot point moved no faster than `queue_max_speed` over the last
`speed_window_s`. Someone walking through the queue area is in the zone but never in the queue.
A member leaves the queue when they leave the zone.

A track that disappears is closed at its last observation once `lost_after_s` has passed (or at
`finish()`).

Because transitions are back-dated, events are confirmed out of time order. The engine holds them in a
reorder buffer and releases them in event-time order once no earlier event can still appear
(`hold` = the longest confirmation delay plus one second), so downstream consumers see a monotonic stream.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from .camera import Membership, ZoneSpec


def point_in_polygon(x: float, y: float, poly: np.ndarray) -> bool:
    """Even-odd rule (ray casting to +x)."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def signed_distance(x: float, y: float, poly: np.ndarray) -> float:
    """Distance to the polygon boundary: positive inside, negative outside."""
    a = poly
    b = np.roll(poly, -1, axis=0)
    ab = b - a
    ap = np.array([x, y]) - a
    t = np.clip((ap * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0.0, 1.0)
    closest = a + ab * t[:, None]
    d = float(np.sqrt(((closest - [x, y]) ** 2).sum(1)).min())
    return d if point_in_polygon(x, y, poly) else -d


class ZState(Enum):
    OUTSIDE = 0
    ENTERING = 1
    INSIDE = 2
    EXITING = 3


@dataclass(frozen=True)
class ZoneEvent:
    """kind: zone.entered | zone.exited | queue.joined | queue.left (store-sim names)."""

    kind: str
    t_ms: int
    zone_id: str
    track_id: int
    queue_id: str | None = None
    served: bool | None = None


@dataclass
class _ZT:
    state: ZState = ZState.OUTSIDE
    since: int = 0
    entered_at: int | None = None
    member: bool = False
    exit_xy: tuple[float, float] | None = None  # where the person was when they first stepped out


# On equal event times, leaving comes before arriving (a person moving from one zone to the next).
_ORDER = {"queue.left": 0, "zone.exited": 1, "zone.entered": 2, "queue.joined": 3}


def _key(e: "ZoneEvent") -> tuple:
    return (e.t_ms, _ORDER[e.kind], e.track_id, e.zone_id)


@dataclass
class _Track:
    last_t: int
    last_xy: tuple[float, float]
    history: deque = field(default_factory=deque)  # (t_ms, x, y) within the speed window
    zones: dict[str, _ZT] = field(default_factory=dict)


class ZoneEngine:
    def __init__(self, zones: list[ZoneSpec], membership: Membership | None = None, *,
                 lost_after_s: float = 2.0, queue_heads: dict[str, tuple[float, float]] | None = None,
                 head_radius: float = 0.75) -> None:
        self.zones = zones
        self.m = membership or Membership()
        self._poly = {z.id: np.array(z.polygon, dtype=float) for z in zones}
        self.lost_after_ms = int(lost_after_s * 1000)
        self.heads = queue_heads or {}
        self.head_radius = head_radius
        self.tracks: dict[int, _Track] = {}
        self.hold_ms = int(max(self.m.enter_s, self.m.exit_s, lost_after_s) * 1000) + 1000
        self._pending: list[ZoneEvent] = []
        self.released_until = -1

    def _release(self, upto: int | None) -> list[ZoneEvent]:
        self._pending.sort(key=_key)
        if upto is None:
            out, self._pending = self._pending, []
        else:
            k = 0
            while k < len(self._pending) and self._pending[k].t_ms <= upto:
                k += 1
            out, self._pending = self._pending[:k], self._pending[k:]
        if out:
            self.released_until = max(self.released_until, out[-1].t_ms)
        return out

    # -- public ---------------------------------------------------------------------------------
    def update(self, t_ms: int, observations: list[tuple[int, float, float]]) -> list[ZoneEvent]:
        """One frame: (track id, floor x, floor y) for every visible track. Returns the events that are
        now final, in event-time order (see the reorder buffer above)."""
        out: list[ZoneEvent] = []
        seen = set()
        for tid, x, y in observations:
            seen.add(tid)
            tr = self.tracks.get(tid)
            if tr is None:
                tr = self.tracks[tid] = _Track(t_ms, (x, y))
            tr.last_t, tr.last_xy = t_ms, (x, y)
            tr.history.append((t_ms, x, y))
            window = int(self.m.speed_window_s * 1000)
            while tr.history and tr.history[0][0] < t_ms - window:
                tr.history.popleft()
            for z in self.zones:
                out.extend(self._step(tid, tr, z, t_ms, x, y))
        for tid in [t for t in self.tracks if t not in seen and t_ms - self.tracks[t].last_t > self.lost_after_ms]:
            out.extend(self._close(tid))
        self._pending.extend(out)
        return self._release(t_ms - self.hold_ms)

    def finish(self) -> list[ZoneEvent]:
        """End of stream: close every open membership at the track's last observation."""
        out: list[ZoneEvent] = []
        for tid in list(self.tracks):
            out.extend(self._close(tid))
        self._pending.extend(out)
        return self._release(None)

    def occupancy(self) -> dict[str, int]:
        """Confirmed occupants per zone right now (INSIDE or EXITING)."""
        return {z.id: sum(1 for tr in self.tracks.values()
                          if tr.zones.get(z.id, _ZT()).state in (ZState.INSIDE, ZState.EXITING)) for z in self.zones}

    def queue_lengths(self) -> dict[str, int]:
        return {z.queue: sum(1 for tr in self.tracks.values() if tr.zones.get(z.id, _ZT()).member)
                for z in self.zones if z.kind == "queue"}

    # -- internals ------------------------------------------------------------------------------
    def _speed(self, tr: _Track) -> float:
        h = tr.history
        if len(h) < 2:
            return 0.0
        dt = (h[-1][0] - h[0][0]) / 1000.0
        if dt <= 0:
            return 0.0
        return float(np.hypot(h[-1][1] - h[0][1], h[-1][2] - h[0][2]) / dt)

    def _step(self, tid: int, tr: _Track, z: ZoneSpec, t: int, x: float, y: float) -> list[ZoneEvent]:
        st = tr.zones.setdefault(z.id, _ZT())
        d = signed_distance(x, y, self._poly[z.id])
        inside, outside = d >= self.m.margin, d <= -self.m.margin
        ev: list[ZoneEvent] = []
        if st.state is ZState.OUTSIDE and inside:
            st.state, st.since = ZState.ENTERING, t
        if st.state is ZState.ENTERING:
            if outside:
                st.state = ZState.OUTSIDE
            elif t - st.since >= self.m.enter_s * 1000:
                st.state, st.entered_at = ZState.INSIDE, st.since
                ev.append(ZoneEvent("zone.entered", st.since, z.id, tid))
        elif st.state is ZState.INSIDE and outside:
            st.state, st.since, st.exit_xy = ZState.EXITING, t, (x, y)
        if st.state is ZState.EXITING:
            if inside:
                st.state = ZState.INSIDE
            elif t - st.since >= self.m.exit_s * 1000:
                ev.extend(self._exit(tid, tr, z, st, st.since))
                return ev
        if (z.kind == "queue" and st.state in (ZState.INSIDE, ZState.EXITING) and not st.member
                and st.entered_at is not None and t - st.entered_at >= self.m.queue_min_dwell_s * 1000
                and self._speed(tr) <= self.m.queue_max_speed):
            st.member = True
            ev.append(ZoneEvent("queue.joined", t, z.id, tid, queue_id=z.queue))
        return ev

    def _exit(self, tid: int, tr: _Track, z: ZoneSpec, st: _ZT, t: int) -> list[ZoneEvent]:
        ev = []
        if st.member:
            head = self.heads.get(z.id, z.head)
            radius = self.head_radius if z.id in self.heads else z.head_radius
            px, py = st.exit_xy or tr.last_xy
            served = True if head is None else bool(np.hypot(px - head[0], py - head[1]) <= radius)
            ev.append(ZoneEvent("queue.left", t, z.id, tid, queue_id=z.queue, served=served))
        ev.append(ZoneEvent("zone.exited", t, z.id, tid))
        tr.zones[z.id] = _ZT()
        return ev

    def _close(self, tid: int) -> list[ZoneEvent]:
        tr = self.tracks.pop(tid)
        ev: list[ZoneEvent] = []
        for z in self.zones:
            st = tr.zones.get(z.id)
            if st and st.state in (ZState.INSIDE, ZState.EXITING):
                ev.extend(self._exit(tid, tr, z, st, st.since if st.state is ZState.EXITING else tr.last_t))
        return ev
