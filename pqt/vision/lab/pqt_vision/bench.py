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

from . import __version__, cache
from .detect.replay import ReplayDetector
from .detect.yolox import YoloxOnnxDetector
from .evaluate import evaluate
from .metrics import evaluate_tracking
from .models import MODELS, resolve
from .mot import load_gt
from .runner import run_detection
from .sources import open_source
from .track import ByteTrackConfig, frame_times, run_tracking, write_tracks

STATIC_SEQUENCES = ("MOT17-02", "MOT17-04", "MOT17-09")
ATTRIBUTION = ("MOT17: Milan, Leal-Taixé, Reid, Roth, Schindler, \"MOT16: A Benchmark for Multi-Object "
               "Tracking\", arXiv:1603.00831. Licensed CC BY-NC-SA 3.0; used for benchmarking only (ADR-013).")
DETECT_CONF = 0.05


def sequence_dir(root: Path, seq: str, variant: str = "FRCNN") -> Path:
    """MOT17 ships each sequence three times (one per public detector); images and GT are identical."""
    for cand in (root / f"{seq}-{variant}", root / seq):
        if (cand / "seqinfo.ini").is_file():
            return cand
    raise FileNotFoundError(f"{seq} not found under {root}")


def bench_mot17(root: str | Path, *, model: str, out: str | Path, sequences: tuple[str, ...] = STATIC_SEQUENCES,
                threads: int | None = None, max_frames: int | None = None,
                tracker: ByteTrackConfig | None = None) -> dict:
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    model_path = resolve(model)
    det = YoloxOnnxDetector(model_path, name=model, conf=DETECT_CONF, threads=threads)
    per_seq = {}
    for seq in sequences:
        sdir = sequence_dir(root, seq)
        src = open_source(sdir)
        stats = run_detection(src, det, out / "dets" / seq, max_frames=max_frames)
        meta, by_frame = cache.read(out / "dets" / seq)
        gt = load_gt(sdir)
        ev = evaluate(by_frame, gt, frames=meta["frames"])
        replay = ReplayDetector(out / "dets" / seq)
        tracks = run_tracking(replay, tracker)
        write_tracks(out / "tracks" / f"{seq}.parquet", tracks, times=frame_times(replay),
                     meta={"tracker": (tracker or ByteTrackConfig()).as_dict(), "detector": meta["detector"]})
        tr = evaluate_tracking({f: gt.people(f) for f in meta["frames"]}, {f: gt.ignore(f) for f in meta["frames"]},
                               {f: [(t.track_id, t.box) for t in ts] for f, ts in tracks.items()}, meta["frames"])
        per_seq[seq] = {"source": src.info.as_dict(), "run": stats.as_dict(), "detection": ev.as_dict(),
                        "tracking": tr}
    report = {
        "kind": "pqt.vision-bench/v1", "stage": "V2 detection + tracking",
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": os.environ.get("GITHUB_SHA", "local"), "pqtVision": __version__,
        "model": {"name": model, **({"licence": MODELS[model].licence} if model in MODELS else {}),
                  **{k: v for k, v in det.describe().items() if k in ("sha256", "input", "nms", "onnxruntime")}},
        "detectConf": DETECT_CONF, "tracker": (tracker or ByteTrackConfig()).as_dict(),
        "runner": {"cpus": os.cpu_count(), "threads": threads, "python": platform.python_version(),
                   "machine": platform.machine()},
        "sequences": per_seq, "attribution": ATTRIBUTION,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_md(report), encoding="utf-8")
    return report


def render_md(r: dict) -> str:
    lines = [f"# Vision bench: {r['model']['name']} on MOT17 (stage: {r['stage']})", "",
             f"Commit `{r['commit'][:12]}`, {r['createdAt']}, {r['runner']['cpus']} CPUs, "
             f"ONNX Runtime {r['model'].get('onnxruntime', '?')}, input {r['model'].get('input')}.", "",
             "| Sequence | Frames | GT people | AP50 | P@0.3 | R@0.3 | P@0.5 | R@0.5 | ms/frame |",
             "|---|---|---|---|---|---|---|---|---|"]
    for seq, s in r["sequences"].items():
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
    lines += ["", "People = MOT17 classes 1 and 7 with visibility ≥ 0.25; classes 2, 8, 12 and less visible "
              "people are ignore regions. Matching at IoU 0.5.", "", f"_{r['attribution']}_", ""]
    return "\n".join(lines)
