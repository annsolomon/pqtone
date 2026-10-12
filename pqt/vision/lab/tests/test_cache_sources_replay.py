from pathlib import Path

import cv2
import numpy as np
import pytest

from pqt_vision import cache
from pqt_vision.detect import MissingFrame, ReplayDetector
from pqt_vision.runner import run_detection
from pqt_vision.sources import SourceError, open_source
from pqt_vision.types import Frame

from vision_synth import FakeDetector


def test_mot_sequence_source_uses_file_numbers_and_seqinfo(mot_seq: Path):
    src = open_source(mot_seq)
    assert src.info.kind == "mot-sequence" and src.info.fps == 10 and src.info.frames == 12
    frames = list(src.frames())
    assert [f.index for f in frames] == list(range(1, 13))
    assert [f.t_ms for f in frames[:3]] == [0, 100, 200]
    assert frames[0].image.shape == (48, 64, 3)
    assert [f.index for f in src.frames(stride=4)] == [1, 5, 9]
    assert [f.index for f in src.frames(max_frames=2)] == [1, 2]


def test_skipped_frames_are_not_decoded(mot_seq: Path):
    (mot_seq / "img1" / "000003.jpg").write_bytes(b"not a jpeg")
    src = open_source(mot_seq)
    with pytest.raises(SourceError):
        list(src.frames())
    assert 3 not in [f.index for f in src.frames(skip=lambda i: i == 3)]


def test_video_file_source(tmp_path: Path):
    path = tmp_path / "syn.avi"
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (32, 24))
    assert vw.isOpened()
    for i in range(8):
        vw.write(np.full((24, 32, 3), i * 20, dtype=np.uint8))
    vw.release()
    src = open_source(path)
    assert src.info.kind == "video" and src.info.fps == pytest.approx(10)
    got = list(src.frames(skip=lambda i: i in (2, 3)))
    assert [f.index for f in got] == [0, 1, 4, 5, 6, 7]
    assert [f.t_ms for f in got][:3] == [0, 100, 400]


def test_open_source_errors(tmp_path: Path):
    with pytest.raises(SourceError):
        open_source(tmp_path)  # a plain directory needs --fps
    with pytest.raises(SourceError):
        open_source(tmp_path / "missing.mp4")


def test_detect_cache_replay_round_trip(mot_seq: Path, tmp_path: Path):
    out = tmp_path / "dets"
    det = FakeDetector()
    stats = run_detection(open_source(mot_seq), det, out, chunk=5)
    assert stats.frames == 12 and stats.skipped == 0
    assert sorted(p.name for p in out.glob("*.parquet")) == [
        "part-0000001-0000006.parquet", "part-0000006-0000011.parquet", "part-0000011-0000013.parquet"]
    meta = cache.read_meta(out)
    assert meta["detector"] == det.describe() and meta["source"]["name"] == "SYN-01"
    assert meta["frames"] == list(range(1, 13))
    replay = ReplayDetector(out)
    for i in range(1, 13):
        expected = FakeDetector().detect(Frame(i, 0, np.zeros((1, 1, 3))))
        got = replay.detect(Frame(i, 0))  # no pixels needed
        assert [(b.x1, b.y2, round(b.score, 3)) for b in got] == [(b.x1, b.y2, b.score) for b in expected]
    assert replay.detect(Frame(3, 0)) == []  # covered, no people
    with pytest.raises(MissingFrame):
        replay.detect(Frame(99, 0))
    assert ReplayDetector(out, min_score=0.85).detect(Frame(5, 0))[0].score >= 0.85


def test_interrupted_run_resumes_without_redoing_frames(mot_seq: Path, tmp_path: Path):
    out = tmp_path / "dets"
    first = FakeDetector()
    run_detection(open_source(mot_seq), first, out, max_frames=7, chunk=5)
    assert cache.read_meta(out)["frames"] == list(range(1, 8))
    second = FakeDetector()
    stats = run_detection(open_source(mot_seq), second, out, chunk=5)
    assert second.calls == list(range(8, 13)) and stats.skipped == 7
    assert cache.read_meta(out)["frames"] == list(range(1, 13))


def test_a_cache_never_mixes_detector_configurations(mot_seq: Path, tmp_path: Path):
    out = tmp_path / "dets"
    run_detection(open_source(mot_seq), FakeDetector("a"), out, max_frames=3)
    with pytest.raises(cache.CacheError):
        run_detection(open_source(mot_seq), FakeDetector("b"), out)


def test_no_temporary_files_are_left_behind(mot_seq: Path, tmp_path: Path):
    out = tmp_path / "dets"
    run_detection(open_source(mot_seq), FakeDetector(), out, chunk=4)
    assert not [p for p in out.iterdir() if p.name.startswith(".")]
