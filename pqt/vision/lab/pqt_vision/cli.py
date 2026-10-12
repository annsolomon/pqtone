"""`pqt-vision` command line."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cache, models


def _detect(a: argparse.Namespace) -> int:
    from .detect.yolox import YoloxOnnxDetector
    from .runner import run_detection
    from .sources import open_source

    src = open_source(a.source, fps=a.fps)
    det = YoloxOnnxDetector(models.resolve(a.model), name=Path(a.model).stem, conf=a.conf, nms_iou=a.nms,
                            device=a.device, threads=a.threads)
    stats = run_detection(src, det, a.out, stride=a.stride, max_frames=a.max_frames, chunk=a.chunk)
    print(json.dumps({"source": src.info.as_dict(), "detector": det.describe(), "run": stats.as_dict()}, indent=2))
    return 0


def _fetch(a: argparse.Namespace) -> int:
    for name in a.names:
        print(models.fetch(name, directory=Path(a.dir) if a.dir else None))
    return 0


def _info(a: argparse.Namespace) -> int:
    meta = cache.read_meta(a.cache)
    if meta is None:
        print(f"no detection parts in {a.cache}", file=sys.stderr)
        return 1
    print(json.dumps({"detector": meta["detector"], "source": meta["source"], "framesCovered": len(meta["frames"]),
                      "firstFrame": meta["frames"][0], "lastFrame": meta["frames"][-1]}, indent=2))
    return 0


def _eval_det(a: argparse.Namespace) -> int:
    from .evaluate import evaluate
    from .mot import load_gt

    meta, by_frame = cache.read(a.dets)
    ev = evaluate(by_frame, load_gt(a.seq, min_vis=a.min_vis), frames=meta["frames"])
    print(json.dumps(ev.as_dict(), indent=2))
    return 0


def _bench(a: argparse.Namespace) -> int:
    from .bench import STATIC_SEQUENCES, bench_mot17

    seqs = tuple(a.sequences.split(",")) if a.sequences else STATIC_SEQUENCES
    r = bench_mot17(a.root, model=a.model, out=a.out, sequences=seqs, threads=a.threads, max_frames=a.max_frames)
    print((Path(a.out) / "report.md").read_text(encoding="utf-8"))
    return 0 if r["sequences"] else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pqt-vision", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="run a detector over a clip into a resumable Parquet cache")
    d.add_argument("--source", required=True, help="MOT sequence dir, image dir (with --fps) or video file")
    d.add_argument("--fps", type=float)
    d.add_argument("--model", default="yolox_s", help="registry name or path to an .onnx file")
    d.add_argument("--out", required=True, help="cache directory (re-running resumes)")
    d.add_argument("--conf", type=float, default=0.05)
    d.add_argument("--nms", type=float, default=0.45)
    d.add_argument("--stride", type=int, default=1)
    d.add_argument("--max-frames", type=int)
    d.add_argument("--chunk", type=int, default=300)
    d.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    d.add_argument("--threads", type=int)
    d.set_defaults(fn=_detect)

    f = sub.add_parser("fetch-model", help="download registry models and verify their SHA-256")
    f.add_argument("names", nargs="+", choices=sorted(models.MODELS))
    f.add_argument("--dir")
    f.set_defaults(fn=_fetch)

    i = sub.add_parser("cache-info", help="what produced a detection cache and what it covers")
    i.add_argument("cache")
    i.set_defaults(fn=_info)

    e = sub.add_parser("eval-det", help="AP50 and precision/recall of a cache against MOT ground truth")
    e.add_argument("--seq", required=True)
    e.add_argument("--dets", required=True)
    e.add_argument("--min-vis", type=float, default=0.25)
    e.set_defaults(fn=_eval_det)

    b = sub.add_parser("bench-mot17", help="detect and evaluate the fixed-camera MOT17 sequences (ADR-013)")
    b.add_argument("--root", required=True, help="the MOT17/train directory")
    b.add_argument("--model", default="yolox_s")
    b.add_argument("--out", required=True)
    b.add_argument("--sequences", help="comma-separated, default MOT17-02,MOT17-04,MOT17-09")
    b.add_argument("--threads", type=int)
    b.add_argument("--max-frames", type=int)
    b.set_defaults(fn=_bench)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
