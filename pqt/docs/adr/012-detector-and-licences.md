# ADR-012: Person detector and vision licences

- **Status:** accepted
- **Date:** 2026-10-12
- **Milestone:** P1 (Tier 2)

## Context

Tier 2 needs a person detector that can ship inside a commercial SaaS and on an edge box we lease to
shops. The best-known option, Ultralytics YOLO (v8, 11 and later), is AGPL-3.0: shipping it means either
an Ultralytics enterprise licence or releasing our source under AGPL. CLAUDE.md requires an ADR for any
AGPL, SSPL, BSL or non-commercial dependency, and a stated licence for every other one.

The detector must also run on CPU: on GitHub runners for the open-dataset benchmark, in Codespaces, and
on an Intel N100-class edge box, where queue counting needs only 2–5 detections per second.

## Decision

1. **Ship YOLOX-S first.** YOLOX (Megvii, `github.com/Megvii-BaseDetection/YOLOX`) is Apache-2.0, code
   and released weights alike. We run the official ONNX export (`yolox_s.onnx`, release `0.1.1rc0`)
   with ONNX Runtime, so neither PyTorch nor the YOLOX Python package is a runtime dependency. The model
   file is downloaded by `vision/lab/scripts/fetch-models.sh` and checked against a pinned SHA-256; it
   is never committed.
2. **Keep the detector behind an interface** (`Detector.detect(frame) -> list[Box]`). V7 may try one
   challenger, chosen from Apache-2.0 models only: YOLOX-M/L (same family), RT-DETRv2 or D-FINE.
   RF-DETR is allowed in its Nano to Large sizes (Apache-2.0) and **not** in XL or 2XL, which use
   Roboflow's PML licence.
3. **Ultralytics is for learning only.** It may be used in a personal notebook; it never enters
   `pyproject.toml`, a Docker image, or a model file we ship.
4. **Tracking is our own ByteTrack implementation**, written from the paper (Zhang et al., 2022,
   arXiv:2110.06864) rather than copied. The reference implementation is MIT, so either route is clean,
   but our own keeps the dependency tree small and the code reviewable.
5. **Weights are a licence question of their own.** The YOLOX weights were trained on COCO 2017. COCO's
   annotations are CC BY 4.0; its images come from Flickr under their owners' terms. Training on COCO and
   shipping the resulting weights is standard industry practice, but it is a residual risk we record
   here and revisit before the first paid contract (MASTER-README P9, open-source compliance pack).

## Dependencies added by vision-lab

| Package | Licence | Why |
|---|---|---|
| numpy | BSD-3-Clause | Arrays |
| opencv-python-headless | Apache-2.0 (OpenCV ≥ 4.5); the wheel bundles an LGPL-2.1 FFmpeg build, dynamically linked | Image and video decode, resize, homography |
| onnxruntime | MIT | Runs the YOLOX ONNX model on CPU |
| scipy | BSD-3-Clause | Hungarian assignment in the tracker |
| pyarrow | Apache-2.0 | Parquet detection cache (the replay backend) |
| PyYAML | MIT | Camera and zone configuration |
| jsonschema | MIT | Event validation against `schemas/` |
| pytest | MIT | Tests |

Tracking metrics (HOTA, MOTA, IDF1) are implemented in `pqt_vision/metrics.py` from the TrackEval
definitions instead of adding py-motmetrics or TrackEval (V2).

Later milestones add PyAV (BSD-3-Clause) for RTSP at the edge, MediaMTX (MIT) for simulated cameras
and toxiproxy (MIT) for chaos tests; each is restated in its milestone's PR.

## Consequences

- No AGPL code or weights in anything we ship; the licence table above goes into the Tier 2 SBOM.
- ONNX Runtime keeps the edge image small and lets us switch to OpenVINO (Apache-2.0) later for Intel
  boxes without changing the interface.
- YOLOX-S is not the most accurate detector available. If V7 cannot reach the bar with it, the
  challenger route above applies before any licence question is reopened.
