# ADR-029: A wall-clock processing-latency gate in the e2e score

- **Status:** accepted
- **Date:** 2026-10-09
- **Milestone:** Q2

## Context

The scorer measured rule latency in event time only: how long after the episode's ground-truth
time the incident was *detected*, in the store's clock. That says nothing about how long the
pipeline itself takes to turn evidence into an incident a reviewer can see. Milestone Q2 asks
for wall-clock processing latency in e2e, p50 and p95, with a gate.

## Decision

1. **What is measured.** For each incident of the e2e run: `incident.created_at` minus the
   `received_at` of its *decision event*, the first stored event of the same store and run whose
   event time is at or after `detected_at + grace`. That event is the one that moved the
   rules-engine watermark past the detection point, so the measure covers event-core → outbox →
   Kafka → rules-engine → incidents topic → incident row, and excludes the grace wait, which is
   by design (`docs/learn/watermarks.md`).
2. **Gate.** `thresholds.yaml` `e2e.processingLatencyP95: PT10S`. An incident with no decision
   event also fails the gate rather than being skipped.
3. **Why 10 s.** The first measured run (PR #12, CI run on a 2-core hosted runner with the whole
   stack up) gave p50 240 ms, p95 582 ms, max 582 ms over 6 incidents. 10 s leaves room for a
   noisy shared runner while still catching a real regression (a stuck outbox relay, a consumer
   lag, a rebalance loop), which shows up as tens of seconds. The console's alert budget
   (event → screen p95 < 30 s, MASTER-README §9.5) is grace (30 s) plus this.

## Consequences

- `score.json` carries `metrics.processingLatencyMs`; `score.md` and the PR comment print it.
- With 6 incidents per run the p95 is the maximum; the number gets more meaningful as the e2e
  scenario grows. Tightening the gate needs a new ADR with more runs behind it.
- Offline runs have no wall clock, so the gate applies to e2e only.
