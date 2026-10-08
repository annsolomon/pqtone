# Watermarks in the rules engine

Milestone R1. How the rules engine decides *when* something happened, and which events
arrive too late to count. The worked sequence below is executed by
`services/rules-engine/src/test/java/com/pip/rules/WatermarkLatenessTest.java`; if this page
and that test ever disagree, the page is wrong.

## Three clocks

| Clock | What it is | Where it comes from | Used for rule decisions? |
|---|---|---|---|
| **Event time** | When the thing happened in the store | The CloudEvents `time` attribute, set by the producer | Yes, always |
| **Stream time** | The highest event time seen so far for one (store, run) | `StoreState.streamTimeMs`, updated on every accepted event | Only through the watermark |
| **Wall time** | The clock on the server processing the event | `System.currentTimeMillis()` | Never. Only the liveness heartbeat and idle-state eviction use it |

Why never wall time: the same input must always give the same incidents. If rules used wall
time, a replay at 20x speed, a slow consumer or a restart would change the answer, and the
scorer could not compare the engine with ground truth.

## The watermark

Events from a store do not arrive in order. A camera box batches, the network retries, a
Kafka partition lags. The engine needs a point in event time of which it can say: *nothing
earlier is still on its way*. That point is the watermark.

```
watermark = stream time − grace        (grace: PT30S, set in config/rules.yaml)
```

`RuleEngine.onEvent` does this for every event, per (store, run) state:

1. **Late check.** If a watermark has been reached and the event's time is **at or below** it
   (`time <= watermark`), the event is late: `lateDropped` goes up, the
   `pip.rules.events.late` metric is counted, and the event is not evaluated.
2. **Buffer.** Otherwise it goes into a reorder buffer sorted by (time, sequence, id).
3. **Advance.** Stream time becomes `max(stream time, time)`. If `stream time − grace` is now
   higher than the current watermark, the watermark moves up to it.
4. **Release.** Buffered events at or below the new watermark are released in time order.
   Before each one, every timer (queue sustain, clear, absence deadline, dwell limit) that is
   due earlier fires. After the last one, timers up to the watermark itself fire.

So an event can be up to 30 s out of order and still be evaluated exactly as if it had arrived
in order, and every rule decision happens in event time. The `reorderingWithinGraceGives…`
test in `RuleEngineTest` proves that shuffling events within grace gives identical incidents.

**Why grace exists.** Without it (grace 0), any event that arrives after a later one would be
late, and normal jitter would silently drop data. With a very large grace nothing is ever
late, but incidents are only detected `grace` after they happen and the buffer grows. 30 s is
the trade-off: it covers the jitter the edge path produces (the `out_of_order` scenario
delivers 10% of events up to 20 s late and none are dropped), and it bounds detection delay.

**Why ticks matter.** The watermark only moves when events arrive. A quiet store would never
fire "no register opened within 300 s". The simulator (and later the edge box) sends
`com.pip.store.clock.tick` every 10 s, so the watermark keeps moving with the store's clock
even when nobody is in it (`ticksMoveTheWatermarkWithoutAnyStoreActivity`).

## A worked sequence (grace 30 s)

Twelve events for one store, in arrival order. Times are in seconds of event time.

| # | Event time | Late? | Why | Watermark after |
|---:|---:|---|---|---:|
| 1 | 0 | no | first event; no watermark yet | −30 |
| 2 | 50 | no | stream time 50 | 20 |
| 3 | 25 | no | out of order, but 25 > 20: buffered | 20 |
| 4 | 20 | **yes** | 20 ≤ 20: at the watermark counts as late | 20 |
| 5 | 100 | no | stream time 100 | 70 |
| 6 | 69 | **yes** | 69 ≤ 70 | 70 |
| 7 | 71 | no | 71 > 70 | 70 |
| 8 | 100 | no | equal to stream time is fine | 70 |
| 9 | 40 | **yes** | 40 ≤ 70 | 70 |
| 10 | 130 | no | stream time 130 | 100 |
| 11 | 100 | **yes** | 100 ≤ 100 | 100 |
| 12 | 101 | no | 101 > 100 | 100 |

Dropped: 4, 6, 9 and 11 (`lateDropped = 4`). Released for evaluation by the end, in time
order: 1, 3, 2, 7, 5, 8. Events 12 (101 s) and 10 (130 s) are still in the buffer, waiting for
the watermark to pass them.

Two details the table shows:

- Event 3 is out of order (it arrived after event 2) but still counts, because it is above the
  watermark. The buffer puts it back in order before the rules see it.
- The boundary is inclusive: an event exactly at the watermark is late (events 4 and 11).

## What "late" means here, and what happens to a late event

A late event is **still stored**: event-core validated it, deduplicated it and wrote it to
PostgreSQL before it reached Kafka, so the evidence trail and the floor's read model have it.
It is only excluded from **rule evaluation**, because the decisions it could have changed
have already been made and published. It is counted in `lateDropped` (the rules heartbeat payload
and the offline runner's `offline_stats.json`) and in the `pip.rules.events.late` metric, and runbook RB-06
covers a rise in late events.

### In the `late_beyond_grace` scenario

`late_beyond_grace` delivers 2% of events 120 to 300 s late, which is always beyond grace.
Across the three matrix seeds:

| Seed | Late events injected | `lateDropped` | Mostly |
|---:|---:|---:|---|
| 11 | 68 | 68 | zone entries and exits (55), queue events (13) |
| 42 | 59 | 59 | zone entries and exits (45), queue events (13), one `register.opened` |
| 1337 | 73 | 73 | zone entries and exits (51), queue events (22) |

Every injected late event is dropped and nothing else is. The effect on rules is what the
scorer measures: a dropped `zone.exited` means a fitting-room visit looks longer than it was,
so R-DWELL-001 can fire on a visit that actually ended in time. That is why
`scorer/thresholds.yaml` gives this scenario lower gates (precision and recall ≥ 0.50) and
`scorer/baseline.json` records R-DWELL-001 precision 0.6667 there. Raising grace to 300 s would
recover those events but delay every alert by 5 minutes; dropping and measuring is the
deliberate choice.

## Compared with Flink

| | This engine | Apache Flink |
|---|---|---|
| Who computes the watermark | The engine, per (store, run) key, from that key's own events | Sources or `WatermarkStrategy` (for example bounded out-of-orderness), per source split |
| How it combines inputs | It doesn't: each store has its own watermark, so a lagging store never holds back another | An operator's watermark is the **minimum** across its inputs, so one slow partition holds back everything downstream |
| Idle inputs | Clock ticks keep each store's watermark moving | `withIdleness` marks idle splits so they stop holding back the minimum |
| Late data | Dropped from evaluation and counted; the stored event remains | Dropped by windows after `allowedLateness`, or routed to a side output |
| Formula | `max event time − grace`; late if `time <= watermark` | `max timestamp − out-of-orderness − 1 ms`; late if timestamp ≤ current watermark |

Kafka Streams has no watermarks of this kind. It tracks **stream time** per task (the highest
record timestamp per partition group) and closes **windows** at `window end + grace`, dropping
later records into the `dropped-records` metric. The rules here are threshold-for-duration
and absence rules, not windows, so the engine keeps its own per-store watermark and reorder
buffer instead. Milestone R2 adds the first rule that *is* a window (R-FOOT-001) and uses
Kafka Streams' native windows; ADR-011 (milestone R3) compares the two approaches.
