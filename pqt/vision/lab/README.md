# vision-lab (`pqt-vision`)

Tier 2, Project 6. Turns camera video into the same CloudEvents store-sim produces. Plan:
`docs/plans/tier-2-real-vision.md`. Decisions: ADR-012 (detector, licences), ADR-013 (MOT17 terms).
Privacy: `docs/privacy/vision-data.md`.

```
source (video file, image directory, MOT17 sequence)
  → Detector.detect(frame) → boxes        YOLOX on ONNX Runtime, or replay from a Parquet cache
  → (V2+) tracker → zones → events
```

## Commands

```bash
make dev-setup                                   # installs pqt-vision into .venv with sim and scorer
.venv/bin/pqt-vision fetch-model yolox_s         # → ~/.cache/pqt/models (or $PQT_MODEL_DIR), SHA-256 checked
.venv/bin/pqt-vision detect --source clip.mp4 --model yolox_s --out vision/lab/data/runs/r1/clip
.venv/bin/pqt-vision cache-info vision/lab/data/runs/r1/clip
.venv/bin/pqt-vision eval-det --seq <MOT17 sequence dir> --dets <cache dir>
.venv/bin/pqt-vision bench-mot17 --root <MOT17/train> --model yolox_s --out .work/vision-bench/yolox_s
```

`detect` writes a part file every `--chunk` frames; running it again with the same `--out` resumes and
refuses to mix detector settings. Detections are cached at a low threshold (0.05) so the operating
threshold can be chosen later without re-running the model.

## Where things run

| What | Where |
|---|---|
| Unit tests (synthetic frames only) | `make test-fast`, CI `unit` job |
| MOT17 benchmark | `.github/workflows/vision-bench.yml` on pull requests touching `pqt/vision/**` (report as a check annotation) and by hand (report committed to the `vision-results` branch) |
| GPU detection on your own consented clips | `notebooks/detect.ipynb` on Colab Pro; caches come back to `vision/lab/data/runs/` (git-ignored) |

Nothing under `vision/` may be a video, image, model file or detection cache in git; a test enforces it.
