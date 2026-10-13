"""A synthetic detector for chaos tests (A4): the V8 synthetic queue, replayed in a loop on frame time.

Frames still come from the camera stream (so ingest is exercised for real); the people are synthetic, so
the test has a steady, known flow of events without any real footage (CLAUDE.md privacy rules).
"""
from __future__ import annotations

from pqt_vision.synthetic import DEFAULT, FPS, Scenario, tracks
from pqt_vision.types import Box, Frame


class SyntheticQueueDetector:
    name = "synthetic-queue"

    def __init__(self, scenario: Scenario = DEFAULT, *, speed: float = 1.0) -> None:
        trk, times = tracks(scenario)
        self.speed = speed  # > 1 replays the scenario faster than real time (tests)
        self.period_ms = int(scenario.end_s * 1000)
        self.step_ms = 1000 // FPS
        self.by_slot = {times[f] // self.step_ms: [b for _, b in obs] for f, obs in trk.items()}

    def detect(self, frame: Frame) -> list[Box]:
        t = int(frame.t_ms * self.speed) % self.period_ms
        return list(self.by_slot.get(t // self.step_ms, []))

    def describe(self) -> dict:
        return {"backend": "synthetic-queue", "periodMs": self.period_ms, "speed": self.speed}
