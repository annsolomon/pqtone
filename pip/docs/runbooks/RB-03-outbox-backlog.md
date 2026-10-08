# RB-03 Outbox backlog growing

**Signal.** `OutboxBacklogGrowing` (`pip_outbox_unpublished_rows > 5000` for 5 min).

**Impact.** Events are durable in PostgreSQL but not yet visible to rules-engine; incidents lag.

1. event-core logs: `outbox publish failed` → Kafka unreachable or `store.events.v1` ACL missing.
2. Check broker health: `rpk cluster health` inside the redpanda container.
3. Once Kafka is back, the relay drains automatically (500 rows per batch, multiple instances safe via `SKIP LOCKED`).
4. Do not delete outbox rows by hand; unpublished rows are the only copy in flight.
