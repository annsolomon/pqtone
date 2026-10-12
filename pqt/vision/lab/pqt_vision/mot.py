"""MOT17 ground truth (ADR-013: benchmarking only; the data is never committed).

`gt/gt.txt` rows: frame, id, left, top, width, height, consider, class, visibility.
Classes: 1 pedestrian, 2 person on vehicle, 3 car, 4 bicycle, 5 motorbike, 6 non-motorised vehicle,
7 static person, 8 distractor, 9 occluder, 10 occluder on the ground, 11 occluder full, 12 reflection.

For PQT, a *person* is class 1 or 7 (a person standing still is what a queue holds) with visibility at
least `min_vis`. Classes 2, 8 and 12, and people below `min_vis`, are *ignore* regions: a detection on
one is neither a hit nor a false alarm. Everything else is background.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .types import Box

PERSON_CLASSES = frozenset({1, 7})
IGNORE_CLASSES = frozenset({2, 8, 12})
DEFAULT_MIN_VIS = 0.25


@dataclass
class FrameGT:
    people: list[tuple[int, Box]] = field(default_factory=list)  # (track id, box)
    ignore: list[Box] = field(default_factory=list)


@dataclass
class GroundTruth:
    sequence: str
    frames: dict[int, FrameGT]
    min_vis: float

    def people(self, frame: int) -> list[tuple[int, Box]]:
        f = self.frames.get(frame)
        return f.people if f else []

    def ignore(self, frame: int) -> list[Box]:
        f = self.frames.get(frame)
        return f.ignore if f else []


def parse_gt(text: str) -> np.ndarray:
    """gt.txt contents → (N, 9) float array; blank lines ignored."""
    rows = [list(map(float, line.split(",")[:9])) for line in text.splitlines() if line.strip()]
    if not rows:
        return np.zeros((0, 9), dtype=np.float64)
    arr = np.array(rows, dtype=np.float64)
    if arr.shape[1] != 9:
        raise ValueError("gt.txt rows need 9 columns")
    return arr


def build_gt(rows: np.ndarray, *, sequence: str, frames: int, min_vis: float = DEFAULT_MIN_VIS) -> GroundTruth:
    out: dict[int, FrameGT] = {f: FrameGT() for f in range(1, frames + 1)}
    for frame, tid, left, top, w, h, _consider, cls, vis in rows:
        f, c = int(frame), int(cls)
        box = Box(float(left), float(top), float(left + w), float(top + h), 1.0)
        slot = out.setdefault(f, FrameGT())
        if c in PERSON_CLASSES:
            if vis >= min_vis:
                slot.people.append((int(tid), box))
            else:
                slot.ignore.append(box)
        elif c in IGNORE_CLASSES:
            slot.ignore.append(box)
    return GroundTruth(sequence=sequence, frames=out, min_vis=min_vis)


def load_gt(seq_dir: str | Path, *, min_vis: float = DEFAULT_MIN_VIS) -> GroundTruth:
    from .sources import read_seqinfo

    p = Path(seq_dir)
    info = read_seqinfo(p)
    rows = parse_gt((p / "gt" / "gt.txt").read_text(encoding="utf-8"))
    return build_gt(rows, sequence=info["name"], frames=int(info["seqlength"]), min_vis=min_vis)
