"""Homography: image pixels → floor coordinates (V3).

A camera looking at a flat floor sees it through a perspective projection. Any four floor points with
known positions (no three on a line) fix the 3×3 matrix H that maps image pixels to floor coordinates,
up to scale:   [X, Y, 1]ᵀ ∝ H · [u, v, 1]ᵀ.

`fit` solves H with the normalised Direct Linear Transform (Hartley and Zisserman, "Multiple View
Geometry", 2nd ed., algorithm 4.2): each correspondence gives two linear equations in the nine entries
of H; with points shifted to their centroid and scaled to mean distance √2 the system is well
conditioned; the least-squares solution is the right singular vector of the smallest singular value.
With five or more points a small RANSAC loop rejects a mis-measured point first.

Only a person's *foot point* (bottom-centre of the box) is mapped: it lies on the floor, so the
homography is exact for it. The head is not on the floor plane and is never mapped.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np


class CalibrationError(ValueError):
    pass


def _normaliser(p: np.ndarray) -> np.ndarray:
    c = p.mean(axis=0)
    d = np.sqrt(((p - c) ** 2).sum(axis=1)).mean()
    s = np.sqrt(2) / d if d > 0 else 1.0
    return np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]])


def dlt(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """H with dst ∝ H·src from n ≥ 4 correspondences (normalised DLT, least squares for n > 4)."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if src.shape != dst.shape or src.shape[0] < 4 or src.shape[1] != 2:
        raise CalibrationError("need at least 4 point pairs, each (x, y)")
    ts, td = _normaliser(src), _normaliser(dst)
    s = (ts @ np.c_[src, np.ones(len(src))].T).T
    d = (td @ np.c_[dst, np.ones(len(dst))].T).T
    rows = []
    for (x, y, w), (u, v, z) in zip(s, d):
        rows.append([0, 0, 0, -z * x, -z * y, -z * w, v * x, v * y, v * w])
        rows.append([z * x, z * y, z * w, 0, 0, 0, -u * x, -u * y, -u * w])
    a = np.array(rows)
    _, sv, vt = np.linalg.svd(a)
    if sv[-2] < 1e-9 * sv[0]:
        raise CalibrationError("points are degenerate (three or more on a line, or repeated)")
    hn = vt[-1].reshape(3, 3)
    h = np.linalg.inv(td) @ hn @ ts
    if abs(h[2, 2]) < 1e-12:
        raise CalibrationError("degenerate homography")
    return h / h[2, 2]


def apply(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Map (n, 2) points through H."""
    p = np.c_[np.asarray(pts, float), np.ones(len(pts))] @ np.asarray(h, float).T
    with np.errstate(divide="ignore", invalid="ignore"):  # a point on H's horizon maps to infinity
        out = p[:, :2] / p[:, 2:3]
    return np.where(np.isfinite(out), out, np.inf)


def errors(h: np.ndarray, src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Distance between H·src and dst for each pair, in destination units (metres on the floor)."""
    with np.errstate(invalid="ignore"):
        d = np.linalg.norm(apply(h, src) - np.asarray(dst, float), axis=1)
    return np.where(np.isfinite(d), d, np.inf)


@dataclass
class Calibration:
    h: np.ndarray
    inliers: np.ndarray            # bool per point
    fit_error: np.ndarray          # per point, with H fitted on all inliers
    held_out_error: np.ndarray     # per inlier, with H fitted on the other inliers (leave-one-out)

    @property
    def held_out_max(self) -> float:
        return float(self.held_out_error.max()) if self.held_out_error.size else float("nan")

    @property
    def held_out_mean(self) -> float:
        return float(self.held_out_error.mean()) if self.held_out_error.size else float("nan")


def fit(src: np.ndarray, dst: np.ndarray, *, ransac_threshold: float = 0.10) -> Calibration:
    """Fit H image→floor. With ≥ 5 points, the largest consensus set (errors ≤ threshold, floor units)
    found by trying every 4-point subset is refitted; held-out error is leave-one-out over inliers."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    n = len(src)
    if n < 4:
        raise CalibrationError("need at least 4 points")
    inliers = np.ones(n, dtype=bool)
    if n >= 5:
        best: np.ndarray | None = None
        for subset in combinations(range(n), 4):  # n is small (a handful of tape-measured points)
            try:
                h = dlt(src[list(subset)], dst[list(subset)])
            except CalibrationError:
                continue
            mask = errors(h, src, dst) <= ransac_threshold
            if best is None or mask.sum() > best.sum():
                best = mask
        if best is None or best.sum() < 4:
            raise CalibrationError("no consistent set of 4 points; re-measure")
        inliers = best
    h = dlt(src[inliers], dst[inliers])
    idx = np.flatnonzero(inliers)
    held = []
    if len(idx) >= 5:
        for i in idx:
            rest = idx[idx != i]
            hi = dlt(src[rest], dst[rest])
            held.append(errors(hi, src[i : i + 1], dst[i : i + 1])[0])
    return Calibration(h, inliers, errors(h, src, dst), np.array(held))
