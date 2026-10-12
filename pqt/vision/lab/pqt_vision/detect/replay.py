"""Replay detector: serves boxes from a detection cache, so everything after detection runs on CPU
without a model or pixels (detections computed once on a GPU or a runner, replayed in Codespaces)."""
from __future__ import annotations

from pathlib import Path

from .. import cache
from ..types import Box, Frame


class MissingFrame(KeyError):
    """The cache does not cover this frame (as opposed to covering it with no people)."""


class ReplayDetector:
    def __init__(self, directory: str | Path, *, min_score: float = 0.0) -> None:
        self.meta, self._boxes = cache.read(directory)
        self.min_score = min_score
        self.name = f"replay:{self.meta['detector'].get('model', '?')}"

    @property
    def frames(self) -> list[int]:
        return self.meta["frames"]

    def detect(self, frame: Frame) -> list[Box]:
        try:
            boxes = self._boxes[frame.index]
        except KeyError:
            raise MissingFrame(frame.index) from None
        return [b for b in boxes if b.score >= self.min_score]

    def describe(self) -> dict:
        return {"backend": "replay", "of": self.meta["detector"], "min_score": self.min_score}
