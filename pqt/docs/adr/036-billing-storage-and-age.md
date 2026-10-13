# ADR-036: Billing storage: a 35-day ingest age, typed append-only tables, store-scoped row-level security

- **Status:** accepted
- **Date:** 2026-10-12
- **Milestone:** B3 (X1 billing and stock sync, `docs/plans/x1-billing-sync.md`)

## Context

B1 registered the billing and stock contracts (ADR-033). Before the console can show sales next to footfall,
event-core has to accept those events, store them so they can be summed per hour, day, till, tender and SKU,
and keep each store's sales to itself. Three things stood in the way:

- event-core refuses a real event older than 24 hours (`pqt.ingest.max-age`), which protects the rules from
  stale camera data but would refuse yesterday's billing export, and a shop may send a month at once;
- `pqt.event` keeps the payload as `jsonb`, partitioned by arrival day: fine for replay and audit, a poor
  base for money sums that must be exact and fast;
- nothing in the database stops one store's query from reading another's rows, and nothing at ingest stops
  one store's edge box from writing events for another store (a client is bound to its sources, but the
  event's `storeid` was not bound to the source).

## Decision

1. **Ingest age per type.** `pqt.ingest.max-age-by-type` maps event-type prefixes to an age that replaces
   `max-age`; the longest matching prefix wins. `com.pqt.pos.` and `com.pqt.stock.` get **35 days** (a
   month's export plus four days); every other type keeps 24 hours. Simulated runs stay exempt as before.
   Storage is unaffected: partitions are by arrival day, so a 34-day-old bill lands in today's partition.
2. **Typed tables.** Migration V7 adds `pos_sale_line`, `pos_payment`, `pos_till_event`, `stock_movement`,
   `stock_level` and `pos_day_total`. `BillingProjector` writes the row in the same transaction as the event
   (dedup ledger → event → typed row → outbox), copying only the contract's fields, so the tables never
   disagree with `pqt.event` and a retried event adds nothing. The tables are append-only for every role
   (a bill is corrected by a new revision, ADR-033), partitioned by arrival day and dropped with the event
   retention (`pqt.retention.event-days`) by `ensure_billing_partitions` / `drop_billing_partitions_older_than`.
3. **Row-level security, forced.** `pqt_app` sees and writes only rows whose `store_id` equals the
   transaction-local setting `pqt.store_id` (`set_config(..., true)`), set by event-core to the one store a
   write or a read concerns; with no setting, nothing is visible, and the setting cannot outlive its
   transaction on a pooled connection. A wrong `WHERE` clause therefore cannot leak another store's sales.
   `pqt_read` (scorer, analysts, read-only) reads every store. Tier 3 replaces the store scope with the
   tenant scope.
4. **An edge source writes only its own store.** Edge sources are `urn:pqt:edge:<store>:<device>`;
   `SourcePolicy.storeMatchesSource` requires the event's `storeid` to be that store (403
   `store-not-allowed`; `forbidden` in a batch; the dead-letter topic for the raw Kafka path). Billing
   payloads also carry `data.storeId`, which must equal `storeid` (a contract error otherwise). Simulator
   sources keep their own naming.
5. **Read API.** `GET /api/stores/{storeId}/sales`, `/payments`, `/stock` on the console API (the `/api/`
   BFF prefix, session and `viewer` role, like the other console reads; `/v1/` stays the machine ingest API,
   which corrects the path written in the X1 plan). Sales use the reconcile's rules (B6, ADR-034): a bill
   counts once at its latest revision unless that revision was voided, at the time of that revision's first
   line, so the console and the reconcile always agree. Buckets are hours or days in the store's time zone
   (`tz`, IANA, default `Asia/Kolkata`), returned as UTC instants. A window covers at most 35 days (7 for
   hourly buckets). `run` selects a simulator run; without it, live data. Money stays integer minor units.

## Alternatives considered

- **Raise `max-age` for everything.** Rejected: the 24-hour bound is what keeps a replayed or delayed camera
  stream out of the live rules.
- **Read `pqt.event.data` directly.** No new tables, but every sum casts `jsonb` text to numbers, no
  constraint checks a stored amount, and row-level security on `pqt.event` would touch every existing path.
- **Unpartitioned tables with a purge job.** A purge needs `DELETE`, which the append-only rule forbids for
  every role; dropping a day's partition does not.
- **A list of allowed stores per request in the RLS setting.** Today every console role may see every store
  and every read is for one store; a single store is the strictest scope that serves both. Tier 3 brings
  per-user store grants and the tenant column.

## Consequences

- A real billing event up to 35 days old is accepted; a camera event older than 24 hours is still refused.
- Each billing event costs one more insert in its ingest transaction.
- An edge client configured with a source prefix covering several stores still cannot write a store its
  source does not name. A producer that relied on mismatched names (none in this repository) would now be
  refused.
- B7 draws the console from these reads; the reconcile (B6) and the read API share one definition of a sale.
