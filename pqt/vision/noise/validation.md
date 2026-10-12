# N5: does the noise model reproduce real count errors?

Plan: `docs/plans/tier-2-real-vision.md`, N5. The simulator (N2) does not replay the count errors measured
on real clips; it applies the profile's *mechanisms* (whole visits missed, phantom tracks, fragments, ID
switches, entry/exit timing jitter) to clean visits. If the mechanisms are right, the count errors they
cause should have the same distribution as the real ones. Test: two-sample Kolmogorov-Smirnov on zone
occupancy error (predicted minus true, one sample per second), α = 0.05, fixed before the first run.

Two designs, in the order they were run:

| Design | Compares | Confounds | Where |
|---|---|---|---|
| 1. Our scenarios | store-sim queue scenarios + profile vs the profile's measured `queueCountError` | Scene: shoppers dwell for minutes, MOT17's walkers cross a zone in seconds, so timing errors weigh differently | `make noise-validate` (`sim/store_sim/noise_validate.py`) |
| 2. Same clips (decisive) | store-sim applied to the ground-truth visits of MOT17-02/04/09 (30 seeds) vs the real pipeline's errors on the same clips | None from the scene: same people, same comings and goings | `vision-bench.yml` (`pqt_vision/bench.py`, `_validate_noise`) |

## Run 1: our scenarios (12 Oct 2026, profile `mot17-yolox-s-roi` from PR #46)

Scenarios baseline, rush_hour, register_delay, staff_shortage, flash_sale, two_queues × seeds 11, 42, 1337;
samples with true occupancy 2-8.

| Errors | n | Mean | SD | Exact | Within ±1 | Min | Max |
|---|---|---|---|---|---|---|---|
| Real (MOT17, measured) | 73 | +0.055 | 0.949 | 50.7% | 95.9% | -1 | 5 |
| store-sim + profile | 521,658 | -0.344 | 0.615 | 68.8% | 95.0% | -5 | 2 |

| Error | -4 | -3 | -2 | -1 | 0 | +1 | +2 | +5 |
|---|---|---|---|---|---|---|---|---|
| Real | 0.0% | 0.0% | 0.0% | 26.0% | 50.7% | 19.2% | 2.7% | 1.4% |
| Simulated | 0.1% | 0.5% | 4.4% | 25.0% | 68.8% | 1.2% | 0.0% | 0.0% |

**KS D = 0.221, p = 0.001: rejected.** Two findings:

1. **The simulator under-counts where the pipeline does not** (bias −0.34 vs +0.06; −2 errors 4.4% vs none).
   Cause: N1 measures the miss rate per person-*frame* (8.05% of frames a person in the zone has no track),
   but N2 applied it per *visit*, dropping 8% of whole visits. Most missed frames are short gaps the zone
   engine bridges, so they barely move counts; a missed visit removes a person for minutes.
   **Fix (this milestone):** N1 now also measures `detection.visitMissRate`, the share of visits (≥ 1 s in
   the zone) during which no track ever matched the person, and N2 drops visits at that rate when a profile
   has it. Profiles without it keep the old behaviour as a documented upper bound.
2. **The simulator rarely over-counts** (+1: 1.2% vs 19.2%). Over-counts on the clips most likely come
   from timing jitter at entries and exits and from short-lived extra tracks; both scale with how often
   people come and go, which is far higher in MOT17's street scenes than in a queue. Design 1 cannot separate the mechanism
   from the scene, which is why design 2 is the decisive test.

## Run 2: same clips

Pending: the first `vision-bench.yml` run with N5 (measures `visitMissRate`, then runs the same-clip test).
