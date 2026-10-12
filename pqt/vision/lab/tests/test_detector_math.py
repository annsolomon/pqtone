import math

import numpy as np
import pytest

from pqt_vision.detect.yolox import decode, letterbox, nms, postprocess
from pqt_vision.types import Box, iou_matrix


def test_box_geometry():
    b = Box(10, 20, 30, 60, 0.9)
    assert (b.w, b.h, b.area) == (20, 40, 800)
    assert b.foot() == (20.0, 60)
    assert b.iou(b) == 1.0
    assert b.iou(Box(30, 60, 40, 70)) == 0.0
    assert b.iou(Box(20, 20, 30, 60)) == pytest.approx(0.5)


def test_iou_matrix_matches_scalar_iou():
    a = [Box(0, 0, 10, 10), Box(5, 5, 15, 15)]
    b = [Box(0, 0, 10, 10), Box(100, 100, 110, 110), Box(0, 5, 10, 15)]
    m = iou_matrix(np.array([[x.x1, x.y1, x.x2, x.y2] for x in a]), np.array([[x.x1, x.y1, x.x2, x.y2] for x in b]))
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            assert m[i, j] == pytest.approx(x.iou(y), abs=1e-6)
    assert iou_matrix(np.zeros((0, 4)), np.zeros((3, 4))).shape == (0, 3)


def test_letterbox_keeps_aspect_and_pads_with_114():
    img = np.full((100, 200, 3), 7, dtype=np.uint8)
    t, r = letterbox(img, (64, 64))
    assert t.shape == (3, 64, 64) and t.dtype == np.float32
    assert r == pytest.approx(0.32)
    assert t[0, 0, 0] == 7  # image in the top-left
    assert t[0, 63, 63] == 114 and t[0, 40, 10] == 114  # padding below the 32-px-high image


def _raw(size=(640, 640)) -> np.ndarray:
    n = sum((size[0] // s) * (size[1] // s) for s in (8, 16, 32))
    return np.zeros((n, 85), dtype=np.float32)


def test_decode_places_an_anchor_on_its_grid_cell():
    raw = _raw()
    k = 20 * 80 + 10  # stride-8 grid, row 20, column 10
    raw[k, :4] = [0.5, 0.5, math.log(4), math.log(8)]
    out = decode(raw, (640, 640))
    assert out[k, :4] == pytest.approx([84.0, 164.0, 32.0, 64.0])
    k32 = 80 * 80 + 40 * 40 + 3 * 20 + 2  # stride-32 grid, row 3, column 2
    raw[k32, :2] = [0, 0]
    assert decode(raw, (640, 640))[k32, :2] == pytest.approx([64.0, 96.0])


def test_decode_rejects_a_mismatched_input_size():
    with pytest.raises(ValueError):
        decode(_raw((640, 640)), (416, 416))


def test_postprocess_scales_filters_classes_and_suppresses_duplicates():
    raw = _raw()
    person = 20 * 80 + 10
    raw[person, :4] = [0.5, 0.5, math.log(4), math.log(8)]
    raw[person, 4], raw[person, 5] = 0.9, 0.8  # objectness x person score = 0.72
    dup = 20 * 80 + 11  # overlapping, weaker person: removed by NMS
    raw[dup, :4] = [-0.3, 0.5, math.log(4), math.log(8)]
    raw[dup, 4], raw[dup, 5] = 0.9, 0.5
    car = 50 * 80 + 50  # strong, but not a person
    raw[car, :4] = [0.5, 0.5, 0, 0]
    raw[car, 4], raw[car, 5 + 2] = 0.99, 0.99
    boxes = postprocess(raw, 0.5, (1280, 1280), (640, 640), conf=0.3, nms_iou=0.45)
    assert len(boxes) == 1
    b = boxes[0]
    assert (b.x1, b.y1, b.x2, b.y2) == pytest.approx((136.0, 264.0, 200.0, 392.0))
    assert b.score == pytest.approx(0.72, abs=1e-4) and b.cls == 0
    assert postprocess(raw, 0.5, (1280, 1280), (640, 640), conf=0.8, nms_iou=0.45) == []


def test_postprocess_clips_to_the_image():
    raw = _raw()
    k = 0
    raw[k, :4] = [0, 0, math.log(10), math.log(10)]  # centred on (0, 0): half outside
    raw[k, 4], raw[k, 5] = 1.0, 1.0
    (b,) = postprocess(raw, 1.0, (100, 100), (640, 640), conf=0.5, nms_iou=0.45)
    assert (b.x1, b.y1) == (0.0, 0.0) and b.x2 == pytest.approx(40.0)


def test_nms_keeps_best_of_each_cluster():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60], [0, 0, 10, 10.5]], dtype=np.float32)
    scores = np.array([0.6, 0.9, 0.5, 0.1], dtype=np.float32)
    assert nms(boxes, scores, 0.5) == [1, 2]
    assert nms(boxes, scores, 0.99) == [1, 0, 2, 3]
