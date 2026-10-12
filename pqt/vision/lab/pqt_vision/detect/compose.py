"""Detector wrappers for small, distant people (V7): run the model on regions instead of the whole frame.

A 640 × 640 model looking at a 1920 × 1080 frame shrinks everything by three; a person 60 px tall becomes
20 px, which YOLOX-S often misses. Two remedies, both behind the same Detector interface:

- `RoiDetector`: only the region around the configured zones matters for counting, so crop that region
  (plus a margin, so people near the boundary are tracked before they step in) and detect on the crop.
  This is also what an edge box should do: less work and more resolution where it counts.
- `TiledDetector`: cover the whole frame with overlapping tiles plus one full-frame pass (for people too
  big for a tile), and merge with NMS. Several times the compute; used to measure full-frame tracking.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..types import Box, Frame
from .base import Detector
from .yolox import nms


@dataclass(frozen=True)
class Region:
    x1: int
    y1: int
    x2: int
    y2: int

    def crop(self, image: np.ndarray) -> np.ndarray:
        return image[self.y1 : self.y2, self.x1 : self.x2]

    def as_list(self) -> list[int]:
        return [self.x1, self.y1, self.x2, self.y2]


def roi_for_zones(polygons: list[np.ndarray], *, width: int, height: int, margin_frac: float = 0.5,
                  min_margin: int = 64) -> Region:
    """Bounding box of all zone polygons (image pixels), grown by a margin on every side, clipped."""
    pts = np.vstack(polygons)
    x1, y1 = pts.min(axis=0)
    x2, y2 = pts.max(axis=0)
    mx = max(min_margin, margin_frac * (x2 - x1))
    my = max(min_margin, margin_frac * (y2 - y1))
    return Region(max(0, int(np.floor(x1 - mx))), max(0, int(np.floor(y1 - my))),
                  min(width, int(np.ceil(x2 + mx))), min(height, int(np.ceil(y2 + my))))


def tiles_for(width: int, height: int, *, cols: int = 2, rows: int = 2, overlap: float = 0.2) -> list[Region]:
    """A cols × rows grid of tiles, each overlapping its neighbours by `overlap` of the tile size."""
    tw = int(np.ceil(width / (cols - (cols - 1) * overlap)))
    th = int(np.ceil(height / (rows - (rows - 1) * overlap)))
    out = []
    for r in range(rows):
        for c in range(cols):
            x1 = min(int(round(c * tw * (1 - overlap))), width - tw)
            y1 = min(int(round(r * th * (1 - overlap))), height - th)
            out.append(Region(max(0, x1), max(0, y1), min(width, x1 + tw), min(height, y1 + th)))
    return out


def _shift(boxes: list[Box], dx: int, dy: int) -> list[Box]:
    return [Box(b.x1 + dx, b.y1 + dy, b.x2 + dx, b.y2 + dy, b.score, b.cls) for b in boxes]


def _touches_inner_edge(b: Box, r: Region, frame_w: int, frame_h: int, tol: float = 2.0) -> bool:
    """A box cut off by a tile edge that is not also the frame edge (a partial person)."""
    return ((b.x1 - r.x1 <= tol and r.x1 > 0) or (r.x2 - b.x2 <= tol and r.x2 < frame_w)
            or (b.y1 - r.y1 <= tol and r.y1 > 0) or (r.y2 - b.y2 <= tol and r.y2 < frame_h))


def merge(boxes: list[Box], iou: float) -> list[Box]:
    if not boxes:
        return []
    arr = np.array([[b.x1, b.y1, b.x2, b.y2] for b in boxes], dtype=np.float32)
    keep = nms(arr, np.array([b.score for b in boxes], dtype=np.float32), iou)
    return [boxes[i] for i in keep]


class RoiDetector:
    def __init__(self, base: Detector, region: Region) -> None:
        self.base, self.region = base, region
        self.name = f"{base.name}@roi"

    def detect(self, frame: Frame) -> list[Box]:
        if frame.image is None:
            raise ValueError("RoiDetector needs pixels")
        crop = self.region.crop(frame.image)
        return _shift(self.base.detect(Frame(frame.index, frame.t_ms, crop)), self.region.x1, self.region.y1)

    def describe(self) -> dict:
        return {**self.base.describe(), "wrapper": "roi", "roi": self.region.as_list()}


class TiledDetector:
    def __init__(self, base: Detector, *, cols: int = 2, rows: int = 2, overlap: float = 0.2,
                 full_frame: bool = True, merge_iou: float = 0.5) -> None:
        self.base, self.cols, self.rows, self.overlap = base, cols, rows, overlap
        self.full_frame, self.merge_iou = full_frame, merge_iou
        self.name = f"{base.name}@tiled{cols}x{rows}"

    def detect(self, frame: Frame) -> list[Box]:
        if frame.image is None:
            raise ValueError("TiledDetector needs pixels")
        h, w = frame.image.shape[:2]
        found: list[Box] = []
        for r in tiles_for(w, h, cols=self.cols, rows=self.rows, overlap=self.overlap):
            part = self.base.detect(Frame(frame.index, frame.t_ms, r.crop(frame.image)))
            found += [b for b in _shift(part, r.x1, r.y1) if not _touches_inner_edge(b, r, w, h)]
        if self.full_frame:
            found += self.base.detect(frame)
        return merge(found, self.merge_iou)

    def describe(self) -> dict:
        return {**self.base.describe(), "wrapper": "tiled", "tiles": [self.cols, self.rows], "overlap": self.overlap,
                "fullFrame": self.full_frame, "mergeIou": self.merge_iou}
