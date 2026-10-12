"""N1: measure how the camera pipeline gets people wrong, as a store-sim noise profile (noise.pqt/v1).

Everything is measured *inside the zone*, because that is what the rules see. Per sequence, from the
ground truth (people per mot.py) and the tracker's output (the same tracks the zone engine used):

- detection.missRate: per person-frame, a person in the zone with no matching track (IoU ≥ 0.5); fitted
  as base + perPersonInZone × (people in the zone), capped at the highest well-sampled rate.
- detection.visitMissRate (N5): the share of people's zone visits (a stretch of ≥ 1 s in the zone) during
  which no track ever matched them: the visit the counts lose entirely.
- detection.falsePositivesPerMinute: tracks in the zone that match nobody for ≥ 80% of their zone
  frames ("phantom tracks"), per camera-minute; phantomTrackDurationMs: their durations (lognormal).
- tracking.idSwitchesPerTrackMinute: a person's matched track id changes between consecutive frames.
- tracking.fragmentation: a person's track id changes after a gap (lost, then found under a new id);
  probability per person and the number of extra tracks (empirical).
- timing.entryJitterMs / exitJitterMs: emitted zone entry/exit time minus the true one, for people
  whose track entered/exited within 3 s of them (normal).
- queueCountError: predicted minus true zone count, one sample per second (empirical).

Only rates and distributions are produced: no boxes, frames or identities (noise_profile.schema.json).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.optimize import linear_sum_assignment

from .camera import CameraConfig
from .mot import GroundTruth
from .types import Box, boxes_to_array, iou_matrix
from .zones import ZoneEvent, point_in_polygon

PHANTOM_UNMATCHED = 0.8
JITTER_WINDOW_MS = 3000
MIN_BIN_N = 30


@dataclass
class Samples:
    """Raw counts and samples from one or more sequences; `profile()` turns them into the YAML document."""

    seconds: float = 0.0
    miss_by_k: dict[int, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))  # k → [missed, total]
    phantom_durations_ms: list[float] = field(default_factory=list)
    switches: int = 0
    gt_track_ms: float = 0.0
    fragments_per_person: list[int] = field(default_factory=list)
    entry_jitter: list[float] = field(default_factory=list)
    exit_jitter: list[float] = field(default_factory=list)
    count_errors: list[int] = field(default_factory=list)
    cameras: list[str] = field(default_factory=list)
    visit_runs: int = 0
    visit_missed: int = 0

    def merge(self, o: "Samples") -> None:
        self.seconds += o.seconds
        for k, (m, t) in o.miss_by_k.items():
            self.miss_by_k[k][0] += m
            self.miss_by_k[k][1] += t
        self.phantom_durations_ms += o.phantom_durations_ms
        self.switches += o.switches
        self.gt_track_ms += o.gt_track_ms
        self.fragments_per_person += o.fragments_per_person
        self.entry_jitter += o.entry_jitter
        self.exit_jitter += o.exit_jitter
        self.count_errors += o.count_errors
        self.cameras += o.cameras
        self.visit_runs += o.visit_runs
        self.visit_missed += o.visit_missed


def _in(poly: np.ndarray, b: Box, camera: CameraConfig) -> bool:
    x, y = camera.to_floor(np.array([b.foot()]))[0]
    return point_in_polygon(float(x), float(y), poly)


def measure_sequence(gt: GroundTruth, tracks: dict[int, list[tuple[int, Box]]], times: dict[int, int],
                     camera: CameraConfig, zone_events: list[ZoneEvent], count_errors: list[int]) -> Samples:
    s = Samples(cameras=[camera.camera])
    frames = sorted(tracks)
    if not frames:
        return s
    pos = {f: i for i, f in enumerate(frames)}
    dt_ms = (times[frames[-1]] - times[frames[0]]) / max(1, len(frames) - 1)
    s.seconds = (times[frames[-1]] - times[frames[0]] + dt_ms) / 1000.0
    s.count_errors = list(count_errors)
    for zone in camera.zones:
        poly = np.array(zone.polygon, dtype=float)
        track_frames: dict[int, list[bool]] = defaultdict(list)   # tracker id → matched? per zone frame
        gt_seq: dict[int, list[tuple[int, int | None]]] = defaultdict(list)  # gt id → (frame, tracker id or None)
        gt_in_frames: dict[int, list[int]] = defaultdict(list)
        for f in frames:
            people = [(gid, b) for gid, b in gt.people(f) if _in(poly, b, camera)]
            trk = [(tid, b) for tid, b in tracks[f] if _in(poly, b, camera)]
            ignore = gt.ignore(f)
            k = len(people)
            matched_t: dict[int, int] = {}
            if people and trk:
                iou = iou_matrix(boxes_to_array([b for _, b in people]), boxes_to_array([b for _, b in trk]))
                rows, cols = linear_sum_assignment(-iou)
                for r, c in zip(rows, cols):
                    if iou[r, c] >= 0.5:
                        matched_t[c] = r
            by_row = {r: c for c, r in matched_t.items()}
            for r, (gid, _) in enumerate(people):
                hit = by_row.get(r)
                s.miss_by_k[k][1] += 1
                if hit is None:
                    s.miss_by_k[k][0] += 1
                gt_seq[gid].append((f, trk[hit][0] if hit is not None else None))
                gt_in_frames[gid].append(f)
            for c, (tid, b) in enumerate(trk):
                on_ignore = bool(ignore) and float(iou_matrix(boxes_to_array([b]), boxes_to_array(ignore)).max()) >= 0.5
                if c in matched_t or not on_ignore:
                    track_frames[tid].append(c in matched_t)
        # phantom tracks
        for tid, flags in track_frames.items():
            if flags and (1 - sum(flags) / len(flags)) >= PHANTOM_UNMATCHED:
                s.phantom_durations_ms.append(len(flags) * dt_ms)
        # switches (no gap) and fragments (after a gap), per person
        for gid, seq in gt_seq.items():
            s.gt_track_ms += len(seq) * dt_ms
            last_id, last_f, frags = None, None, 0
            for f, tid in seq:
                if tid is None:
                    continue
                if last_id is not None and tid != last_id:
                    consecutive = pos[f] - pos[last_f] == 1
                    if consecutive:
                        s.switches += 1
                    else:
                        frags += 1
                last_id, last_f = tid, f
            if len(seq) * dt_ms >= 1000:
                s.fragments_per_person.append(frags)
        # whole visits never tracked (N5): every stretch of >= 1 s in the zone, matched or not
        for gid, fs in gt_in_frames.items():
            matched_at = {f for f, tid in gt_seq[gid] if tid is not None}
            for a, b in _runs(fs, frames, pos):
                if times[b] - times[a] + dt_ms >= 1000:
                    s.visit_runs += 1
                    if not any(f in matched_at for f in frames[pos[a]:pos[b] + 1]):
                        s.visit_missed += 1
        # timing: true entry/exit per person vs the nearest emitted event of a track that matched them
        zid = zone.id
        ev_in = [e for e in zone_events if e.zone_id == zid and e.kind == "zone.entered"]
        ev_out = [e for e in zone_events if e.zone_id == zid and e.kind == "zone.exited"]
        for gid, fs in gt_in_frames.items():
            ids = {tid for _, tid in gt_seq[gid] if tid is not None}
            if not ids:
                continue
            runs = _runs(fs, frames, pos)
            for a, b in runs:
                t_in, t_out = times[a], times[b]
                cand = [e.t_ms - t_in for e in ev_in if e.track_id in ids and abs(e.t_ms - t_in) <= JITTER_WINDOW_MS]
                if cand and a != frames[0]:  # a person already inside at the first frame has no true entry
                    s.entry_jitter.append(min(cand, key=abs))
                cand = [e.t_ms - t_out for e in ev_out if e.track_id in ids and abs(e.t_ms - t_out) <= JITTER_WINDOW_MS]
                if cand and b != frames[-1]:
                    s.exit_jitter.append(min(cand, key=abs))
    return s


def _runs(fs: list[int], frames: list[int], pos: dict[int, int]) -> list[tuple[int, int]]:
    """Consecutive stretches (in sequence frame order) of the frames a person was in the zone."""
    idx = sorted(pos[f] for f in fs)
    out, start, prev = [], None, None
    for i in idx:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append((frames[start], frames[prev]))
            start = prev = i
    if start is not None:
        out.append((frames[start], frames[prev]))
    return out


def _normal(xs: list[float]) -> dict:
    if not xs:
        return {"kind": "constant", "value": 0, "n": 0}
    return {"kind": "normal", "mean": round(float(np.mean(xs)), 1), "sd": round(float(np.std(xs)), 1), "n": len(xs)}


def _lognormal(xs: list[float]) -> dict | None:
    xs = [x for x in xs if x > 0]
    if not xs:
        return None
    logs = np.log(xs)
    return {"kind": "lognormal", "mu": round(float(logs.mean()), 3), "sigma": round(float(logs.std()), 3), "n": len(xs)}


def _empirical(xs: list[int]) -> dict:
    c = Counter(xs)
    values = sorted(c)
    return {"kind": "empirical", "values": values, "weights": [round(c[v] / len(xs), 4) for v in values], "n": len(xs)}


def fit_miss_rate(miss_by_k: dict[int, list[int]]) -> dict:
    """Weighted least squares p(k) = base + per × k over zone occupancy k; base, per ≥ 0; max from data."""
    ks = sorted(k for k, (_, t) in miss_by_k.items() if t > 0)
    if not ks:
        return {"base": 0.0}
    k = np.array(ks, dtype=float)
    p = np.array([miss_by_k[x][0] / miss_by_k[x][1] for x in ks])
    w = np.array([miss_by_k[x][1] for x in ks], dtype=float)
    a = np.vstack([np.ones_like(k), k]).T * np.sqrt(w)[:, None]
    coef, *_ = np.linalg.lstsq(a, p * np.sqrt(w), rcond=None)
    base, per = float(coef[0]), float(coef[1])
    if per < 0:  # no growth with crowding: a constant rate
        base, per = float(np.average(p, weights=w)), 0.0
    base = min(max(base, 0.0), 1.0)
    well = [miss_by_k[x][0] / miss_by_k[x][1] for x in ks if miss_by_k[x][1] >= MIN_BIN_N]
    out = {"base": round(base, 4)}
    if per > 0:
        out["perPersonInZone"] = round(min(per, 1.0), 4)
        out["max"] = round(min(1.0, max(well) if well else 1.0), 4)
    return out


def profile(s: Samples, *, name: str, description: str, today: date | None = None) -> dict:
    minutes = s.seconds / 60.0
    track_minutes = s.gt_track_ms / 60000.0
    frag = s.fragments_per_person
    fragmented = [x for x in frag if x > 0]
    det: dict = {"missRate": fit_miss_rate(s.miss_by_k),
                 **({"visitMissRate": {"probability": round(s.visit_missed / s.visit_runs, 4), "n": s.visit_runs}}
                    if s.visit_runs else {}),
                 "falsePositivesPerMinute": round(len(s.phantom_durations_ms) / minutes, 3) if minutes else 0.0}
    ph = _lognormal(s.phantom_durations_ms)
    if ph:
        det["phantomTrackDurationMs"] = ph
    trk: dict = {"idSwitchesPerTrackMinute": round(s.switches / track_minutes, 4) if track_minutes else 0.0,
                 "fragmentation": {"probability": round(len(fragmented) / len(frag), 4) if frag else 0.0}}
    if fragmented:
        trk["fragmentation"]["extraTracks"] = _empirical(fragmented)
    doc = {
        "apiVersion": "noise.pqt/v1", "name": name, "description": description[:1000],
        "measuredFrom": {"clips": len(s.cameras), "trackMinutes": round(track_minutes, 2), "cameras": sorted(set(s.cameras)),
                         "measuredAt": (today or date.today()).isoformat(),
                         "notes": ("Open-dataset track (ADR-013): MOT17 fixed-camera sequences, zones from ground truth. "
                                   "queueCountError is zone occupancy error from 1-second samples, not 10-second "
                                   "queue samples. Replace with a profile from our own footage at P5.")},
        "detection": det, "tracking": trk,
        "timing": {"entryJitterMs": _normal(s.entry_jitter), "exitJitterMs": _normal(s.exit_jitter)},
    }
    if s.count_errors:
        doc["queueCountError"] = _empirical(s.count_errors)
    return doc


def thin_evidence(doc: dict) -> list[str]:
    """Parameters fitted on fewer than 30 samples (the plan's flag)."""
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            if "n" in node and isinstance(node["n"], int) and node["n"] < MIN_BIN_N:
                out.append(f"{path} (n={node['n']})")
            for k, v in node.items():
                walk(v, f"{path}/{k}" if path else k)

    walk(doc, "")
    return out

