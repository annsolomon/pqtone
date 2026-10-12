"""Detection cache: Parquet part files, written as a run goes so an interrupted run resumes.

Layout of a cache directory:

    <dir>/part-0000001-0000301.parquet   frames [1, 301) of the source, one row per box
    <dir>/part-0000301-0000601.parquet   ...

Every part carries the detector description and source info as Parquet key-value metadata, so a cache
says exactly which model and parameters produced it. A frame with no people is still covered: coverage
comes from the part's frame range and its `frames` list, not from the rows.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .types import Box

SCHEMA = pa.schema([
    ("frame", pa.int32()), ("t_ms", pa.int64()),
    ("x1", pa.float32()), ("y1", pa.float32()), ("x2", pa.float32()), ("y2", pa.float32()),
    ("score", pa.float32()), ("cls", pa.int16()),
])
FORMAT = "pqt.detections/v1"


class CacheError(ValueError):
    pass


@dataclass
class _Pending:
    frames: list[int] = field(default_factory=list)
    t_ms: dict[int, int] = field(default_factory=dict)
    rows: list[tuple] = field(default_factory=list)


def _meta(detector: dict, source: dict, frames: list[int]) -> dict[bytes, bytes]:
    return {b"pqt.format": FORMAT.encode(), b"pqt.detector": json.dumps(detector, sort_keys=True).encode(),
            b"pqt.source": json.dumps(source, sort_keys=True).encode(),
            b"pqt.frames": json.dumps(frames).encode()}


class CacheWriter:
    """Collects boxes per frame and writes a part every `chunk` frames (atomic rename)."""

    def __init__(self, directory: str | Path, *, detector: dict, source: dict, chunk: int = 300) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.detector, self.source, self.chunk = detector, source, chunk
        existing = read_meta(self.dir)
        if existing and existing["detector"] != detector:
            raise CacheError(f"{self.dir} holds detections from a different detector configuration; "
                             "use a new output directory")
        self.done: set[int] = set(existing["frames"]) if existing else set()
        self._p = _Pending()

    def add(self, frame: int, t_ms: int, boxes: Iterable[Box]) -> None:
        self._p.frames.append(frame)
        self._p.t_ms[frame] = t_ms
        self._p.rows.extend((frame, t_ms, b.x1, b.y1, b.x2, b.y2, b.score, b.cls) for b in boxes)
        if len(self._p.frames) >= self.chunk:
            self.flush()

    def flush(self) -> None:
        if not self._p.frames:
            return
        frames = self._p.frames
        cols = list(zip(*self._p.rows)) if self._p.rows else [[] for _ in SCHEMA.names]
        table = pa.Table.from_arrays([pa.array(c, type=f.type) for c, f in zip(cols, SCHEMA)], schema=SCHEMA)
        table = table.replace_schema_metadata(_meta(self.detector, self.source, frames))
        name = f"part-{min(frames):07d}-{max(frames) + 1:07d}.parquet"
        tmp = self.dir / f".{name}.tmp"
        pq.write_table(table, tmp)
        os.replace(tmp, self.dir / name)
        self.done.update(frames)
        self._p = _Pending()


def _parts(directory: Path) -> list[Path]:
    if directory.is_file():
        return [directory]
    return sorted(directory.glob("part-*.parquet"))


def read_meta(directory: str | Path) -> dict | None:
    """Detector, source and covered frames of a cache, or None if it has no parts yet."""
    parts = _parts(Path(directory))
    if not parts:
        return None
    detector = source = None
    frames: list[int] = []
    for p in parts:
        md = pq.read_schema(p).metadata or {}
        if md.get(b"pqt.format") != FORMAT.encode():
            raise CacheError(f"{p} is not a {FORMAT} file")
        d = json.loads(md[b"pqt.detector"])
        if detector is None:
            detector, source = d, json.loads(md[b"pqt.source"])
        elif d != detector:
            raise CacheError(f"{p} was written by a different detector configuration")
        frames.extend(json.loads(md[b"pqt.frames"]))
    return {"detector": detector, "source": source, "frames": sorted(set(frames))}


def read(directory: str | Path) -> tuple[dict, dict[int, list[Box]]]:
    """(metadata, frame → boxes sorted by score, highest first) for every covered frame."""
    meta = read_meta(directory)
    if meta is None:
        raise CacheError(f"no detection parts in {directory}")
    by_frame: dict[int, list[Box]] = {f: [] for f in meta["frames"]}
    for p in _parts(Path(directory)):
        t = pq.read_table(p).to_pydict()
        for i in range(len(t["frame"])):
            by_frame.setdefault(t["frame"][i], []).append(
                Box(t["x1"][i], t["y1"][i], t["x2"][i], t["y2"][i], t["score"][i], t["cls"][i]))
    for boxes in by_frame.values():
        boxes.sort(key=lambda b: -b.score)
    return meta, by_frame
