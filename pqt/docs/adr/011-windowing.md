# ADR-011: Two ways to handle event time in the rules engine, and when to use each

- **Status:** accepted
- **Date:** 2026-10-09
- **Milestone:** R3 (after R2)

## Context

The rules engine now decides in event time in two different ways:

- **Custom per-key watermark with a reorder buffer** (R-QUEUE-001, R-DWELL-001, R-ABS-001;
  `RuleEngine`, `StoreRulesProcessor`). Each (store, run) key keeps its own stream time;
  `watermark = stream time − grace`; events above the watermark wait in a sorted buffer and are
  released in time order; timers (sustain, clear, absence deadline, dwell limit) fire as the
  watermark passes them. `docs/learn/watermarks.md` explains it.
- **Native Kafka Streams windows** (R-FOOT-001, milestone R2; `FootfallTopology`).
  `groupByKey().windowedBy(TimeWindows.ofSizeAndGrace(5 min, 30 s)).aggregate(...)`, then
  `suppress(untilWindowCloses(unbounded()))`, then a processor that keeps a 12-window history
  per (store, run) and decides on each final window.

Both meet their tests and the scorer gate. They behave differently in ways that matter when
choosing how to build the next rule.

## Comparison

| | Custom watermark + reorder buffer | Native windows + suppress |
|---|---|---|
| **Fits** | Threshold-for-duration, absence ("no X within T"), sessions: anything decided at an arbitrary point in event time | Fixed buckets of time: counts, sums, rates per window |
| **Whose clock closes things** | The key's own: each (store, run) has its own stream time, so one store never moves another's watermark | The task's: Kafka Streams tracks stream time **per partition** (task). Any store on the same partition advances it |
| **Late definition** | `time <= watermark` of that key | `time < window end + grace` is still accepted; later → dropped by the window (`dropped-records` metric) |
| **Determinism across replays** | Same events per key → same incidents, regardless of what else shares the partition | Depends on what else is on the partition. A replay whose epoch is older than events already seen on that partition is dropped as late in full |
| **Memory** | Per key: buffered events within grace (seconds of traffic), open episodes, timers. RocksDB + changelog | Per key: the open windows (current one, plus the previous one during its grace) in the window store; the suppress buffer holds one record per open window per key. Plus the 12-count history per (store, run). All changelogged |
| **Unbounded-growth risk** | Low: the buffer is bounded by grace × event rate | `BufferConfig.unbounded()`: if stream time stops (no ticks), windows never close and the buffer grows by one entry per window per key. Clock ticks every 10 s prevent it |
| **Latency to a decision** | As soon as the watermark passes the decision point: event time + grace (about 30 s) | At window close: up to window size + grace after the first event of the window (5 min 30 s for R-FOOT-001). By design: the rule is about the whole window |
| **Exactly-once** | Yes (EOS v2; state and output in one transaction) | Yes (EOS v2; window store and suppress buffer are changelogged) |
| **Code to own** | Ours: the buffer, the watermark, timer ordering (~ a few hundred lines with tests) | Kafka's: windowing, grace and suppress are library code; we own only the decision |
| **Offline equivalence** | `OfflineRunner` runs the same `RuleEngine` class | `OfflineRunner` feeds the production topology through `TopologyTestDriver` |

### The partition stream-time problem, concretely

`store.events.v1` has 12 partitions, keyed by store id. Suppose store A's replay at the
2026-01-01 epoch shares a partition with store B, live in October 2026. Stream time on that
task is October. Every window store A opens is already past `end + grace`, so all of A's
footfall is dropped as late. The custom-watermark rules on the same events are unaffected,
because they keep stream time per (store, run).

It also applies to two simulator runs of the same scenario in one stack (the e2e run and the
browser demo run both start at the scenario's epoch): the second run's early windows are
closed by the first run's stream time. CI scores only the first run, and R-FOOT-001 never
fires in `register_delay`, so no gate is affected today.

## Decision

1. **Keep both.** Use native windows for rules that are naturally fixed buckets of time
   (R-FOOT-001 and future rate or count rules). Use the custom per-key watermark for
   threshold-for-duration, absence and session rules, and for anything that must give the
   same answer whatever else shares the partition.
2. **Live data only for native windows.** In production, every producer's clock is NTP-synced
   (edge-agent alarms beyond 2 s offset, Tier 2 A5), so stores on one partition move together and
   the shared stream time is harmless. Simulated replays at historical epochs must not share a
   running rules engine with live stores. The rule for now: replays run in their own stack
   (as `make offline` and the CI e2e already do), never into a production cell.
3. **Grace is shared.** R-FOOT-001's window grace is the global `grace` (30 s), so lateness means
   the same thing for every rule.
4. **Ticks are required.** Every producer keeps sending `store.clock.tick`, which closes windows
   in quiet periods and keeps the unbounded suppress buffer at one or two entries per key.

## Consequences

- A rule author has a clear test: "is the decision about a bucket of time, or about a moment?"
- Before a pilot runs replays and live data in one place, either give replays their own topic
  and application id, or move R-FOOT-001 onto the per-key watermark. Tracked for Tier 3
  (tenant and cell layout).
- `FootfallTopologyTest` covers the window boundary (an event exactly at the end belongs to the
  next window), out-of-order within grace, and a record after close being dropped. The
  per-key behaviour of the custom engine is covered by `RuleEngineTest` and
  `WatermarkLatenessTest`.
