"""Count mode (V6) on hand-made event streams."""
import json
from pathlib import Path

import pytest

from pqt_scorer import cli, video

T0 = "2026-10-12T09:00:"


def ev(sec: float, kind: str, data: dict, seq: int) -> dict:
    whole, frac = int(sec), int(round((sec - int(sec)) * 1000))
    return {"time": f"{T0}{whole:02d}.{frac:03d}Z", "type": f"com.pqt.store.{kind}", "data": data, "sequence": f"{seq:010d}"}


EVENTS = [
    ev(0, "clock.tick", {"seq": 0}, 1),
    ev(0, "queue.length", {"queueId": "q1", "length": 0, "openRegisters": 1}, 2),
    ev(2, "zone.entered", {"zoneId": "z1", "trackId": "trk-a"}, 3),
    ev(3, "zone.entered", {"zoneId": "z1", "trackId": "trk-b"}, 4),
    ev(5, "queue.length", {"queueId": "q1", "length": 2, "openRegisters": 1}, 5),
    ev(7.5, "zone.exited", {"zoneId": "z1", "trackId": "trk-a"}, 6),
    ev(8, "queue.length", {"queueId": "q1", "length": 1, "openRegisters": 1}, 7),
]


def write_gt(tmp_path: Path, rows: list[tuple]) -> Path:
    p = tmp_path / "counts.csv"
    p.write_text("offset_s,zone,count\n" + "".join(f"{a},{b},{c}\n" for a, b, c in rows), encoding="utf-8")
    return p


def test_occupancy_counts_follow_enter_and_exit():
    tl = video.timelines(EVENTS, "occupancy")["z1"]
    start = video.parse_time_ms(f"{T0}00.000Z")
    assert [video.count_at(tl, start + s * 1000) for s in (0, 2, 3, 7, 8, 20)] == [0, 1, 2, 2, 1, 1]
    assert video.count_at(None, start) == 0


def test_queue_metric_uses_queue_length_events(tmp_path: Path):
    gt = video.read_gt(write_gt(tmp_path, [(0, "q1", 0), (5, "q1", 2), (6, "q1", 3), (9, "q1", 1)]))
    r = video.score(gt, EVENTS, metric="queue")
    q = r["zones"]["q1"]
    assert q["n"] == 4 and q["mae"] == pytest.approx(0.25) and q["within1"] == 1.0 and q["bias"] == pytest.approx(-0.25)
    assert r["pass"] is True


def test_the_gate_fails_on_large_errors(tmp_path: Path):
    gt = video.read_gt(write_gt(tmp_path, [(0, "z1", 0), (4, "z1", 5), (9, "z1", 4)]))
    r = video.score(gt, EVENTS, metric="occupancy")
    z = r["zones"]["z1"]
    assert z["within1"] == pytest.approx(1 / 3, abs=1e-4) and z["maxAbsError"] == 3 and not z["pass"] and not r["pass"]
    assert "FAIL" in video.render(r)


def test_explicit_start_shifts_samples(tmp_path: Path):
    gt = video.read_gt(write_gt(tmp_path, [(0, "z1", 2)]))
    r = video.score(gt, EVENTS, metric="occupancy", start_ms=video.parse_time_ms(f"{T0}04.000Z"))
    assert r["zones"]["z1"]["mae"] == 0


def test_bad_inputs(tmp_path: Path):
    bad = tmp_path / "bad.csv"
    bad.write_text("t,zone,count\n0,z1,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        video.read_gt(bad)
    with pytest.raises(ValueError):
        video.score([], [], metric="occupancy")
    with pytest.raises(ValueError):
        video.timelines(EVENTS, "dwell")


def test_cli_video(tmp_path: Path, capsys):
    events = tmp_path / "events.jsonl"
    events.write_text("".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")
    gt = write_gt(tmp_path, [(0, "q1", 0), (6, "q1", 2)])
    out = tmp_path / "r.json"
    assert cli.main(["video", "--gt", str(gt), "--events", str(events), "--json", str(out), "--gate"]) == 0
    assert "Within ±1" in capsys.readouterr().out and json.loads(out.read_text())["pass"] is True
    gt_bad = write_gt(tmp_path, [(0, "q1", 5)])
    assert cli.main(["video", "--gt", str(gt_bad), "--events", str(events), "--gate"]) == 1
    assert cli.main(["video", "--gt", str(gt_bad), "--events", str(events)]) == 0  # report only
