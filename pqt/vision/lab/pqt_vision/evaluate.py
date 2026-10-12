"""Detection quality against ground truth: AP at IoU 0.5 and precision/recall at score thresholds.

Matching is greedy in descending score per frame (the PASCAL VOC / COCO convention). Because a
detection's outcome never depends on lower-scoring detections, one matching pass at the lowest score
gives exact precision and recall at every higher threshold.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mot import GroundTruth
from .types import Box, boxes_to_array, iou_matrix

TP, FP, IGNORED = 1, 0, -1


def match_frame(dets: list[Box], people: list[Box], ignore: list[Box], iou: float = 0.5) -> list[int]:
    """Outcome per detection (same order as `dets`, which must be sorted by score, highest first)."""
    if not dets:
        return []
    d = boxes_to_array(dets)
    ious_p = iou_matrix(d, boxes_to_array(people))
    ious_i = iou_matrix(d, boxes_to_array(ignore))
    taken = np.zeros(len(people), dtype=bool)
    out: list[int] = []
    for k in range(len(dets)):
        if len(people):
            cand = np.where(~taken, ious_p[k], -1.0)
            j = int(np.argmax(cand))
            if cand[j] >= iou:
                taken[j] = True
                out.append(TP)
                continue
        if len(ignore) and float(ious_i[k].max()) >= iou:
            out.append(IGNORED)
            continue
        out.append(FP)
    return out


@dataclass
class DetectionEval:
    frames: int
    gt_people: int
    detections: int
    ap50: float
    at: dict[float, dict[str, float]]  # threshold → precision, recall, f1, tp, fp

    def as_dict(self) -> dict:
        return {"frames": self.frames, "gtPeople": self.gt_people, "detections": self.detections,
                "ap50": round(self.ap50, 4),
                "atThreshold": {f"{t:.2f}": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
                                for t, m in self.at.items()}}


def average_precision(recall: np.ndarray, precision: np.ndarray) -> float:
    """All-point interpolated AP (area under the monotone precision envelope)."""
    if recall.size == 0:
        return 0.0
    r = np.concatenate(([0.0], recall, [recall[-1]]))
    p = np.concatenate(([1.0], precision, [0.0]))
    for i in range(len(p) - 2, -1, -1):
        p[i] = max(p[i], p[i + 1])
    idx = np.where(r[1:] != r[:-1])[0]
    return float(np.sum((r[idx + 1] - r[idx]) * p[idx + 1]))


def evaluate(dets_by_frame: dict[int, list[Box]], gt: GroundTruth, *, frames: list[int],
             thresholds: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7), iou: float = 0.5) -> DetectionEval:
    scores: list[float] = []
    flags: list[int] = []
    n_pos = 0
    for f in frames:
        people = [b for _, b in gt.people(f)]
        n_pos += len(people)
        dets = sorted(dets_by_frame.get(f, []), key=lambda b: -b.score)
        for b, o in zip(dets, match_frame(dets, people, gt.ignore(f), iou)):
            if o != IGNORED:
                scores.append(b.score)
                flags.append(o)
    s = np.array(scores, dtype=np.float64)
    fl = np.array(flags, dtype=np.int8)
    order = np.argsort(-s, kind="stable")
    s, fl = s[order], fl[order]
    tp_cum = np.cumsum(fl == TP)
    fp_cum = np.cumsum(fl == FP)
    recall = tp_cum / n_pos if n_pos else np.zeros_like(tp_cum, dtype=np.float64)
    precision = tp_cum / np.maximum(tp_cum + fp_cum, 1)
    at: dict[float, dict[str, float]] = {}
    for t in thresholds:
        k = int(np.searchsorted(-s, -t, side="right"))  # detections with score >= t
        tp = int(tp_cum[k - 1]) if k else 0
        fp = int(fp_cum[k - 1]) if k else 0
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / n_pos if n_pos else 0.0
        at[t] = {"precision": p, "recall": r, "f1": (2 * p * r / (p + r)) if p + r else 0.0, "tp": tp, "fp": fp}
    return DetectionEval(frames=len(frames), gt_people=n_pos, detections=int(len(s)),
                         ap50=average_precision(recall, precision), at=at)
