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
| 6 | 2026-10-12 | MOT17 | yolox_s roi with the setting from run 5, evaluated once | 90.0% / 94.3% / 83.3% within ±1, MAE 0.650 / 0.600 / 0.889 (mean 89.2%): bar missed, and below run 3's 94.8%. The MOT20 setting does not transfer: lower track threshold adds phantom tracks (10.8/min vs 2.5), the longer enter delay misses short visits. V7 stays open (ADR-032) | `511ebd4`, #49 |

## What run 6 says (V7 part 2)

MOT20 was the only public set with the right annotations and terms, and ADR-032 warned it is far more
crowded than a queue. The tuned setting won on MOT20 (53.8% vs 49.4%) and lost on MOT17 (89.2% vs the
part-1 configuration's 94.8%, which was itself picked with MOT17 in the loop). Per ADR-032 the number
stands and is not used to pick between the two. Next, in order:

1. A development set that looks like the target: your own consented clips (P3-P5) are the real answer;
   until then, MOT15's static sequences (PETS09-S2L1, TUD-Stadtmitte, Venice-2, ADL-Rundle-6) need only a
   person-definition adapter (no class or visibility columns).
2. Pipeline changes that MOT20 cannot judge: a larger input for the zone crop, a person-height prior per
   zone, and a dwell-aware occupancy session (N4) instead of single tracks.
