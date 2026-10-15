# Connecting a billing system to PQT

For the shop's IT person or the developer of the shop's billing software. PQT reads sales, payments,
refunds, stock movements, stock levels and day totals from **any** billing system, including software
written for one shop. Nothing is written back to the billing system, and nothing personal is copied:
customer names, phone numbers, card numbers and notes are never read (ADR-033, ADR-037).

Everything arrives at the **PQT edge box** in the shop, which turns it into events, replaces every bill,
payment and document number with a keyed hash that cannot be reversed outside the shop, and sends them
on through the same outbox as the cameras, so a network cut loses nothing.

## Pick a way in

| Your software can... | Use | Status |
|---|---|---|
| export or save a file (CSV, Excel, JSON) | **File drop**: save files into a folder on the edge box (or a share it reads) | available |
| make an HTTP request when a bill is saved | **Push API** on the edge box, canonical JSON, no mapping needed | B5b |
| be read from its database (a view is enough) | **Database source**: a read-only query with a cursor | B5c |
| offer a REST API (cloud billing) | **REST source**: polled with your API key or OAuth | B5d |

Writing new software, or adding PQT to your own? Send **canonical records** (below) by file drop now, or
by the push API. Using an existing product (TallyPrime, Zoho, Vyapar, Marg, Busy, GoFrugal, a gateway)?
A ready profile may exist (B5e); otherwise a profile maps its export in a few minutes.

## Canonical records

The schema is `edge/connectors/schemas/records.schema.json` (JSON Schema 2020-12). A JSON document holds
one list per kind; a file may carry one kind or several.

```json
{
  "bills": [
    {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:02+05:30", "till": "Counter 1", "lineNo": 1,
     "sku": "ATTA-5KG", "qty": 2, "unit": "pcs", "amount": "490.00", "tax": "23.33"},
    {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:02+05:30", "till": "Counter 1", "lineNo": 2,
     "sku": "ONION-LOOSE", "qty": 1.25, "unit": "kg", "amount": 50}
  ],
  "payments": [
    {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:40+05:30", "till": "Counter 1", "tender": "upi",
     "amount": "500.00", "direction": "in", "reference": "UPI-77881"},
    {"billNo": "INV-1001", "timestamp": "2026-01-01T10:15:41+05:30", "tender": "cash", "amount": 40}
  ],
  "movements": [
    {"docNo": "GRN-77", "timestamp": "2026-01-01T09:05:00+05:30", "sku": "ATTA-5KG", "qty": 40, "unit": "pcs",
     "direction": "in", "reason": "purchase_receipt"}
  ],
  "stock": [{"sku": "ATTA-5KG", "qty": 118, "unit": "pcs", "timestamp": "2026-01-01T22:00:00+05:30"}],
  "dayTotals": [{"date": "2026-01-01", "sales": "14820.50", "refunds": "120.00", "bills": 178,
                 "tenders": {"cash": "5200.00", "upi": "8400.50", "card": "1220.00"}}]
}
```

| Kind | Required | Optional | Notes |
|---|---|---|---|
| `bills` | `billNo`, `timestamp`, `sku`, `qty`, `amount` | `till`, `lineNo`, `unit`, `tax` | One record per line. Send all lines of a bill together; when a bill changes, send all its lines again (it becomes the next revision, the old one is voided). `amount` includes tax. |
| `payments` | `billNo`, `timestamp`, `tender`, `amount` | `till`, `direction`, `reference` | `direction` `in` (paid) or `out` (refunded); without it a negative amount is a refund. |
| `movements` | `docNo`, `timestamp`, `sku`, `qty`, `direction`, `reason` | `unit`, `lineNo` | Purchases, returns, transfers, damage. Do not send sales here: bills move stock by themselves. |
| `stock` | `sku`, `qty`, `timestamp` | `unit` | Stock on hand at a moment. |
| `dayTotals` | `date`, `sales`, `refunds`, `bills` | `tenders` | Your system's own totals for the day; PQT reconciles against them daily. |

Values:

- **Money** in rupees, as a JSON number or decimal text (`"490.00"`, `490`, `490.5`). It is read exactly,
  never as a float. Paise as integers instead: say `moneyUnit: minor` in the profile.
- **Time** as ISO 8601 with the offset (`2026-01-01T10:15:02+05:30`). It is when it happened in the shop,
  never when it was sent.
- **Words**: `tender` cash, card, upi, wallet, credit, voucher, other; `unit` pcs, kg, g, l, ml, m, box,
  pack, other; `direction` in, out; `reason` purchase_receipt, sale, customer_return, supplier_return,
  transfer_in, transfer_out, adjustment, damage. A profile can map your own words onto these.
- **Numbers** (`billNo`, `docNo`, `reference`) are your own; they stay on the edge box.

Sending the same records again changes nothing; that is how retries and re-exports are meant to work.

## File drop

Save files into the drop folder; the edge box picks up each file once it has not changed for 10 seconds,
imports it and moves it to `done/<date>/`. A file with a problem goes to `failed/`, with
`<name>.report.json` naming each bad row (by row, never by bill number); its good rows were imported, and
dropping a corrected file sends only what is still missing. Names decide the kind: `bills*`, `payments*`,
`movements*`, `stock*`, `day*` (`.csv`, `.txt`, `.xlsx`, `.json`, `.ndjson`, `.jsonl`), or the patterns in
the profile's `match`. Write to a temporary name (`.part`, `.tmp`) and rename when done, or just save:
both work.

```bash
pqt-connectors watch --dir /srv/billing-drop --profile profiles/canonical.yaml --store store-001 \
  --key /var/lib/pqt-edge/billing.key --state /var/lib/pqt-edge/connector-state.db \
  --outbox /var/lib/pqt-edge/outbox.db --schemas /app/schemas
```

## Mapping an existing export: profiles

A profile (YAML) says where each canonical field is in your files. Start from a sample with fake data:

```bash
pqt-connectors suggest --kind bills --sample SalesRegister.xlsx --system my-pos > my-pos.yaml   # a starting point
pqt-connectors check --profile my-pos.yaml --bills SalesRegister.xlsx --payments Receipts.csv      # dry run
```

`suggest` matches familiar column names and lists the columns it leaves out (customer details among them).
`check` shows the records it read, the events it would send (already pseudonymised) and every error, and
keeps nothing. Iterate until `check` exits 0.

```yaml
apiVersion: connector.pqt/v1
name: my-pos
system: my-pos                    # short name; part of every event's source and id
timezone: Asia/Kolkata
dateFormat: "%d/%m/%Y"            # when date and time are separate fields...
timeFormat: "%H:%M"
timestampFormat: iso8601          # ...or one timestamp field: iso8601, epoch_ms, epoch_s or a strftime pattern
currency: INR
moneyUnit: major                  # or minor (paise as integers)
csv: {delimiter: ";"}             # optional
files:
  bills:
    path: data.invoices           # JSON: where the records are (default: a top-level list, or the kind's name)
    each: items                   # JSON: one row per element of this list; `$.field` reads the invoice itself
    sheet: Sales                  # Excel: which sheet (default: the first)
    match: ["SalesRegister*.xlsx"]  # watched folder: which files are bills
    columns: {billNo: $.invoice_number, timestamp: $.created_at, till: $.counter, sku: item_code, qty: quantity,
              unit: uom, amount: net_amount, tax: gst_amount}
maps:                             # your words -> canonical words (case and spacing ignored)
  tender: {upi: [gpay, phonepe, paytm upi], card: [credit card, debit card], cash: [cash]}
  unit: {pcs: [nos, pcs], kg: [kgs]}
```

Field names follow the canonical record (`billNo`, `docNo`, `timestamp` or `date` + `time`, `till`,
`lineNo`, `sku`, `qty`, `unit`, `amount`, `tax`, `tender`, `direction`, `reference`, `reason`; day totals:
`date`, `sales`, `refunds`, `bills`, `tenders: {cash: ..., upi: ...}`). Only mapped fields are ever read.
`profiles/generic-csv.yaml` and `profiles/canonical.yaml` are complete examples.

## What PQT guarantees

- Only mapped fields are read; personal data in the same file or record never leaves the edge box (a
  test per source feeds names, phones and GSTINs and checks none survives).
- Bill, document and payment numbers leave the shop only as keyed hashes; the key never leaves the box.
- Money is exact; a value that cannot be read exactly is reported, never guessed.
- Re-sending adds nothing; an edited bill becomes a new revision, history is never rewritten.
- A bad row is reported, never skipped silently; a bill with a bad line is held back whole.
- Events reach the platform even after a network cut (the edge outbox; tested in `make chaos-edge`).
