# ADR-033: Billing data: integer money, allow-listed fields, pseudonymous references, revisions

- **Status:** accepted
- **Date:** 2026-10-12
- **Milestone:** B0 (X1 billing and stock sync, `docs/plans/x1-billing-sync.md`)

## Context

X1 brings sales, payments, refunds, till events and stock movements from a shop's billing system into the
platform. Billing systems hold personal data (customer names, phones, GSTINs, card digits), use their own
document numbers, let users edit or cancel bills after the fact, and are exported again and again. Four
decisions have to hold for every connector, whichever billing system it reads.

## Decision

1. **Money.** Every amount is an integer number of minor units (`amountMinor`, paise for INR) with an
   ISO 4217 `currency`. Connectors convert from the source's decimal text with exact decimal arithmetic
   (Python `decimal.Decimal`, Java `BigDecimal`), rounding half-even only where the source itself has more
   than two decimals, and reject a value they cannot convert exactly. No float holds money anywhere on the
   path: schemas declare these fields as integers.
2. **Allow-list.** A connector maps each source record to the contract's fields and drops everything else.
   Customer identity (name, phone, email, address, loyalty id, customer GSTIN), card numbers and free-text
   notes are never copied. Each connector has a test that feeds a record carrying all of them and asserts
   that none appears in any emitted event or log line.
3. **Pseudonymous references.** Bill numbers, payment references and stock document numbers become
   `HMAC-SHA256(store key, source system ‖ document number)`, hex, truncated to 32 characters, prefixed by
   kind (`bill-`, `pay-`, `doc-`). The store key is generated on the edge box at install and never leaves
   it, so the same bill always maps to the same reference within a store and nothing outside the shop can
   reverse it. Unlike track ids (daily keys), billing keys do not rotate: refunds and stock returns must
   join to bills weeks later. A key is rotated only by re-installing, which starts a new reference space
   (recorded in the store's audit log).
4. **Identity and revisions.** Event `id` = UUIDv5 over (store, source system, document reference, line or
   payment number, revision). A re-import produces the same ids and deduplicates on event-core's
   `(source, id)` key. When the source alters a bill, the connector emits the new lines with
   `revision + 1` and a `pos.till.event` of kind `void` for the previous revision; consumers take the
   highest revision per bill. Nothing already stored is updated or deleted.
5. **B5 system.** The first live connector is chosen when the shop's billing system is known; its
   transport-specific choices (TallyPrime XML over the LAN, a cloud API with OAuth, or gateway webhooks)
   are recorded in a follow-up ADR.

## Consequences

- Sums are exact and comparable across connectors, so the reconcile job (B6) can use a fixed 0.5% bound
  without rounding noise.
- The platform can join a refund to its sale and a delivery to its purchase bill without ever holding a
  real bill number or a customer's identity.
- Revisions make a bill's history auditable; analytics must always select the latest revision.
- Losing the edge box's store key orphans old references from new ones; the box backup (sealed, on the
  box) covers it.
