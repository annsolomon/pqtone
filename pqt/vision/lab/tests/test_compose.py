"""ROI and tiled detection on synthetic frames (white rectangles stand in for people)."""
import cv2
import numpy as np
import pytest

from pqt_vision.detect.compose import Region, RoiDetector, TiledDetector, roi_for_zones, tiles_for
from pqt_vision.types import Box, Frame


class BlobDetector:
    """Finds white rectangles in whatever image it is given (local coordinates), like a real model would."""

    name = "blobs"

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def detect(self, frame: Frame) -> list[Box]:
        img = frame.image
        self.calls.append(img.shape[:2])
        mask = (img[:, :, 0] > 128).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        return [Box(float(x), float(y), float(x + w), float(y + h), 0.9) for x, y, w, h, _ in stats[1:n]]

    def describe(self) -> dict:
        return {"backend": "blobs"}


def frame_with(boxes: list[tuple[int, int, int, int]], w: int = 1920, h: int = 1080) -> Frame:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for x1, y1, x2, y2 in boxes:
        img[y1:y2, x1:x2] = 255
    return Frame(0, 0, img)


def as_tuples(boxes: list[Box]) -> list[tuple]:
    return sorted((round(b.x1), round(b.y1), round(b.x2), round(b.y2)) for b in boxes)


def test_roi_covers_the_zones_plus_margin_and_clips_to_the_frame():
    r = roi_for_zones([np.array([[800, 400], [1200, 400], [1200, 700], [800, 700]])], width=1920, height=1080)
    assert (r.x1, r.y1, r.x2, r.y2) == (600, 250, 1400, 850)
    edge = roi_for_zones([np.array([[0, 0], [100, 0], [100, 100]])], width=1920, height=1080)
    assert (edge.x1, edge.y1) == (0, 0) and edge.x2 == 164 and edge.y2 == 164


def test_roi_detector_maps_crop_boxes_back_to_frame_coordinates():
    base = BlobDetector()
    det = RoiDetector(base, Region(600, 250, 1400, 850))
    got = det.detect(frame_with([(700, 300, 740, 400), (100, 100, 140, 200)]))
    assert as_tuples(got) == [(700, 300, 740, 400)]  # the person outside the ROI is not looked at
    assert base.calls == [(600, 800)]
    assert det.describe()["roi"] == [600, 250, 1400, 850]


def test_tiles_cover_the_frame_with_overlap():
    tiles = tiles_for(1920, 1080, cols=2, rows=2, overlap=0.2)
    assert len(tiles) == 4
    covered = np.zeros((1080, 1920), dtype=bool)
    for t in tiles:
        covered[t.y1 : t.y2, t.x1 : t.x2] = True
    assert covered.all()
    assert tiles[0].x2 > tiles[1].x1  # neighbours overlap


def test_tiled_detection_finds_each_person_once_even_across_tile_edges():
    people = [(940, 500, 990, 620), (100, 100, 130, 180), (1800, 950, 1850, 1060)]  # first straddles the centre
    det = TiledDetector(BlobDetector(), cols=2, rows=2, overlap=0.2)
    assert as_tuples(det.detect(frame_with(people))) == sorted(people)


def test_wrappers_need_pixels():
    with pytest.raises(ValueError):
        RoiDetector(BlobDetector(), Region(0, 0, 10, 10)).detect(Frame(0, 0))
    with pytest.raises(ValueError):
        TiledDetector(BlobDetector()).detect(Frame(0, 0))
