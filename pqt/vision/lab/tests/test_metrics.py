"""Tracking metrics against hand-computed values on synthetic sequences."""
import math

import pytest

from pqt_vision.metrics import evaluate_tracking
from pqt_vision.types import Box


def b(x: float) -> Box:
    return Box(x, 0, x + 10, 30)


def test_perfect_tracking_scores_one():
    gt = {f: [(7, b(10 + f)), (9, b(100))] for f in range(1, 11)}
    m = evaluate_tracking(gt, {}, {f: [(1, bx) for _, bx in v[:1]] + [(2, v[1][1])] for f, v in gt.items()}, list(range(1, 11)))
    assert (m["HOTA"], m["DetA"], m["AssA"], m["MOTA"], m["IDF1"], m["IDSW"]) == (1.0, 1.0, 1.0, 1.0, 1.0, 0)
    assert m["MT"] == 2 and m["ML"] == 0


def test_an_id_switch_halfway():
    frames = list(range(1, 11))
    gt = {f: [(1, b(10))] for f in frames}
    tr = {f: [(1 if f <= 5 else 2, b(10))] for f in frames}
    m = evaluate_tracking(gt, {}, tr, frames)
    assert m["IDSW"] == 1
    assert m["MOTA"] == pytest.approx(0.9)
    assert m["IDF1"] == pytest.approx(0.5)
    assert m["DetA"] == pytest.approx(1.0) and m["AssA"] == pytest.approx(0.5)
    assert m["HOTA"] == pytest.approx(math.sqrt(0.5), abs=1e-4)


def test_misses_and_false_alarms():
    frames = [1, 2, 3, 4]
    gt = {f: [(1, b(10))] for f in frames}
    tr = {1: [(5, b(10))], 2: [(5, b(10)), (6, b(200))], 3: [], 4: [(5, b(10))]}
    m = evaluate_tracking(gt, {}, tr, frames)
    assert (m["TP"], m["FP"], m["FN"], m["IDSW"]) == (3, 1, 1, 0)
    assert m["MOTA"] == pytest.approx((3 - 1 - 0) / 4)
    assert m["recall"] == pytest.approx(0.75) and m["precision"] == pytest.approx(0.75)


def test_hits_on_ignore_regions_are_removed_not_counted():
    frames = [1, 2]
    gt = {f: [(1, b(10))] for f in frames}
    ignore = {f: [b(300)] for f in frames}
    tr = {f: [(1, b(10)), (2, b(300))] for f in frames}
    m = evaluate_tracking(gt, ignore, tr, frames)
    assert m["FP"] == 0 and m["removedOnIgnore"] == 2 and m["MOTA"] == 1.0


def test_empty_frames_and_no_tracker_output():
    frames = [1, 2, 3]
    m = evaluate_tracking({1: [(1, b(10))], 2: [], 3: [(1, b(10))]}, {}, {}, frames)
    assert m["FN"] == 2 and m["MOTA"] == 0.0 and m["HOTA"] == 0.0 and m["IDF1"] == 0.0
    m2 = evaluate_tracking({}, {}, {1: [(1, b(10))]}, frames)
    assert m2["FP"] == 1


def test_low_overlap_is_a_miss_for_clear_but_partial_for_hota():
    frames = [1]
    gt = {1: [(1, Box(0, 0, 10, 10))]}
    tr = {1: [(1, Box(0, 0, 10, 4))]}  # IoU 0.4
    m = evaluate_tracking(gt, {}, tr, frames)
    assert m["TP"] == 0 and m["FN"] == 1 and m["FP"] == 1
    assert 0.0 < m["DetA"] < 1.0  # matched at alpha <= 0.4 only
