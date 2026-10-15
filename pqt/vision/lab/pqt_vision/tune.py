"""V7 part 2: choose tracker and zone-timing settings on a development set, never on the test set (ADR-032).

MOT17-02, 04 and 09 are the test set of the open-dataset track. V7 part 1 showed every candidate missing
the proxy bar narrowly; tuning on those three sequences would fit the test set. So the settings are
chosen on MOT20's training sequences (static, elevated cameras; ground truth public; CC BY-NC-SA 3.0,
development use only under ADR-032), and MOT17 is then evaluated with the chosen setting. MOT17 numbers
never feed back into the choice.

Procedure, fixed before the first run:

1. Development clips: MOT20-01, 02, 03 and 05, the first `max_frames` (1000) frames of each.
2. One zone per clip, chosen from ground truth only by the MOT17 procedure (`mot_zones.propose_zone`).
   A clip whose best zone holds 2-8 people in fewer than 80% of 1-second samples is not queue-like and is
   excluded (recorded in the report).
3. Detections once per clip: the selected V7 candidate (YOLOX-S on the zone crop, confidence 0.05).
4. Every setting of the grid below is scored exactly like the MOT17 bench: zone CloudEvents from the
   tracks, occupancy against ground truth once per second, pqt-scorer's count score.
5. The chosen setting is, in order: the most clips meeting the proxy bar (≥ 95% within ±1 and MAE ≤ 0.5),
   then the highest mean share within ±1, then the lowest mean MAE, then the earliest grid position.

Grid (108 settings; the V7 part-1 configuration is one of them, `BASELINE`):

- tracker: track_thresh ∈ {0.4, 0.5, 0.6} (new_track_thresh = track_thresh + 0.1), lost-track buffer ∈
  {30, 60} frames (at 30 fps)
- zone engine: enter delay ∈ {0.3, 0.5, 1.0} s, exit delay ∈ {0.3, 0.5, 1.0} s, lost-track timeout ∈ {1, 2} s

Only aggregates and zone geometry leave the runner (no images, boxes or tracks).
"""
from __future__ import annotations

import itertools
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import __version__
from .camera import CameraConfig, from_doc
from .counts import count_events, gt_counts, score_occupancy
from .detect.compose import RoiDetector, roi_for_zones
from .detect.replay import ReplayDetector
from .mot import GroundTruth, load_gt
from .mot_zones import QUEUE_RANGE, ZoneProposal, camera_doc, propose_zone
from .runner import run_detection
from .sources import open_source, read_seqinfo
from .track import ByteTrackConfig, frame_times, run_tracking

DEV_SEQUENCES = ("MOT20-01", "MOT20-02", "MOT20-03", "MOT20-05")
MAX_FRAMES = 1000
MIN_ZONE_SCORE = 0.8
DETECT_CONF = 0.05
TRACK_THRESH = (0.4, 0.5, 0.6)
BUFFER_FRAMES = (30, 60)
ENTER_S = (0.3, 0.5, 1.0)
EXIT_S = (0.3, 0.5, 1.0)
LOST_S = (1.0, 2.0)
ATTRIBUTION = ("MOT20: Dendorfer, Rezatofighi, Milan, Shi, Cremers, Reid, Roth, Schindler, Leal-Taixé, \"MOT20: A "
               "benchmark for multi object tracking in crowded scenes\", arXiv:2003.09003. Licensed CC BY-NC-SA 3.0; "
               "development set only (ADR-032).")
SETTING_KIND = "pqt.vision-setting/v1"


@dataclass(frozen=True)
class Setting:
    """The knobs V7 part 2 tunes. Defaults are the V7 part-1 configuration (BASELINE)."""

    track_thresh: float = 0.5
    buffer_frames: int = 30
    enter_s: float = 0.5
    exit_s: float = 0.5
    lost_after_s: float = 2.0

    def __post_init__(self) -> None:
        if not 0.1 <= self.track_thresh <= 0.89:
            raise ValueError("trackThresh must be in [0.1, 0.89]")
        if not 1 <= self.buffer_frames <= 300:
            raise ValueError("bufferFrames must be in [1, 300]")
        for name, v in (("enterSeconds", self.enter_s), ("exitSeconds", self.exit_s), ("lostAfterSeconds", self.lost_after_s)):
            if not 0.0 <= v <= 30.0:
                raise ValueError(f"{name} must be in [0, 30]")

    def tracker(self) -> ByteTrackConfig:
        return ByteTrackConfig(track_thresh=self.track_thresh, new_track_thresh=round(self.track_thresh + 0.1, 3),
                               buffer_frames=self.buffer_frames)

    def apply(self, camera: CameraConfig) -> CameraConfig:
        """The camera with this setting's enter/exit delays (zones, margin and queue rules unchanged)."""
        return replace(camera, membership=replace(camera.membership, enter_s=self.enter_s, exit_s=self.exit_s))

    def as_dict(self) -> dict:
        return {"trackThresh": self.track_thresh, "bufferFrames": self.buffer_frames, "enterSeconds": self.enter_s,
                "exitSeconds": self.exit_s, "lostAfterSeconds": self.lost_after_s}

    @classmethod
    def from_dict(cls, d: dict) -> "Setting":
        keys = {"trackThresh", "bufferFrames", "enterSeconds", "exitSeconds", "lostAfterSeconds"}
        if set(d) != keys:
            raise ValueError(f"a setting has exactly the keys {sorted(keys)}; got {sorted(d)}")
        return cls(float(d["trackThresh"]), int(d["bufferFrames"]), float(d["enterSeconds"]), float(d["exitSeconds"]),
                   float(d["lostAfterSeconds"]))


BASELINE = Setting()


def grid() -> list[Setting]:
    return [Setting(tt, bf, en, ex, lo)
            for tt, bf, en, ex, lo in itertools.product(TRACK_THRESH, BUFFER_FRAMES, ENTER_S, EXIT_S, LOST_S)]


@dataclass
class DevClip:
    name: str
    camera: CameraConfig
    gt: GroundTruth
    fps: float
    replay: ReplayDetector
    proposal: ZoneProposal


def score_setting(clip: DevClip, setting: Setting, tracks: dict) -> dict:
    """The MOT17 bench's count score of one clip under one setting (tracks already run with its tracker)."""
    camera = setting.apply(clip.camera)
    events = count_events(camera, tracks, frame_times(clip.replay), run_key=f"tune:{clip.name}",
                          lost_after_s=setting.lost_after_s)
    rows = gt_counts(clip.gt, camera, fps=clip.fps, every_s=1.0)
    z = score_occupancy(rows, events)["zones"]["zone-1"]
    return {k: z[k] for k in ("n", "within1", "mae", "bias", "pass")} if z.get("n") else {"n": 0, "pass": False}


def evaluate_grid(clips: list[DevClip], settings: list[Setting]) -> list[dict]:
    track_cache: dict[tuple[str, float, int], dict] = {}  # tracking depends only on the tracker knobs
    results = []
    for s in settings:
        per = {}
        for c in clips:
            key = (c.name, s.track_thresh, s.buffer_frames)
            if key not in track_cache:
                track_cache[key] = {f: [(t.track_id, t.box) for t in ts] for f, ts in run_tracking(c.replay, s.tracker()).items()}
            per[c.name] = score_setting(c, s, track_cache[key])
        scored = [v for v in per.values() if v.get("n")]
        results.append({"setting": s.as_dict(), "sequences": per, "passing": sum(1 for v in scored if v["pass"]),
                        "meanWithin1": round(float(np.mean([v["within1"] for v in scored])), 4) if scored else 0.0,
                        "meanMae": round(float(np.mean([v["mae"] for v in scored])), 4) if scored else float("inf")})
    return results


def choose(results: list[dict]) -> int:
    """Index of the chosen setting (module docstring, step 5)."""
    return min(range(len(results)), key=lambda i: (-results[i]["passing"], -results[i]["meanWithin1"],
                                                    results[i]["meanMae"], i))


def _sequence_dir(root: Path, seq: str) -> Path:
    for cand in (root / seq, root / f"{seq}-FRCNN"):
        if (cand / "seqinfo.ini").is_file():
            return cand
    raise FileNotFoundError(f"{seq} not found under {root}")


def prepare(root: str | Path, *, detector, out: Path, sequences: tuple[str, ...] = DEV_SEQUENCES,
            max_frames: int | None = MAX_FRAMES) -> tuple[list[DevClip], dict[str, str], dict[str, dict]]:
    """Zones from ground truth, then cached detections on the zone crop. Returns (clips, excluded, zones)."""
    clips, excluded, zones = [], {}, {}
    for seq in sequences:
        sdir = _sequence_dir(Path(root), seq)
        si = read_seqinfo(sdir)
        w, h, fps = int(si["imwidth"]), int(si["imheight"]), float(si["framerate"])
        gt = load_gt(sdir)
        if max_frames:
            gt = GroundTruth(gt.sequence, {f: v for f, v in gt.frames.items() if f <= max_frames}, gt.min_vis)
        prop = propose_zone(gt, width=w, height=h, fps=fps)
        zones[seq] = {"polygon": prop.polygon(), "score": prop.score, "samples": prop.samples, "mean": prop.mean,
                      "min": prop.min, "max": prop.max}
        if prop.score < MIN_ZONE_SCORE:
            excluded[seq] = (f"best zone holds {QUEUE_RANGE[0]}-{QUEUE_RANGE[1]} people in {prop.score:.0%} of samples "
                             f"(< {MIN_ZONE_SCORE:.0%}): not queue-like")
            continue
        camera = from_doc(camera_doc(seq, prop, width=w, height=h, dataset="MOT20", use="development set only: ADR-032"))
        region = roi_for_zones([np.array(z.polygon) for z in camera.zones], width=w, height=h)
        run_detection(open_source(sdir), RoiDetector(detector, region), out / "dets" / seq, max_frames=max_frames)
        clips.append(DevClip(seq, camera, gt, fps, ReplayDetector(out / "dets" / seq), prop))
    return clips, excluded, zones


def tune(root: str | Path, *, model: str, out: str | Path, sequences: tuple[str, ...] = DEV_SEQUENCES,
         max_frames: int | None = MAX_FRAMES, threads: int | None = None, detector=None,
         settings: list[Setting] | None = None) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if detector is None:
        from .detect.yolox import YoloxOnnxDetector
        from .models import resolve

        detector = YoloxOnnxDetector(resolve(model), name=model, conf=DETECT_CONF, threads=threads)
    clips, excluded, zones = prepare(root, detector=detector, out=out, sequences=sequences, max_frames=max_frames)
    if not clips:
        raise RuntimeError(f"no usable development clip: {excluded}")
    settings = settings or grid()
    results = evaluate_grid(clips, settings)
    best = results[choose(results)]
    base = next((r for r in results if r["setting"] == BASELINE.as_dict()), None)
    describe = detector.describe()
    doc = {
        "kind": "pqt.vision-tune/v1", "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": os.environ.get("GITHUB_SHA", "local"), "pqtVision": __version__,
        "model": {"name": model, **{k: v for k, v in describe.items() if k in ("sha256", "input")}}, "mode": "roi",
        "detectConf": DETECT_CONF,
        "devSet": {"dataset": "MOT20", "sequences": [c.name for c in clips], "excluded": excluded,
                   "maxFrames": max_frames, "zones": zones},
        "selection": "most clips passing (>= 95% within ±1 and MAE <= 0.5), then mean within ±1, then mean MAE, "
                     "then grid order",
        "grid": results, "best": best, "baseline": base, "attribution": ATTRIBUTION,
    }
    (out / "tuned.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    setting_doc = {"kind": SETTING_KIND, "setting": best["setting"],
                   "tunedOn": {"dataset": "MOT20", "sequences": [c.name for c in clips], "maxFrames": max_frames,
                               "model": model, "mode": "roi", "commit": doc["commit"], "createdAt": doc["createdAt"]},
                   "devScore": {k: best[k] for k in ("passing", "meanWithin1", "meanMae")},
                   "baselineDevScore": {k: base[k] for k in ("passing", "meanWithin1", "meanMae")} if base else None}
    (out / "setting.json").write_text(json.dumps(setting_doc, indent=2) + "\n", encoding="utf-8")
    (out / "tuned.md").write_text(render_md(doc), encoding="utf-8")
    return doc


def _row(r: dict, mark: str = "") -> str:
    s = r["setting"]
    return (f"| {s['trackThresh']} | {s['bufferFrames']} | {s['enterSeconds']} | {s['exitSeconds']} | "
            f"{s['lostAfterSeconds']} | {r['passing']} | {r['meanWithin1']:.1%} | {r['meanMae']:.3f} |{mark}")


def render_md(doc: dict, top: int = 10) -> str:
    seqs = doc["devSet"]["sequences"]
    lines = [f"# V7 part 2: settings tuned on MOT20 (development set), {doc['model']['name']} {doc['mode']}", "",
             f"Commit `{doc['commit'][:12]}`, {doc['createdAt']}. Clips: {', '.join(seqs)} (first "
             f"{doc['devSet']['maxFrames']} frames). Selection: {doc['selection']}.", ""]
    for seq, why in doc["devSet"]["excluded"].items():
        lines.append(f"- Excluded {seq}: {why}")
    lines += ["", f"Top {top} of {len(doc['grid'])} settings:", "",
              "| Track thresh | Buffer | Enter (s) | Exit (s) | Lost after (s) | Clips passing | Mean within ±1 | Mean MAE | |",
              "|---|---|---|---|---|---|---|---|---|"]
    ranked = sorted(range(len(doc["grid"])), key=lambda i: (-doc["grid"][i]["passing"], -doc["grid"][i]["meanWithin1"],
                                                            doc["grid"][i]["meanMae"], i))
    for i in ranked[:top]:
        r = doc["grid"][i]
        lines.append(_row(r, " chosen |" if r == doc["best"] else " |"))
    if doc.get("baseline"):
        lines += ["", "V7 part-1 configuration (baseline) on the same clips:", "",
                  "| Track thresh | Buffer | Enter (s) | Exit (s) | Lost after (s) | Clips passing | Mean within ±1 | Mean MAE | |",
                  "|---|---|---|---|---|---|---|---|---|", _row(doc["baseline"], " baseline |")]
    lines += ["", "Chosen setting per clip:", "", "| Clip | Samples | Within ±1 | MAE | Bias | Bar |", "|---|---|---|---|---|---|"]
    for seq, v in doc["best"]["sequences"].items():
        if v.get("n"):
            lines.append(f"| {seq} | {v['n']} | {v['within1']:.1%} | {v['mae']:.3f} | {v['bias']:+.3f} | "
                         f"{'ok' if v['pass'] else 'miss'} |")
    lines += ["", "Chosen: `" + json.dumps(doc["best"]["setting"], sort_keys=True) + "`", "", f"_{doc['attribution']}_", ""]
    return "\n".join(lines)


def load_setting(path: str | Path) -> Setting:
    """A committed `setting.json` (written by `tune`)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("kind") != SETTING_KIND:
        raise ValueError(f"{path} is not a {SETTING_KIND} document")
    return Setting.from_dict(doc["setting"])


__all__ = ["BASELINE", "DEV_SEQUENCES", "Setting", "choose", "evaluate_grid", "grid", "load_setting", "prepare",
           "render_md", "score_setting", "tune"]
