"""Tracking metrics: CLEAR MOT (MOTA, ID switches), Identity (IDF1) and HOTA.

Implemented from the definitions used by TrackEval (Luiten et al., "HOTA: A Higher Order Metric for
Evaluating Multi-Object Tracking", IJCV 2021; Ristani et al. 2016 for IDF1; Bernardin and Stiefelhagen
2008 for CLEAR MOT), so no extra dependency is needed (ADR-012). Numbers use PQT's person definition
(mot.py: classes 1 and 7, visibility >= 0.25), so they are comparable between our runs but not with the
MOTChallenge leaderboard.

Input per frame: ground-truth (id, box) pairs, ignore boxes, and tracker (id, box) pairs. Before any
metric, tracker boxes that match an ignore region (IoU >= 0.5, Hungarian) and no person are removed,
as the MOTChallenge devkit does with distractors.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from .types import Box, boxes_to_array, iou_matrix

EPS = np.finfo(float).eps
ALPHAS = np.arange(0.05, 0.96, 0.05)


@dataclass
class FrameData:
    gt_ids: np.ndarray       # (G,) int
    tr_ids: np.ndarray       # (T,) int
    sim: np.ndarray          # (G, T) IoU


def prepare(gt: dict[int, list[tuple[int, Box]]], ignore: dict[int, list[Box]],
            tracks: dict[int, list[tuple[int, Box]]], frames: list[int]) -> tuple[list[FrameData], dict]:
    """Per-frame IoU matrices with ignore-region hits removed, and dense id maps."""
    out: list[FrameData] = []
    gt_map: dict[int, int] = {}
    tr_map: dict[int, int] = {}
    removed = 0
    for f in frames:
        g = gt.get(f, [])
        t = tracks.get(f, [])
        ig = ignore.get(f, [])
        gb, tb = boxes_to_array([b for _, b in g]), boxes_to_array([b for _, b in t])
        sim = iou_matrix(gb, tb).astype(np.float64)
        keep = np.ones(len(t), dtype=bool)
        if len(t) and len(ig):
            # Match trackers against people and ignore regions together; drop those won by an ignore region.
            allb = np.vstack([gb[:, :4], boxes_to_array(ig)[:, :4]]) if len(g) else boxes_to_array(ig)[:, :4]
            s_all = iou_matrix(allb, tb)
            cost = np.where(s_all >= 0.5 - EPS, -s_all, 0.0)
            rows, cols = linear_sum_assignment(cost)
            for r, k in zip(rows, cols):
                if s_all[r, k] >= 0.5 - EPS and r >= len(g):
                    keep[k] = False
            removed += int((~keep).sum())
        g_ids = np.array([gt_map.setdefault(i, len(gt_map)) for i, _ in g], dtype=int)
        t_ids = np.array([tr_map.setdefault(i, len(tr_map)) for (i, _), k in zip(t, keep) if k], dtype=int)
        out.append(FrameData(g_ids, t_ids, sim[:, keep]))
    return out, {"gtIds": len(gt_map), "trackerIds": len(tr_map), "removedOnIgnore": removed}


def clear(frames: list[FrameData], n_gt: int, threshold: float = 0.5) -> dict:
    tp = fn = fp = idsw = 0
    prev_tr = np.full(n_gt, -1)          # tracker last matched to each gt (persists through gaps)
    prev_frame_match = np.full(n_gt, -1)  # tracker matched in the previous frame only
    gt_frames = np.zeros(n_gt)
    gt_matched = np.zeros(n_gt)
    for fd in frames:
        g, t, s = fd.gt_ids, fd.tr_ids, fd.sim
        gt_frames[g] += 1
        if len(g) == 0:  # as in TrackEval, an empty frame does not reset the previous-frame matches
            fp += len(t)
            continue
        if len(t) == 0:
            fn += len(g)
            continue
        score = 1000.0 * (prev_frame_match[g][:, None] == t[None, :]) + s
        score[s < threshold - EPS] = 0.0
        rows, cols = linear_sum_assignment(-score)
        ok = score[rows, cols] > EPS
        rows, cols = rows[ok], cols[ok]
        mg, mt = g[rows], t[cols]
        switched = (prev_tr[mg] != mt) & (prev_tr[mg] != -1)
        idsw += int(switched.sum())
        prev_tr[mg] = mt
        prev_frame_match[:] = -1
        prev_frame_match[mg] = mt
        gt_matched[mg] += 1
        tp += len(rows)
        fn += len(g) - len(rows)
        fp += len(t) - len(rows)
    ratio = np.divide(gt_matched, gt_frames, out=np.zeros(n_gt), where=gt_frames > 0)
    present = gt_frames > 0
    n_gt_dets = tp + fn
    return {"MOTA": (tp - fp - idsw) / max(1, n_gt_dets), "IDSW": idsw, "TP": tp, "FP": fp, "FN": fn,
            "MT": int((ratio[present] > 0.8).sum()), "ML": int((ratio[present] < 0.2).sum()),
            "recall": tp / max(1, n_gt_dets), "precision": tp / max(1, tp + fp)}


def identity(frames: list[FrameData], n_gt: int, n_tr: int, threshold: float = 0.5) -> dict:
    counts = np.zeros((n_gt, n_tr))
    gt_n = np.zeros(n_gt)
    tr_n = np.zeros(n_tr)
    for fd in frames:
        gt_n[fd.gt_ids] += 1
        tr_n[fd.tr_ids] += 1
        if len(fd.gt_ids) and len(fd.tr_ids):
            r, k = np.where(fd.sim >= threshold - EPS)
            np.add.at(counts, (fd.gt_ids[r], fd.tr_ids[k]), 1)
    idtp = 0.0
    if n_gt and n_tr:
        rows, cols = linear_sum_assignment(-counts)
        idtp = float(counts[rows, cols].sum())
    total_gt, total_tr = float(gt_n.sum()), float(tr_n.sum())
    idfn, idfp = total_gt - idtp, total_tr - idtp
    return {"IDF1": 2 * idtp / max(1.0, 2 * idtp + idfp + idfn), "IDTP": int(idtp), "IDFP": int(idfp), "IDFN": int(idfn),
            "IDR": idtp / max(1.0, total_gt), "IDP": idtp / max(1.0, total_tr)}


def hota(frames: list[FrameData], n_gt: int, n_tr: int) -> dict:
    potential = np.zeros((n_gt, n_tr))
    gt_count = np.zeros((n_gt, 1))
    tr_count = np.zeros((1, n_tr))
    for fd in frames:
        g, t, s = fd.gt_ids, fd.tr_ids, fd.sim
        gt_count[g] += 1
        tr_count[0, t] += 1
        if len(g) and len(t):
            denom = s.sum(0)[None, :] + s.sum(1)[:, None] - s
            sim_iou = np.divide(s, denom, out=np.zeros_like(s), where=denom > EPS)
            potential[g[:, None], t[None, :]] += sim_iou
    with np.errstate(divide="ignore", invalid="ignore"):
        gas = np.nan_to_num(potential / (gt_count + tr_count - potential))
    a = len(ALPHAS)
    tp, fn, fp, loc = np.zeros(a), np.zeros(a), np.zeros(a), np.zeros(a)
    matches = [np.zeros((n_gt, n_tr)) for _ in range(a)]
    for fd in frames:
        g, t, s = fd.gt_ids, fd.tr_ids, fd.sim
        if len(g) == 0:
            fp += len(t)
            continue
        if len(t) == 0:
            fn += len(g)
            continue
        score = gas[g[:, None], t[None, :]] * s
        rows, cols = linear_sum_assignment(-score)
        for i, alpha in enumerate(ALPHAS):
            ok = s[rows, cols] >= alpha - EPS
            r, k = rows[ok], cols[ok]
            n = len(r)
            tp[i] += n
            fn[i] += len(g) - n
            fp[i] += len(t) - n
            if n:
                loc[i] += s[r, k].sum()
                matches[i][g[r], t[k]] += 1
    ass_a = np.zeros(a)
    for i in range(a):
        m = matches[i]
        denom = np.maximum(1, gt_count + tr_count - m)
        ass_a[i] = float((m * (m / denom)).sum() / max(1.0, tp[i]))
    det_a = tp / np.maximum(1, tp + fn + fp)
    hota_a = np.sqrt(det_a * ass_a)
    loc_a = np.maximum(1e-10, loc) / np.maximum(1e-10, tp)
    return {"HOTA": float(hota_a.mean()), "DetA": float(det_a.mean()), "AssA": float(ass_a.mean()),
            "LocA": float(loc_a.mean()), "HOTA50": float(hota_a[np.argmin(np.abs(ALPHAS - 0.5))])}


def evaluate_tracking(gt: dict[int, list[tuple[int, Box]]], ignore: dict[int, list[Box]],
                      tracks: dict[int, list[tuple[int, Box]]], frames: list[int]) -> dict:
    fds, info = prepare(gt, ignore, tracks, frames)
    n_gt, n_tr = info["gtIds"], info["trackerIds"]
    out = {**hota(fds, n_gt, n_tr), **clear(fds, n_gt), **identity(fds, n_gt, n_tr), **info, "frames": len(frames)}
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}
