"""Constant-velocity Kalman filter on (cx, cy, aspect, height), as in SORT, DeepSORT and ByteTrack.

State x = [cx, cy, a, h, vcx, vcy, va, vh]; measurement z = [cx, cy, a, h] with a = w / h.
Noise scales with the box height, so a near (large) person is allowed to move more pixels per frame
than a far (small) one.
"""
from __future__ import annotations

import numpy as np

STD_POS = 1.0 / 20
STD_VEL = 1.0 / 160

_F = np.eye(8)
_F[:4, 4:] = np.eye(4)
_H = np.eye(4, 8)


def xyxy_to_xyah(b: np.ndarray) -> np.ndarray:
    w, h = b[2] - b[0], b[3] - b[1]
    return np.array([b[0] + w / 2, b[1] + h / 2, w / max(h, 1e-6), h], dtype=np.float64)


def xyah_to_xyxy(m: np.ndarray) -> np.ndarray:
    cx, cy, a, h = m[:4]
    w = a * h
    return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], dtype=np.float64)


def initiate(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = np.r_[z, np.zeros(4)]
    h = z[3]
    std = [2 * STD_POS * h, 2 * STD_POS * h, 1e-2, 2 * STD_POS * h,
           10 * STD_VEL * h, 10 * STD_VEL * h, 1e-5, 10 * STD_VEL * h]
    return mean, np.diag(np.square(std))


def predict(mean: np.ndarray, cov: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h = mean[3]
    q = np.diag(np.square([STD_POS * h, STD_POS * h, 1e-2, STD_POS * h,
                           STD_VEL * h, STD_VEL * h, 1e-5, STD_VEL * h]))
    return _F @ mean, _F @ cov @ _F.T + q


def update(mean: np.ndarray, cov: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h = mean[3]
    r = np.diag(np.square([STD_POS * h, STD_POS * h, 1e-1, STD_POS * h]))
    s = _H @ cov @ _H.T + r
    k = np.linalg.solve(s, (cov @ _H.T).T).T  # cov H^T S^-1, without forming the inverse
    mean = mean + k @ (z - _H @ mean)
    cov = cov - k @ s @ k.T
    return mean, cov
