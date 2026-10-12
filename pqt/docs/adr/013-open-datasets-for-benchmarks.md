# ADR-013: Non-commercial open datasets, for benchmarking only

- **Status:** accepted
- **Date:** 2026-10-12
- **Milestone:** P1 (Tier 2, open-dataset track)

## Context

Tier 2's vision code can be built and measured before any shop footage exists, but only against video
with ground truth. CLAUDE.md requires an ADR for any non-commercial licence.

| Dataset | Licence | Fit |
|---|---|---|
| MOT17 | CC BY-NC-SA 3.0 | Three fixed-camera training sequences with full person ground truth (02, 04, 09). Chosen. |
| MOT20 | CC BY-NC-SA 3.0 | Fixed cameras, but extremely crowded (over 100 people in frame): not queue-like. Not used now. |
| CrowdHuman | Non-commercial | Still images; no tracking. Not used. |
| MEVA | CC BY 4.0 | Commercial use allowed and recorded with consenting actors, but annotations cover activities, not every person in every frame. Candidate for a commercially clean benchmark later. |
| Oxford Town Centre | Withdrawn by its authors | Never used. |

## Decision

1. **MOT17 is used to benchmark during development, and for nothing else.**
   - No model we ship is trained or fine-tuned on it.
   - It is never committed to this repository or copied to Drive, Colab or any other storage: it is
     downloaded fresh on a GitHub runner by `.github/workflows/vision-bench.yml` and deleted with the runner.
   - What leaves the runner is aggregate: metrics, per-sample count errors and run metadata. No images,
     boxes, tracks or crops.
   - Reports carry the attribution: "MOT17, Milan et al., MOT16: A Benchmark for Multi-Object Tracking,
     arXiv:1603.00831, CC BY-NC-SA 3.0."
2. **The open-dataset results are a proxy, never the gate.** The Tier 2 exit gate is measured on our own
   consented footage (P4, P5). A number from MOT17 is never quoted to a customer or in sales material.
3. **Review before revenue.** Before the first paid contract, either remove MOT17 from CI or obtain
   written permission from the dataset owners. Developing a product that will be sold is arguably a
   commercial purpose, so the risk is recorded here rather than assumed away. This ADR is not legal
   advice; the P2 legal review covers it.

## Consequences

- The detector, tracker, zone logic, emitter, scorer count mode and noise measurement are exercised on
  real video months before our own footage exists.
- MOT17's scenes are street and square views, not checkouts, so the proxy flatters or penalises the
  pipeline in ways our own footage will correct. The real-footage gate stays as written.
