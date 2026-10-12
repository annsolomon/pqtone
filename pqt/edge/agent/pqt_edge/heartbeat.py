"""Agent heartbeat (A5): `com.pqt.edge.heartbeat` every 30 s, through the same outbox as every other event.

Alarms (schemas/data/edge.heartbeat-1.0.0.schema.json):
- clock_offset: |agent clock − server clock| > 2 s. Event times are never rewritten: an offset is reported,
  so a person can fix NTP, rather than silently corrected.
- outbox_full: a camera's perception is paused because unsent events fill the quota.
- outbox_rejected: event-core refused at least one event (a bug or a contract mismatch: look at it).
- camera_down: a camera has delivered no frame for 30 s.
- disk_low: less than 10% or less than 1 GiB free where the outbox lives.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

CLOCK_ALARM_MS = 2000
CAMERA_DOWN_MS = 30_000
NS = uuid.UUID("0b8b3a5e-6f43-4b8f-a3d4-5c1e2f7a9d61")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def temperature_c() -> float | None:
    for p in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            return round(int(p.read_text().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            continue
    return None


def alarms(*, now_ms: int, cameras: list[dict], outbox: dict, disk_free: int | None, disk_total: int | None,
           clock_offset_ms: int | None) -> list[str]:
    out = []
    if clock_offset_ms is not None and abs(clock_offset_ms) > CLOCK_ALARM_MS:
        out.append("clock_offset")
    if any(c.get("paused") for c in cameras):
        out.append("outbox_full")
    if outbox.get("rejected", 0) > 0:
        out.append("outbox_rejected")
    if any(c.get("lastFrameMs") is None or now_ms - c["lastFrameMs"] > CAMERA_DOWN_MS for c in cameras):
        out.append("camera_down")
    if disk_free is not None and disk_total and (disk_free < 1 << 30 or disk_free / disk_total < 0.10):
        out.append("disk_low")
    return out


def event(*, agent_id: str, store: str, session: str, seq: int, now_ms: int, started_ms: int, cameras: list[dict],
          outbox: dict, state_dir: Path, clock_offset_ms: int | None) -> dict:
    try:
        du = shutil.disk_usage(state_dir)
        free, total = du.free, du.total
    except OSError:
        free = total = None
    cams = [{"cameraId": c["id"], "connected": bool(c["connected"]), "fps": float(c["fps"]), "frames": int(c["frames"]),
             "readErrors": int(c["readErrors"]), "reconnects": int(c["reconnects"]), "paused": bool(c.get("paused", False))}
            for c in cameras]
    data = {"agentId": agent_id, "agentVersion": __version__, "uptimeSeconds": max(0, (now_ms - started_ms) // 1000),
            "cameras": cams,
            "outbox": {k: int(outbox[k]) for k in ("pending", "rejected", "bytes", "quotaBytes", "oldestPendingAgeMs")},
            "alarms": alarms(now_ms=now_ms, cameras=cameras, outbox=outbox, disk_free=free, disk_total=total,
                             clock_offset_ms=clock_offset_ms)}
    if free is not None:
        data["diskFreeBytes"] = int(free)
    t = temperature_c()
    if t is not None:
        data["temperatureC"] = t
    if clock_offset_ms is not None:
        data["clockOffsetMs"] = int(clock_offset_ms)
    source = f"urn:pqt:edge:{store}:{agent_id}"
    return {
        "specversion": "1.0", "id": str(uuid.uuid5(NS, f"{source}:{session}:{seq}")), "source": source,
        "type": "com.pqt.edge.heartbeat", "time": _iso(now_ms), "subject": f"edge:{agent_id}",
        "dataschema": "https://schemas.pqt.local/edge/heartbeat/1.0.0", "datacontenttype": "application/json",
        "storeid": store, "partitionkey": store, "data": data,
    }
