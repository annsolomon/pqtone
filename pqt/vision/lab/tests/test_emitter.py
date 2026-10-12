"""CloudEvents from synthetic zone activity, checked against the repository's schemas/ contract."""
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from pqt_vision import camera, cli
from pqt_vision.emitter import Emitter, TrackPseudonymiser, contract_validator, iso_ms, parse_iso
from pqt_vision.pipeline import cloud_events
from pqt_vision.track import write_tracks
from pqt_vision.track.bytetrack import TrackOut
from pqt_vision.types import Box
from pqt_vision.zones import ZoneEvent

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"
SECRET = b"synthetic-test-secret-0123456789"
START = parse_iso("2026-10-12T09:00:00Z")

CAM = camera.from_doc({
    "apiVersion": "camera.pqt/v1", "camera": "cam-01", "store": "store-001", "units": "px",
    "image": {"width": 1000, "height": 600}, "homography": np.eye(3).tolist(),
    "zones": [{"id": "checkout-1", "kind": "queue", "queueId": "q-main", "openRegisters": 2,
               "polygon": [[100, 100], [500, 100], [500, 500], [100, 500]]}],
    "membership": {"enterSeconds": 0.5, "exitSeconds": 0.5, "boundaryMargin": 5, "queueMinDwellSeconds": 3,
                   "queueMaxSpeed": 20, "speedWindowSeconds": 2},
})


def standing_tracks(fps: int = 10, seconds: int = 30):
    """Two people stand in the queue area for a while, one walks past outside it."""
    tracks, times = {}, {}
    for k in range(fps * seconds):
        f = k + 1
        times[f] = k * 1000 // fps
        obs = []
        if 20 <= k < 200:
            obs.append((1, Box(280, 200, 320, 300, 0.9)))   # foot (300, 300): inside
        if 50 <= k < 260:
            obs.append((2, Box(380, 250, 420, 350, 0.9)))   # foot (400, 350): inside
        obs.append((3, Box(700 + k, 300, 740 + k, 400, 0.9)))  # outside the zone, walking
        tracks[f] = obs
    return tracks, times


def test_every_event_satisfies_the_contract():
    tracks, times = standing_tracks()
    events = cloud_events(CAM, tracks, times, start_ms=START, run_key="clip-1", secret=SECRET, trailing_ms=20_000)
    check = contract_validator(SCHEMAS)
    assert [(e["type"], err) for e in events for err in check(e)] == []
    kinds = Counter(e["type"].removeprefix("com.pqt.store.") for e in events)
    assert kinds["zone.entered"] == 2 and kinds["zone.exited"] == 2
    assert kinds["queue.joined"] == 2 and kinds["queue.left"] == 2
    assert kinds["queue.length"] == 5  # initial 0, then +1, +1, -1, -1
    assert kinds["clock.tick"] == 5  # every 10 s from 0 to the last frame (29.9 s) + 20 s trailing: 0..40 s
    assert {e["source"] for e in events} == {"urn:pqt:edge:store-001:cam-01"}


def test_event_times_and_sequence_are_monotonic_and_ids_unique():
    tracks, times = standing_tracks()
    events = cloud_events(CAM, tracks, times, start_ms=START, run_key="clip-1", secret=SECRET)
    ts = [e["time"] for e in events]
    assert ts == sorted(ts)
    assert [int(e["sequence"]) for e in events] == list(range(1, len(events) + 1))
    assert len({e["id"] for e in events}) == len(events)
    assert events[0]["time"] == "2026-10-12T09:00:00.000Z"


def test_queue_length_follows_joins_and_leaves():
    tracks, times = standing_tracks()
    events = cloud_events(CAM, tracks, times, start_ms=START, run_key="clip-1", secret=SECRET)
    lengths = [e["data"]["length"] for e in events if e["type"].endswith("queue.length")]
    assert lengths == [0, 1, 2, 1, 0]
    assert all(e["data"]["openRegisters"] == 2 and e["data"]["queueId"] == "q-main"
               for e in events if e["type"].endswith("queue.length"))


def test_reprocessing_the_same_clip_gives_the_same_ids_but_another_clip_does_not():
    tracks, times = standing_tracks()
    a = cloud_events(CAM, tracks, times, start_ms=START, run_key="clip-1", secret=SECRET)
    b = cloud_events(CAM, tracks, times, start_ms=START, run_key="clip-1", secret=SECRET)
    c = cloud_events(CAM, tracks, times, start_ms=START, run_key="clip-2", secret=SECRET)
    assert [e["id"] for e in a] == [e["id"] for e in b]
    assert not {e["id"] for e in a} & {e["id"] for e in c}


def test_track_ids_are_pseudonymous_and_rotate_daily():
    p = TrackPseudonymiser(SECRET, "cam-01")
    day1 = parse_iso("2026-10-12T10:00:00Z")
    assert p.track_id(7, day1) == p.track_id(7, day1 + 3_600_000)           # same day: stable
    assert p.track_id(7, day1) != p.track_id(7, day1 + 86_400_000)          # next day: unrelated
    assert p.track_id(7, day1) != TrackPseudonymiser(SECRET, "cam-02").track_id(7, day1)  # other camera
    assert p.track_id(7, day1) != TrackPseudonymiser(b"another-secret-0123456789", "cam-01").track_id(7, day1)
    assert p.track_id(7, day1).startswith("trk-") and len(p.track_id(7, day1)) == 20
    assert "synthetic" not in repr(p)
    with pytest.raises(ValueError):
        TrackPseudonymiser(b"short", "cam-01")


def test_emit_rejects_unknown_kinds():
    em = Emitter(CAM, START, "k", TrackPseudonymiser(SECRET, "cam-01"))
    em.begin()
    with pytest.raises(ValueError):
        em.emit([ZoneEvent("zone.teleported", 0, "checkout-1", 1)])


def test_iso_helpers():
    assert iso_ms(START + 1234) == "2026-10-12T09:00:01.234Z"
    with pytest.raises(ValueError):
        parse_iso("2026-10-12T09:00:00")


def _tracks_file(tmp_path: Path) -> Path:
    tracks, times = standing_tracks()
    path = tmp_path / "tracks.parquet"
    write_tracks(path, {f: [TrackOut(tid, b) for tid, b in obs] for f, obs in tracks.items()}, times=times, meta={})
    return path


def test_cli_events_writes_valid_jsonl(tmp_path: Path, monkeypatch, capsys):
    cam_path = tmp_path / "cam.yaml"
    import yaml
    cam_path.write_text(yaml.safe_dump(CAM.doc), encoding="utf-8")
    monkeypatch.setenv("PQT_TRACK_SECRET", SECRET.decode())
    out = tmp_path / "events.jsonl"
    rc = cli.main(["events", "--tracks", str(_tracks_file(tmp_path)), "--camera", str(cam_path), "--out", str(out),
                   "--run-key", "clip-1", "--start", "2026-10-12T09:00:00Z", "--validate", "--schemas", str(SCHEMAS)])
    assert rc == 0
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert len(lines) == json.loads(capsys.readouterr().out)["events"]


def test_cli_events_requires_a_secret(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PQT_TRACK_SECRET", raising=False)
    cam_path = tmp_path / "cam.yaml"
    import yaml
    cam_path.write_text(yaml.safe_dump(CAM.doc), encoding="utf-8")
    assert cli.main(["events", "--tracks", str(_tracks_file(tmp_path)), "--camera", str(cam_path),
                     "--out", str(tmp_path / "e.jsonl"), "--run-key", "x"]) == 2
