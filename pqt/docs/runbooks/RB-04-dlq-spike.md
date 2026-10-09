# RB-04 Dead-letter spike

**Signal.** `DeadLetterSpike`.

1. Inspect reasons: `rpk topic consume store.events.dlq -n 20 -f '%h %v\n'` (headers `dlq-reason`, `dlq-errors`, `dlq-source`).
2. `invalid-event`: a producer deployed a contract change. Contact the owner of the `source`; schemas change only by PR to `schemas/`.
3. `forbidden-source`: a producer is writing for a source it does not own — treat as a security event (RB-05).
4. After a fix, replay selected DLQ records to `store.events.raw`; dedup makes replays safe.
