"""Synthetic helpers for vision-lab tests only: no real people, frames or footage (CLAUDE.md privacy rules)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from pqt_vision.types import Box, Frame


def write_mot_sequence(root: Path, name: str = "SYN-01", frames: int = 12, fps: int = 10,
                       w: int = 64, h: int = 48, gt_rows: list[str] | None = None) -> Path:
    """A tiny MOT17-shaped sequence of flat grey frames with a hand-written gt.txt."""
    seq = root / name
    (seq / "img1").mkdir(parents=True)
    (seq / "gt").mkdir()
    for i in range(1, frames + 1):
        cv2.imwrite(str(seq / "img1" / f"{i:06d}.jpg"), np.full((h, w, 3), 40 + i, dtype=np.uint8))
    (seq / "seqinfo.ini").write_text(
        f"[Sequence]\nname={name}\nimDir=img1\nframeRate={fps}\nseqLength={frames}\n"
        f"imWidth={w}\nimHeight={h}\nimExt=.jpg\n", encoding="utf-8")
    (seq / "gt" / "gt.txt").write_text("\n".join(gt_rows or []) + "\n", encoding="utf-8")
    return seq


class FakeDetector:
    """Deterministic boxes from the frame index; counts calls so resume can be checked."""

    name = "fake"

    def __init__(self, tag: str = "a") -> None:
        self.tag, self.calls = tag, []

    def detect(self, frame: Frame) -> list[Box]:
        assert frame.image is not None
        self.calls.append(frame.index)
        n = frame.index % 3  # some frames have no people at all
        return [Box(1.0 + k, 2.0, 11.0 + k, 30.0, round(0.9 - 0.1 * k, 3)) for k in range(n)]

    def describe(self) -> dict:
        return {"backend": "fake", "model": "fake", "tag": self.tag}
