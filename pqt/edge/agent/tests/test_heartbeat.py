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


# -- X1 B5f: billing connector health in the heartbeat (edge.heartbeat 1.1.0) ---------------------------------------

def _status(path, sources, updated_ms=NOW):
    from datetime import datetime, timezone
    doc = {"updatedAt": datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "store": "store-001", "sources": sources}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _src(i, ok=True, failures=0, stale=False, last="2026-10-01T00:00:00Z"):
    return {"id": i, "type": "sql", "ok": ok, "stale": stale, "failures": failures, "intervalSeconds": 60,
            "lastAttemptAt": last, "lastSuccessAt": last if ok else None, "lastError": None if ok else "unreachable",
            "events": 12, "rowErrors": 0}


def test_connector_health_rides_in_the_heartbeat_as_1_1_0(tmp_path):
    st = _status(tmp_path / "connectors.json", [_src("shop-db"), _src("push")])
    hb = heartbeat.event(agent_id="edge-001", store="store-001", session="s1", seq=1, now_ms=NOW, started_ms=NOW - 1000,
                         cameras=[CAM], outbox=OUTBOX, state_dir=tmp_path, clock_offset_ms=0, connector_status=st)
    assert hb["dataschema"].endswith("/edge/heartbeat/1.1.0")
    assert contract_validator(SCHEMAS)(hb) == []
    conns = hb["data"]["connectors"]
    assert [c["id"] for c in conns] == ["shop-db", "push"] and all(c["ok"] for c in conns)
    assert "lastError" not in conns[0], "errors stay on the box: the heartbeat says ok or not, failures and age"
    assert "connector_failing" not in hb["data"]["alarms"]


def test_a_failing_or_stale_connector_raises_an_alarm(tmp_path):
    for sources in ([_src("shop-db", ok=False, failures=3)], [_src("shop-db", stale=True)]):
        st = _status(tmp_path / "connectors.json", sources)
        hb = heartbeat.event(agent_id="edge-001", store="store-001", session="s1", seq=1, now_ms=NOW, started_ms=NOW,
                             cameras=[CAM], outbox=OUTBOX, state_dir=tmp_path, clock_offset_ms=0, connector_status=st)
        assert "connector_failing" in hb["data"]["alarms"]
    one_blip = _status(tmp_path / "connectors.json", [_src("shop-db", ok=False, failures=1)])
    hb = heartbeat.event(agent_id="edge-001", store="store-001", session="s1", seq=1, now_ms=NOW, started_ms=NOW,
                         cameras=[CAM], outbox=OUTBOX, state_dir=tmp_path, clock_offset_ms=0, connector_status=one_blip)
    assert "connector_failing" not in hb["data"]["alarms"], "one failed poll is not an alarm yet"


def test_a_connector_service_that_stopped_writing_is_reported(tmp_path):
    st = _status(tmp_path / "connectors.json", [_src("shop-db")], updated_ms=NOW - 11 * 60_000)
    hb = heartbeat.event(agent_id="edge-001", store="store-001", session="s1", seq=1, now_ms=NOW, started_ms=NOW,
                         cameras=[CAM], outbox=OUTBOX, state_dir=tmp_path, clock_offset_ms=0, connector_status=st)
    assert "connector_failing" in hb["data"]["alarms"] and hb["data"]["connectors"][0]["stale"] is True


def test_without_connectors_the_heartbeat_stays_1_0_0(tmp_path):
    hb = heartbeat.event(agent_id="edge-001", store="store-001", session="s1", seq=1, now_ms=NOW, started_ms=NOW,
                         cameras=[CAM], outbox=OUTBOX, state_dir=tmp_path, clock_offset_ms=0,
                         connector_status=tmp_path / "missing.json")
    assert hb["dataschema"].endswith("/1.0.0") and "connectors" not in hb["data"]
    assert contract_validator(SCHEMAS)(hb) == []
