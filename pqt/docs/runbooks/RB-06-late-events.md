# RB-06 Late events above 1%

**Signal.** `LateEventsHigh`. Events arriving after the watermark (stream time − grace) are dropped by the rules.

1. Find the store: `pqt_rules_events_late_total{store=...}`.
2. Typical causes: edge gateway buffering after a network outage, clock skew on a producer, a stalled raw-topic partition.
3. If late arrival is legitimate and persistent, raise `grace` in `config/rules.yaml` via PR; the scorer's `late_beyond_grace` scenario quantifies the trade-off (more grace = more detection latency).
