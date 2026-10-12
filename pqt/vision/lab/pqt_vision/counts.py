"""Ground-truth zone counts from MOT annotations (V6, open-dataset track).

Writes the scorer's count CSV (offset_s, zone, count): one sample per `every_s` seconds, counting people
(mot.py's definition) whose foot point, mapped through the camera homography, is inside each zone. On our
own footage the same CSV comes from manual counts every 10 s instead.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from .camera import CameraConfig
from .mot import GroundTruth
from .zones import point_in_polygon


def gt_counts(gt: GroundTruth, camera: CameraConfig, *, fps: float, every_s: float = 1.0) -> list[tuple[float, str, int]]:
    step = max(1, int(round(every_s * fps)))
    first = min(gt.frames)
    rows = []
    polys = {z.id: np.array(z.polygon, dtype=float) for z in camera.zones}
    for f in range(first, max(gt.frames) + 1, step):
        people = gt.people(f)
        feet = camera.to_floor(np.array([b.foot() for _, b in people], dtype=float).reshape(-1, 2)) if people else np.zeros((0, 2))
        for zid, poly in polys.items():
            n = sum(1 for x, y in feet if point_in_polygon(float(x), float(y), poly))
            rows.append((round((f - first) / fps, 3), zid, n))
    return rows


def write_counts(path: str | Path, rows: list[tuple[float, str, int]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["offset_s", "zone", "count"])
        w.writerows(rows)
