"""Tracks → floor points → zone events → CloudEvents (V5). Used by the CLI, the bench and the edge agent."""
from __future__ import annotations

import numpy as np

from .camera import CameraConfig
from .emitter import Emitter, TrackPseudonymiser
from .types import Box
from .zones import ZoneEngine, ZoneEvent


def zone_events(camera: CameraConfig, tracks: dict[int, list[tuple[int, Box]]], times: dict[int, int],
                *, lost_after_s: float = 2.0) -> tuple[list[ZoneEvent], int]:
    """All zone events of a tracked clip, in event-time order, and the clip's last event time."""
    engine = ZoneEngine(camera.zones, camera.membership, lost_after_s=lost_after_s)
    out: list[ZoneEvent] = []
    last = 0
    for f in sorted(tracks):
        t = times[f]
        last = t
        obs = tracks[f]
        if obs:
            feet = camera.to_floor(np.array([b.foot() for _, b in obs], dtype=float))
            out += engine.update(t, [(tid, float(x), float(y)) for (tid, _), (x, y) in zip(obs, feet)])
        else:
            out += engine.update(t, [])
    out += engine.finish()
    return out, last


def cloud_events(camera: CameraConfig, tracks: dict[int, list[tuple[int, Box]]], times: dict[int, int], *,
                 start_ms: int, run_key: str, secret: bytes, trailing_ms: int = 0, lost_after_s: float = 2.0) -> list[dict]:
    events, last = zone_events(camera, tracks, times, lost_after_s=lost_after_s)
    em = Emitter(camera, start_ms, run_key, TrackPseudonymiser(secret, camera.camera))
    return em.begin() + em.emit(events) + em.end(last, trailing_ms=trailing_ms)
