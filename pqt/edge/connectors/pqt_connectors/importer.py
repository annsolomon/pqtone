"""The file connector (X1, milestone B4): billing-system CSV exports -> X1 CloudEvents -> the edge outbox.

Rules (ADR-033):
- only the profile's mapped columns are read; anything else in the file never enters an event;
- bill, document and payment numbers become HMAC references with the store key;
- money and quantities are parsed exactly (decimal text -> integer paise / thousandths);
- re-importing the same file adds nothing: event ids are derived from the content's identity and the
  state remembers every id already handed to the outbox; a bill whose lines changed is emitted again as
  the next revision, after a `pos.till.event` of kind `void` for the previous one;
- a bad row is reported with its file and row number, never skipped silently; a bill with any bad row is
  held back whole, so no partial bill is ever sent. Reports name rows, never bill or document numbers.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from pathlib import Path

from .money import ParseError, to_milli, to_minor
from .profile import Profile
from .refs import Refs

NAMESPACE = uuid.UUID("8f3c6f0e-2b7d-4a51-9c0e-6d1b2a3f4e5c")
SKU_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RowError(ValueError):
    pass


@dataclass
class Report:
    files: dict[str, int] = field(default_factory=dict)       # file -> data rows read
    events: int = 0
    already_sent: int = 0
    bills_new: int = 0
    bills_revised: int = 0
    bills_unchanged: int = 0
    errors: list[dict] = field(default_factory=list)

    def error(self, file: str, row: int | None, msg: str) -> None:
        self.errors.append({"file": file, "row": row, "error": msg})

    def as_dict(self) -> dict:
        return {"files": self.files, "events": self.events, "alreadySent": self.already_sent, "bills": {"new": self.bills_new, "revised": self.bills_revised,
                                                                      "unchanged": self.bills_unchanged},
                "errors": self.errors}


class State:
    """What earlier imports sent: bill revisions and payment numbers (SQLite next to the outbox)."""

    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS bills (bill TEXT PRIMARY KEY, revision INTEGER NOT NULL, hash TEXT NOT NULL,
                                              total INTEGER NOT NULL, till TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS payments (bill TEXT NOT NULL, content TEXT NOT NULL, no INTEGER NOT NULL,
                                                 PRIMARY KEY (bill, content));
            CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY);
        """)

    def was_sent(self, event_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM sent WHERE id = ?", (event_id,)).fetchone() is not None

    def bill(self, ref: str) -> tuple[int, str, int, str] | None:
        return self.db.execute("SELECT revision, hash, total, till FROM bills WHERE bill = ?", (ref,)).fetchone()

    def payment_no(self, bill: str, content: str, pending: dict) -> tuple[int, bool]:
        row = self.db.execute("SELECT no FROM payments WHERE bill = ? AND content = ?", (bill, content)).fetchone()
        if row:
            return int(row[0]), False
        if (bill, content) in pending:
            return pending[(bill, content)], False
        used = self.db.execute("SELECT coalesce(max(no), 0) FROM payments WHERE bill = ?", (bill,)).fetchone()[0]
        used = max([used] + [n for (b, _), n in pending.items() if b == bill])
        pending[(bill, content)] = used + 1
        return used + 1, True

    def commit(self, bills: dict[str, tuple[int, str, int, str]], payments: dict[tuple[str, str], int],
               sent: list[str] = ()) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.executemany("INSERT OR IGNORE INTO sent (id) VALUES (?)", [(i,) for i in sent])
            for ref, (rev, h, total, till) in bills.items():
                self.db.execute("INSERT INTO bills (bill, revision, hash, total, till) VALUES (?, ?, ?, ?, ?) "
                                "ON CONFLICT (bill) DO UPDATE SET revision = excluded.revision, hash = excluded.hash, "
                                "total = excluded.total, till = excluded.till", (ref, rev, h, total, till))
            for (bill, content), no in payments.items():
                self.db.execute("INSERT OR IGNORE INTO payments (bill, content, no) VALUES (?, ?, ?)", (bill, content, no))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.db.close()


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return s[:64]


def _sku(value: str) -> str:
    s = re.sub(r"\s+", "-", str(value).strip())
    s = re.sub(r"[^A-Za-z0-9._-]", "", s)[:64]
    if not SKU_RE.fullmatch(s or "-" * 65):
        raise RowError(f"item code {value!r} has no usable characters")
    return s


def _iso(ms: int) -> str:
    dt = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms % 1000:03d}Z"


class Importer:
    def __init__(self, profile: Profile, *, store: str, refs: Refs, state: State, schemas: str | Path | None = None) -> None:
        if not re.fullmatch(r"[a-z0-9-]{1,64}", store):
            raise ValueError("store id must match [a-z0-9-]{1,64}")
        self.p, self.store, self.refs, self.state = profile, store, refs, state
        self.report = Report()
        self.events: list[dict] = []
        self._bills: dict[str, tuple[int, str, int, str]] = {}
        self._payments: dict[tuple[str, str], int] = {}
        self._validate = _validator(Path(schemas)) if schemas else None
        self._ids: set[str] = set()

    # -- shared -------------------------------------------------------------------------------------
    def _rows(self, kind: str, path: str | Path):
        cols = self.p.columns.get(kind)
        if cols is None:
            raise ValueError(f"profile {self.p.name} has no {kind} file")
        name = Path(path).name
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = set(reader.fieldnames or [])
            missing = [c for c in cols.values() if c not in header]
            if kind == "dayTotals":
                missing += [c for c in self.p.tenders_columns.values() if c not in header]
            if missing:
                self.report.error(name, 1, f"missing column(s) {missing}")
                self.report.files[name] = 0
                return
            n = 0
            for i, raw in enumerate(reader, start=2):
                n += 1
                # Only the mapped columns are kept: nothing else from the row is ever touched again.
                row = {field_: (raw.get(col) or "").strip() for field_, col in cols.items()}
                if kind == "dayTotals":
                    row["_tenders"] = {t: (raw.get(col) or "").strip() for t, col in self.p.tenders_columns.items()}
                yield name, i, row
            self.report.files[name] = n

    def _time(self, row: dict) -> int:
        try:
            d = datetime.strptime(row["date"], self.p.date_format).date()
            t = datetime.strptime(row["time"], self.p.time_format).time() if row.get("time") else dtime(23, 59, 59)
        except ValueError as e:
            raise RowError(f"date/time {row.get('date')!r} {row.get('time')!r}: {e}") from e
        return int(datetime.combine(d, t, tzinfo=self.p.tz).timestamp() * 1000)

    def _minor(self, text: str, what: str) -> int:
        try:
            return to_minor(text, decimal_sep=self.p.decimal_sep, thousands_sep=self.p.thousands_sep)
        except ParseError as e:
            raise RowError(f"{what}: {e}") from e

    def _milli(self, text: str, what: str) -> int:
        try:
            return to_milli(text, decimal_sep=self.p.decimal_sep, thousands_sep=self.p.thousands_sep)
        except ParseError as e:
            raise RowError(f"{what}: {e}") from e

    def _map(self, kind: str, value: str) -> str:
        try:
            return self.p.lookup(kind, value)
        except ValueError as e:
            raise RowError(str(e)) from e

    def _till(self, row: dict) -> str:
        return _slug(row["till"]) if row.get("till") else self.p.till_default

    def _event(self, short: str, t_ms: int, subject: str, data: dict, key: str, where: tuple[str, int | None]) -> bool:
        ns, rest = short.split(".", 1)
        ev = {"specversion": "1.0", "id": str(uuid.uuid5(NAMESPACE, f"{self.store}|{self.p.system}|{key}")),
              "source": f"urn:pqt:edge:{self.store}:pos-{self.p.system}", "type": f"com.pqt.{short}", "time": _iso(t_ms),
              "subject": subject, "dataschema": f"https://schemas.pqt.local/{ns}/{rest}/1.0.0",
              "datacontenttype": "application/json", "storeid": self.store, "partitionkey": self.store,
              "data": {"storeId": self.store, **data}}
        if ev["id"] in self._ids or self.state.was_sent(ev["id"]):  # an earlier import already sent it
            self.report.already_sent += 1
            return True
        if self._validate:
            errs = self._validate(ev)
            if errs:
                self.report.error(where[0], where[1], f"{short} would not match its contract: {errs[:3]}")
                return False
        self.events.append(ev)
        self._ids.add(ev["id"])
        return True

    # -- bills --------------------------------------------------------------------------------------
    def bills(self, path: str | Path) -> None:
        groups: dict[str, list[tuple[str, int, dict]]] = defaultdict(list)
        order: list[str] = []
        for name, i, row in self._rows("bills", path):
            if not row["billNo"]:
                self.report.error(name, i, "empty bill number")
                continue
            if row["billNo"] not in groups:
                order.append(row["billNo"])
            groups[row["billNo"]].append((name, i, row))
        for no in order:
            self._bill(no, groups[no])

    def _bill(self, no: str, rows: list[tuple[str, int, dict]]) -> None:
        lines, bad = [], False
        for pos, (name, i, row) in enumerate(rows, 1):
            try:
                ln = row.get("lineNo")
                line_no = int(ln) if ln else pos
                if not 1 <= line_no <= 10000:
                    raise RowError(f"line number {line_no} out of range")
                unit = self._map("unit", row["unit"]) if row.get("unit") else "pcs"
                lines.append({"t": self._time(row), "till": self._till(row), "lineNo": line_no, "sku": _sku(row["sku"]),
                              "qtyMilli": self._milli(row["qty"], "qty"), "unit": unit,
                              "amountMinor": self._minor(row["amount"], "amount"),
                              **({"taxMinor": self._minor(row["tax"], "tax")} if row.get("tax") else {}),
                              "_where": (name, i)})
            except RowError as e:
                self.report.error(name, i, f"bill line: {e}")
                bad = True
        if bad:
            self.report.error(rows[0][0], rows[0][1], f"bill starting at row {rows[0][1]} held back: rows {[r[1] for r in rows]} include a bad one")
            return
        if len({ln["lineNo"] for ln in lines}) != len(lines):
            self.report.error(rows[0][0], rows[0][1], f"bill starting at row {rows[0][1]} held back: repeated line numbers")
            return
        hexref = self.refs.hex(no)
        ref = f"bill-{hexref}"
        t = min(ln["t"] for ln in lines)
        till = lines[0]["till"]
        content = json.dumps(sorted((ln["lineNo"], ln["sku"], ln["qtyMilli"], ln["unit"], ln["amountMinor"],
                                     ln.get("taxMinor"), ln["till"], ln["t"]) for ln in lines))
        h = hashlib.sha256(content.encode()).hexdigest()
        total = sum(ln["amountMinor"] for ln in lines)
        prev = self._bills.get(ref) or self.state.bill(ref)
        if prev and prev[1] == h:
            self.report.bills_unchanged += 1
            return
        revision = 0 if prev is None else prev[0] + 1
        where = lines[0]["_where"]
        if prev is not None:
            ok = self._event("pos.till.event", t, f"till:{prev[3]}",
                             {"tillId": prev[3], "kind": "void", "billRef": ref, "revision": prev[0], "amountMinor": prev[2],
                              "currency": self.p.currency}, f"void|{hexref}|{prev[0]}", where)
            if not ok:
                return
            self.report.bills_revised += 1
        else:
            self.report.bills_new += 1
        for ln in sorted(lines, key=lambda x: x["lineNo"]):
            data = {"tillId": ln["till"], "billRef": ref, "revision": revision, "lineNo": ln["lineNo"], "sku": ln["sku"],
                    "qtyMilli": ln["qtyMilli"], "unit": ln["unit"], "amountMinor": ln["amountMinor"],
                    "currency": self.p.currency}
            if "taxMinor" in ln:
                data["taxMinor"] = ln["taxMinor"]
            self._event("pos.sale.line", ln["t"], f"till:{ln['till']}", data, f"line|{hexref}|{revision}|{ln['lineNo']}",
                        ln["_where"])
        self._bills[ref] = (revision, h, total, till)

    # -- payments -----------------------------------------------------------------------------------
    def payments(self, path: str | Path) -> None:
        seen: dict[tuple, int] = defaultdict(int)
        for name, i, row in self._rows("payments", path):
            try:
                if not row["billNo"]:
                    raise RowError("empty bill number")
                amount = self._minor(row["amount"], "amount")
                if row.get("direction"):
                    direction = self._map("direction", row["direction"])
                    amount = abs(amount)
                else:
                    direction = "out" if amount < 0 else "in"
                    amount = abs(amount)
                if amount < 1:
                    raise RowError("zero amount")
                tender = self._map("tender", row["tender"])
                t = self._time(row)
                till = self._till(row)
            except RowError as e:
                self.report.error(name, i, f"payment: {e}")
                continue
            hexref = self.refs.hex(row["billNo"])
            ident = (hexref, t, tender, direction, amount, row.get("reference") or "")
            seen[ident] += 1
            content = json.dumps([*ident[1:], seen[ident]])
            no, _new = self.state.payment_no(hexref, content, self._payments)
            data = {"tillId": till, "billRef": f"bill-{hexref}", "paymentNo": no, "tender": tender, "direction": direction,
                    "amountMinor": amount, "currency": self.p.currency}
            if row.get("reference"):
                data["providerRef"] = self.refs.pay(row["reference"])
            self._event("pos.payment", t, f"till:{till}", data, f"pay|{hexref}|{content}", (name, i))

    # -- stock --------------------------------------------------------------------------------------
    def movements(self, path: str | Path) -> None:
        positions: dict[str, int] = defaultdict(int)
        for name, i, row in self._rows("movements", path):
            try:
                if not row["docNo"]:
                    raise RowError("empty document number")
                positions[row["docNo"]] += 1
                line_no = int(row["lineNo"]) if row.get("lineNo") else positions[row["docNo"]]
                qty = self._milli(row["qty"], "qty")
                if qty <= 0:
                    raise RowError("quantity must be positive; the direction gives the sign")
                data = {"sku": _sku(row["sku"]), "direction": self._map("direction", row["direction"]),
                        "reason": self._map("reason", row["reason"]), "qtyMilli": qty,
                        "unit": self._map("unit", row["unit"]) if row.get("unit") else "pcs",
                        "docRef": self.refs.doc(row["docNo"]), "lineNo": line_no}
                t = self._time(row)
            except RowError as e:
                self.report.error(name, i, f"movement: {e}")
                continue
            self._event("stock.movement", t, f"sku:{data['sku']}", data,
                        f"mv|{data['docRef']}|{line_no}|{data['direction']}|{data['reason']}", (name, i))

    def stock(self, path: str | Path) -> None:
        for name, i, row in self._rows("stock", path):
            try:
                sku = _sku(row["sku"])
                data = {"sku": sku, "qtyMilli": self._milli(row["qty"], "qty"),
                        "unit": self._map("unit", row["unit"]) if row.get("unit") else "pcs", "source": self.p.system}
                t = self._time(row)
            except RowError as e:
                self.report.error(name, i, f"stock {row.get('sku')!r}: {e}")
                continue
            self._event("stock.level", t, f"sku:{sku}", data, f"lvl|{sku}|{t}", (name, i))

    def day_totals(self, path: str | Path) -> None:
        for name, i, row in self._rows("dayTotals", path):
            try:
                d = datetime.strptime(row["date"], self.p.date_format).date()
                t = int(datetime.combine(d, dtime(23, 59, 59), tzinfo=self.p.tz).timestamp() * 1000)
                tenders = {k: self._minor(v, f"tender {k}") for k, v in row["_tenders"].items() if v}
                data = {"businessDate": d.isoformat(), "salesMinor": self._minor(row["sales"], "sales"),
                        "refundsMinor": self._minor(row["refunds"], "refunds"), "bills": int(row["bills"]),
                        "paymentsByTender": tenders, "currency": self.p.currency, "source": self.p.system}
            except (RowError, ValueError) as e:
                self.report.error(name, i, f"day total {row.get('date')!r}: {e}")
                continue
            self._event("pos.day.total", t, f"store:{self.store}", data, f"day|{d.isoformat()}", (name, i))

    # -- finish -------------------------------------------------------------------------------------
    def commit(self, sink) -> Report:
        """Hand the events to `sink` (the edge outbox's append, or a file writer); record state only after
        the sink accepted them, so a failed hand-over is simply imported again."""
        self.events.sort(key=lambda e: (e["time"], e["type"], e["id"]))
        if self.events:
            sink(self.events)
        self.state.commit(self._bills, self._payments, [e["id"] for e in self.events])
        self.report.events = len(self.events)
        return self.report


def _validator(schemas: Path):
    from jsonschema import Draft202012Validator

    catalog = json.loads((schemas / "catalog.json").read_text(encoding="utf-8"))
    envelope = Draft202012Validator(json.loads((schemas / "envelope.schema.json").read_text(encoding="utf-8")))
    data, subjects = {}, {}
    for t in catalog["types"]:
        subjects[t["type"]] = re.compile(t["subjectPattern"])
        for v in t["versions"]:
            data[v["dataschema"]] = Draft202012Validator(json.loads((schemas / v["file"]).read_text(encoding="utf-8")))

    def check(ev: dict) -> list[str]:
        errs = [e.message for e in envelope.iter_errors(ev)]
        v = data.get(ev["dataschema"])
        if v is None:
            return errs + [f"dataschema {ev['dataschema']} not registered"]
        errs += [f"{'/'.join(map(str, e.path))}: {e.message}" for e in v.iter_errors(ev["data"])]
        if not subjects[ev["type"]].fullmatch(ev["subject"]):
            errs.append(f"subject {ev['subject']!r}")
        return errs

    return check
