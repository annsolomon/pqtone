"""A synthetic queue seen by a camera (V8): tracks, the true queue length, and the expected R-QUEUE episodes.

Synthetic tracks (no real people) exercise everything after detection, end to end through the stack.
The expected incidents are derived from the *true* queue length with the rule specification in
config/rules.yaml, which is what "episodes derived from the manual counts with the same rule spec" means
in the plan; on our own footage the true length comes from the manual 10-second counts instead.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .types import Box

FPS = 10
SLOT_PX = 45.0
HEAD = (0.0, 200.0)


def camera_doc(*, store: str, camera: str, queue_id: str) -> dict:
    """A pixel-unit camera whose queue runs along y = 200 from the register at x = 0."""
    return {
        "apiVersion": "camera.pqt/v1", "camera": camera, "store": store, "units": "px",
        "description": "Synthetic camera for the V8 end-to-end test; no real footage.",
        "image": {"width": 1280, "height": 720}, "homography": np.eye(3).tolist(),
        "zones": [{"id": f"{queue_id}-zone", "kind": "queue", "queueId": queue_id, "openRegisters": 1,
                   "head": list(HEAD), "headRadius": 60.0,
                   "polygon": [[20, 120], [460, 120], [460, 280], [20, 280]]}],
        "membership": {"enterSeconds": 0.5, "exitSeconds": 0.5, "boundaryMargin": 5.0,
                       "queueMinDwellSeconds": 3.0, "queueMaxSpeed": 60.0, "speedWindowSeconds": 2.0},
    }


@dataclass
class Scenario:
    arrivals_s: list[float]
    departures_s: list[float]   # in queue order: the first to arrive leaves first
    end_s: float

    def true_lengths(self) -> list[tuple[int, int]]:
        """(t_ms, length) after every change, starting at (0, 0)."""
        changes = sorted([(int(a * 1000), 1) for a in self.arrivals_s] + [(int(d * 1000), -1) for d in self.departures_s])
        out, n = [(0, 0)], 0
        for t, dn in changes:
            n += dn
            out.append((t, n))
        return out


DEFAULT = Scenario(arrivals_s=[5.0 * k for k in range(1, 9)],               # 8 people join, one every 5 s
                   departures_s=[120.0 + 15.0 * k for k in range(8)],       # then one is served every 15 s
                   end_s=240.0)


def tracks(sc: Scenario = DEFAULT) -> tuple[dict[int, list[tuple[int, Box]]], dict[int, int]]:
    """Per-frame (track id, box): each person walks in from the right, stands in the next slot,
    shuffles forward as people ahead are served, and leaves past the register (the queue head)."""
    frames = int(sc.end_s * FPS) + 1
    out: dict[int, list[tuple[int, Box]]] = {}
    times: dict[int, int] = {}
    for k in range(frames):
        t = k / FPS
        f = k + 1
        times[f] = int(round(t * 1000))
        obs = []
        for pid, (arr, dep) in enumerate(zip(sc.arrivals_s, sc.departures_s), start=1):
            if t < arr - 2.0 or t > dep + 3.0:
                continue
            ahead = sum(1 for d in sc.departures_s[: pid - 1] if d <= t)
            slot = (pid - 1) - ahead
            target = 50.0 + SLOT_PX * slot
            if t < arr:                       # walking in along the queue line from x = 700
                x = target + (700.0 - target) * (arr - t) / 2.0
            elif t <= dep:                    # standing; moves up a slot over 1 s after each departure ahead
                prev = 50.0 + SLOT_PX * (slot + 1)
                last_dep = max([d for d in sc.departures_s[: pid - 1] if d <= t], default=-1e9)
                x = prev + (target - prev) * min(1.0, (t - last_dep)) if t - last_dep < 1.0 else target
            else:                             # served: walks past the register and out of view
                x = 50.0 - 120.0 * (t - dep)
            y = 200.0
            obs.append((pid, Box(x - 20, y - 100, x + 20, y, 0.9)))
        out[f] = obs
    return out, times


def expected_queue_incidents(lengths: list[tuple[int, int]], *, threshold: int, sustain_ms: int, hysteresis: int,
                             clear_sustain_ms: int, end_ms: int) -> list[dict]:
    """R-QUEUE-001 on a length timeline: opens when length >= threshold has held for `sustain_ms`; clears
    when length <= threshold - hysteresis has held for `clear_sustain_ms`. Same semantics as rules-engine."""
    out: list[dict] = []
    open_, breach, clear, onset = False, None, None, None
    points = lengths + [(end_ms, lengths[-1][1])]
    for (t, n), (t_next, _) in zip(points, points[1:]):
        if not open_:
            breach = (breach if breach is not None else t) if n >= threshold else None
            if breach is not None and t_next >= breach + sustain_ms:
                open_, onset = True, breach
                out.append({"ruleId": "R-QUEUE-001", "onsetMs": onset, "detectedMs": breach + sustain_ms})
                clear = None
        if open_:
            clear = (clear if clear is not None else t) if n <= threshold - hysteresis else None
            if clear is not None and t_next >= clear + clear_sustain_ms:
                out[-1]["resolvedMs"] = clear + clear_sustain_ms
                open_, breach, clear = False, None, None
    return out
