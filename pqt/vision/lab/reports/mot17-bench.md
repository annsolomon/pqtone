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
