# Ready profiles (X1 B5e, ADR-037)

Profiles for systems common in Indian retail, on top of the open sources (files, push API, database, REST,
TallyPrime). Each was written from the vendor's public documentation; the notes say what was checked and what
to confirm on the shop's own account before going live (run `pqt-connectors check` on a real sample, or one
`poll` into a JSONL file with `--out`).

| Profile | Source | Verified (2026-10-15) | Confirm on the shop's account |
|---|---|---|---|
| `canonical.yaml` | any | the canonical records schema (`pqt_connectors/schemas/records.schema.json`) | nothing: this is PQT's own format |
| `generic-csv.yaml` | file | B4/B8 end-to-end test | the export's column names and words |
| `razorpay-webhook.yaml` | push webhook | `X-Razorpay-Signature` is HMAC-SHA256 hex of the raw body with the webhook secret; `payment.captured` carries `payload.payment.entity` with `id`, `amount` in currency subunits (₹1 = 100), `currency`, `method`, `order_id`, `notes`, `created_at` (UNIX time) | whether the POS puts its bill number in `notes` (then map `billNo` to it); the methods enabled on the account |
| `tallyprime.yaml` | `tally` | TallyPrime's HTTP server on port 9000 (F1 > Settings > Advanced Configuration); XML Export requests (`TALLYREQUEST` Export, `TYPE` Data, static variables); dates `YYYYMMDD`; quantities `" 2 Nos"`; rates `"245.00/Nos"`; party ledger `ISDEEMEDPOSITIVE` Yes with a negative amount | the company name, the voucher type names in use (`voucherTypes`), the units, and which ledgers are cash, bank, UPI or wallet (list them under `maps.tender`); Tally vouchers have a date but no time, so events sit at the end of the day |

Not yet included: Zoho Books (its invoice list carries no line items, so each changed invoice must be fetched
on its own; to be added with its API reference at hand), Vyapar, Marg, Busy and GoFrugal (their exports work
today through a file profile made with `pqt-connectors suggest` from a sample export).

## TallyPrime

```yaml
# sources.yaml
apiVersion: connector.pqt/v1
sources:
  - id: shop-tally
    type: tally
    profile: tally-shop.yaml          # a copy of profiles/tallyprime.yaml with the shop's ledgers and units
    url: http://192.168.1.20:9000     # the PC running TallyPrime, with its HTTP server enabled
    company: "Example Stores"         # exactly as named in Tally
    start: "2026-10-01"
    lookbackDays: 7                   # re-read a week each poll: back-dated and altered vouchers are picked up
    voucherTypes: {sales: [Sales, POS Sales], creditNote: [Credit Note], purchase: [Purchase]}
```

Only Export requests are sent; nothing is ever written to Tally. Party names, addresses and narrations are
never read.
