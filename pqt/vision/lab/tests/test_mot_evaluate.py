from pathlib import Path

import pytest

from pqt_vision.evaluate import FP, IGNORED, TP, average_precision, evaluate, match_frame
from pqt_vision.mot import build_gt, load_gt, parse_gt
from pqt_vision.types import Box

from vision_synth import write_mot_sequence

# frame, id, left, top, width, height, consider, class, visibility (synthetic numbers)
ROWS = [
    "1,1,10,10,10,20,1,1,1.0",   # person
    "1,2,40,10,10,20,0,7,0.8",   # static person: counts as a person
    "1,3,70,10,10,20,1,1,0.1",   # barely visible: ignore region
    "1,4,100,10,10,20,0,8,1.0",  # distractor: ignore region
    "1,5,130,10,20,10,0,3,1.0",  # car: background
    "2,1,12,10,10,20,1,1,1.0",
]


def test_ground_truth_classes_and_visibility():
    gt = build_gt(parse_gt("\n".join(ROWS)), sequence="SYN", frames=3)
    assert [tid for tid, _ in gt.people(1)] == [1, 2]
    assert len(gt.ignore(1)) == 2
    assert gt.people(3) == [] and gt.ignore(3) == []
    assert gt.people(2)[0][1] == Box(12, 10, 22, 30, 1.0)
    assert [tid for tid, _ in build_gt(parse_gt("\n".join(ROWS)), sequence="S", frames=3, min_vis=0.05).people(1)] == [1, 2, 3]


def test_load_gt_from_a_sequence_directory(tmp_path: Path):
    seq = write_mot_sequence(tmp_path, frames=3, gt_rows=ROWS)
    gt = load_gt(seq)
    assert gt.sequence == "SYN-01" and set(gt.frames) == {1, 2, 3}


def test_match_frame_outcomes():
    people = [Box(10, 10, 20, 30)]
    ignore = [Box(100, 10, 110, 30)]
    dets = [Box(10, 10, 20, 30, 0.9), Box(11, 10, 21, 30, 0.8), Box(100, 10, 110, 30, 0.7), Box(300, 0, 310, 20, 0.6)]
    assert match_frame(dets, people, ignore) == [TP, FP, IGNORED, FP]  # the duplicate is a false alarm
    assert match_frame([], people, ignore) == []
    assert match_frame([Box(10, 10, 20, 30, 0.5)], [], []) == [FP]


def test_average_precision_edges():
    import numpy as np
    assert average_precision(np.array([0.5, 1.0]), np.array([1.0, 1.0])) == pytest.approx(1.0)
    assert average_precision(np.array([]), np.array([])) == 0.0


def test_evaluate_counts_and_thresholds():
    gt = build_gt(parse_gt("\n".join(ROWS)), sequence="SYN", frames=2)
    dets = {
        1: [Box(10, 10, 20, 30, 0.9), Box(40, 10, 50, 30, 0.4), Box(100, 10, 110, 30, 0.8), Box(300, 0, 310, 20, 0.2)],
        2: [Box(12, 10, 22, 30, 0.95)],
    }
    ev = evaluate(dets, gt, frames=[1, 2], thresholds=(0.1, 0.5))
    assert ev.gt_people == 3 and ev.detections == 4  # the ignore-region hit is not counted
    assert ev.at[0.1] == {"precision": 0.75, "recall": 1.0, "f1": pytest.approx(6 / 7), "tp": 3, "fp": 1}
    assert ev.at[0.5]["tp"] == 2 and ev.at[0.5]["fp"] == 0 and ev.at[0.5]["recall"] == pytest.approx(2 / 3)
    assert ev.ap50 == pytest.approx(1.0)  # all hits rank above the one false alarm
    assert ev.as_dict()["atThreshold"]["0.10"]["tp"] == 3
