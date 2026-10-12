"""Homography fit, calibration report and camera config files on synthetic geometry."""
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from pqt_vision import camera, cli
from pqt_vision.geometry import CalibrationError, apply, dlt, errors, fit

# A plausible ceiling camera: floor metres → image pixels (1920 × 1080). Its inverse is what we fit.
H_IMG_FROM_FLOOR = np.array([[180.0, -35.0, 700.0], [8.0, 55.0, 260.0], [0.004, 0.045, 1.0]])
H_TRUE = np.linalg.inv(H_IMG_FROM_FLOOR)
H_TRUE /= H_TRUE[2, 2]
FLOOR = np.array([[x, y] for x in (0.0, 1.5, 3.0) for y in (0.0, 2.0, 4.0, 6.0)])  # 12 taped points
IMAGE = apply(H_IMG_FROM_FLOOR, FLOOR)


def test_synthetic_camera_sees_the_floor_inside_the_frame():
    assert (IMAGE[:, 0] > 0).all() and (IMAGE[:, 0] < 1920).all() and (IMAGE[:, 1] > 0).all() and (IMAGE[:, 1] < 1080).all()


def test_dlt_recovers_the_homography_from_four_and_from_many_points():
    for idx in ([0, 3, 8, 11], list(range(12))):
        h = dlt(IMAGE[idx], FLOOR[idx])
        assert h == pytest.approx(H_TRUE, rel=1e-6, abs=1e-9)
    assert errors(H_TRUE, IMAGE, FLOOR).max() < 1e-9


def test_dlt_agrees_with_opencv():
    ours = dlt(IMAGE, FLOOR)
    theirs, _ = cv2.findHomography(IMAGE, FLOOR, 0)
    assert ours == pytest.approx(theirs / theirs[2, 2], rel=1e-5, abs=1e-8)


def test_degenerate_points_are_refused():
    line = np.array([[0, 0], [1, 1], [2, 2], [3, 3]], dtype=float)
    with pytest.raises(CalibrationError):
        dlt(line, line)
    with pytest.raises(CalibrationError):
        fit(IMAGE[:3], FLOOR[:3])


def test_pixel_noise_stays_under_15_cm_held_out():
    rng = np.random.default_rng(7)
    noisy = IMAGE + rng.normal(0, 1.0, IMAGE.shape)  # one pixel of clicking error
    cal = fit(noisy, FLOOR)
    assert cal.inliers.all()
    assert cal.held_out_error.size == 12 and cal.held_out_max < 0.15


def test_ransac_rejects_a_mis_measured_point():
    floor = FLOOR.copy()
    floor[5] += [1.0, 0.0]  # taped a metre off
    cal = fit(IMAGE, floor)
    assert not cal.inliers[5] and cal.inliers.sum() == 11
    assert cal.held_out_max < 1e-6
    assert cal.fit_error[5] == pytest.approx(1.0, abs=1e-6)


def _points_file(tmp_path: Path, image: np.ndarray) -> Path:
    p = tmp_path / "points.yaml"
    p.write_text(yaml.safe_dump({"camera": "cam-01", "store": "store-001", "image": {"width": 1920, "height": 1080},
                                 "points": [{"image": [float(a), float(b)], "floor": [float(c), float(d)]}
                                            for (a, b), (c, d) in zip(image, FLOOR)]}), encoding="utf-8")
    return p


def test_calibrate_writes_a_valid_config_and_keeps_existing_zones(tmp_path: Path, capsys):
    out = tmp_path / "cameras" / "cam-01.yaml"
    out.parent.mkdir()
    zones = [{"id": "queue-1", "kind": "queue", "polygon": [[0, 0], [1, 0], [1, 3], [0, 3]]}]
    out.write_text(yaml.safe_dump({"zones": zones, "membership": {"queueMinDwellSeconds": 8}}), encoding="utf-8")
    assert cli.main(["calibrate", "--points", str(_points_file(tmp_path, IMAGE)), "--out", str(out)]) == 0
    cfg = camera.load(out)
    assert cfg.units == "m" and cfg.zones[0].id == "queue-1" and cfg.membership.queue_min_dwell_s == 8
    assert cfg.to_floor(IMAGE[:1]) == pytest.approx(FLOOR[:1], abs=1e-6)
    assert cfg.doc["calibration"]["inliers"] == 12 and cfg.doc["calibration"]["heldOutMaxM"] < 0.15


def test_calibrate_refuses_to_write_when_held_out_error_is_too_high(tmp_path: Path):
    rng = np.random.default_rng(3)
    sloppy = IMAGE + rng.normal(0, 25.0, IMAGE.shape)  # 25 px clicking error
    out = tmp_path / "cam.yaml"
    assert cli.main(["calibrate", "--points", str(_points_file(tmp_path, sloppy)), "--out", str(out),
                     "--ransac-threshold", "5"]) == 2
    assert not out.exists()


def _px_doc(**over):
    doc = {"apiVersion": "camera.pqt/v1", "camera": "mot17-04", "store": "mot17", "units": "px",
           "image": {"width": 1920, "height": 1080}, "homography": np.eye(3).tolist(),
           "zones": [{"id": "z1", "kind": "zone", "polygon": [[0, 0], [10, 0], [10, 10]]}]}
    doc.update(over)
    return doc


def test_pixel_unit_config_for_open_datasets():
    cfg = camera.from_doc(_px_doc())
    assert cfg.to_floor(np.array([[5.0, 7.0]])) == pytest.approx(np.array([[5.0, 7.0]]))
    assert cfg.membership == camera.Membership()


def test_invalid_configs_are_rejected():
    with pytest.raises(camera.CameraConfigError, match="units"):
        camera.from_doc(_px_doc(units="ft"))
    with pytest.raises(camera.CameraConfigError, match="singular"):
        camera.from_doc(_px_doc(homography=[[1, 0, 0], [0, 0, 0], [0, 0, 1]]))
    z = {"id": "z1", "kind": "zone", "polygon": [[0, 0], [1, 0], [1, 1]]}
    with pytest.raises(camera.CameraConfigError, match="unique"):
        camera.from_doc(_px_doc(zones=[z, z]))
    with pytest.raises(camera.CameraConfigError):
        camera.from_doc(_px_doc(zones=[{"id": "z1", "kind": "zone", "polygon": [[0, 0], [1, 1]]}]))


def test_the_example_points_file_calibrates(tmp_path: Path):
    example = Path(__file__).resolve().parents[1] / "config" / "points.example.yaml"
    assert cli.main(["calibrate", "--points", str(example), "--out", str(tmp_path / "cam.yaml")]) == 0
    assert camera.load(tmp_path / "cam.yaml").doc["calibration"]["inliers"] == 10
