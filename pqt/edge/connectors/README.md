# Billing connectors (`pqt-connectors`)

X1 (`docs/plans/x1-billing-sync.md`): the shop's billing data, as the same kind of CloudEvents the
cameras send, through the same edge outbox. Data rules: ADR-033.

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

Try it with simulated data: `store-sim run --scenario sim/scenarios/billing_day.yaml --seed 42 --out run`,
then `store-sim export-billing --run run --out exports` and import `exports/*.csv`.

Live connectors for a specific billing system (B5) wait for the shop's choice of system.
