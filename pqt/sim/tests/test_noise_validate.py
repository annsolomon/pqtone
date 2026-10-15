"""Tier 2 N5: noise-model validation (KS test of simulated vs measured count errors) and the visit miss rate."""
from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from store_sim import noise_validate as nv
from store_sim import vision_noise
from store_sim.events import iso_ms

NOISE = Path("config/noise").resolve()
T0 = 1_767_258_000_000  # 2026-01-01T09:00:00Z


def none_profile() -> dict:
    return yaml.safe_load((NOISE / "none.yaml").read_text())


def zone_event(kind: str, t: int, trk: str, zone: str = "zone-1", store: str = "cam-store", n: int = 0) -> dict:
    return {"specversion": "1.0", "id": f"e-{kind}-{trk}-{t}-{n}", "source": "urn:test", "type": f"com.pqt.store.zone.{kind}",
            "time": iso_ms(t), "subject": f"track:{trk}", "storeid": store,
            "data": {"zoneId": zone, "trackId": trk}}


def clip(people: int = 3, seconds: int = 30) -> tuple[list[dict], list[tuple[int, str, int]]]:
    """Synthetic clip: `people` tracks in the zone for the whole clip, one sample per second."""
    ev = [zone_event("entered", T0, f"t{k}", n=k) for k in range(people)]
    ev += [zone_event("exited", T0 + seconds * 1000, f"t{k}", n=k) for k in range(people)]
    truth = [(T0 + 500 + s * 1000, "zone-1", people) for s in range(seconds)]
    return ev, truth


# -- statistics ----------------------------------------------------------------------------------

def test_ks_is_zero_for_identical_samples_and_one_for_disjoint_ones():
    d, p = nv.ks_2samp([0, 1, 1, 2] * 10, [0, 1, 1, 2] * 10)
    assert d == 0 and p == 1.0
    d, p = nv.ks_2samp([0] * 50, [3] * 50)
    assert d == 1.0 and p < 1e-9


def test_ks_statistic_matches_a_hand_computation():
    # F_a jumps 0.5 at 0 and 1.0 at 1; F_b is 0.25 at 0, 0.5 at 1, 1.0 at 2: max gap 0.5 at x = 1.
    d, _ = nv.ks_2samp([0, 0, 1, 1], [0, 1, 2, 2])
    assert d == pytest.approx(0.5)


def test_the_kolmogorov_tail_matches_an_independent_implementation():
    scipy_stats = pytest.importorskip("scipy.stats")
    for lam in (0.3, 0.5, 0.8, 1.0, 1.36, 1.63, 2.0):
        assert nv.kolmogorov_q(lam) == pytest.approx(float(scipy_stats.kstwobign.sf(lam)), abs=1e-9)
    assert nv.kolmogorov_q(1.358) == pytest.approx(0.05, abs=0.001)  # the familiar 5% critical value


def test_ks_statistic_matches_scipy_on_integer_samples():
    scipy_stats = pytest.importorskip("scipy.stats")
    a = [-1] * 19 + [0] * 37 + [1] * 14 + [2] * 2 + [5]
    b = [-2] * 40 + [-1] * 250 + [0] * 690 + [1] * 15 + [2] * 5
    d, p = nv.ks_2samp(a, b)
    assert d == pytest.approx(float(scipy_stats.ks_2samp(a, b).statistic), abs=1e-12)
    assert 0 <= p <= 1


def test_the_measured_sample_comes_back_from_the_profile():
    prof = yaml.safe_load((NOISE / "mot17-yolox-s-roi.yaml").read_text())
    sample = nv.measured_sample(prof)
    q = prof["queueCountError"]
    assert abs(len(sample) - q["n"]) <= len(q["values"]) and set(sample) == set(q["values"])
    with pytest.raises(ValueError, match="no empirical queueCountError"):
        nv.measured_sample(none_profile())


def test_describe_reports_shares_and_spread():
    s = nv.describe([-1, 0, 0, 1, 3])
    assert s["n"] == 5 and s["exact"] == 0.4 and s["within1"] == 0.8 and s["max"] == 3
    assert s["shares"] == {"-1": 0.2, "0": 0.4, "1": 0.2, "3": 0.2}
    assert s["sd"] == pytest.approx(math.sqrt(sum((x - 0.6) ** 2 for x in [-1, 0, 0, 1, 3]) / 5), abs=1e-3)


# -- occupancy and the same-clip test --------------------------------------------------------------

def test_occupancy_errors_compare_noisy_and_clean_counts_inside_the_true_range():
    clean = [zone_event("entered", T0, "a"), zone_event("entered", T0, "b", n=1),
             zone_event("exited", T0 + 5000, "a"), zone_event("exited", T0 + 5000, "b", n=1)]
    noisy = [zone_event("entered", T0 + 2000, "a"), zone_event("entered", T0, "b", n=1),
             zone_event("entered", T0 + 1000, "ghost"), zone_event("exited", T0 + 3000, "ghost"),
             zone_event("exited", T0 + 5000, "a"), zone_event("exited", T0 + 5000, "b", n=1)]
    errs = nv.occupancy_errors(clean, noisy, start_ms=T0, end_ms=T0 + 6000)
    # t = 0..4 s: truth 2 (kept). Noisy: 1 (a enters late), 2 (b + ghost), 3 (a, b, ghost), 2 (ghost left), 2.
    # t = 5, 6 s: truth 0, outside the 2-8 range (dropped).
    assert errs == [-1, 0, 1, 0, 0]


def test_the_control_profile_reproduces_the_clip_exactly():
    clean, truth = clip()
    assert set(nv.clip_errors(clean, truth, none_profile(), seeds=(1, 2))) == {0}


def test_a_profile_that_misses_every_visit_counts_nobody():
    clean, truth = clip(people=3)
    prof = copy.deepcopy(none_profile())
    prof["detection"]["visitMissRate"] = {"probability": 1.0, "n": 50}
    assert vision_noise.validate(prof) == []
    assert set(nv.clip_errors(clean, truth, prof, seeds=(1,))) == {-3}


def test_clip_errors_are_deterministic_and_need_one_camera():
    clean, truth = clip()
    prof = yaml.safe_load((NOISE / "mot17-yolox-s-roi.yaml").read_text())
    assert nv.clip_errors(clean, truth, prof, seeds=(4, 5)) == nv.clip_errors(clean, truth, prof, seeds=(4, 5))
    with pytest.raises(ValueError, match="one camera"):
        nv.clip_errors(clean + [zone_event("entered", T0, "x", store="other")], truth, prof, seeds=(1,))


def test_compare_flags_a_wrong_model():
    good = nv.compare([0] * 300 + [1] * 100 + [-1] * 100, [0] * 60 + [1] * 20 + [-1] * 20)
    bad = nv.compare([0] * 500, [-1] * 30 + [1] * 40 + [2] * 30)
    assert not good["rejected"] and good["pValue"] > 0.5
    assert bad["rejected"] and bad["ksD"] == pytest.approx(0.7)  # |F_sim(0) - F_meas(0)| = |1.0 - 0.3|


# -- the visit miss rate (what N5 changed in N2) ---------------------------------------------------

def test_the_visit_miss_rate_replaces_the_per_frame_rate_for_dropping_visits():
    clean, truth = clip(people=4)
    prof = copy.deepcopy(none_profile())
    prof["detection"]["missRate"] = {"base": 1.0}       # per frame: everyone is sometimes missed...
    prof["detection"]["visitMissRate"] = {"probability": 0.0, "n": 40}  # ...but no visit is missed whole
    assert set(nv.clip_errors(clean, truth, prof, seeds=(1, 2, 3))) == {0}
    del prof["detection"]["visitMissRate"]               # pre-N5 profiles keep the old upper bound
    assert set(nv.clip_errors(clean, truth, prof, seeds=(1,))) == {-4}


def test_scaling_and_identity_know_the_visit_miss_rate():
    prof = copy.deepcopy(none_profile())
    prof["detection"]["visitMissRate"] = {"probability": 0.3, "n": 40}
    assert not vision_noise.is_identity(prof)
    assert vision_noise.scale(prof, 2)["detection"]["visitMissRate"]["probability"] == pytest.approx(0.6)
    assert vision_noise.scale(prof, 5)["detection"]["visitMissRate"]["probability"] == 1.0
    prof["detection"]["visitMissRate"]["probability"] = 0.0
    assert vision_noise.is_identity(prof)


def test_the_visit_miss_rate_is_schema_checked():
    prof = copy.deepcopy(none_profile())
    prof["detection"]["visitMissRate"] = {"probability": 1.2}
    assert any("visitMissRate" in e for e in vision_noise.validate(prof))
    prof["detection"]["visitMissRate"] = {"probability": 0.1, "faces": 3}
    assert vision_noise.validate(prof)


# -- the scenario comparison (secondary) ---------------------------------------------------------

def test_validate_profile_on_one_scenario_writes_a_verdict():
    v = nv.validate_profile(NOISE / "mot17-yolox-s-roi.yaml", scenarios=("register_delay",), seeds=(11,))
    d = v.as_dict()
    assert d["simulated"]["n"] > 100 and d["measured"]["n"] == 73
    assert 0 <= d["pValue"] <= 1 and d["rejected"] == (d["pValue"] < 0.05)
    md = nv.render_md(v, profile_path="config/noise/mot17-yolox-s-roi.yaml")
    assert "Two-sample KS" in md and "register_delay" in md


# -- fragment gaps (what N5's second run changed in N2) --------------------------------------------

def gap_profile(gap_ms: int | None) -> dict:
    prof = copy.deepcopy(none_profile())
    prof["tracking"]["fragmentation"] = {"probability": 1.0, "extraTracks": {"kind": "constant", "value": 1, "n": 40}}
    if gap_ms is not None:
        prof["tracking"]["fragmentation"]["gapMs"] = {"kind": "constant", "value": gap_ms, "n": 40}
    return prof


def test_a_fragment_leaves_the_person_uncounted_for_the_gap():
    clean, truth = clip(people=3, seconds=30)
    assert vision_noise.validate(gap_profile(2000)) == []
    for seed in range(5):
        errs = nv.clip_errors(clean, truth, gap_profile(2000), seeds=(seed,))
        assert min(errs) >= -3 and max(errs) <= 0
        assert -9 <= sum(errs) <= -3  # three people, each missing for about 2 s of 1-s samples


def test_without_a_gap_a_fragment_is_an_instant_split_as_before():
    clean, truth = clip(people=3, seconds=30)
    assert set(nv.clip_errors(clean, truth, gap_profile(None), seeds=(1, 2))) == {0}
    assert nv.clip_errors(clean, truth, gap_profile(None), seeds=(3,)) == nv.clip_errors(clean, truth, gap_profile(0), seeds=(3,))
