# Rules-engine scale test (milestone R5)

**Question:** how does the live pipeline (event-core ingest → Kafka → rules-engine → incidents) behave with
50 simulated stores at 10x real time, and where are the limits?

## Method

- `make scale` (harness: `scripts/scale.py`) simulates N copies of store-001 (`store-s001` … `store-sNNN`)
  in **one** run with the S2 `stores:` feature: rush-hour traffic (180 shoppers per hour per store), one
  register open at the start and slow staff reactions, so every store raises queue incidents. Events of all
  stores interleave in event-time order and are streamed to `store.events.raw` at SPEED x real time.
- While it runs, every 5 s: events stored by event-core (Postgres), events received by rules-engine and its
  worst consumer lag (`kafka_consumer_fetch_manager_records_lag_max`) from the rules-engine `/metrics`.
- After the send: wait until every event is stored and seen by the rules engine, and every store's watermark
  has reached its horizon (from `pip.pipeline_heartbeat`), then until incidents stop changing.
- Then: incidents vs ground truth with the scorer's own matching (`pip_scorer.match.score`), the Q2
  wall-clock processing latency (decision event received → incident row, `pip_scorer.latency`), the
  rules-engine state directory size (`du` in the container) and its memory (`docker stats`).
- `make scale-ladder` runs the documented ladder: stream threads 1/2/4 at 40x, then 10x and 160x at
  2 threads, 50 stores, 30 simulated minutes each, and writes the report and three SVG charts.
- The numbers below come from the manual workflow `.github/workflows/scale.yml` on a GitHub-hosted
  `ubuntu-24.04` runner (its vCPU and memory are recorded with the results), which commits them to a review branch. They are measured,
  not estimated; the runner is shared hardware, so treat them as a floor, not a capacity guarantee.

## Results

See **Measured results** below (generated from the run; `docs/perf/results/*.json` keeps every sample).

<!-- RESULTS -->

## Reading the limits

To be completed from the measured run: which stage saturates first (event-core's raw consumer
concurrency, Postgres inserts, or rules-engine processing), what extra stream threads buy with
`store.events.v1` at 12 partitions, and the state size per store.
