"""CloudEvents from zone events (V5): the same contract store-sim produces, so nothing downstream changes.

- `source` = urn:pqt:edge:<store>:<camera>; `type` = com.pqt.store.<kind>; `dataschema` 1.0.0.
- `id` is stable across retries and re-runs: uuid5 of (source, run key, order). The run key names the
  input (a clip, or an agent session), so processing the same clip twice yields the same ids and
  event-core's (source, id) dedup drops the repeat.
- `sequence` is a per-run monotonic counter (the edge outbox relies on it for gap detection).
- `trackId` is pseudonymous and rotates daily: trk- + 16 hex chars of HMAC-SHA256 over the camera and
  local tracker id, keyed by a per-day key derived from a secret that never leaves the edge. The same
  person gets unrelated ids on different days and at different cameras (docs/privacy/vision-data.md).
- queue.length is emitted at the start and on every change (one per queue and instant, N4); clock.tick every 10 s of event time, as
  store-sim does, so rule windows advance even when nobody moves.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from .camera import CameraConfig
from .zones import ZoneEvent

NAMESPACE = uuid.UUID("2f0d6c1e-4b7a-4a39-9f1e-8c3d5b7a1e20")  # edge events (store-sim uses its own)
TYPE_PREFIX = "com.pqt.store."
SCHEMA_BASE = "https://schemas.pqt.local/store/"
TICK_MS = 10_000


def iso_ms(epoch_ms: int) -> str:
    dt = datetime.fromtimestamp(epoch_ms // 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{epoch_ms % 1000:03d}Z"


def parse_iso(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("start time needs a timezone (use Z for UTC)")
    return int(dt.timestamp() * 1000)


class TrackPseudonymiser:
    """Daily-rotating pseudonymous track ids. The secret is held in memory only and never logged."""

    def __init__(self, secret: bytes, camera: str) -> None:
        if len(secret) < 16:
            raise ValueError("the track secret must be at least 16 bytes")
        self._secret = secret
        self._camera = camera
        self._day_keys: dict[str, bytes] = {}

    def __repr__(self) -> str:
        return f"TrackPseudonymiser(camera={self._camera!r}, secret=<hidden>)"

    def _day_key(self, day: str) -> bytes:
        k = self._day_keys.get(day)
        if k is None:
            k = self._day_keys[day] = hmac.new(self._secret, f"pqt-track-day:{day}".encode(), hashlib.sha256).digest()
        return k

    def track_id(self, local_id: int, epoch_ms: int) -> str:
        day = datetime.fromtimestamp(epoch_ms // 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        mac = hmac.new(self._day_key(day), f"{self._camera}:{local_id}".encode(), hashlib.sha256).hexdigest()
        return "trk-" + mac[:16]


@dataclass
class Emitter:
    camera: CameraConfig
    start_ms: int                    # wall-clock epoch of the stream's t = 0
    run_key: str                     # names the input; same input → same ids
    pseudonymiser: TrackPseudonymiser

    def __post_init__(self) -> None:
        self.source = f"urn:pqt:edge:{self.camera.store}:{self.camera.camera}"
        self._order = 0
        self._lengths: dict[str, int] = {}
        self._sent_lengths: dict[str, int] = {}
        self._next_tick = 0
        self._tick_seq = 0
        self._queues = {z.queue: z for z in self.camera.zones if z.kind == "queue"}

    def _event(self, short: str, t_ms: int, subject: str, data: dict, version: str = "1.0.0") -> dict:
        self._order += 1
        return {
            "specversion": "1.0",
            "id": str(uuid.uuid5(NAMESPACE, f"{self.source}:{self.run_key}:{self._order}")),
            "source": self.source,
            "type": TYPE_PREFIX + short,
            "time": iso_ms(self.start_ms + t_ms),
            "subject": subject,
            "dataschema": f"{SCHEMA_BASE}{short}/{version}",
            "datacontenttype": "application/json",
            "storeid": self.camera.store,
            "partitionkey": self.camera.store,
            "sequence": f"{self._order:010d}",
            "data": data,
        }

    def _ticks_until(self, t_ms: int) -> list[dict]:
        out = []
        while self._next_tick <= t_ms:
            out.append(self._event("clock.tick", self._next_tick, "store", {"seq": self._tick_seq}))
            self._tick_seq += 1
            self._next_tick += TICK_MS
        return out

    def _length(self, t_ms: int, queue: str) -> dict:
        z = self._queues[queue]
        self._sent_lengths[queue] = self._lengths[queue]
        return self._event("queue.length", t_ms, f"queue:{queue}",
                           {"queueId": queue, "length": self._lengths[queue], "openRegisters": z.open_registers})

    def _flush_lengths(self, t_ms: int, touched: set[str]) -> list[dict]:
        """One queue.length per queue and instant, only if it moved: a hand-over (one member out, one in at
        the same moment) changes nothing (N4)."""
        out = [self._length(t_ms, q) for q in sorted(touched) if self._lengths[q] != self._sent_lengths.get(q)]
        touched.clear()
        return out

    def begin(self) -> list[dict]:
        out = self._ticks_until(0)
        for q in self._queues:
            self._lengths[q] = 0
            out.append(self._length(0, q))
        return out

    def emit(self, events: Iterable[ZoneEvent]) -> list[dict]:
        """CloudEvents for zone events, which must arrive in event-time order (ZoneEngine guarantees it)."""
        out: list[dict] = []
        touched: set[str] = set()
        t_cur: int | None = None
        for e in events:
            if t_cur is not None and e.t_ms != t_cur:
                out += self._flush_lengths(t_cur, touched)
            t_cur = e.t_ms
            out += self._ticks_until(e.t_ms)
            trk = self.pseudonymiser.track_id(e.track_id, self.start_ms + e.t_ms)
            if e.kind in ("zone.entered", "zone.exited"):
                data = {"zoneId": e.zone_id, "trackId": trk}
                if e.how:
                    data["how"] = e.how
                out.append(self._event(e.kind, e.t_ms, f"track:{trk}", data, "1.1.0" if e.how else "1.0.0"))
            elif e.kind == "queue.joined":
                out.append(self._event(e.kind, e.t_ms, f"queue:{e.queue_id}", {"queueId": e.queue_id, "trackId": trk}))
                self._lengths[e.queue_id] += 1
                touched.add(e.queue_id)
            elif e.kind == "queue.left":
                out.append(self._event(e.kind, e.t_ms, f"queue:{e.queue_id}",
                                       {"queueId": e.queue_id, "trackId": trk, "served": bool(e.served)}))
                self._lengths[e.queue_id] = max(0, self._lengths[e.queue_id] - 1)
                touched.add(e.queue_id)
            else:
                raise ValueError(f"unknown zone event {e.kind}")
        if t_cur is not None:
            out += self._flush_lengths(t_cur, touched)
        return out

    def advance(self, t_ms: int) -> list[dict]:
        """Clock ticks due up to `t_ms`, for a live stream where nobody may move for a while."""
        return self._ticks_until(t_ms)

    def end(self, t_ms: int, *, trailing_ms: int = 0) -> list[dict]:
        """Ticks up to the end of the stream (plus `trailing_ms`, so rule windows can close)."""
        return self._ticks_until(t_ms + trailing_ms)


def write_jsonl(path: str | Path, events: Iterable[dict]) -> int:
    n = 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n")
            n += 1
    return n


def contract_validator(schemas_dir: str | Path):
    """A function returning schema errors for one event, using the repository's `schemas/` contract."""
    import jsonschema
    from referencing import Registry, Resource

    root = Path(schemas_dir)
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    envelope = json.loads((root / "envelope.schema.json").read_text(encoding="utf-8"))
    env_v = jsonschema.Draft202012Validator(envelope)
    by_schema: dict[str, jsonschema.Draft202012Validator] = {}
    subjects: dict[str, str] = {}
    for t in catalog["types"]:
        subjects[t["type"]] = t["subjectPattern"]
        for v in t["versions"]:
            doc = json.loads((root / v["file"]).read_text(encoding="utf-8"))
            by_schema[v["dataschema"]] = jsonschema.Draft202012Validator(doc, registry=Registry().with_resource(
                v["dataschema"], Resource.from_contents(doc)))

    import re

    def check(event: dict) -> list[str]:
        errs = [f"envelope: {e.message}" for e in env_v.iter_errors(event)]
        v = by_schema.get(event.get("dataschema", ""))
        if v is None:
            errs.append(f"unknown dataschema {event.get('dataschema')}")
        else:
            errs += [f"data: {e.message}" for e in v.iter_errors(event.get("data"))]
        pat = subjects.get(event.get("type", ""))
        if pat and not re.fullmatch(pat, event.get("subject", "")):
            errs.append(f"subject {event.get('subject')!r} does not match {pat}")
        return errs

    return check


def schemas_dir_default() -> Path:
    """`schemas/` of the repository this package was installed from (editable installs and CI)."""
    here = Path(str(resources.files("pqt_vision")))
    for parent in here.parents:
        if (parent / "schemas" / "catalog.json").is_file():
            return parent / "schemas"
    raise FileNotFoundError("schemas/ not found; pass --schemas")
