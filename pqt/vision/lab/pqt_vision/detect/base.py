"""The detector interface. Everything after detection depends only on this."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..types import Box, Frame


@runtime_checkable
class Detector(Protocol):
    """Finds people in a frame. Implementations: YOLOX (ONNX Runtime) and replay (Parquet cache)."""

    name: str

    def detect(self, frame: Frame) -> list[Box]:
        """People in `frame`, in image pixels, highest score first."""
        ...

    def describe(self) -> dict:
        """Model name, file hash and parameters; stored with every cached result."""
        ...
