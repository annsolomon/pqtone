# RB-02 Ingest unavailable or returning 5xx

**Signal.** `EventCoreDown` / `IngestErrorsHigh`.

**Impact.** HTTP producers retry (idempotent by `(source, id)`); the raw Kafka topic buffers simulator traffic for 7 days.

1. `make ps`; check `event-core` health at `:8081/actuator/health` from inside the network.
2. Database: `pg_isready`, connection limit for `pip_app` (60), disk space. Flyway must have completed.
3. Kafka: SASL errors in logs mean credentials/ACLs drifted — re-run `redpanda-init`.
4. After recovery, the raw-topic consumer resumes from its committed offset; nothing needs replaying by hand.
