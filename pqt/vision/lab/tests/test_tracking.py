"""ByteTrack and Kalman behaviour on synthetic tracks (no real footage)."""
import numpy as np
import pytest

from pqt_vision.track import ByteTrackConfig, ByteTracker, read_tracks, write_tracks
from pqt_vision.track import kalman
from pqt_vision.track.bytetrack import TrackOut
from pqt_vision.types import Box


def person(x: float, y: float, score: float = 0.9, w: float = 40, h: float = 100) -> Box:
    return Box(x, y, x + w, y + h, score)


def run(frames: list[list[Box]], cfg: ByteTrackConfig | None = None, fps: float = 30) -> list[list[TrackOut]]:
    t = ByteTracker(cfg, fps=fps)
    return [t.update(d, i + 1) for i, d in enumerate(frames)]


def ids(out: list[list[TrackOut]]) -> set[int]:
    return {t.track_id for f in out for t in f}


def test_kalman_learns_constant_velocity():
    mean, cov = kalman.initiate(kalman.xyxy_to_xyah(np.array([0, 0, 40, 100.0])))
    for k in range(1, 30):
        mean, cov = kalman.predict(mean, cov)
        mean, cov = kalman.update(mean, cov, kalman.xyxy_to_xyah(np.array([3.0 * k, 0, 3.0 * k + 40, 100])))
    assert mean[4] == pytest.approx(3.0, abs=0.1) and abs(mean[5]) < 0.1
    pred, _ = kalman.predict(mean, cov)
    assert kalman.xyah_to_xyxy(pred)[0] == pytest.approx(90.0, abs=1.0)


def test_xyah_round_trip():
    b = np.array([10, 20, 50, 120.0])
    assert kalman.xyah_to_xyxy(kalman.xyxy_to_xyah(b)) == pytest.approx(b)


def test_two_walkers_keep_two_ids():
    frames = [[person(100 + 3 * k, 50), person(600 - 3 * k, 300)] for k in range(60)]
    out = run(frames)
    assert ids(out) == {1, 2}
    assert all(len(f) == 2 for f in out)
    # the id that starts on the left stays on the left-hand walker throughout
    left = min(out[0], key=lambda t: t.box.x1).track_id
    assert all(min(f, key=lambda t: t.box.y1).track_id == left for f in out)


def test_short_occlusion_keeps_the_id():
    frames = [[person(100 + 2 * k, 50)] if not 20 <= k < 30 else [] for k in range(50)]
    out = run(frames)
    assert ids(out) == {1}
    assert out[25] == []  # not reported while hidden
    assert out[30][0].track_id == 1


def test_long_absence_starts_a_new_id_and_buffer_scales_with_fps():
    frames = [[person(100, 50)] if not 10 <= k < 60 else [] for k in range(70)]
    assert ids(run(frames, fps=30)) == {1, 2}   # 50 frames > 30-frame buffer
    assert ids(run(frames, fps=60)) == {1}      # at 60 fps the buffer is 60 frames


def test_low_score_detections_extend_a_track_but_never_start_one():
    frames = [[person(100 + 2 * k, 50, 0.9 if k < 10 or k >= 15 else 0.3)] for k in range(25)]
    out = run(frames)
    assert ids(out) == {1} and all(len(f) == 1 for f in out)  # reported through the low-score frames
    assert ids(run([[person(100, 50, 0.3)] for _ in range(20)])) == set()


def test_a_one_frame_false_alarm_is_never_reported():
    frames = [[person(100, 50)] for _ in range(10)]
    frames[5] = frames[5] + [person(700, 400, 0.95)]
    out = run(frames)
    assert ids(out) == {1}


def test_a_new_person_is_confirmed_on_their_second_frame():
    frames = [[person(100, 50)] for _ in range(10)]
    for k in range(5, 10):
        frames[k] = frames[k] + [person(700, 400)]
    out = run(frames)
    assert len(out[5]) == 1 and len(out[6]) == 2 and ids(out) == {1, 2}


def test_degenerate_outputs_are_filtered():
    out = run([[Box(0, 0, 200, 50, 0.9)] for _ in range(5)])  # 4:1 wide box: not a person
    assert ids(out) == set()


def test_tracks_file_round_trip(tmp_path):
    out = run([[person(100 + k, 50), person(400, 60)] for k in range(5)])
    tracks = {i + 1: f for i, f in enumerate(out)}
    tracks[6] = []
    write_tracks(tmp_path / "t.parquet", tracks, times={f: (f - 1) * 33 for f in tracks}, meta={"tracker": {"x": 1}})
    meta, back = read_tracks(tmp_path / "t.parquet")
    assert meta == {"tracker": {"x": 1}} and sorted(back) == [1, 2, 3, 4, 5, 6] and back[6] == []
    assert [tid for tid, _ in back[3]] == [t.track_id for t in tracks[3]]
    assert back[3][0][1].x1 == pytest.approx(tracks[3][0].box.x1, abs=1e-3)
