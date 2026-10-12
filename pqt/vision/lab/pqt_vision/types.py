"""Core value types shared by detectors, trackers and zones."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PERSON = 0  # COCO class index for "person"; the only class the pipeline keeps


@dataclass(frozen=True, slots=True)
class Box:
    """An axis-aligned box in image pixels, (x1, y1) top-left and (x2, y2) bottom-right."""

    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 1.0
    cls: int = PERSON

    @property
    def w(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def h(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.w * self.h

    def foot(self) -> tuple[float, float]:
        """Bottom-centre of the box: where a standing person touches the floor."""
        return ((self.x1 + self.x2) / 2.0, self.y2)

    def iou(self, other: Box) -> float:
        ix = max(0.0, min(self.x2, other.x2) - max(self.x1, other.x1))
        iy = max(0.0, min(self.y2, other.y2) - max(self.y1, other.y1))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


@dataclass(slots=True)
class Frame:
    """One decoded frame. `image` is None when a backend (replay) does not need pixels."""

    index: int
    t_ms: int
    image: np.ndarray | None = field(default=None, repr=False)


def boxes_to_array(boxes: list[Box]) -> np.ndarray:
    """(N, 5) float array of x1, y1, x2, y2, score."""
    if not boxes:
        return np.zeros((0, 5), dtype=np.float32)
    return np.array([[b.x1, b.y1, b.x2, b.y2, b.score] for b in boxes], dtype=np.float32)


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU between (N, >=4) and (M, >=4) arrays of x1, y1, x2, y2."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    ax1, ay1, ax2, ay2 = (a[:, i : i + 1] for i in range(4))
    bx1, by1, bx2, by2 = (b[:, i] for i in range(4))
    iw = np.clip(np.minimum(ax2, bx2) - np.maximum(ax1, bx1), 0, None)
    ih = np.clip(np.minimum(ay2, by2) - np.maximum(ay1, by1), 0, None)
    inter = iw * ih
    area_a = np.clip(ax2 - ax1, 0, None) * np.clip(ay2 - ay1, 0, None)
    area_b = np.clip(bx2 - bx1, 0, None) * np.clip(by2 - by1, 0, None)
    union = area_a + area_b - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(union > 0, inter / union, 0.0)
    return out.astype(np.float32)
