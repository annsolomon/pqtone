"""Detector backends behind one interface (`base.Detector`)."""
from .base import Detector
from .replay import MissingFrame, ReplayDetector

__all__ = ["Detector", "MissingFrame", "ReplayDetector"]
