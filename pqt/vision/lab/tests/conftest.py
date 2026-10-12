"""Synthetic fixtures only: no real people, frames or footage (CLAUDE.md privacy rules)."""
from __future__ import annotations

from pathlib import Path

import pytest

from vision_synth import write_mot_sequence


@pytest.fixture
def mot_seq(tmp_path: Path) -> Path:
    return write_mot_sequence(tmp_path)
