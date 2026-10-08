# RB-05 Conflicting duplicates

**Signal.** `ConflictingDuplicates`: the same `(source, id)` arrived with a different payload.

This is either a producer bug (reusing ids) or tampering. The first payload stays authoritative; the second is rejected (HTTP 409 / DLQ) and recorded in the audit log (`ingest.conflicting-duplicate`, with both hashes).

1. Audit log (admin console) → find the entries and the producer.
2. Pause the producer's credential in Keycloak if tampering is suspected.
3. Never "fix" by deleting the dedup row; the ledger is append-only by design.
