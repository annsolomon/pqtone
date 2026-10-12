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


def _tracker_config(a: argparse.Namespace):
    from .track import ByteTrackConfig

    base = ByteTrackConfig()
    return ByteTrackConfig(**{k: (getattr(a, k) if getattr(a, k, None) is not None else v) for k, v in base.as_dict().items()})


def _track(a: argparse.Namespace) -> int:
    from .detect.replay import ReplayDetector
    from .track import frame_times, run_tracking, write_tracks

    replay = ReplayDetector(a.dets)
    cfg = _tracker_config(a)
    tracks = run_tracking(replay, cfg)
    write_tracks(a.out, tracks, times=frame_times(replay), meta={"tracker": cfg.as_dict(), "detector": replay.meta["detector"]})
    ids = {t.track_id for ts in tracks.values() for t in ts}
    print(json.dumps({"frames": len(tracks), "tracks": len(ids), "boxes": sum(len(v) for v in tracks.values()),
                      "tracker": cfg.as_dict()}, indent=2))
    return 0


def _eval_track(a: argparse.Namespace) -> int:
    from .metrics import evaluate_tracking
    from .mot import load_gt
    from .track import read_tracks

    _, tracks = read_tracks(a.tracks)
    gt = load_gt(a.seq, min_vis=a.min_vis)
    frames = sorted(tracks)
    print(json.dumps(evaluate_tracking({f: gt.people(f) for f in frames}, {f: gt.ignore(f) for f in frames},
                                       tracks, frames), indent=2))
    return 0


HELD_OUT_LIMIT_M = 0.15  # plan V3: error < 15 cm on held-out points


def _calibrate(a: argparse.Namespace) -> int:
    import numpy as np
    import yaml

    from . import camera
    from .geometry import CalibrationError, fit

    doc = yaml.safe_load(Path(a.points).read_text(encoding="utf-8"))
    pts = doc.get("points") or []
    src = np.array([p["image"] for p in pts], dtype=float)
    dst = np.array([p["floor"] for p in pts], dtype=float)
    try:
        cal = fit(src, dst, ransac_threshold=a.ransac_threshold)
    except CalibrationError as e:
        print(f"calibration failed: {e}", file=sys.stderr)
        return 2
    out = Path(a.out)
    zones, membership = [], None
    if out.is_file():  # keep zones and timing already configured for this camera
        old = yaml.safe_load(out.read_text(encoding="utf-8")) or {}
        zones, membership = old.get("zones", []), old.get("membership")
    cfg = camera.calibrated_doc(camera=doc["camera"], store=doc["store"], width=doc["image"]["width"],
                                height=doc["image"]["height"], src=src, dst=dst, cal=cal, zones=zones,
                                membership=membership)
    report = {"points": len(src), "inliers": int(cal.inliers.sum()),
              "outliers": [i for i, ok in enumerate(cal.inliers) if not ok],
              "fitErrorMaxM": round(float(cal.fit_error[cal.inliers].max()), 4),
              "heldOutMaxM": cfg["calibration"]["heldOutMaxM"], "heldOutMeanM": cfg["calibration"]["heldOutMeanM"],
              "limitM": HELD_OUT_LIMIT_M}
    print(json.dumps(report, indent=2))
    if cal.held_out_error.size == 0:
        print("need at least 5 consistent points to measure held-out error; add points", file=sys.stderr)
        return 2
    if cal.held_out_max >= HELD_OUT_LIMIT_M:
        print(f"held-out error {cal.held_out_max:.3f} m is not below {HELD_OUT_LIMIT_M} m; re-measure or add points",
              file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def _propose_zones(a: argparse.Namespace) -> int:
    import yaml

    from .bench import STATIC_SEQUENCES, sequence_dir
    from .mot import load_gt
    from .mot_zones import camera_doc, propose_zone
    from .sources import read_seqinfo

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for seq in (a.sequences.split(",") if a.sequences else STATIC_SEQUENCES):
        sdir = sequence_dir(Path(a.root), seq)
        si = read_seqinfo(sdir)
        w, h, fps = int(si["imwidth"]), int(si["imheight"]), float(si["framerate"])
        prop = propose_zone(load_gt(sdir), width=w, height=h, fps=fps)
        doc = camera_doc(seq, prop, width=w, height=h)
        path = out / f"{seq.lower()}.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False, width=120), encoding="utf-8")
        print(f"{path.name}: zone {prop.polygon()} score {prop.score} mean {prop.mean} range {prop.min}-{prop.max}")
    return 0


def _events(a: argparse.Namespace) -> int:
    import os
    import secrets as _secrets
    import time

    from . import camera as cam
    from .emitter import contract_validator, parse_iso, schemas_dir_default, write_jsonl
    from .pipeline import cloud_events
    from .track import read_times, read_tracks

    if a.ephemeral_secret:
        secret = _secrets.token_bytes(32)  # benchmarks: ids are unlinkable even between runs
    else:
        value = os.environ.get(a.secret_env, "")
        if len(value) < 16:
            print(f"set {a.secret_env} to a secret of at least 16 characters (or pass --ephemeral-secret)", file=sys.stderr)
            return 2
        secret = value.encode()
    camera = cam.load(a.camera)
    _, tracks = read_tracks(a.tracks)
    start = parse_iso(a.start) if a.start else int(time.time()) * 1000
    events = cloud_events(camera, tracks, read_times(a.tracks), start_ms=start, run_key=a.run_key, secret=secret,
                          trailing_ms=int(a.trailing_s * 1000))
    if a.validate:
        check = contract_validator(a.schemas or schemas_dir_default())
        bad = [(e["type"], err) for e in events for err in check(e)]
        if bad:
            for t, err in bad[:20]:
                print(f"{t}: {err}", file=sys.stderr)
            return 1
    n = write_jsonl(a.out, events)
    counts: dict[str, int] = {}
    for e in events:
        counts[e["type"].removeprefix("com.pqt.store.")] = counts.get(e["type"].removeprefix("com.pqt.store."), 0) + 1
    print(json.dumps({"events": n, "byType": counts, "source": events[0]["source"] if events else None}, indent=2))
    return 0


def _gt_counts(a: argparse.Namespace) -> int:
    from . import camera as cam
    from .counts import gt_counts, write_counts
    from .mot import load_gt
    from .sources import read_seqinfo

    si = read_seqinfo(Path(a.seq))
    rows = gt_counts(load_gt(a.seq, min_vis=a.min_vis), cam.load(a.camera), fps=float(si["framerate"]), every_s=a.every_s)
    write_counts(a.out, rows)
    print(f"{len(rows)} samples -> {a.out}")
    return 0


def _bench(a: argparse.Namespace) -> int:
    from .bench import STATIC_SEQUENCES, bench_mot17

    seqs = tuple(a.sequences.split(",")) if a.sequences else STATIC_SEQUENCES
    r = bench_mot17(a.root, model=a.model, out=a.out, sequences=seqs, threads=a.threads, max_frames=a.max_frames,
                    configs=a.configs, schemas=a.schemas)
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

    t = sub.add_parser("track", help="ByteTrack over a detection cache into a tracks Parquet file")
    t.add_argument("--dets", required=True)
    t.add_argument("--out", required=True)
    for name, typ in (("track_thresh", float), ("low_thresh", float), ("new_track_thresh", float),
                      ("match_thresh", float), ("buffer_frames", int)):
        t.add_argument("--" + name.replace("_", "-"), dest=name, type=typ)
    t.set_defaults(fn=_track)

    et = sub.add_parser("eval-track", help="HOTA, MOTA and IDF1 of a tracks file against MOT ground truth")
    et.add_argument("--seq", required=True)
    et.add_argument("--tracks", required=True)
    et.add_argument("--min-vis", type=float, default=0.25)
    et.set_defaults(fn=_eval_track)

    c = sub.add_parser("calibrate", help="fit the image-to-floor homography from measured points (V3)")
    c.add_argument("--points", required=True, help="YAML: camera, store, image {width, height}, points [{image, floor}]")
    c.add_argument("--out", required=True, help="camera config to write, e.g. config/cameras/cam-01.yaml")
    c.add_argument("--ransac-threshold", type=float, default=0.10, help="metres")
    c.set_defaults(fn=_calibrate)

    z = sub.add_parser("propose-zones", help="queue-sized zones for MOT17 sequences from ground truth only (V4)")
    z.add_argument("--root", required=True, help="the MOT17/train directory")
    z.add_argument("--out", required=True)
    z.add_argument("--sequences")
    z.set_defaults(fn=_propose_zones)

    ev = sub.add_parser("events", help="zone and queue CloudEvents from a tracks file and a camera config (V5)")
    ev.add_argument("--tracks", required=True)
    ev.add_argument("--camera", required=True)
    ev.add_argument("--out", required=True, help="events.jsonl")
    ev.add_argument("--run-key", required=True, help="names the input (clip id); same input, same event ids")
    ev.add_argument("--start", help="wall-clock time of the clip's first frame, ISO-8601 with Z (default: now)")
    ev.add_argument("--trailing-s", type=float, default=0.0, help="clock ticks after the end, so rule windows close")
    ev.add_argument("--secret-env", default="PQT_TRACK_SECRET", help="environment variable holding the track secret")
    ev.add_argument("--ephemeral-secret", action="store_true", help="random secret for this run only (benchmarks)")
    ev.add_argument("--validate", action="store_true", help="check every event against schemas/")
    ev.add_argument("--schemas", help="schemas/ directory (default: found from the package location)")
    ev.set_defaults(fn=_events)

    g = sub.add_parser("gt-counts", help="ground-truth zone counts from MOT annotations, as the scorer's CSV (V6)")
    g.add_argument("--seq", required=True)
    g.add_argument("--camera", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--every-s", type=float, default=1.0)
    g.add_argument("--min-vis", type=float, default=0.25)
    g.set_defaults(fn=_gt_counts)

    b = sub.add_parser("bench-mot17", help="detect and evaluate the fixed-camera MOT17 sequences (ADR-013)")
    b.add_argument("--root", required=True, help="the MOT17/train directory")
    b.add_argument("--model", default="yolox_s")
    b.add_argument("--out", required=True)
    b.add_argument("--sequences", help="comma-separated, default MOT17-02,MOT17-04,MOT17-09")
    b.add_argument("--threads", type=int)
    b.add_argument("--max-frames", type=int)
    b.add_argument("--configs", help="directory of per-sequence camera configs with zones (vision/lab/config/mot17)")
    b.add_argument("--schemas", help="schemas/ directory for validating the emitted events")
    b.set_defaults(fn=_bench)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
