"""A5: heartbeat contents, alarms, and clock offset from the server's Date header (never rewriting time)."""
import email.utils
import io
import json
from pathlib import Path

from pqt_edge import heartbeat
from pqt_vision.emitter import contract_validator
from pqt_vision.uplink import OAuthClient, Uplink

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"
NOW = 1_791_000_000_000
CAM = {"id": "cam-01", "connected": True, "fps": 5.0, "frames": 900, "readErrors": 1, "reconnects": 0,
       "paused": False, "lastFrameMs": NOW - 200}
OUTBOX = {"pending": 3, "rejected": 0, "bytes": 4096, "quotaBytes": 1 << 29, "oldestPendingAgeMs": 1200, "sent": 10}


def test_heartbeat_is_a_valid_contract_event(tmp_path):
    hb = heartbeat.event(agent_id="edge-001", store="store-001", session="s1", seq=1, now_ms=NOW, started_ms=NOW - 61_000,
                         cameras=[CAM], outbox=OUTBOX, state_dir=tmp_path, clock_offset_ms=120)
    assert contract_validator(SCHEMAS)(hb) == []
    d = hb["data"]
    assert d["uptimeSeconds"] == 61 and d["clockOffsetMs"] == 120 and d["alarms"] == []
    assert hb["source"] == "urn:pqt:edge:store-001:edge-001" and hb["subject"] == "edge:edge-001"
    again = heartbeat.event(agent_id="edge-001", store="store-001", session="s1", seq=1, now_ms=NOW, started_ms=NOW,
                            cameras=[CAM], outbox=OUTBOX, state_dir=tmp_path, clock_offset_ms=None)
    assert again["id"] == hb["id"] and "clockOffsetMs" not in again["data"]  # same seq, same id: safe to resend


def test_alarms():
    a = lambda **k: heartbeat.alarms(**{"now_ms": NOW, "cameras": [CAM], "outbox": OUTBOX, "disk_free": 50 << 30,
                                        "disk_total": 100 << 30, "clock_offset_ms": 0, **k})
    assert a() == []
    assert a(clock_offset_ms=2500) == ["clock_offset"] and a(clock_offset_ms=-2500) == ["clock_offset"]
    assert a(clock_offset_ms=1500) == []
    assert a(cameras=[{**CAM, "paused": True}]) == ["outbox_full"]
    assert a(outbox={**OUTBOX, "rejected": 2}) == ["outbox_rejected"]
    assert a(cameras=[{**CAM, "lastFrameMs": NOW - 31_000}]) == ["camera_down"]
    assert a(cameras=[{**CAM, "lastFrameMs": None}]) == ["camera_down"]
    assert a(disk_free=500 << 20) == ["disk_low"]


class Resp(io.BytesIO):
    def __init__(self, body: bytes, date: str):
        super().__init__(body)
        self.headers = {"Date": date}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_clock_offset_from_the_date_header(monkeypatch):
    server_now = NOW / 1000 - 5.0  # the server is 5 s behind us
    date = email.utils.formatdate(server_now, usegmt=True)

    def urlopen(req, timeout):
        if req.full_url.endswith("/token"):
            return Resp(json.dumps({"access_token": "t", "expires_in": 300}).encode(), date)
        return Resp(json.dumps({"results": [{"id": "a", "status": "accepted"}]}).encode(), date)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    up = Uplink("http://gw/v1/events", OAuthClient("http://gw/token", "edge", "s"), wall=lambda: NOW / 1000)
    ev = {"id": "a", "time": "2026-10-12T09:00:00.000Z"}
    up.send([ev])
    assert 4000 <= up.clock_offset_ms <= 5500
    assert ev["time"] == "2026-10-12T09:00:00.000Z"  # never rewritten
    assert "clock_offset" in heartbeat.alarms(now_ms=NOW, cameras=[CAM], outbox=OUTBOX, disk_free=None, disk_total=None,
                                              clock_offset_ms=up.clock_offset_ms)


def test_secrets_are_refused_when_readable_by_others_and_never_shown(tmp_path):
    import pytest
    from pqt_edge import config

    f = tmp_path / "s"
    f.write_text("rtsp://admin:hunter2@10.0.0.9/stream")
    f.chmod(0o644)
    with pytest.raises(config.ConfigError, match="chmod 600"):
        config.read_secret(f)
    f.chmod(0o600)
    spec = config.CameraSpec("cam-01", tmp_path / "c.yaml", url_file=f)
    assert spec.url().endswith("/stream") and "hunter2" not in repr(spec)
