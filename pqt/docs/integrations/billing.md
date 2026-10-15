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
| make an HTTP request when a bill is saved | **Push API** on the edge box, canonical JSON, no mapping needed | available |
| receive payment-gateway webhooks | **Webhook** on the edge box, checked by the gateway's signature | available |
| be read from its database (a view is enough) | **Database source**: a read-only query with a cursor | available |
| offer a REST API (cloud billing) | **REST source**: polled with your API key or OAuth | available |

Writing new software, or adding PQT to your own? Send **canonical records** (below) through the push API or
a file drop. Using an existing product? TallyPrime is read directly over its XML/HTTP interface and Razorpay
payments arrive by webhook, with ready profiles (`edge/connectors/profiles/README.md` says what was verified);
for anything else (Vyapar, Marg, Busy, GoFrugal, Zoho, a spreadsheet) a profile maps its export, database or
API in a few minutes, starting from `pqt-connectors suggest`.

## Canonical records

The schema is `edge/connectors/pqt_connectors/schemas/records.schema.json` (JSON Schema 2020-12). A JSON document holds
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

## Push API

The edge box listens on `http://127.0.0.1:8787` (on the shop network only over TLS). Ask the person who
installed PQT for a token; it is shown once and the box keeps only a hash of it.

```bash
pqt-connectors push-client add --clients /etc/pqt/push-clients.yaml --id shop-pos --profile /app/profiles/canonical.yaml
```

Send records whenever a bill is saved, edited or cancelled, or in batches; a retry never duplicates anything.

```bash
curl -X POST http://127.0.0.1:8787/v1/records -H "Authorization: Bearer $PQT_TOKEN" \
  -H "Content-Type: application/json" -d @bill.json
# {"events": 5, "alreadySent": 0, "records": {"bills": 2, "payments": 1}, "bills": {"new": 1, ...}, "errors": []}
```

```python
import json, urllib.request
req = urllib.request.Request("http://127.0.0.1:8787/v1/records", data=json.dumps(doc).encode(),
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req, timeout=10)))
```

```javascript
const r = await fetch("http://127.0.0.1:8787/v1/records", {method: "POST",
  headers: {"Authorization": `Bearer ${token}`, "Content-Type": "application/json"}, body: JSON.stringify(doc)});
```

```csharp
using var http = new HttpClient();
http.DefaultRequestHeaders.Authorization = new("Bearer", token);
var r = await http.PostAsync("http://127.0.0.1:8787/v1/records",
    new StringContent(json, System.Text.Encoding.UTF8, "application/json"));
```

| Response | Meaning | What to do |
|---|---|---|
| 200 | Imported; `errors` lists records that were reported (by kind and number), the rest went through | Fix and resend the reported records |
| 401 | Missing or wrong token (or signature) | Check the token |
| 403 | This client may not send that kind | Ask for the kind to be allowed |
| 413 / 415 / 400 / 422 | Too big (5 MB), not JSON, malformed, or not matching the schema (nothing imported) | Fix the request |
| 429 | Too fast | Wait `Retry-After` seconds and resend |
| 503 | The box cannot store right now | Resend later |

Errors are problem details (`application/problem+json`) with a `code`, and never repeat what was sent. The
full contract is `edge/connectors/openapi.yaml`; `GET /v1/schema` returns the records schema. Software that
already has its own JSON shape can keep it: give its client a profile instead of the canonical one. Clients
can also be set to sign each body (`X-PQT-Signature: sha256=<HMAC-SHA256 hex>`) and be limited to some kinds.

**Webhooks** (payment gateways): `POST /v1/webhooks/<client>` with the gateway's own body, checked only by
its HMAC signature header (no token), mapped by the client's profile (`path` says where the payment is):

```bash
pqt-connectors push-client add --clients /etc/pqt/push-clients.yaml --id gateway --webhook --kind payments \
  --profile my-gateway.yaml --sig-header X-Gateway-Signature --sig-secret env:GATEWAY_WEBHOOK_SECRET
```

## Database source

If the software keeps its data in SQLite, PostgreSQL, MySQL/MariaDB or SQL Server, the edge box can read it
directly. Create a **read-only** database user that can see only the tables or views needed (a view per kind
is best), and write one query per kind that returns what changed after a cursor:

```yaml
# sources.yaml
apiVersion: connector.pqt/v1
sources:
  - id: shop-db
    type: sql
    profile: shop-db.yaml          # maps the query's columns, like any profile
    driver: postgresql             # sqlite | postgresql | mysql | sqlserver
    dsn: env:SHOP_DB_DSN           # a reference, never the password itself (SQLite: a file path)
    queries:
      bills:                       # every line of each bill that changed: put the cursor on the bill header
        sql: |
          SELECT b.bill_no, b.billed_at, b.counter, l.line_no, l.item_code, l.qty, l.uom, l.net, b.modified_at
            FROM bills b JOIN bill_lines l ON l.bill_no = b.bill_no
           WHERE b.modified_at > :cursor
           ORDER BY b.modified_at, b.bill_no, l.line_no
        cursor: modified_at        # returned by the query; the largest value read is remembered
        start: "2026-10-01 00:00:00"   # read changes after this the first time
        lookback: PT10M            # re-read 10 minutes before the cursor for late commits (adds nothing)
      payments:
        sql: SELECT id, bill_no, paid_at, mode, amount FROM payments WHERE id > :cursor ORDER BY id
        cursor: id
        start: 0
```

```bash
pqt-connectors poll --sources sources.yaml --id shop-db --store store-001 --key /var/lib/pqt-edge/billing.key \
  --state /var/lib/pqt-edge/connector-state.db --outbox /var/lib/pqt-edge/outbox.db
```

Only a single `SELECT` with the `:cursor` parameter is accepted, and the connection is opened read-only as
well. The cursor moves only after the events are safely in the outbox, never past a row that could not be
read (it is reported until fixed), and a page never splits rows that share a cursor value. Drivers for
PostgreSQL, MySQL and SQL Server are optional installs (`pip install 'pqt-connectors[postgresql]'`).

## REST source

For cloud billing systems with a JSON API. The edge box polls it; nothing is ever written to it.

```yaml
# in sources.yaml
  - id: cloud-pos
    type: http
    profile: cloud-pos.yaml        # maps the API's fields (use `each` for invoice lines)
    baseUrl: https://api.example.com
    auth:                          # apiKey | bearer | basic | oauth2ClientCredentials | oauth2RefreshToken
      type: oauth2RefreshToken
      tokenUrl: https://accounts.example.com/oauth/token
      clientId: env:CLOUD_POS_CLIENT_ID
      clientSecret: env:CLOUD_POS_CLIENT_SECRET
      refreshToken: file:/var/lib/pqt-edge/secrets/cloud-pos.refresh   # rotated tokens are written back here
    endpoints:
      bills:
        path: /v1/invoices
        params: {status: all}
        records: data.invoices     # where the list is in each reply
        pagination: {type: page, param: page, start: 1}   # page | offset | cursor | link | nextUrl | none
        since: {param: updated_since, cursor: updated_at, format: iso8601,
                start: "2026-10-01T00:00:00+05:30", lookback: PT10M}
```

```bash
pqt-connectors poll --sources sources.yaml --id cloud-pos --store store-001 --key /var/lib/pqt-edge/billing.key \
  --state /var/lib/pqt-edge/connector-state.db --outbox /var/lib/pqt-edge/outbox.db
```

Only HTTPS. Credentials are `env:`/`file:` references; a refused OAuth token is renewed once. Redirects and
next-page links to another host are refused, so credentials never leave the configured host. `429` and
`503` are retried after `Retry-After`, other server errors with back-off; if the API stays down the poll is
reported and the next one retries the same window.

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
