"""Camera configuration files (camera.schema.json): homography, calibration evidence, zones, timing."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path

import jsonschema
import numpy as np
import yaml

from .geometry import Calibration, apply


class CameraConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ZoneSpec:
    id: str
    kind: str  # "zone" | "queue"
    polygon: tuple[tuple[float, float], ...]
    queue_id: str | None = None
    open_registers: int = 1
    head: tuple[float, float] | None = None
    head_radius: float = 0.75

    @property
    def queue(self) -> str:
        return self.queue_id or self.id


@dataclass(frozen=True)
class Membership:
    enter_s: float = 0.5
    exit_s: float = 1.0
    margin: float = 0.0
    queue_min_dwell_s: float = 5.0
    queue_max_speed: float = 0.5   # floor units per second (0.5 m/s: shuffling forward, not walking past)
    speed_window_s: float = 2.0


@dataclass
class CameraConfig:
    camera: str
    store: str
    width: int
    height: int
    units: str
    h: np.ndarray
    zones: list[ZoneSpec]
    membership: Membership = field(default_factory=Membership)
    doc: dict = field(default_factory=dict, repr=False)

    def to_floor(self, pts: np.ndarray) -> np.ndarray:
        return apply(self.h, pts)


def _schema() -> dict:
    return json.loads(resources.files("pqt_vision").joinpath("camera.schema.json").read_text(encoding="utf-8"))


def validate(doc: object) -> list[str]:
    v = jsonschema.Draft202012Validator(_schema())
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path))]


def from_doc(doc: dict) -> CameraConfig:
    errs = validate(doc)
    if errs:
        raise CameraConfigError("; ".join(errs))
    h = np.array(doc["homography"], dtype=float)
    if abs(np.linalg.det(h)) < 1e-12:
        raise CameraConfigError("homography is singular")
    ids = [z["id"] for z in doc["zones"]]
    if len(ids) != len(set(ids)):
        raise CameraConfigError("zone ids must be unique")
    zones = [ZoneSpec(z["id"], z["kind"], tuple((float(x), float(y)) for x, y in z["polygon"]), z.get("queueId"),
                      int(z.get("openRegisters", 1)), tuple(map(float, z["head"])) if "head" in z else None,
                      float(z.get("headRadius", 0.75)))
             for z in doc["zones"]]
    m = doc.get("membership", {})
    d = Membership()
    membership = Membership(m.get("enterSeconds", d.enter_s), m.get("exitSeconds", d.exit_s),
                            m.get("boundaryMargin", d.margin), m.get("queueMinDwellSeconds", d.queue_min_dwell_s),
                            m.get("queueMaxSpeed", d.queue_max_speed), m.get("speedWindowSeconds", d.speed_window_s))
    return CameraConfig(doc["camera"], doc["store"], doc["image"]["width"], doc["image"]["height"], doc["units"],
                        h, zones, membership, doc)


def load(path: str | Path) -> CameraConfig:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise CameraConfigError(f"{path}: {e}") from e
    return from_doc(doc)


def calibrated_doc(*, camera: str, store: str, width: int, height: int, src: np.ndarray, dst: np.ndarray,
                   cal: Calibration, zones: list | None = None, membership: dict | None = None,
                   today: date | None = None) -> dict:
    doc = {
        "apiVersion": "camera.pqt/v1", "camera": camera, "store": store,
        "image": {"width": int(width), "height": int(height)}, "units": "m",
        "homography": [[round(float(v), 10) for v in row] for row in cal.h],
        "calibration": {
            "points": [{"image": [float(a), float(b)], "floor": [float(c), float(d)], "inlier": bool(ok)}
                       for (a, b), (c, d), ok in zip(src, dst, cal.inliers)],
            "inliers": int(cal.inliers.sum()),
            "heldOutMaxM": round(cal.held_out_max, 4), "heldOutMeanM": round(cal.held_out_mean, 4),
            "calibratedAt": (today or date.today()).isoformat(),
        },
        "zones": zones or [],
    }
    if membership:
        doc["membership"] = membership
    errs = validate(doc)
    if errs:
        raise CameraConfigError("; ".join(errs))
    return doc
