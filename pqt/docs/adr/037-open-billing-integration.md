# ADR-037: Open billing integration: one record model, any transport

- **Status:** accepted
- **Date:** 2026-10-15
- **Milestone:** B5 (X1 billing and stock sync, `docs/plans/x1-billing-sync.md`); replaces ADR-033 §5

## Context

ADR-033 §5 left B5 as "one live connector, chosen when the shop's billing system is known". The product
requirement is wider: the platform must connect to **any** billing system, including a shop's own custom
software, without a project per shop. Shops in India run TallyPrime, Zoho, Vyapar, Marg, Busy, GoFrugal,
in-house POS builds on SQL Server or MySQL, spreadsheets, and payment gateways. They differ in transport
(files, a database, a REST API, webhooks, XML over the LAN), not in what a sale is.

B4 already separates the two: the importer turns **canonical records** (a bill line, a payment, a stock
movement, a stock level, a day total, with fixed field names) into contract events, and a profile maps an
export's columns onto those fields. Everything ADR-033 guarantees (allow-list, HMAC references, exact
money, idempotent ids, revisions) lives in that importer, not in the CSV reader.

## Decision

1. **The canonical record is the integration contract.** Five record kinds (`bills`, `payments`,
   `movements`, `stock`, `dayTotals`) with the field names of B4, published as a JSON Schema
   (`edge/connectors/schemas/records.schema.json`) and a guide (`docs/integrations/billing.md`). Any
   source produces records; one pipeline turns records into events. A new transport never re-implements
   pseudonymisation, money parsing, dedup or revisions.
2. **Sources are pluggable, all read-only towards the billing system.**
   - `file`: CSV, XLSX, JSON, NDJSON; one-shot import or a watched drop folder (processed files move to
     `done/`, files with errors to `failed/` with a report beside them).
   - `push`: an HTTP endpoint on the edge box that the shop's software (or a webhook) posts records to.
     For custom software this is the recommended path: post JSON in the canonical field names, no profile
     needed.
   - `sql`: polls a read-only query against the billing database with an incremental cursor column.
   - `http`: polls a REST/JSON API (API key, bearer, OAuth2 client credentials or refresh token;
     page, offset, cursor or link pagination; `Retry-After` honoured).
   Named systems are **profiles** on top of these (B5e), not code paths, except where a system's wire
   format needs an adapter (TallyPrime XML), which then emits records like any other source.
3. **Mapping is declarative and checkable.** A profile maps source fields (CSV columns, JSON paths, SQL
   result columns) to record fields, with value maps for tenders, units, directions and reasons, and
   time and money formats (separate date and time, one ISO 8601 or epoch timestamp, major or minor money
   units). `pqt-connectors check` dry-runs a profile against a sample and prints the records and events it
   would send, with every error by file and row, without touching state or the outbox.
4. **Secrets never sit in profiles.** Credentials are referenced as `env:NAME` or `file:/path` and read
   at run time; push tokens are stored as SHA-256 hashes; nothing secret is logged or sent upstream.
5. **The push endpoint is local and authenticated.** It listens on loopback by default; binding to the
   shop LAN is explicit and should use TLS (certificate files on the edge box). Each client has its own
   bearer token; webhook senders can be checked with an HMAC-SHA256 signature over the raw body. Request
   size and rate are capped. A request's records go through the same importer, so a retried post adds
   nothing.
6. **Dependencies stay small and optional.** Standard library for HTTP server and client, SQLite and
   XML parsing with `defusedxml` (PSF-2.0); `openpyxl` (MIT) for XLSX; database drivers are optional
   extras installed only where needed: `psycopg` (LGPL-3.0, used unmodified as a library), `PyMySQL`
   (MIT), `pyodbc` (MIT-0). No AGPL, SSPL, BSL or non-commercial licences.

## Consequences

- A shop with any software that can export a file, expose a database view, offer an API or make an HTTP
  call can be connected by writing a profile, usually without code. The guide gives the shop's developer
  the canonical fields and example posts.
- Contract tests per source use recorded replies or local fakes; CI never calls a real billing system.
- Every source shares the allow-list test of ADR-033 §2: a record full of personal data leaves nothing
  in any event or log line.
- Live sources run in the edge connector service (B5f) with their state beside the outbox, so a network
  cut never loses or duplicates billing data (the cable test covers them).

## Amendment (2026-10-15, B5f): running the sources and reporting their health

- **One service per box.** `pqt-connectors run` runs every source in a `connector.pqt/v1` sources file (`watch`,
  `sql`, `http`, `tally`, `push`) on its own schedule, sharing one importer state written by one source at a time
  (the push API takes the same lock). A failing source backs off (interval x 2^failures, at most 15 minutes);
  the others carry on.
- **Health without data.** The service writes a status file on the box (mode 600) with each source's state and
  last error (value-free). The edge agent reads it into its heartbeat, which becomes `edge.heartbeat` 1.1.0
  (additive: a `connectors` array and the alarm `connector_failing`; schema-compat passes). The heartbeat carries
  only ok, stale, failures in a row, seconds since the last success, events sent and rows reported, never error
  text, data or secrets. A source failing three times in a row, without success for three intervals, or a
  service that stopped updating its file for 10 minutes raises `connector_failing`.
- **On the console.** `GET /api/stores/{id}/edge` (viewer) returns each edge agent's latest heartbeat of the last
  7 days; the Sales page lists the billing sources with their state in words.
- **In the cable test.** `make chaos-edge` runs the service next to the agent with a push API and a POS database
  source; both must come through the cut with 0 missing and 0 duplicate events, a repeated post must add nothing,
  and the console must show both sources healthy.
