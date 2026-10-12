"""N1 measurement on a synthetic sequence with planted errors."""
import numpy as np
import pytest

from pqt_vision import camera as cam
from pqt_vision.mot import build_gt, parse_gt
from pqt_vision.noise_measure import Samples, fit_miss_rate, measure_sequence, profile, thin_evidence
from pqt_vision.types import Box
from pqt_vision.zones import ZoneEvent

CAMERA = cam.from_doc({"apiVersion": "camera.pqt/v1", "camera": "syn", "store": "s", "units": "px",
                       "image": {"width": 400, "height": 300}, "homography": np.eye(3).tolist(),
                       "zones": [{"id": "z", "kind": "zone", "polygon": [[0, 0], [300, 0], [300, 300], [0, 300]]}]})
N = 100  # frames at 10 fps


def box(x):
    return Box(x, 100, x + 20, 200)


def planted():
    rows = []
    for f in range(1, N + 1):
        rows.append(f"{f},1,50,100,20,100,1,1,1.0")   # person A at x=50
        rows.append(f"{f},2,150,100,20,100,1,1,1.0")  # person B at x=150
    gt = build_gt(parse_gt("\n".join(rows)), sequence="SYN", frames=N)
    tracks, times = {}, {}
    for f in range(1, N + 1):
        times[f] = (f - 1) * 100
        obs = []
        if f <= 40:
            obs.append((1, box(50)))
        elif f > 50:
            obs.append((2, box(50)))           # A: lost for 10 frames, back as a new id (a fragment)
        obs.append((3 if f <= 60 else 4, box(150)))  # B: id changes between consecutive frames (a switch)
        if 20 <= f < 40:
            obs.append((9, box(250)))          # a phantom for 20 frames, matching nobody
        tracks[f] = obs
    return gt, tracks, times


def test_planted_errors_are_measured():
    gt, tracks, times = planted()
    events = [ZoneEvent("zone.entered", 5000 + 400, "z", 2)]  # A's new track; A has no true entry (inside from frame 1)
    s = measure_sequence(gt, tracks, times, CAMERA, events, count_errors=[0, 1, 0, -1])
    total_missed = sum(m for m, _ in s.miss_by_k.values())
    total = sum(t for _, t in s.miss_by_k.values())
    assert (total_missed, total) == (10, 200)
    assert s.switches == 1
    assert sorted(s.fragments_per_person) == [0, 1]
    assert s.phantom_durations_ms == [pytest.approx(2000.0)]
    assert s.entry_jitter == []  # first-frame occupants have no true entry time
    assert s.seconds == pytest.approx(10.0)


def test_profile_is_valid_and_flags_thin_evidence():
    gt, tracks, times = planted()
    s = measure_sequence(gt, tracks, times, CAMERA, [], count_errors=[0, 1, 0, -1])
    doc = profile(s, name="syn-test", description="synthetic")
    assert doc["detection"]["falsePositivesPerMinute"] == pytest.approx(6.0)  # 1 phantom in 10 s
    assert doc["tracking"]["fragmentation"]["probability"] == 0.5
    assert doc["tracking"]["idSwitchesPerTrackMinute"] == pytest.approx(1 / (200 * 0.1 / 60), rel=1e-3)
    assert doc["queueCountError"]["values"] == [-1, 0, 1]
    assert any("queueCountError" in t for t in thin_evidence(doc))
    store_sim = pytest.importorskip("store_sim.vision_noise")
    assert store_sim.validate(doc) == []


def test_miss_rate_fit_grows_with_crowding_or_stays_constant():
    grow = {1: [5, 100], 2: [20, 200], 3: [45, 300], 4: [80, 400]}  # 5%, 10%, 15%, 20%
    fit = fit_miss_rate(grow)
    assert fit["base"] == pytest.approx(0.0, abs=1e-6) and fit["perPersonInZone"] == pytest.approx(0.05, abs=1e-6)
    assert fit["max"] == pytest.approx(0.2)
    flat = fit_miss_rate({1: [10, 100], 5: [8, 100]})
    assert "perPersonInZone" not in flat and flat["base"] == pytest.approx(0.09)
    assert fit_miss_rate({}) == {"base": 0.0}


def test_samples_merge():
    a, b = Samples(seconds=10, switches=1, cameras=["a"]), Samples(seconds=5, switches=2, cameras=["b"])
    a.miss_by_k[1] = [1, 10]
    b.miss_by_k[1] = [2, 10]
    a.merge(b)
    assert (a.seconds, a.switches, a.miss_by_k[1], a.cameras) == (15, 3, [3, 20], ["a", "b"])
