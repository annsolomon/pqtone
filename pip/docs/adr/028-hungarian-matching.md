# ADR-028: Hungarian matching in the scorer

- **Status:** accepted
- **Date:** 2026-10-09
- **Milestone:** Q5

## Context

The scorer pairs ground-truth episodes with detected incidents, one to one, inside a window
(30 s before to 120 s after, `thresholds.yaml`). Tier 1 shipped a greedy matcher: sort every
in-window pair by time distance and take the closest first. `ARCHITECTURE.md` §10.1 specified
the Hungarian algorithm, and listed greedy as a known gap.

Greedy can under-count true positives. Episodes at 0 s and 60 s with incidents at 50 s and
130 s: greedy takes the closest pair (60 s ↔ 50 s, 10 s apart), and the 0 s episode then has
nothing in range, so it scores 1 TP, 1 FP, 1 FN. Pairing 0 s ↔ 50 s and 60 s ↔ 130 s scores 2 TP.
A rule that is right would be reported as wrong, and the gate would push us to "fix" it.

## Decision

1. Per (rule, store, key) group, solve an assignment problem: an in-window pair costs its time
   distance in ms; an out-of-window pair costs more than all in-window pairs together. The
   minimum-cost assignment therefore has the **most matches**, and among those the **least
   total time distance**. Out-of-window pairs in the result are dropped.
2. A pure-Python O(n²m) Hungarian implementation (`scorer/pip_scorer/assignment.py`), no new
   dependency. scipy's `linear_sum_assignment` would do the same, but it would add a large
   native dependency to the tools image for groups that hold a handful of rows.
3. No greedy fallback for large sets: a group is one rule, one store and one key (a queue, a
   track, a zone), so it stays small. A 200 × 200 group takes well under a second.
4. Property tests: 300 random cases checked against a brute-force search over every matching,
   and 100 cases checking the result never has fewer matches than greedy.

## Consequences

- True positives can only go up compared with greedy; precision and recall never drop because
  of the matcher, so the regression gate is unaffected. A rise in a baseline row after this
  change is the matcher being fairer, not the rule changing.
- Latency percentiles may shift slightly, because the chosen pairs can differ from greedy's.
- Determinism holds: the algorithm has no randomness, so the same inputs give the same pairs.
