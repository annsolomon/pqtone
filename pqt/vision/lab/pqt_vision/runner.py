"""Run a detector over a source into a resumable detection cache."""
from __future__ import annotations

import time
from dataclasses import dataclass

from .cache import CacheWriter
from .detect.base import Detector
from .sources import FrameSource


@dataclass
class RunStats:
    frames: int  # frames detected in this run
    skipped: int  # frames already in the cache (resumed)
    detections: int
    wall_s: float
    infer_ms_mean: float

    def as_dict(self) -> dict:
        return {"frames": self.frames, "skipped": self.skipped, "detections": self.detections,
                "wallSeconds": round(self.wall_s, 2), "inferMsMean": round(self.infer_ms_mean, 2),
                "fps": round(self.frames / self.wall_s, 2) if self.wall_s > 0 else None}


def run_detection(source: FrameSource, detector: Detector, out_dir, *, stride: int = 1,
                  max_frames: int | None = None, chunk: int = 300) -> RunStats:
    writer = CacheWriter(out_dir, detector=detector.describe(), source=source.info.as_dict(), chunk=chunk)
    already = set(writer.done)
    skipped = 0

    def skip(index: int) -> bool:
        nonlocal skipped
        if index in already:
            skipped += 1
            return True
        return False

    frames = detections = 0
    infer = 0.0
    start = time.perf_counter()
    for frame in source.frames(stride=stride, max_frames=max_frames, skip=skip):
        t0 = time.perf_counter()
        boxes = detector.detect(frame)
        infer += time.perf_counter() - t0
        writer.add(frame.index, frame.t_ms, boxes)
        frames += 1
        detections += len(boxes)
    writer.flush()
    wall = time.perf_counter() - start
    return RunStats(frames, skipped, detections, wall, (infer / frames * 1000) if frames else 0.0)
