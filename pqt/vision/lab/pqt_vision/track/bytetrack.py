"""ByteTrack, written from the paper (Zhang et al., "ByteTrack: Multi-Object Tracking by Associating
Every Detection Box", ECCV 2022, arXiv:2110.06864). ADR-012.

The idea: low-score detections are usually real people who are partly hidden. Instead of discarding
them, match them to existing tracks in a second pass, after the confident detections have been used.

Per frame:
1. Split detections into high (score >= track_thresh) and low (low_thresh <= score < track_thresh).
2. Predict every confirmed and lost track with its Kalman filter.
3. First pass: confirmed + lost tracks vs high detections, IoU (optionally weighted by score).
4. Second pass: still-unmatched *confirmed* tracks vs low detections, plain IoU. Unmatched → lost.
5. Tentative tracks (seen once) vs the remaining high detections; unmatched tentatives are dropped.
6. Remaining high detections with score >= new_track_thresh start new tentative tracks.
7. Lost tracks older than the buffer are removed.

Track ids are small integers local to one tracker instance (one camera, one run). They are not
identities: the event emitter maps them to pseudonymous ids that rotate daily (V5).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..types import Box, boxes_to_array, iou_matrix
from . import kalman


@dataclass(frozen=True)
class ByteTrackConfig:
    track_thresh: float = 0.5        # high/low split
    low_thresh: float = 0.1          # below this a detection is ignored entirely
    new_track_thresh: float = 0.6    # a new track needs a confident start
    match_thresh: float = 0.8        # first pass: max (1 - IoU) cost
    second_match_thresh: float = 0.5  # second pass (low detections)
    unconfirmed_thresh: float = 0.7  # tentative tracks
    buffer_frames: int = 30          # how long a lost track may come back, at 30 fps
    fuse_score: bool = True          # first pass cost uses IoU x detection score
    min_box_area: float = 10.0       # outputs smaller than this are not reported
    max_aspect: float = 1.6          # outputs wider than tall by more than this are not people

    def as_dict(self) -> dict:
        return asdict(self)


class State(Enum):
    TENTATIVE = "tentative"
    TRACKED = "tracked"
    LOST = "lost"
    REMOVED = "removed"


@dataclass
class Track:
    track_id: int
    mean: np.ndarray = field(repr=False)
    cov: np.ndarray = field(repr=False)
    score: float
    start_frame: int
    last_frame: int
    state: State = State.TENTATIVE
    hits: int = 1

    @property
    def box(self) -> Box:
        x1, y1, x2, y2 = kalman.xyah_to_xyxy(self.mean)
        return Box(float(x1), float(y1), float(x2), float(y2), self.score)


@dataclass(frozen=True)
class TrackOut:
    """One reported track in one frame."""

    track_id: int
    box: Box


def _assign(cost: np.ndarray, thresh: float) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Min-cost matching keeping only pairs with cost <= thresh."""
    if cost.size == 0:
        return [], list(range(cost.shape[0])), list(range(cost.shape[1]))
    c = np.where(cost > thresh, thresh + 1e4, cost)
    rows, cols = linear_sum_assignment(c)
    matches = [(int(r), int(k)) for r, k in zip(rows, cols) if cost[r, k] <= thresh]
    mr = {r for r, _ in matches}
    mc = {k for _, k in matches}
    return matches, [r for r in range(cost.shape[0]) if r not in mr], [k for k in range(cost.shape[1]) if k not in mc]


class ByteTracker:
    def __init__(self, config: ByteTrackConfig | None = None, *, fps: float = 30.0) -> None:
        self.cfg = config or ByteTrackConfig()
        self.fps = fps
        self.max_lost = max(1, int(round(self.cfg.buffer_frames * fps / 30.0)))
        self.tracks: list[Track] = []  # tentative, tracked and lost; removed ones are dropped
        self._next_id = 1
        self.frame = 0

    def _cost(self, tracks: list[Track], dets: list[Box], *, fuse: bool) -> np.ndarray:
        if not tracks or not dets:
            return np.zeros((len(tracks), len(dets)))
        t = np.array([kalman.xyah_to_xyxy(tr.mean) for tr in tracks])
        d = boxes_to_array(dets)
        iou = iou_matrix(t, d).astype(np.float64)
        if fuse:
            iou = iou * d[:, 4][None, :]
        return 1.0 - iou

    def _update(self, tr: Track, det: Box, frame: int) -> None:
        z = kalman.xyxy_to_xyah(np.array([det.x1, det.y1, det.x2, det.y2]))
        tr.mean, tr.cov = kalman.update(tr.mean, tr.cov, z)
        tr.score, tr.last_frame, tr.hits = det.score, frame, tr.hits + 1
        tr.state = State.TRACKED

    def _new(self, det: Box, frame: int, *, first_frame: bool) -> Track:
        mean, cov = kalman.initiate(kalman.xyxy_to_xyah(np.array([det.x1, det.y1, det.x2, det.y2])))
        tr = Track(self._next_id, mean, cov, det.score, frame, frame,
                   State.TRACKED if first_frame else State.TENTATIVE)
        self._next_id += 1
        return tr

    def update(self, dets: list[Box], frame: int | None = None) -> list[TrackOut]:
        """Advance one frame with this frame's detections; returns the confirmed, visible tracks."""
        self.frame = frame if frame is not None else self.frame + 1
        f = self.frame
        first = not self.tracks and self._next_id == 1
        c = self.cfg
        high = [d for d in dets if d.score >= c.track_thresh]
        low = [d for d in dets if c.low_thresh <= d.score < c.track_thresh]

        tentative = [t for t in self.tracks if t.state is State.TENTATIVE]
        pool = [t for t in self.tracks if t.state in (State.TRACKED, State.LOST)]
        for t in pool:
            if t.state is State.LOST:
                t.mean[7] = 0.0  # a lost track's height stops changing
            t.mean, t.cov = kalman.predict(t.mean, t.cov)
        for t in tentative:
            t.mean, t.cov = kalman.predict(t.mean, t.cov)

        # 1. confident detections against confirmed and lost tracks
        m1, u_pool, u_high = _assign(self._cost(pool, high, fuse=c.fuse_score), c.match_thresh)
        for ti, di in m1:
            self._update(pool[ti], high[di], f)
        # 2. low-score detections against confirmed tracks that are still unmatched
        rest = [pool[i] for i in u_pool if pool[i].state is State.TRACKED]
        m2, u_rest, _ = _assign(self._cost(rest, low, fuse=False), c.second_match_thresh)
        for ti, di in m2:
            self._update(rest[ti], low[di], f)
        for i in u_rest:
            rest[i].state = State.LOST
        # 3. tentative tracks get one chance at the remaining confident detections
        high_left = [high[i] for i in u_high]
        m3, u_tent, u_high2 = _assign(self._cost(tentative, high_left, fuse=c.fuse_score), c.unconfirmed_thresh)
        for ti, di in m3:
            self._update(tentative[ti], high_left[di], f)
        for i in u_tent:
            tentative[i].state = State.REMOVED
        # 4. new tracks from confident leftovers
        born = [self._new(high_left[i], f, first_frame=first) for i in u_high2
                if high_left[i].score >= c.new_track_thresh]
        # 5. expire lost tracks
        for t in self.tracks:
            if t.state is State.LOST and f - t.last_frame > self.max_lost:
                t.state = State.REMOVED
        self.tracks = [t for t in self.tracks if t.state is not State.REMOVED] + born
        self._dedupe()

        out = []
        for t in self.tracks:
            if t.state is State.TRACKED and t.last_frame == f:
                b = t.box
                if b.area > c.min_box_area and b.w / max(b.h, 1e-6) <= c.max_aspect:
                    out.append(TrackOut(t.track_id, b))
        return out

    def _dedupe(self) -> None:
        """A tracked and a lost track on the same person (IoU > 0.85): keep the older one."""
        tracked = [t for t in self.tracks if t.state is State.TRACKED]
        lost = [t for t in self.tracks if t.state is State.LOST]
        if not tracked or not lost:
            return
        iou = iou_matrix(np.array([kalman.xyah_to_xyxy(t.mean) for t in tracked]),
                         np.array([kalman.xyah_to_xyxy(t.mean) for t in lost]))
        drop: set[int] = set()
        for i, j in zip(*np.where(iou > 0.85)):
            a, b = tracked[i], lost[j]
            drop.add(id(b) if (a.last_frame - a.start_frame) >= (b.last_frame - b.start_frame) else id(a))
        self.tracks = [t for t in self.tracks if id(t) not in drop]
