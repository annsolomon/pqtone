# Billing connectors (`pqt-connectors`)

X1 (`docs/plans/x1-billing-sync.md`): the shop's billing data, as the same kind of CloudEvents the
cameras send, through the same edge outbox. Data rules: ADR-033. Any billing system, including a shop's own
software, connects through the canonical records (ADR-037): the guide for the shop's developer is
`docs/integrations/billing.md`, the schema `pqt_connectors/schemas/records.schema.json`.

## Sources (B5a)

- **Files**: CSV, XLSX, JSON (a list, an object holding lists, or nested invoices with `each`), NDJSON;
  `import` once, or `watch` a drop folder (`done/`, `failed/` with a report per file).
- **Push API (B5b)**: `serve` takes records over HTTP from the shop's software (bearer token per client,
  stored hashed; optional HMAC signature; kinds per client) and webhooks (`/v1/webhooks/<client>`, signature
  only); loopback unless TLS is given; size and rate limits; `openapi.yaml`. `push-client add|remove|list`.
- **Records fed by a program** (`Importer.feed`): the database and REST sources (B5c, B5d).
- `profiles/canonical.yaml`: the canonical field names, nothing to map. `profiles/generic-csv.yaml`: the
  generic CSV export below.
- `check` dry-runs a profile on samples (throw-away key, in-memory state, nothing kept); `suggest` writes a
  starting profile from a sample's column names and lists what it left out.

## File connector (B4)

Imports CSV exports of bills, payments, purchases and returns, stock and day totals. Any billing system
that can export CSV works through a **profile** that names its columns and its words for tenders, units
and directions (`profiles/generic-csv.yaml`; copy it for another system).

```bash
pqt-connectors init-key --key /var/lib/pqt-edge/billing.key          # once, at install (mode 600)
pqt-connectors import --profile profiles/generic-csv.yaml --store store-001 \
  --key /var/lib/pqt-edge/billing.key --state /var/lib/pqt-edge/connector-state.db \
  --outbox /var/lib/pqt-edge/outbox.db --schemas schemas \
  --bills bills.csv --payments payments.csv --movements purchases.csv --stock stock.csv --day-totals day.csv \
  --report import-report.json
```

| File | Columns (generic profile) |
|---|---|
| bills | Bill No, Date, Time, Counter, Line, Item Code, Qty, Unit, Amount, Tax |
| payments | Bill No, Date, Time, Counter, Mode, Amount, Type (Payment/Refund), Txn Ref |
| movements | Doc No, Date, Time, Item Code, Qty, Unit, Direction (Inward/Outward), Reason, Line |
| stock | Item Code, Qty, Unit, Date, Time |
| day totals | Date, Sales, Refunds, Bills, Cash, UPI, Card, Wallet, Credit |

What it guarantees:

- **Only mapped columns are read.** Customer names, phone numbers, GSTINs or notes in the same file are
  never looked at; a test feeds them and checks none leaves.
- **No real document numbers leave the shop.** Bill, document and transaction numbers become HMACs with
  the store key (`bill-…`, `doc-…`, `pay-…`); the key never leaves the box.
- **Exact money.** `₹1,234.50` → `123450` paise; a value it cannot read exactly is reported, not guessed.
- **Re-importing adds nothing.** The state database remembers what was sent; a changed bill goes out as
  its next revision after a `void` of the previous one.
- **Nothing is skipped silently.** Bad rows are reported by file and row (never by bill number); a bill
  with a bad row is held back whole. Exit code 2 when anything was reported.
- **Event time is billing time**, in the profile's time zone.
- **Bills move stock.** Exports list purchases and returns but not sales, so each bill also becomes
  `stock.movement` out/sale per SKU; a revised bill moves only the difference (more sold: out/sale; less:
  in/adjustment). Stock movements then add up to the level snapshots, which the daily reconcile checks.

Try it with simulated data: `store-sim run --scenario sim/scenarios/billing_day.yaml --seed 42 --out run`,
then `store-sim export-billing --run run --out exports --shift-to <yesterday>` and import `exports/*.csv`
(event-core takes real billing events up to 35 days old, ADR-036). `make chaos-edge` does exactly this on the
edge box during the cable test and checks the result end to end (X1 B8).

Database and REST sources follow in B5c-B5d, ready profiles for named systems in B5e (ADR-037).
