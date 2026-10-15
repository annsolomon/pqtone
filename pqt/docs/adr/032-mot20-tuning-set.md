# ADR-032: MOT20 training sequences as the tuning set for V7

- **Status:** accepted
- **Date:** 2026-10-12
- **Milestone:** V7 (Tier 2, open-dataset track)
- **Amends:** ADR-013 (MOT20 row: "Not used now")

## Context

V7 part 1 benchmarked four detector candidates on MOT17-02, 04 and 09. None met the proxy bar on all three
sequences; the best (YOLOX-S on the zone crop) missed it narrowly (94.8% mean within ±1). The remaining
knobs (tracker thresholds, lost-track buffer, zone enter/exit delays, lost-track timeout) could be tuned on
those three sequences, but then the reported number would measure how well we fit the test set, not how
well the pipeline counts.

A separate development set is needed: video with full person ground truth, fixed cameras, the same
annotation format, and terms no worse than MOT17's.

| Option | Licence | Fit |
|---|---|---|
| MOT17 train, other sequences | CC BY-NC-SA 3.0 | 05, 10, 11, 13 are moving cameras; 02, 04, 09 are the test set. No. |
| MOT20 train (01, 02, 03, 05) | CC BY-NC-SA 3.0 | Static, elevated cameras (two indoor, two outdoor at night); same format as MOT17. Crowded, but a queue-sized zone can still be chosen from ground truth. Chosen. |
| MOT15 static sequences | CC BY-NC-SA 3.0 | No class or visibility columns; needs a separate person definition. Not now. |
| Our own footage | Ours | The real answer (P4, P5); not recorded yet. |

## Decision

1. **MOT20's training sequences are the V7 development set, under exactly ADR-013's terms**: downloaded on
   a GitHub runner by `.github/workflows/vision-tune.yml`, never committed or copied elsewhere, no model
   trained on it, only aggregates and zone geometry leave the runner, and the attribution is carried in
   every result ("MOT20, Dendorfer et al., arXiv:2003.09003, CC BY-NC-SA 3.0").
2. **The tuning procedure is fixed in `vision/lab/pqt_vision/tune.py` before its first run**: the clips,
   the zone rule (ground truth only; a clip whose best zone is not queue-like is excluded), the 108-setting
   grid and the selection order (most clips passing the bar, then mean within ±1, then mean MAE, then grid
   order).
3. **MOT17 is evaluated with the chosen setting and the result stands.** If it misses the bar, V7 stays
   open and the next step changes the pipeline (detector, crop, membership logic), re-tunes on MOT20, and
   evaluates MOT17 again; MOT17 numbers never select a setting.
4. The review-before-revenue clause of ADR-013 applies to MOT20 too.

## Consequences

- The MOT17 number reported for V7 is an honest held-out estimate on the open-dataset track.
- MOT20 is far more crowded than a checkout queue; settings that suit heavy occlusion may not be the best
  for a quiet queue. Our own footage (P5) replaces both sets for the real gate.
- One more 5 GB download on the runner (cached after the first run, pruned to the frames used).
