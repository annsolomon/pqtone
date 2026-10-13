# ADR-034: The daily billing reconcile and its 0.5% threshold

- **Status:** accepted
- **Date:** 2026-10-12
- **Milestone:** B6 (X1, `docs/plans/x1-billing-sync.md`)

## Context

Billing events arrive by export or API, through the edge outbox, sometimes days late, sometimes twice.
Before anyone trusts "sales next to footfall", the platform must show that what it holds is what the
billing system holds. CLAUDE.md requires thresholds to be set by ADR.

## Decision

Every business day per store (`pqt_connectors.reconcile`, `pqt-connectors reconcile`):

1. **Internal, exact.** Sales from the bills (latest revision per bill, cancelled bills excluded) minus
   refunds paid out must equal net payments received. Both sides come from the same billing system, so
   any difference is an alert: a lost payment or line, not rounding.
2. **Against the billing system's day totals, 0.5% (50 basis points).** Our sales, refunds and net
   payments per tender against `pos.day.total`, as a share of the larger side. Bill counts must match
   exactly. 0.5% is MASTER-README's figure; with integer paise (ADR-033) there is no rounding noise, so it
   is purely the tolerance for timing (a bill keyed at 23:59:59 and exported with the next day) and for
   billing systems whose day totals round to the rupee.
3. **Stock, exact per SKU.** Between two consecutive stock reports, the change must equal the movements
   dated in between. Each SKU that does not add up is listed with the unexplained quantity.
4. Duplicates by `(source, id)` are counted once, as event-core does.
5. The threshold is a parameter for analysis (`--threshold-bp`), but the alerting default changes only by
   a new ADR.

## Consequences

- A planted 0.8% day-total disagreement (scenario `refund_abuse`) alerts; a normal simulated day does not.
- Two findings in the simulator itself were caught by this job and fixed in B2 (a reused bill number,
  a snapshot dated before a delivery it counted): the reconcile also guards our own test data.
- Wiring the job to stored events and to incidents follows B3 (storage) and B7 (console).
