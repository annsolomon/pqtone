"""V7 part 2: settings tuned on a development set (synthetic MOT20-shaped clips, fake detector)."""
import json
from pathlib import Path

import pytest

from pqt_vision import cache, camera, cli, tune
from pqt_vision.counts import gt_counts, score_occupancy, write_counts
from pqt_vision.mot import load_gt
from pqt_vision.pipeline import cloud_events
from pqt_vision.tune import BASELINE, Setting, choose, grid, load_setting
from pqt_vision.types import Box, Frame

from vision_synth import write_mot_sequence

W, H, FPS, FRAMES = 320, 180, 10, 80


def queue_rows(frames: int = FRAMES) -> list[str]:
    """Synthetic: four people standing in a row (feet at y = 100) and one walker crossing near the top."""
    rows = []
    for f in range(1, frames + 1):
        for k in range(4):
            rows.append(f"{f},{k + 1},{140 + 25 * k},60,10,40,1,1,1.0")
        rows.append(f"{f},9,{20 + 3 * f},-10,10,40,1,1,1.0")
    return rows


def lonely_rows(frames: int = FRAMES) -> list[str]:
    return [f"{f},1,{150},60,10,40,1,1,1.0" for f in range(1, frames + 1)]


class GTDetector:
    """Returns the ground-truth people of the synthetic clips, in the coordinates of the crop it is given
    (`offset` is set by the zone-crop wrapper below, so the real crop-and-shift code is exercised)."""

    name = "gt"

    def __init__(self, rows: dict[int, list[Box]]) -> None:
        self.rows, self.offset, self.shapes = rows, (0, 0), set()

    def detect(self, frame: Frame) -> list[Box]:
        assert frame.image is not None
        self.shapes.add(frame.image.shape[:2])
        ox, oy = self.offset
        return [Box(b.x1 - ox, b.y1 - oy, b.x2 - ox, b.y2 - oy, b.score) for b in self.rows.get(frame.index, [])]

    def describe(self) -> dict:
        return {"backend": "fake", "model": "gt", "sha256": "0" * 64, "input": [W, H]}


@pytest.fixture(autouse=True)
def crop_aware(monkeypatch):
    real = tune.RoiDetector

    def wrap(base, region):
        base.offset = (region.x1, region.y1)
        return real(base, region)

    monkeypatch.setattr(tune, "RoiDetector", wrap)


def boxes_by_frame(rows: list[str]) -> dict[int, list[Box]]:
    out: dict[int, list[Box]] = {}
    for r in rows:
        f, _, x, y, w, h, *_ = r.split(",")
        out.setdefault(int(f), []).append(Box(float(x), float(y), float(x) + float(w), float(y) + float(h), 0.9))
    return out


# -- the setting ---------------------------------------------------------------------------------

def test_the_grid_has_108_distinct_settings_and_contains_the_part_1_configuration():
    g = grid()
    assert len(g) == 108 and len(set(g)) == 108
    assert BASELINE in g
    assert BASELINE.as_dict() == {"trackThresh": 0.5, "bufferFrames": 30, "enterSeconds": 0.5, "exitSeconds": 0.5,
                                  "lostAfterSeconds": 2.0}


def test_a_setting_round_trips_and_rejects_unknown_or_missing_keys_and_bad_ranges():
    s = Setting(0.4, 60, 0.3, 1.0, 1.0)
    assert Setting.from_dict(s.as_dict()) == s
    with pytest.raises(ValueError):
        Setting.from_dict({**s.as_dict(), "matchThresh": 0.8})
    with pytest.raises(ValueError):
        Setting.from_dict({k: v for k, v in s.as_dict().items() if k != "bufferFrames"})
    with pytest.raises(ValueError):
        Setting(track_thresh=0.95)
    with pytest.raises(ValueError):
        Setting(enter_s=-1)


def test_the_tracker_and_the_camera_get_only_the_tuned_knobs():
    s = Setting(0.4, 60, 0.3, 1.0, 1.0)
    t = s.tracker()
    assert (t.track_thresh, t.new_track_thresh, t.buffer_frames) == (0.4, 0.5, 60)
    assert t.match_thresh == 0.8 and t.low_thresh == 0.1  # everything else stays at the V2 defaults
    cam = camera.from_doc({"apiVersion": "camera.pqt/v1", "camera": "c", "store": "s", "units": "px",
                           "image": {"width": W, "height": H}, "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                           "zones": [{"id": "zone-1", "kind": "zone", "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]}],
                           "membership": {"enterSeconds": 0.5, "exitSeconds": 0.5, "boundaryMargin": 3}})
    tuned = s.apply(cam)
    assert (tuned.membership.enter_s, tuned.membership.exit_s, tuned.membership.margin) == (0.3, 1.0, 3)
    assert tuned.zones == cam.zones and cam.membership.enter_s == 0.5  # the original is untouched


def test_choose_prefers_clips_passing_then_within_then_mae_then_grid_order():
    r = [{"passing": 1, "meanWithin1": 0.99, "meanMae": 0.1},
         {"passing": 2, "meanWithin1": 0.90, "meanMae": 0.6},
         {"passing": 2, "meanWithin1": 0.95, "meanMae": 0.5},
         {"passing": 2, "meanWithin1": 0.95, "meanMae": 0.4},
         {"passing": 2, "meanWithin1": 0.95, "meanMae": 0.4}]
    assert choose(r) == 3


def test_load_setting_needs_the_setting_document(tmp_path: Path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"kind": "pqt.vision-setting/v1", "setting": BASELINE.as_dict()}))
    assert load_setting(p) == BASELINE
    p.write_text(json.dumps({"kind": "pqt.vision-tune/v1", "best": {"setting": BASELINE.as_dict()}}))
    with pytest.raises(ValueError):
        load_setting(p)


# -- the shared count path -----------------------------------------------------------------------

def test_in_memory_scoring_equals_scoring_the_written_csv(tmp_path: Path):
    from pqt_scorer import video

    from pqt_vision.counts import COUNT_START
    from pqt_vision.emitter import parse_iso

    seq = write_mot_sequence(tmp_path, name="MOT20-01", frames=FRAMES, fps=FPS, w=W, h=H, gt_rows=queue_rows())
    gt = load_gt(seq)
    cam = camera.from_doc({"apiVersion": "camera.pqt/v1", "camera": "c", "store": "s", "units": "px",
                           "image": {"width": W, "height": H}, "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                           "zones": [{"id": "zone-1", "kind": "zone", "polygon": [[120, 80], [260, 80], [260, 120], [120, 120]]}]})
    tracks = {f: [(i, b) for i, b in enumerate(bs[:3])] for f, bs in boxes_by_frame(queue_rows()).items()}
    times = {f: (f - 1) * 100 for f in tracks}
    events = cloud_events(cam, tracks, times, start_ms=parse_iso(COUNT_START), run_key="t", secret=b"k" * 32)
    rows = gt_counts(gt, cam, fps=FPS)
    write_counts(tmp_path / "c.csv", rows)
    via_csv = video.score(video.read_gt(tmp_path / "c.csv"), events, metric="occupancy", start_ms=parse_iso(COUNT_START))
    assert score_occupancy(rows, events) == via_csv
    assert via_csv["zones"]["zone-1"]["bias"] < 0  # three of the four people tracked: an undercount, as built


def test_the_lost_track_timeout_reaches_the_zone_engine():
    cam = camera.from_doc({"apiVersion": "camera.pqt/v1", "camera": "c", "store": "s", "units": "px",
                           "image": {"width": W, "height": H}, "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                           "zones": [{"id": "zone-1", "kind": "zone", "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]]}],
                           "membership": {"enterSeconds": 0.2, "exitSeconds": 0.2}})
    box = Box(40, 10, 50, 50, 0.9)
    # Seen for 3 s, gone for 1.5 s, back for 3 s (10 fps).
    tracks = {f: ([(7, box)] if not 30 <= f < 45 else []) for f in range(75)}
    times = {f: f * 100 for f in tracks}

    def entries(lost: float) -> int:
        ev = cloud_events(cam, tracks, times, start_ms=0, run_key="t", secret=b"k" * 32, lost_after_s=lost)
        return sum(1 for e in ev if e["type"].endswith("zone.entered"))

    assert entries(1.0) == 2  # closed while gone, entered again
    assert entries(2.0) == 1  # one continuous visit


# -- end to end ----------------------------------------------------------------------------------

def test_tune_writes_a_choice_from_dev_clips_and_excludes_clips_that_are_not_queue_like(tmp_path: Path):
    write_mot_sequence(tmp_path / "train", name="MOT20-01", frames=FRAMES, fps=FPS, w=W, h=H, gt_rows=queue_rows())
    write_mot_sequence(tmp_path / "train", name="MOT20-02", frames=FRAMES, fps=FPS, w=W, h=H, gt_rows=lonely_rows())
    det = GTDetector(boxes_by_frame(queue_rows()))
    few = [BASELINE, Setting(0.4, 60, 0.3, 1.0, 1.0), Setting(0.6, 30, 1.0, 0.3, 2.0)]
    doc = tune.tune(tmp_path / "train", model="fake", out=tmp_path / "out", sequences=("MOT20-01", "MOT20-02"),
                    max_frames=None, detector=det, settings=few)
    assert doc["devSet"]["sequences"] == ["MOT20-01"]
    assert "not queue-like" in doc["devSet"]["excluded"]["MOT20-02"]
    assert doc["devSet"]["zones"]["MOT20-01"]["score"] >= 0.8
    assert len(doc["grid"]) == 3 and doc["baseline"]["setting"] == BASELINE.as_dict()
    assert doc["best"] == doc["grid"][choose(doc["grid"])]
    s = doc["best"]["sequences"]["MOT20-01"]
    assert s["n"] == FRAMES // FPS and s["within1"] == 1.0  # a perfect detector counts the queue
    # Only aggregates and zone geometry are written: no boxes or tracks.
    text = (tmp_path / "out" / "tuned.json").read_text()
    assert "x1" not in text and "boxes" not in text and "CC BY-NC-SA 3.0" in text
    assert load_setting(tmp_path / "out" / "setting.json") == Setting.from_dict(doc["best"]["setting"])
    md = (tmp_path / "out" / "tuned.md").read_text()
    assert "chosen" in md and "baseline" in md and "Excluded MOT20-02" in md
    cfgs = list((tmp_path / "out" / "dets").iterdir())
    assert [p.name for p in cfgs] == ["MOT20-01"]  # the excluded clip was never run through the detector
    assert det.shapes and all(shape != (H, W) for shape in det.shapes)  # the detector saw the zone crop only


def test_tune_refuses_when_no_clip_is_usable(tmp_path: Path):
    write_mot_sequence(tmp_path / "train", name="MOT20-02", frames=FRAMES, fps=FPS, w=W, h=H, gt_rows=lonely_rows())
    with pytest.raises(RuntimeError, match="no usable development clip"):
        tune.tune(tmp_path / "train", model="fake", out=tmp_path / "out", sequences=("MOT20-02",), max_frames=None,
                  detector=GTDetector({}), settings=[BASELINE])


def test_max_frames_limits_ground_truth_and_detection(tmp_path: Path):
    write_mot_sequence(tmp_path / "train", name="MOT20-01", frames=FRAMES, fps=FPS, w=W, h=H, gt_rows=queue_rows())
    det = GTDetector(boxes_by_frame(queue_rows()))
    doc = tune.tune(tmp_path / "train", model="fake", out=tmp_path / "out", sequences=("MOT20-01",), max_frames=40,
                    detector=det, settings=[BASELINE])
    assert doc["devSet"]["maxFrames"] == 40
    assert doc["best"]["sequences"]["MOT20-01"]["n"] == 4
    meta, _ = cache.read(tmp_path / "out" / "dets" / "MOT20-01")
    assert len(meta["frames"]) == 40


def test_cli_tune_passes_its_arguments(tmp_path: Path, monkeypatch):
    seen = {}

    def fake_tune(root, **kw):
        seen.update(root=root, **kw)
        Path(kw["out"]).mkdir(parents=True, exist_ok=True)
        (Path(kw["out"]) / "tuned.md").write_text("# tuned\n")
        return {"best": {"setting": BASELINE.as_dict()}}

    monkeypatch.setattr(tune, "tune", fake_tune)
    rc = cli.main(["tune", "--root", str(tmp_path), "--out", str(tmp_path / "o"), "--sequences", "MOT20-01",
                   "--max-frames", "0"])
    assert rc == 0 and seen["sequences"] == ("MOT20-01",) and seen["max_frames"] is None and seen["model"] == "yolox_s"
