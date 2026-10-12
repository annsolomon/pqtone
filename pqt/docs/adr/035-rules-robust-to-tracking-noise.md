# ADR-035: Rules robust to tracking noise: entry/exit `how`, queue hand-over, visit stitching

- **Status:** accepted
- **Date:** 2026-10-13
- **Milestone:** N4 (Tier 2, `docs/plans/tier-2-real-vision.md`)

## Context

N3 re-scored the rule matrix under the noise profile measured on MOT17 (N1, validated in N5). With
the reference engine standing in for rules-engine, under the measured profile:

| Rule | Clean | Measured noise, before N4 |
|---|---|---|
| R-DWELL-001 (fitting rooms > 10 min) | P 1.00 / R 1.00 | recall 0.00-0.02 in every scenario |
| R-FOOT-001 (footfall spike) | 1.00 / 1.00 | false spikes (baseline, multi_store) |
| R-QUEUE-001 (queue ≥ 6 for 60 s) | 1.00 / 1.00 | misses; once the noise model also made a new track wait the zone engine's 5 s minimum dwell before rejoining the queue (as the real zone engine does), recall fell to 0.14-0.71 |

The cause is one mechanism: the tracker loses people and finds them again under new identities (1.84
identity switches per track-minute, fragmentation 0.33). A 12-minute fitting-room visit becomes thirty
short tracks; every identity change in a queue drops a member until the new track has stood 5 s; every
new identity in the entrance counts as a person coming in.

Thresholds stay as they are: the fix belongs in perception and in the rules, not in the gate.

## Decision

1. **Say how an entry or exit happened.** `store.zone.entered` / `zone.exited` 1.1.0 add an optional
   `how` (additive; schema-compat passes): an entry `appeared` when the track was never seen outside the
   zone, otherwise `crossed`; an exit is `lost` when the track closed because it was no longer seen,
   otherwise `crossed`. Absent means crossed, so every 1.0.0 producer keeps working.
2. **Queue hand-over in the zone engine (V4).** When a queue member is lost and a track appears inside
   the same queue zone within 5 s (`handover_s`), the new track takes the place at once: `queue.left`
   for the old and `queue.joined` for the new at the same instant, and the emitter reports one
   `queue.length` per queue and instant, so the length does not move. Cost: queue-zone events are held
   up to lost-after + 5 s + 1 s before release (event times are unchanged; only edge latency grows).
3. **R-DWELL-001 1.1.0, `stitch: PT30S`.** A visit whose track is lost inside the dwell zone stays open
   30 s; a track that appears in the zone within that time continues it under the original key (the most
   recently lost visit first). A limit that passes during the gap is reported when the visit continues.
   Real entries never continue anyone's visit. 30 s covers well over 99% of the measured gaps (lognormal,
   median 0.74 s).
4. **R-FOOT-001 1.1.0, `countAppeared: false`.** Footfall counts only entries that crossed in.
5. **The simulator mirrors the zone engine** (store-sim `faults.vision`): split pieces carry `appeared` /
   `lost`; a new piece takes over queue membership at once within 5 s and otherwise waits the 5 s dwell;
   `queue.length` is reported once per instant.
6. **The ground-truth reference does not stitch.** Ground truth is what people did; on clean streams there
   is no `lost` or `appeared`, so the rules behave exactly as 1.0.0 and the clean gate is unchanged.
7. **The N4 gate is on**: `make offline-noise` runs with `NOISE_GATE=--gate` in `make all`: every
   enforce-mode rule must meet `scorer/thresholds.yaml` under the measured profile. 2× measured is reported,
   not gated.

## Evidence (reference engine, 10 scenarios × 3 seeds, before CI confirms with rules-engine)

Measured profile, with 1-5: every enforce-mode rule P 1.00 / R 1.00 in every scenario. 2× measured:
R-DWELL-001 restricted_dwell R 0.96, R-QUEUE-001 restricted_dwell P 0.86; everything else 1.00.

## Consequences

- Wrong-person continuation is limited to tracking breaks: a `lost` followed by an `appeared`. Two people
  swapping identities inside a fitting room can still exchange visits; the gate measures it.
- A person hidden for more than 30 s (inside a cubicle) ends the visit: fitting-room zones should be drawn
  over the visible waiting area, not the cubicles (site survey checklist).
- The same `how` lets later rules (abandonment, staff presence) ignore tracking artefacts.
