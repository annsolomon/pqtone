# vision-lab experiments

Every V7 run, in order (plan: `docs/plans/tier-2-real-vision.md`, V7). Open-dataset track: MOT17-02, 04 and
09 are the test set (ADR-013); MOT20's training sequences are the development set (ADR-032). Proxy bar per
MOT17 sequence: ≥ 95% of 1-second samples within ±1 and MAE ≤ 0.5. Full reports:
`reports/mot17-bench.md` and the `vision-results` branch.

| # | Date | Set | Change | Result | Commit / PR |
|---|---|---|---|---|---|
| 1 | 2026-10-12 | MOT17 | V1 detection, YOLOX-S full frame 640 px | AP50 0.555 / 0.694 / 0.843; recall the problem on 02, 04 | `54b5683`, #36 |
| 2 | 2026-10-12 | MOT17 | V2 ByteTrack defaults on run 1 | HOTA 0.414 / 0.444 / 0.534 | `fffea75`, #37 |
| 3 | 2026-10-12 | MOT17 | V7 part 1: four candidates (yolox_s full, roi, tiled; yolox_m roi), zones and timing from the committed configs | best yolox_s roi: 90.0% / 100.0% / 94.4% within ±1, MAE 0.650 / 0.571 / 0.500; bar missed; yolox_s roi selected | `1604229`, #43 |
| 4 | 2026-10-12 | MOT17 | Same as run 3 (yolox_s roi), repeated with the N1 noise measurement | identical counts (deterministic); noise profile `config/noise/mot17-yolox-s-roi.yaml` | `715c2d5`, #46 |
| 5 | 2026-10-12 | MOT20 | V7 part 2: 108-setting grid (tracker threshold and buffer, enter/exit delay, lost timeout), selection fixed in `tune.py` | no setting passes on MOT20 (crowded: 0/4 clips); chosen track 0.4, buffer 30, enter 1.0 s, exit 0.3 s, lost 1.0 s: mean within ±1 53.8% vs 49.4% for the part-1 configuration; exit delay and lost timeout made no difference (ties broken by grid order). Committed as `config/tuned/yolox_s-roi.json` | `83d0c64`, #49 |
| 6 | | MOT17 | yolox_s roi with the setting from run 5, evaluated once | pending (vision-bench.yml) | |
