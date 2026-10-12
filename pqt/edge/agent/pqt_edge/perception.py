"""Perception on the edge: frames → detections → tracks → zone events → CloudEvents (pqt-vision as a library).

Event time is the agent's wall clock when the frame arrived (chrony keeps it honest; A5 reports the
offset). Ids are stable per agent session: the run key is a random session id, and every event is stored
in the outbox before it is sent, so retries resend the same id and event-core deduplicates.
"""
from __future__ import annotations

import uuid

import numpy as np

from pqt_vision.camera import CameraConfig
from pqt_vision.detect.base import Detector
from pqt_vision.emitter import Emitter, TrackPseudonymiser
from pqt_vision.track import ByteTrackConfig, ByteTracker
from pqt_vision.types import Frame
from pqt_vision.zones import ZoneEngine


class Perception:
    def __init__(self, camera: CameraConfig, detector: Detector, *, start_ms: int, secret: bytes, fps: float,
                 tracker: ByteTrackConfig | None = None, session: str | None = None) -> None:
        self.camera, self.detector = camera, detector
        self.start_ms = start_ms
        self.tracker = ByteTracker(tracker, fps=fps)
        self.zones = ZoneEngine(camera.zones, camera.membership)
        self.emitter = Emitter(camera, start_ms, session or f"session-{uuid.uuid4().hex}", TrackPseudonymiser(secret, camera.camera))
        self._frame = 0
        self._started = False

    def process(self, wall_ms: int, image) -> list[dict]:
        out: list[dict] = []
        if not self._started:
            out += self.emitter.begin()
            self._started = True
        t = max(0, wall_ms - self.start_ms)
        self._frame += 1
        boxes = self.detector.detect(Frame(self._frame, t, image))
        tracks = self.tracker.update(boxes, self._frame)
        if tracks:
            feet = self.camera.to_floor(np.array([tr.box.foot() for tr in tracks], dtype=float))
            obs = [(tr.track_id, float(x), float(y)) for tr, (x, y) in zip(tracks, feet)]
        else:
            obs = []
        zone_events = self.zones.update(t, obs)
        released_until = self.zones.released_until if zone_events else None
        out += self.emitter.emit(zone_events)
        # Ticks may only run up to what the zone engine has made final, or a later tick would precede an
        # earlier zone event still in its reorder buffer.
        safe = t - self.zones.hold_ms
        if released_until is not None:
            safe = max(safe, released_until)
        out += self.emitter.advance(max(0, safe))
        return out

    def finish(self, wall_ms: int) -> list[dict]:
        t = max(0, wall_ms - self.start_ms)
        return self.emitter.emit(self.zones.finish()) + self.emitter.end(t)
