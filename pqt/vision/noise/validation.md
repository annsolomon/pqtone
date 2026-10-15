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

## Run 2: same clips, with the visit miss rate (12 Oct 2026, PR #50, first bench run)

N1 now measures the visit miss rate: **0 of 56 visits** (≥ 1 s in a zone) went entirely untracked, which
confirms run 1's first finding: the 8% per-frame miss rate must not drop whole visits.

| Errors | n | Mean | SD | Exact | Within ±1 | Min | Max |
|---|---|---|---|---|---|---|---|
| Real pipeline | 73 | +0.055 | 0.949 | 50.7% | 95.9% | -1 | 5 |
| store-sim + profile (30 seeds) | 2,190 | +0.416 | 1.222 | 43.1% | 81.6% | -6 | 5 |

| Error | -6 | -5 | -4 | -3 | -2 | -1 | 0 | +1 | +2 | +3 | +4 | +5 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Real | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 26.0% | 50.7% | 19.2% | 2.7% | 0.0% | 0.0% | 1.4% |
| Simulated | 0.1% | 0.1% | 0.3% | 0.7% | 2.4% | 12.8% | 43.1% | 25.7% | 9.5% | 3.2% | 1.9% | 0.2% |

**KS D = 0.173, p = 0.026: rejected**, now in the other direction: the simulator over-counts (+0.42) and
spreads too wide. With misses gone, nothing in N2 makes the count *drop*: a fragment was an instant split
(old track out, new track in at the same moment), while on the clips a fragment means the person goes
untracked for a while (the zone engine closes the lost track at its last sighting; the new track starts
later), so the count is short by one during the gap and the entry/exit timing jitter's over-counts are
partly cancelled.
**Fix (this milestone):** N1 measures `tracking.fragmentation.gapMs` (last frame of the old track to the
first frame of the new one) and N2 leaves the person untracked for that long at each fragment. Profiles
without it keep the instant split.

## Run 3: same clips, with fragment gaps (12 Oct 2026, PR #50, commit `e1dc1ef`)

Measured gaps: lognormal μ = 6.60, σ = 1.20 (median 0.74 s, n = 21 fragments).

| Errors | n | Mean | SD | Exact | Within ±1 | Min | Max |
|---|---|---|---|---|---|---|---|
| Real pipeline | 73 | +0.055 | 0.949 | 50.7% | 95.9% | -1 | 5 |
| store-sim + profile (30 seeds) | 2,190 | +0.189 | 1.246 | 39.3% | 81.5% | -5 | 5 |

| Error | -5 | -4 | -3 | -2 | -1 | 0 | +1 | +2 | +3 | +4 | +5 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Real | 0.0% | 0.0% | 0.0% | 0.0% | 26.0% | 50.7% | 19.2% | 2.7% | 0.0% | 0.0% | 1.4% |
| Simulated | 0.1% | 0.3% | 1.1% | 5.2% | 18.6% | 39.3% | 23.6% | 7.4% | 2.9% | 1.4% | 0.1% |

**KS D = 0.121, p = 0.236: not rejected.** The bias is now close (+0.19 vs +0.06). The simulator is still
**wider** than reality (SD 1.25 vs 0.95; 81.5% vs 95.9% within ±1): each mechanism is applied
independently, while on the clips misses, fragments and jitter partly cancel. For scoring rules that is
the safe direction: the simulator is harsher than the camera. With 73 real samples, "not rejected" is weak
evidence; the real sample grows with our own footage (P5).

The committed profile `config/noise/mot17-yolox-s-roi.yaml` is the one measured in this run (it carries
`visitMissRate` and `fragmentation.gapMs`).

## Design 1 again, with the run 3 profile

`make noise-validate`: simulated SD 0.20, 96.1% exact, KS D = 0.241, p < 0.001. Same mechanisms, same
profile, very different errors: in our scenarios shoppers stay for minutes, so entries and exits (where
almost all count errors happen) are rare per sample; on MOT17's streets people cross a zone every few
seconds. Count error is a property of camera *and* scene, which is why design 2 is the test of the model
and design 1 is not.

## Conclusion (N5)

- The model is not rejected on the decisive same-clip test (p = 0.236), after two fixes it forced: whole
  visits are missed at the measured visit rate (0 of 56), not the per-frame rate; fragments leave a gap.
- It errs on the harsh side (wider errors), which is acceptable for rule scoring (N3, N4).
- Count error alone does not describe what noise does to rules: ID switches and fragments also cut one
  person's visit into several tracks, which shortens per-track dwell. N3 and N4 score that directly.
- Re-run on our own footage at P5; the procedure and code stay as they are.
