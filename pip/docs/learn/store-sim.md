# How store-sim works

Milestone S1. The simulator is a **discrete-event simulation**: time does not tick forward in
small steps; it jumps from one scheduled event to the next. Everything it produces is a pure
function of `(seed, layout, scenario, rules, sim version)`, which is what lets the scorer compare
the rules engine with ground truth and lets CI check that two runs are byte-identical.

Code: `sim/store_sim/model.py` (the store), `generate.py` (one run end to end), `faults.py` and
`vision_noise.py` (what goes wrong on the way), `reference.py` (ground-truth rules).

## The loop

```
                 ┌──────────────────────────── heap of (t, insertion#, handler, args) ───┐
 arrival times ──►  (0,0,_staff_check) (412,1,_arrive) (3801,2,_arrive) …              │
                 └────────────────────────────────┬─────────────────────────────────────┘
                                                  │ heappop: smallest t, then smallest insertion#
                                                  ▼
                                      handler(t, *args)
                                      ├─ _emit(t, …)  → records[] (a CloudEvent-to-be, numbered in emit order)
                                      └─ _at(t', …)   → push future work back on the heap
                                                  │
                                       repeat until the heap is empty (or t > duration + 30 min)
                                                  ▼
                       merge clock ticks every 10 s → sort by (t, activity before tick, emit order)
                       → renumber → events, each with a stable uuid5(run id, order)
```

`StoreModel.run`:

1. **Seed the heap.** Every shopper's arrival time is computed up front (`_arrival_times`) and
   pushed as an `_arrive` call. The first `_staff_check` goes in at t = 0.
2. **Pop, run, push.** `heapq.heappop` returns the earliest entry. A handler may emit records
   (`_emit`) and schedule later work (`_at`). Example: `_arrive` emits `zone.entered` for the
   entrance and schedules `_leave_zone` 3–8 s later; `_leave_zone` emits `zone.exited` and
   schedules `_enter_zone` for the next zone in that shopper's plan, or `_join_checkout`.
3. **Stop** when the heap is empty, or at a hard cap of duration + 30 minutes so a bad scenario
   cannot loop forever. Arrivals stop at `duration`; shoppers already inside finish their visit.
4. **Ticks.** `com.pip.store.clock.tick` every 10 s from 0 to the end (last activity rounded up
   plus 2 minutes) is merged in, after activity at the same millisecond. Ticks keep the rules
   engine's watermark moving when the store is quiet (`docs/learn/watermarks.md`).

### Why the insertion counter

Two entries can be due at the same millisecond. `heapq` compares tuples left to right, so
`(t, insertion#, …)` breaks ties by the order in which work was scheduled. Without it Python
would try to compare the handler functions, and the order of simultaneous events would depend on
something other than the model's own logic. With it, ties resolve the same way on every machine.

### Arrivals: thinning

The scenario gives arrival rates per time segment (`arrivals: [{from, to, perHour}]`).
`_arrival_times` draws a Poisson process at the **highest** rate (exponential gaps) and keeps each
candidate with probability `rate(t) / max rate`. That is the Lewis–Shedler thinning method: it
gives exactly a Poisson process with the piecewise rate, without special cases at segment
boundaries. `footfall_spike` relies on it: 110/h, then 480/h for 10 minutes, then 110/h.

### A shopper's visit

`_arrive` draws, from the movement stream: how many sales zones to visit (1 + Poisson(1.5),
capped at 5), which ones (weighted by the layout's zone weights), whether an apparel visit adds
the fitting rooms, and whether the shopper buys. Dwell times are log-normal around the layout's
median for each zone; walking between zones takes 5–20 s.

### Checkout and staff

`_join_checkout` puts the track on the queue and emits `queue.joined` and `queue.length`.
`_try_serve` hands the head of the queue to any free open register (service time log-normal,
median 75 s). Every 30 s `_staff_check` looks at the queue: at 6 or more people it schedules a
register opening after a 60–200 s **reaction delay** (that delay is what `register_delay` makes
long, and what R-ABS-001 notices); after 5 idle minutes it closes a register down to `minOpen`.

## The four random streams

```python
ss = np.random.SeedSequence(seed)
arr, move, svc, staff = ss.spawn(4)
self.r_arr   = default_rng(arr)    # arrival times (thinning)
self.r_move  = default_rng(move)   # visit plans, zone choice, dwell, walking
self.r_svc   = default_rng(svc)    # service times at the registers
self.r_staff = default_rng(staff)  # staff reaction delays
```

`SeedSequence.spawn` derives independent child seeds from one seed. Each part of the model draws
only from its own generator.

**Why that keeps runs stable when you add a new random draw.** A generator is a sequence: the
n-th call gets the n-th number. With **one** shared generator, adding a single extra draw anywhere
(say, a random tip time at the register) shifts every later draw by one. Every arrival after the
first sale would move, every visit plan would change, and a "small" model change would produce a
completely different store, with different ground truth and different scores. You could no longer
tell whether a rule got better or the data just changed.

With **separate** streams, an extra draw in `r_svc` changes only service times. The arrivals,
the visit plans and the staff delays stay exactly as they were, so the diff between two versions
of the simulator is confined to what you actually changed.
`sim/tests/test_sim.py::test_an_extra_draw_in_one_stream_leaves_the_others_alone` proves it.

The same idea goes one level up, in `generate.py`: transport faults draw from
`SeedSequence([seed, 0xFA017])` and vision noise from `SeedSequence([seed, 0x51D0])`, both
separate from the model. Turning on duplicates, late events or (in Tier 2) vision noise never
changes the store itself, only what the pipeline receives.

## From records to files

`generate.simulate`:

1. `StoreModel.run` → ordered records.
2. Each record becomes a CloudEvent (`events.make_event`). The `id` is `uuid5(run id, order)` and
   `sequence` is the zero-padded order: stable across retries and replays, which is what
   event-core's `(source, id)` dedup needs. The run id itself hashes the seed, scenario, layout,
   rules and sim version (plus the noise profile, when one is set).
3. **Ground truth** comes from `reference.py` run over the *clean, in-order* events: what a
   perfect engine would detect. The real engine later sees the *faulted* stream.
4. Vision noise (a no-op until Tier 2), then transport faults (`faults.inject`): out of order
   within grace, late beyond grace, exact duplicates, malformed events. Event times are never
   changed; only emission order and extra or broken copies.
5. `write_files`: `events.jsonl`, `ground_truth.jsonl`, `manifest.json` with SHA-256 of both.
   Same inputs → same hashes (`test_same_seed_is_byte_identical`).

No function reads the wall clock. The epoch is fixed per scenario (default 2026-01-01 09:00 UTC),
and the live sink replays it at a chosen speed.

## Questions to check yourself

1. Two shoppers' `_leave_zone` are due at the same millisecond. Which runs first? (The one
   scheduled first: lower insertion number.)
2. You make service times depend on basket size, drawn from `r_svc`. Which outputs change? (Only
   service times and what follows from them at checkout: queue lengths, register timings.)
3. Why is ground truth computed before faults are injected? (It describes what happened in the
   store; faults describe what the pipeline was told.)
4. Why can two runs of `out_of_order` differ in emission order but never in event times?
