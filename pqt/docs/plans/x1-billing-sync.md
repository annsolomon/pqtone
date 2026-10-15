# X1: Billing and stock sync

**Goal.** Sales, payments in, refunds out, till events and stock movements from the shop's billing system
appear next to what the cameras saw, reconciled every day against the billing system's own totals. The
camera side does not change; billing data arrives as more CloudEvents through the same edge outbox and
event-core.

This brings MASTER-README §12.6 (X1.1–X1.5) forward at your request (it was planned for after the first
paid contract) and adds what that plan lacked: payments by tender and stock movements in and out.

**What it answers.** How many of the people who queued actually paid? Did sales drop while the queue
was long? Which hour has many voids or refunds with nobody at the till? Did the stock received this
morning match the purchase bill? Is today's cash and UPI total what the billing system says?

**Status (15 Oct 2026): B0–B4 and B6–B8 done** (PRs #51, #53–#56), working with any billing system that exports files. B5, the live connector,
waits for one answer from you: **which billing system the shop uses** (TallyPrime, Zoho Books or
Inventory, Vyapar, Marg, another POS, or a payment terminal or gateway such as Razorpay, Pine Labs,
Paytm or PhonePe).

---

## Principles

1. **Allow-listed fields only.** A connector copies the fields below and nothing else. Customer names,
   phone numbers, addresses, loyalty ids, customer GSTINs and card numbers never enter the platform; a test
   per connector feeds a record full of them and checks none survives.
2. **References are pseudonymous.** Bill numbers, payment references and document numbers are HMAC'd
   with a per-store key held on the edge box, so the same bill always maps to the same reference and the
   platform can join lines, payments and refunds without learning the real number.
3. **Money is an integer.** Amounts are integers in minor units (paise) plus an ISO 4217 currency code.
   No floats anywhere on the path (ADR in B0).
4. **Re-importing changes nothing.** Event ids are derived from (store, source system, document, line,
   revision), so a retried upload or a re-imported file deduplicates on event-core's `(source, id)` key.
5. **Edits are explicit.** When the billing system alters or cancels a bill, the connector emits the new
   revision and a `pos.till.event` of kind `void` for the old one; history is never rewritten.
6. **Event time is billing time.** `time` is when the bill, payment or stock movement happened in the
   shop, never when it was imported.

## Events (new contracts, all v1.0.0, additive)

| Type (`com.pqt.`) | Subject | Data | Meaning |
|---|---|---|---|
| `pos.sale.line` | `till:<id>` | `storeId`, `tillId`, `billRef`, `revision`, `lineNo`, `sku`, `qty`, `unit`, `amountMinor`, `taxMinor`, `currency` | One line of a bill |
| `pos.payment` | `till:<id>` | `storeId`, `tillId`, `billRef`, `tender` (cash, card, upi, wallet, credit, voucher, other), `direction` (in, out), `amountMinor`, `currency`, `providerRef` (HMAC, optional) | Money in for a sale, or out for a refund |
| `pos.till.event` | `till:<id>` | `storeId`, `tillId`, `kind` (void, refund, no_sale, price_override, drawer_open, shift_open, shift_close), `billRef` (optional), `amountMinor` (optional) | Things at the till that are not a sale |
| `stock.movement` | `sku:<id>` | `storeId`, `sku`, `direction` (in, out), `reason` (purchase_receipt, sale, customer_return, supplier_return, transfer_in, transfer_out, adjustment, damage), `qty`, `unit`, `docRef` (HMAC) | Stock received or leaving |
| `stock.level` | `sku:<id>` | `storeId`, `sku`, `qty`, `unit`, `source` | Stock on hand, as the billing system reports it |
| `pos.day.total` | `store:<id>` | `storeId`, `businessDate`, `salesMinor`, `refundsMinor`, `paymentsByTender`, `bills`, `currency`, `source` | The billing system's own day totals, for the reconcile job |

"Incoming and outgoing" is covered both ways: money (`pos.payment` direction in or out) and stock
(`stock.movement` direction in or out). People coming in and going out are already counted by the
cameras (footfall).

## Milestones

| ID | Milestone | Done when |
|---|---|---|
| ✅ B0 | **Decisions.** ADR: money as integer minor units; the allow-list and HMAC references; event ids and revisions; which systems B5 supports first. | ADR merged Note: ADR-033 (PR #51) and ADR-034; the B5 system is chosen when the shop's billing system is named (ADR-033 §5). |
| ✅ B1 | **Contracts.** The six schemas above in `schemas/`, catalog entries, samples, subject patterns. event-core accepts and stores them; rules-engine ignores them without counting them invalid (as for `edge.heartbeat`). | `make schema-compat` passes; every sample validates; an ingest test stores one of each Note: PR #53. |
| ✅ B2 | **Billing simulator with ground truth.** store-sim emits bills for served shoppers (queue.left served = a bill at that till), basket lines from a seeded catalogue, payments by tender, refunds, voids and no-sales, stock receipts and day totals. Scenarios: normal day, refund abuse at an empty till, stock-out, short delivery. | Same seed → same bills (hash test); ground truth for each scenario Note: PR #55. |
| ✅ B3 | **Storage and API.** Sales, payments and stock tables (store-scoped forced RLS now, tenant scope in Tier 3), Flyway forward-only; `GET /api/stores/{id}/sales`, `/payments`, `/stock` (per day, hour or 15 minutes, per till; the console's `/api/` prefix, ADR-036); OpenAPI updated. event-core refuses real events older than 24 h (`pqt.ingest.max-age`), which would refuse yesterday's export: billing types get their own maximum age (35 days, ADR-036), nothing else changes. An edge source writes only the store it names. | Integration tests; `make openapi-check`; a 3-day-old bill is accepted and a 3-day-old camera event still refused Note: PR #53 (ADR-036: 35-day billing age, forced RLS per store). |
| ✅ B4 | **File connector.** Imports CSV or Excel exports (bills, payments, stock) through a column-mapping profile per billing system; validates, pseudonymises, emits through the edge outbox. Profiles for the systems you name; a generic profile for the rest. | Re-importing the same file adds 0 events; a file full of personal data leaves none in the platform; a malformed row is reported, not skipped silently Note: PR #55. |
| B5 | **Live connector for your billing system.** One of: TallyPrime over XML/HTTP on the shop LAN (read-only, `defusedxml`, cursor on voucher alter-ids); Zoho Books/Inventory API (OAuth refresh, rate limits, webhooks where offered); a payment gateway's settlement API or webhooks (signature checked). | Contract tests on recorded replies; ids stable across retries; token refresh or signature tests |
| ✅ B6 | **Reconcile job.** Daily per store: lines vs bill totals, payments vs sales (by tender), our sums vs `pos.day.total`, stock movements vs level changes. More than 0.5% difference raises an alert with the breakdown. | Tests on simulated days with planted differences; 0.5% threshold in an ADR Note: PR #55 (ADR-034: 50 bp). |
| ✅ B7 | **Console.** Sales and payments next to footfall and queue length (bills per 100 visitors, queue length vs sales per 15 minutes), till events on the incident timeline, stock movements per SKU. Built on `GET /api/stores/{id}/traffic` (visitors through entrance zones, time-weighted queue length per bucket, counts only). Day on the store's clock; India Standard Time until layouts carry a time zone. | Accessibility checks (as C3); e2e UI test Note: PR #54: tests/ui/test_sales.py with axe, no serious or critical violations. |
| ✅ B8 | **End to end.** Simulator → file connector → edge outbox → event-core → reconcile → console in `make all`; the cable test covers billing events too. Built into `make chaos-edge`: a simulated day is exported as yesterday's CSVs and imported on the edge box during the cut; `scripts/billing_e2e_check.py` (reconcile + ground truth + typed rows) and `tests/http/test_billing_pipeline.py` (console reads) judge it. Found and fixed on the way: bills now move stock in the connector. | `make all` green with billing; 0 missing, 0 duplicate billing events after the cut Note: PR #56: in make chaos-edge, the cut-off import arrives with zero loss, sales and bills equal the simulator's truth, re-import adds nothing, reconcile ok, console figures match. |

## Rules and analytics this enables (after X1)

- Refund or void with no person at the till zone (camera) → incident for review.
- Queue abandonment: shoppers who left the queue unserved vs bills in the same window.
- Conversion: bills per 100 entries, by hour.
- Stock-out suspicion: zero sales of a usually fast SKU while the shelf zone is visited.

## What you must provide

1. The name of the billing system (and, for B5, a test login or a sample export with fake data).
2. Later: written consent from the shop owner to connect it, as for the cameras.
