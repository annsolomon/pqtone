"""Multi-object tracking over cached detections (V2)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ..detect.replay import ReplayDetector
from ..types import Box, Frame
from .bytetrack import ByteTrackConfig, ByteTracker, TrackOut

__all__ = ["ByteTrackConfig", "ByteTracker", "TrackOut", "frame_times", "run_tracking", "write_tracks", "read_tracks"]

SCHEMA = pa.schema([
    ("frame", pa.int32()), ("t_ms", pa.int64()), ("track_id", pa.int32()),
    ("x1", pa.float32()), ("y1", pa.float32()), ("x2", pa.float32()), ("y2", pa.float32()), ("score", pa.float32()),
])
FORMAT = "pqt.tracks/v1"


def frame_times(replay: ReplayDetector) -> dict[int, int]:
    """Event time (ms from the first frame) per covered frame, from the source frame rate."""
    fps = float(replay.meta["source"]["fps"])
    frames = replay.frames
    first = frames[0]
    return {f: int(round((f - first) * 1000.0 / fps)) for f in frames}


def run_tracking(replay: ReplayDetector, config: ByteTrackConfig | None = None) -> dict[int, list[TrackOut]]:
    fps = float(replay.meta["source"]["fps"])
    tracker = ByteTracker(config, fps=fps)
    times = frame_times(replay)
    return {f: tracker.update(replay.detect(Frame(f, times[f])), f) for f in replay.frames}


def write_tracks(path: str | Path, tracks: dict[int, list[TrackOut]], *, times: dict[int, int], meta: dict) -> None:
    rows = [(f, times[f], t.track_id, t.box.x1, t.box.y1, t.box.x2, t.box.y2, t.box.score)
            for f in sorted(tracks) for t in tracks[f]]
    cols = list(zip(*rows)) if rows else [[] for _ in SCHEMA.names]
    table = pa.Table.from_arrays([pa.array(c, type=fl.type) for c, fl in zip(cols, SCHEMA)], schema=SCHEMA)
    table = table.replace_schema_metadata({b"pqt.format": FORMAT.encode(),
                                           b"pqt.meta": json.dumps(meta, sort_keys=True).encode(),
                                           b"pqt.frames": json.dumps(sorted(tracks)).encode()})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, path)


def read_tracks(path: str | Path) -> tuple[dict, dict[int, list[tuple[int, Box]]]]:
    table = pq.read_table(path)
    md = table.schema.metadata or {}
    if md.get(b"pqt.format") != FORMAT.encode():
        raise ValueError(f"{path} is not a {FORMAT} file")
    out: dict[int, list[tuple[int, Box]]] = {f: [] for f in json.loads(md[b"pqt.frames"])}
    t = table.to_pydict()
    for i in range(len(t["frame"])):
        out[t["frame"][i]].append((t["track_id"][i], Box(t["x1"][i], t["y1"][i], t["x2"][i], t["y2"][i], t["score"][i])))
    return json.loads(md[b"pqt.meta"]), out
