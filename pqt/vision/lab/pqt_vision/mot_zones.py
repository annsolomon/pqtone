"""Queue-sized zones for the MOT17 open-dataset track, chosen from ground truth only (V4).

MOT17 has no floor measurements and no checkout, so each sequence gets one rectangular zone in image
pixels (identity homography). The zone is chosen by a fixed, documented procedure that looks only at the
ground truth, never at a detector's output, and the result is committed to `vision/lab/config/mot17/`
before any count is evaluated on it:

1. Cut the image into a 16 × 9 grid of cells.
2. Consider every axis-aligned rectangle of whole cells, 2–8 cells wide and 2–6 tall.
3. Sample ground-truth occupancy (people whose foot point is inside) once per second.
4. Score = fraction of samples with 2–8 people (queue-like). Ties: larger rectangle, then top-left first.

A rectangle touching the image edge is skipped, because people there are cut off by the frame.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mot import GroundTruth

GRID = (16, 9)
WIDTHS = range(2, 9)
HEIGHTS = range(2, 7)
QUEUE_RANGE = (2, 8)


@dataclass(frozen=True)
class ZoneProposal:
    x1: int
    y1: int
    x2: int
    y2: int
    score: float
    samples: int
    mean: float
    min: int
    max: int

    def polygon(self) -> list[list[float]]:
        return [[self.x1, self.y1], [self.x2, self.y1], [self.x2, self.y2], [self.x1, self.y2]]


def sample_frames(frames: int, fps: float) -> list[int]:
    step = max(1, int(round(fps)))
    return list(range(1, frames + 1, step))


def propose_zone(gt: GroundTruth, *, width: int, height: int, fps: float) -> ZoneProposal:
    cw, ch = width / GRID[0], height / GRID[1]
    samples = sample_frames(max(gt.frames), fps)
    # Ground-truth foot points (image pixels) at each 1-second sample.
    feet = [np.array([b.foot() for _, b in gt.people(f)], dtype=float).reshape(-1, 2) for f in samples]
    best: ZoneProposal | None = None
    best_key: tuple | None = None
    for w in WIDTHS:
        for h in HEIGHTS:
            for cx in range(1, GRID[0] - w):        # never touching the left or right edge
                for cy in range(1, GRID[1] - h):    # nor the top or bottom
                    x1, y1, x2, y2 = cx * cw, cy * ch, (cx + w) * cw, (cy + h) * ch
                    counts = np.array([int(((p[:, 0] >= x1) & (p[:, 0] < x2) & (p[:, 1] >= y1) & (p[:, 1] < y2)).sum())
                                       for p in feet])
                    score = float(((counts >= QUEUE_RANGE[0]) & (counts <= QUEUE_RANGE[1])).mean())
                    cand = ZoneProposal(int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)), round(score, 4),
                                        len(counts), round(float(counts.mean()), 3), int(counts.min()), int(counts.max()))
                    key = (cand.score, w * h, -cy, -cx)  # higher score, then larger, then top-left first
                    if best_key is None or key > best_key:
                        best, best_key = cand, key
    assert best is not None
    return best


def camera_doc(seq: str, proposal: ZoneProposal, *, width: int, height: int, dataset: str = "MOT17",
               use: str = "benchmarking only: ADR-013") -> dict:
    name = seq.lower()
    return {
        "apiVersion": "camera.pqt/v1", "camera": name, "store": dataset.lower(),
        "description": (f"{seq} ({dataset}, CC BY-NC-SA 3.0, {use}). Zone chosen from ground truth "
                        f"only by pqt_vision.mot_zones: {proposal.score:.0%} of 1-s samples hold 2-8 people "
                        f"(mean {proposal.mean}, range {proposal.min}-{proposal.max}, n={proposal.samples})."),
        "image": {"width": width, "height": height}, "units": "px",
        "homography": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        "zones": [{"id": "zone-1", "kind": "zone", "polygon": proposal.polygon()}],
        # Pixel units: margins and speeds are in pixels; 10 px of hysteresis, half a second to confirm.
        "membership": {"enterSeconds": 0.5, "exitSeconds": 0.5, "boundaryMargin": 10},
    }
