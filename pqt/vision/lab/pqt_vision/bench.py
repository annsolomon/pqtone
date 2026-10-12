"""The MOT17 benchmark (open-dataset track, ADR-013). Runs on a GitHub runner; only aggregates leave it.

    pqt-vision bench-mot17 --root MOT17/train --model yolox_s --out .work/vision-bench

For each fixed-camera sequence: detect (low threshold, so one pass serves every threshold), evaluate
against ground truth, and write report.json and report.md. No images, boxes or tracks are written to
the report.
"""
from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import __version__, cache
from .detect.compose import Region, RoiDetector, TiledDetector, roi_for_zones
from .detect.replay import ReplayDetector
from .detect.yolox import YoloxOnnxDetector
from .evaluate import evaluate
from .metrics import evaluate_tracking
from .models import MODELS, resolve
from .counts import COUNT_START
from .mot import FrameGT, GroundTruth, load_gt
from .runner import run_detection
from .sources import open_source
from .track import ByteTrackConfig, frame_times, run_tracking, write_tracks
from .tune import Setting

STATIC_SEQUENCES = ("MOT17-02", "MOT17-04", "MOT17-09")
ATTRIBUTION = ("MOT17: Milan, Leal-Taixé, Reid, Roth, Schindler, \"MOT16: A Benchmark for Multi-Object "
               "Tracking\", arXiv:1603.00831. Licensed CC BY-NC-SA 3.0; used for benchmarking only (ADR-013).")
DETECT_CONF = 0.05
MODES = ("full", "roi", "tiled")


def _in_region(b, r: Region) -> bool:
    x, y = b.foot()
    return r.x1 <= x < r.x2 and r.y1 <= y < r.y2


def restrict(gt: GroundTruth, r: Region) -> GroundTruth:
    """Ground truth limited to people whose foot point is inside the region (ROI mode is judged there)."""
    return GroundTruth(gt.sequence, {f: FrameGT([(i, b) for i, b in fg.people if _in_region(b, r)],
                                                 [b for b in fg.ignore if _in_region(b, r)])
                                     for f, fg in gt.frames.items()}, gt.min_vis)


def sequence_dir(root: Path, seq: str, variant: str = "FRCNN") -> Path:
    """MOT17 ships each sequence three times (one per public detector); images and GT are identical."""
    for cand in (root / f"{seq}-{variant}", root / seq):
        if (cand / "seqinfo.ini").is_file():
            return cand
    raise FileNotFoundError(f"{seq} not found under {root}")


def bench_mot17(root: str | Path, *, model: str, out: str | Path, sequences: tuple[str, ...] = STATIC_SEQUENCES,
                threads: int | None = None, max_frames: int | None = None,
                tracker: ByteTrackConfig | None = None, configs: str | Path | None = None,
                schemas: str | Path | None = None, mode: str = "full", noise_out: str | Path | None = None,
                setting: Setting | None = None) -> dict:
    """mode: full (whole frame at the model's input size), roi (the zones' region plus a margin; needs a
    zone config) or tiled (2 × 2 overlapping tiles plus the full frame).

    setting: a tracker and zone-timing setting chosen on the development set (V7 part 2, `tune.py`). It
    replaces the tracker and the zone configs' enter/exit delays and lost-track timeout; zones never change."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if setting is not None:
        if tracker is not None:
            raise ValueError("pass a tracker or a setting, not both")
        tracker = setting.tracker()
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    model_path = resolve(model)
    base = YoloxOnnxDetector(model_path, name=model, conf=DETECT_CONF, threads=threads)
    per_seq = {}
    noise_samples = []
    for seq in sequences:
        sdir = sequence_dir(root, seq)
        src = open_source(sdir)
        cam_path = Path(configs) / f"{seq.lower()}.yaml" if configs else None
        region = None
        if mode == "roi":
            if cam_path is None or not cam_path.is_file():
                per_seq[seq] = {"skipped": "roi mode needs a zone config"}
                continue
            from . import camera as cam

            c = cam.load(cam_path)
            region = roi_for_zones([np.array(z.polygon) for z in c.zones], width=c.width, height=c.height)
            det = RoiDetector(base, region)
        elif mode == "tiled":
            det = TiledDetector(base)
        else:
            det = base
        stats = run_detection(src, det, out / "dets" / seq, max_frames=max_frames)
        meta, by_frame = cache.read(out / "dets" / seq)
        gt_full = load_gt(sdir)
        gt = restrict(gt_full, region) if region else gt_full
        ev = evaluate(by_frame, gt, frames=meta["frames"])
        replay = ReplayDetector(out / "dets" / seq)
        tracks = run_tracking(replay, tracker)
        write_tracks(out / "tracks" / f"{seq}.parquet", tracks, times=frame_times(replay),
                     meta={"tracker": (tracker or ByteTrackConfig()).as_dict(), "detector": meta["detector"]})
        tr_boxes = {f: [(t.track_id, t.box) for t in ts if region is None or _in_region(t.box, region)]
                    for f, ts in tracks.items()}
        tr = evaluate_tracking({f: gt.people(f) for f in meta["frames"]}, {f: gt.ignore(f) for f in meta["frames"]},
                               tr_boxes, meta["frames"])
        per_seq[seq] = {"source": src.info.as_dict(), "run": stats.as_dict(), "detection": ev.as_dict(),
                        "tracking": tr, **({"roi": region.as_list()} if region else {})}
        if cam_path is not None and cam_path.is_file():
            per_seq[seq]["counts"] = _counts(seq, cam_path, tracks, frame_times(replay), gt_full, src.info.fps, out,
                                             schemas, noise_samples if noise_out else None, setting)
    noise = _write_noise_profile(noise_samples, Path(noise_out), model=model, mode=mode) if noise_out else None
    report = {
        "kind": "pqt.vision-bench/v1", "stage": "V7 detection + tracking + counts", "mode": mode,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": os.environ.get("GITHUB_SHA", "local"), "pqtVision": __version__,
        "model": {"name": model, **({"licence": MODELS[model].licence} if model in MODELS else {}),
                  **{k: v for k, v in base.describe().items() if k in ("sha256", "input", "nms", "onnxruntime")}},
        "detectConf": DETECT_CONF, "tracker": (tracker or ByteTrackConfig()).as_dict(),
        "runner": {"cpus": os.cpu_count(), "threads": threads, "python": platform.python_version(),
                   "machine": platform.machine()},
        "setting": setting.as_dict() if setting else None,
        "sequences": per_seq, "attribution": ATTRIBUTION, **({"noiseProfile": noise} if noise else {}),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_md(report), encoding="utf-8")
    return report



def _write_noise_profile(samples: list, path: Path, *, model: str, mode: str) -> dict:
    """N1: pool the per-sequence measurements into one noise profile, validate it, flag thin evidence."""
    import yaml

    from .noise_measure import Samples, profile, thin_evidence

    pooled = Samples()
    for s in samples:
        pooled.merge(s)
    name = f"mot17-{model.replace('_', '-')}-{mode}"
    doc = profile(pooled, name=name, description=(f"Measured on MOT17 fixed-camera sequences with {model} ({mode} mode), "
                                                   "ByteTrack defaults and the V4 zone engine (open-dataset track)."))
    errors: list[str] = []
    try:
        from store_sim import vision_noise

        errors = vision_noise.validate(doc)
    except ImportError:
        errors = ["store-sim not installed: profile not validated"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Measured by pqt-vision bench-mot17 (milestone N1). Rates and distributions only.\n"
                    + yaml.safe_dump(doc, sort_keys=False, width=120), encoding="utf-8")
    return {"name": name, "file": str(path), "schemaErrors": errors, "thinEvidence": thin_evidence(doc),
            "summary": {"missRate": doc["detection"]["missRate"],
                        "falsePositivesPerMinute": doc["detection"]["falsePositivesPerMinute"],
                        "idSwitchesPerTrackMinute": doc["tracking"]["idSwitchesPerTrackMinute"],
                        "fragmentation": doc["tracking"]["fragmentation"].get("probability"),
                        "entryJitterMs": doc["timing"]["entryJitterMs"], "exitJitterMs": doc["timing"]["exitJitterMs"]}}


def _counts(seq: str, cam_path: Path, tracks: dict, times: dict[int, int], gt, fps: float, out: Path,
            schemas: str | Path | None, noise_samples: list | None = None, setting: Setting | None = None) -> dict:
    """Zone events from the tracks, ground-truth counts from the annotations, scored by pqt-scorer (V6)."""
    from . import camera as cam
    from .counts import count_events, gt_counts, write_counts
    from .emitter import contract_validator, parse_iso, schemas_dir_default, write_jsonl

    camera = cam.load(cam_path)
    lost_after_s = 2.0
    if setting is not None:
        camera, lost_after_s = setting.apply(camera), setting.lost_after_s
    events = count_events(camera, {f: [(t.track_id, t.box) for t in ts] for f, ts in tracks.items()}, times,
                          run_key=f"mot17:{seq}", lost_after_s=lost_after_s)
    check = contract_validator(schemas or schemas_dir_default())
    schema_errors = sum(len(check(e)) for e in events)
    write_jsonl(out / "events" / f"{seq}.jsonl", events)
    rows = gt_counts(gt, camera, fps=fps, every_s=1.0)
    write_counts(out / "counts" / f"{seq}.csv", rows)
    result: dict = {"camera": camera.camera, "zones": [z.id for z in camera.zones], "events": len(events),
                    "schemaErrors": schema_errors}
    try:
        from pqt_scorer import video
    except ImportError:
        result["score"] = "pqt-scorer not installed"
        return result
    gt_samples = video.read_gt(out / "counts" / f"{seq}.csv")
    result["score"] = video.score(gt_samples, events, metric="occupancy", start_ms=parse_iso(COUNT_START))
    if noise_samples is not None:
        from .noise_measure import measure_sequence
        from .pipeline import zone_events

        tl = video.timelines(events, "occupancy")
        start = parse_iso(COUNT_START)
        errs = [video.count_at(tl.get(g.zone), start + g.offset_ms) - g.count for g in gt_samples]
        zev, _ = zone_events(camera, {f: [(t.track_id, t.box) for t in ts] for f, ts in tracks.items()}, times,
                             lost_after_s=lost_after_s)
        noise_samples.append(measure_sequence(gt, {f: [(t.track_id, t.box) for t in ts] for f, ts in tracks.items()},
                                              times, camera, zev, errs))
    return result


def render_md(r: dict) -> str:
    lines = [f"# Vision bench: {r['model']['name']}, {r.get('mode', 'full')} mode, on MOT17 (stage: {r['stage']})", "",
             f"Commit `{r['commit'][:12]}`, {r['createdAt']}, {r['runner']['cpus']} CPUs, "
             f"ONNX Runtime {r['model'].get('onnxruntime', '?')}, input {r['model'].get('input')}.", "",
             *([f"Setting chosen on the development set (V7 part 2, never on MOT17): `{json.dumps(r['setting'], sort_keys=True)}`", ""]
               if r.get("setting") else []),
             "| Sequence | Frames | GT people | AP50 | P@0.3 | R@0.3 | P@0.5 | R@0.5 | ms/frame |",
             "|---|---|---|---|---|---|---|---|---|"]
    for seq, s in r["sequences"].items():
        if "skipped" in s:
            lines.append(f"| {seq} | skipped: {s['skipped']} | | | | | | | |")
            continue
        d, a3, a5 = s["detection"], s["detection"]["atThreshold"]["0.30"], s["detection"]["atThreshold"]["0.50"]
        lines.append(f"| {seq} | {d['frames']} | {d['gtPeople']} | {d['ap50']:.3f} | {a3['precision']:.3f} | "
                     f"{a3['recall']:.3f} | {a5['precision']:.3f} | {a5['recall']:.3f} | {s['run']['inferMsMean']:.0f} |")
    lines += ["", "| Sequence | HOTA | DetA | AssA | MOTA | IDF1 | IDSW | MT | ML | GT ids | Track ids |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for seq, s in r["sequences"].items():
        t = s.get("tracking")
        if t:
            lines.append(f"| {seq} | {t['HOTA']:.3f} | {t['DetA']:.3f} | {t['AssA']:.3f} | {t['MOTA']:.3f} | "
                         f"{t['IDF1']:.3f} | {t['IDSW']} | {t['MT']} | {t['ML']} | {t['gtIds']} | {t['trackerIds']} |")
    count_rows = [(seq, s["counts"]) for seq, s in r["sequences"].items() if isinstance(s.get("counts", {}).get("score"), dict)]
    if count_rows:
        lines += ["", "| Sequence | Zone | Samples | Mean truth | MAE | Within ±1 | Bias | Max error | Gate | Events | Schema errors |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for seq, c in count_rows:
            for zone, z in c["score"]["zones"].items():
                if z.get("n"):
                    lines.append(f"| {seq} | {zone} | {z['n']} | {z['meanTruth']} | {z['mae']:.3f} | {z['within1']:.1%} | "
                                 f"{z['bias']:+.3f} | {z['maxAbsError']} | {'ok' if z['pass'] else 'miss'} | {c['events']} | "
                                 f"{c['schemaErrors']} |")
        lines += ["", "Counts: zone occupancy from the emitted zone.entered/exited events vs ground truth, one sample per "
                  "second; proxy bar ≥ 95% within ±1 and MAE ≤ 0.5 per sequence (open-dataset track)."]
    nz = r.get("noiseProfile")
    if nz:
        sm = nz["summary"]
        lines += ["", f"Noise profile `{nz['name']}` (N1): miss rate {sm['missRate']}, phantom tracks "
                  f"{sm['falsePositivesPerMinute']}/min, ID switches {sm['idSwitchesPerTrackMinute']}/track-min, "
                  f"fragmentation {sm['fragmentation']}, entry jitter {sm['entryJitterMs']}, exit jitter {sm['exitJitterMs']}. "
                  f"Schema errors: {nz['schemaErrors'] or 'none'}. Thin evidence (n < 30): {nz['thinEvidence'] or 'none'}."]
    lines += ["", "People = MOT17 classes 1 and 7 with visibility ≥ 0.25; classes 2, 8, 12 and less visible "
              "people are ignore regions. Matching at IoU 0.5.", "", f"_{r['attribution']}_", ""]
    return "\n".join(lines)
