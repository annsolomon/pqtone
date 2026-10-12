# MOT17 bench results (open-dataset track)

Aggregates from `.github/workflows/vision-bench.yml` (ADR-013: MOT17 is CC BY-NC-SA 3.0, used for
benchmarking only; no images, boxes or tracks leave the runner). People = MOT17 classes 1 and 7 with
visibility ≥ 0.25; classes 2, 8, 12 and less visible people are ignore regions; matching at IoU 0.5.
Our person definition differs from the MOTChallenge evaluation, so these are not leaderboard numbers.

_MOT17: Milan, Leal-Taixé, Reid, Roth, Schindler, "MOT16: A Benchmark for Multi-Object Tracking",
arXiv:1603.00831._

## V1: detection (YOLOX-S, ONNX Runtime 1.20.1, CPU, 640 × 640 input)

GitHub-hosted runner, 4 vCPU. Run on commit `54b5683` (PR #36), 12 Oct 2026.

| Sequence | Frames | GT people | AP50 | P@0.3 | R@0.3 | P@0.5 | R@0.5 | ms/frame |
|---|---|---|---|---|---|---|---|---|
| MOT17-02 | 600 | 11378 | 0.555 | 0.827 | 0.448 | 0.915 | 0.361 | 172 |
| MOT17-04 | 1050 | 42959 | 0.694 | 0.932 | 0.511 | 0.939 | 0.373 | 172 |
| MOT17-09 | 525 | 4092 | 0.843 | 0.841 | 0.845 | 0.933 | 0.831 | 171 |

Precision is high everywhere; **recall is the problem on the two wide shots** (02, 04), where distant
people are 20–60 px tall in a 1920 × 1080 frame and shrink by a factor of three at 640 px input. The
close-up sequence (09) is fine. V7 addresses recall.

## V2: tracking (ByteTrack defaults on the cached YOLOX-S detections)

Run on commit `fffea75` (PR #37), 12 Oct 2026. Tracker: track 0.5, low 0.1, new track 0.6, match 0.8,
buffer 30 frames at 30 fps, score fusion on. Metrics from `pqt_vision/metrics.py` (TrackEval definitions).

| Sequence | HOTA | DetA | AssA | MOTA | IDF1 | IDSW | MT | ML | GT ids | Track ids |
|---|---|---|---|---|---|---|---|---|---|---|
| MOT17-02 | 0.414 | 0.302 | 0.568 | 0.332 | 0.465 | 21 | 14 | 37 | 62 | 55 |
| MOT17-04 | 0.444 | 0.352 | 0.562 | 0.391 | 0.528 | 28 | 17 | 37 | 83 | 66 |
| MOT17-09 | 0.534 | 0.636 | 0.449 | 0.733 | 0.577 | 31 | 19 | 4 | 27 | 34 |

Detection accuracy (DetA) limits HOTA on 02 and 04, as the V1 recall predicted; association (AssA)
is similar across sequences.
