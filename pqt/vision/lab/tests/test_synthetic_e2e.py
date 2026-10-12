"""The V8 synthetic queue and its expected incidents, offline (the live run is `make e2e-vision`)."""
import io
import json
import urllib.error
from pathlib import Path

import pytest

from pqt_vision import camera as cam
from pqt_vision.emitter import contract_validator
from pqt_vision.pipeline import cloud_events, zone_events
from pqt_vision.synthetic import DEFAULT, Scenario, camera_doc, expected_queue_incidents, tracks
from pqt_vision.uplink import OAuthClient, Uplink, UplinkError

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"
RULE = dict(threshold=6, sustain_ms=60_000, hysteresis=2, clear_sustain_ms=30_000)


def test_true_lengths_rise_to_eight_and_drain():
    lengths = DEFAULT.true_lengths()
    assert max(n for _, n in lengths) == 8 and lengths[-1][1] == 0
    assert (30_000, 6) in lengths and (165_000, 4) in lengths


def test_expected_incidents_follow_the_rule_spec():
    inc = expected_queue_incidents(DEFAULT.true_lengths(), end_ms=300_000, **RULE)
    assert inc == [{"ruleId": "R-QUEUE-001", "onsetMs": 30_000, "detectedMs": 90_000, "resolvedMs": 195_000}]
    short = Scenario(arrivals_s=[1, 2, 3, 4, 5, 6], departures_s=[40, 41, 42, 43, 44, 45], end_s=60)
    assert expected_queue_incidents(short.true_lengths(), end_ms=60_000, **RULE) == []  # breach only ~34 s


def test_vision_reproduces_the_true_queue_length_within_a_few_seconds():
    camera = cam.from_doc(camera_doc(store="store-001", camera="cam-vision-e2e", queue_id="q-vis-test"))
    trk, times = tracks(DEFAULT)
    events, _ = zone_events(camera, trk, times)
    joined = [e.t_ms for e in events if e.kind == "queue.joined"]
    left = [e for e in events if e.kind == "queue.left"]
    assert len(joined) == 8 and len(left) == 8 and all(e.served for e in left)
    for true_t, t in zip([int(a * 1000) for a in DEFAULT.arrivals_s], joined):
        assert 0 <= t - true_t <= 5_000  # min dwell 3 s after the back-dated entry
    ces = cloud_events(camera, trk, times, start_ms=1_791_000_000_000, run_key="t", secret=b"x" * 32, trailing_ms=60_000)
    check = contract_validator(SCHEMAS)
    assert [err for e in ces for err in check(e)] == []
    lengths = [(e["time"], e["data"]["length"]) for e in ces if e["type"].endswith("queue.length")]
    assert max(n for _, n in lengths) == 8 and lengths[-1][1] == 0


class FakeHTTP:
    """Scripted responses for urlopen: tokens, then batch replies (status codes or result lists)."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, req, timeout):
        self.calls.append(req.full_url)
        if req.full_url.endswith("/token"):
            return io.BytesIO(json.dumps({"access_token": "tok", "expires_in": 300}).encode())
        r = self.replies.pop(0)
        if isinstance(r, int):
            raise urllib.error.HTTPError(req.full_url, r, "x", {"Retry-After": "0"}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps({"results": r}).encode())


def test_uplink_retries_429_and_5xx_then_counts_statuses(monkeypatch):
    fake = FakeHTTP([429, 503, [{"status": "accepted"}, {"status": "duplicate"}]])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    up = Uplink("http://gw/v1/events", OAuthClient("http://gw/token", "edge-demo", "s"), sleep=lambda s: None)
    assert up.send([{"id": "a"}, {"id": "b"}]) == {"accepted": 1, "duplicate": 1}
    assert sum(1 for c in fake.calls if c.endswith("/batch")) == 3


def test_uplink_refreshes_the_token_once_on_401_and_fails_fast_on_400(monkeypatch):
    fake = FakeHTTP([401, [{"status": "accepted"}]])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    up = Uplink("http://gw/v1/events", OAuthClient("http://gw/token", "edge-demo", "s"), sleep=lambda s: None)
    assert up.send([{"id": "a"}]) == {"accepted": 1}
    assert sum(1 for c in fake.calls if c.endswith("/token")) == 2
    monkeypatch.setattr("urllib.request.urlopen", FakeHTTP([400]))
    with pytest.raises(UplinkError):
        Uplink("http://gw/v1/events", OAuthClient("http://gw/token", "e", "s"), sleep=lambda s: None).send([{"id": "a"}])
    assert "secret-value" not in repr(OAuthClient("http://gw/token", "edge-demo", "secret-value"))
