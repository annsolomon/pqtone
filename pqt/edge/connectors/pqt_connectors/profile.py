"""Mapping profiles: how one billing system's records map onto the canonical records (B4, B5a; ADR-037).

A profile names, for each record kind, which source field holds each canonical field (a CSV or Excel
column, a JSON key or dotted path, a database column), how times and numbers are written, and how the
system's words for tenders, units and directions map onto ours. Only the mapped fields are ever read:
whatever else a source carries (customer names, phone numbers, notes) is never looked at (ADR-033).

Times: a record has either `date` (+ `time`) in `dateFormat` / `timeFormat`, or one `timestamp` in
`timestampFormat` (`iso8601`, `epoch_ms`, `epoch_s` or a strftime pattern); a time without an offset is in
the profile's `timezone`. Money is decimal text in major units (rupees) unless `moneyUnit: minor` (paise).

Per kind, a file spec may also say where the records are (`path`, a dotted path in a JSON document), that
each record holds several rows (`each`: a list inside the record; then `$.field` reads the outer record),
which Excel sheet (`sheet`) and which file names a watched folder treats as this kind (`match`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

# Canonical record fields per kind. Every timed kind needs either `timestamp` or `date` (+ `time`).
KINDS = {
    "bills": {"required": {"billNo", "sku", "qty", "amount"}, "optional": {"till", "lineNo", "unit", "tax"}},
    "payments": {"required": {"billNo", "tender", "amount"}, "optional": {"till", "direction", "reference"}},
    "movements": {"required": {"docNo", "sku", "qty", "direction", "reason"}, "optional": {"unit", "lineNo"}},
    "stock": {"required": {"sku", "qty"}, "optional": {"unit"}},
    "dayTotals": {"required": {"date", "sales", "refunds", "bills"}, "optional": set()},
}
TIME_FIELDS = {"date", "time", "timestamp"}
TIMED = ("bills", "payments", "movements", "stock")
ORDER = ("bills", "payments", "movements", "stock", "dayTotals")   # import order: bills before their payments
TOP = {"apiVersion", "name", "system", "description", "timezone", "dateFormat", "timeFormat", "timestampFormat",
       "currency", "moneyUnit", "decimalSeparator", "thousandsSeparator", "tillDefault", "csv", "files", "maps"}
FILE_KEYS = {"columns", "path", "each", "sheet", "match"}
CSV_KEYS = {"delimiter", "encoding"}
TIMESTAMP_FORMATS = {"iso8601", "epoch_ms", "epoch_s"}
MAPS = {"tender", "unit", "direction", "reason"}
TENDERS = {"cash", "card", "upi", "wallet", "credit", "voucher", "other"}
UNITS = {"pcs", "kg", "g", "l", "ml", "m", "box", "pack", "other"}
REASONS = {"purchase_receipt", "sale", "customer_return", "supplier_return", "transfer_in", "transfer_out",
           "adjustment", "damage"}


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    name: str
    system: str
    tz: ZoneInfo
    date_format: str
    time_format: str
    currency: str
    decimal_sep: str
    thousands_sep: str
    till_default: str
    columns: dict[str, dict]
    tenders_columns: dict[str, str]
    maps: dict[str, dict[str, str]] = field(default_factory=dict)  # kind -> lowercased source word -> ours
    timestamp_format: str = "iso8601"
    money_unit: str = "major"
    csv_delimiter: str = ","
    csv_encoding: str = "utf-8-sig"
    file_opts: dict[str, dict] = field(default_factory=dict)  # kind -> {path, each, sheet, match}

    def patterns(self, kind: str) -> list[str]:
        """File names a watched folder treats as `kind` (default: names starting with the kind)."""
        m = self.file_opts.get(kind, {}).get("match")
        if m:
            return [m] if isinstance(m, str) else list(m)
        stem = "day" if kind == "dayTotals" else kind
        return [f"{stem}*.{ext}" for ext in ("csv", "txt", "xlsx", "json", "ndjson", "jsonl")]

    def lookup(self, kind: str, value: str) -> str:
        key = re.sub(r"\s+", " ", str(value).strip().lower())
        if key in self.maps.get(kind, {}):
            return self.maps[kind][key]
        raise ValueError(f"unknown {kind} {value!r} (add it to the profile's maps.{kind})")


def load(path: str | Path) -> Profile:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("apiVersion") != "connector.pqt/v1":
        raise ProfileError(f"{path}: not a connector.pqt/v1 profile")
    unknown = set(doc) - TOP
    if unknown:
        raise ProfileError(f"{path}: unknown key(s) {sorted(unknown)}")
    system = str(doc.get("system", ""))
    if not re.fullmatch(r"[a-z0-9-]{1,32}", system):
        raise ProfileError(f"{path}: system must match [a-z0-9-]{{1,32}}")
    files = doc.get("files") or {}
    if not files or set(files) - set(KINDS):
        raise ProfileError(f"{path}: files must be some of {sorted(KINDS)}")
    columns, tenders_columns, file_opts = {}, {}, {}
    for kind, spec in files.items():
        spec = spec or {}
        if not isinstance(spec, dict) or set(spec) - FILE_KEYS:
            raise ProfileError(f"{path}: files.{kind} keys must be among {sorted(FILE_KEYS)}")
        cols = dict(spec.get("columns") or {})
        if kind == "dayTotals":
            tenders_columns = dict(cols.pop("tenders", {}) or {})
            if set(tenders_columns) - TENDERS:
                raise ProfileError(f"{path}: dayTotals tenders must be among {sorted(TENDERS)}")
        allowed_fields = KINDS[kind]["required"] | KINDS[kind]["optional"] | (TIME_FIELDS if kind in TIMED else set())
        missing = KINDS[kind]["required"] - set(cols)
        extra = set(cols) - allowed_fields
        if missing or extra:
            raise ProfileError(f"{path}: files.{kind}.columns missing {sorted(missing)} / unknown {sorted(extra)}")
        if kind in TIMED and "timestamp" not in cols and "date" not in cols:
            raise ProfileError(f"{path}: files.{kind}.columns needs `timestamp` or `date` (with `time`)")
        if "timestamp" in cols and ({"date", "time"} & set(cols)):
            raise ProfileError(f"{path}: files.{kind}.columns: give `timestamp` or `date`/`time`, not both")
        for f_, src in [*cols.items(), *tenders_columns.items()]:
            if not isinstance(src, str) or not src.strip():
                raise ProfileError(f"{path}: files.{kind}.columns.{f_} must name a source field")
        opts = {}
        for key in ("path", "each", "sheet"):
            if key in spec:
                if not isinstance(spec[key], str) or not spec[key].strip():
                    raise ProfileError(f"{path}: files.{kind}.{key} must be a non-empty string")
                opts[key] = spec[key]
        if "match" in spec:
            m = spec["match"]
            if isinstance(m, str):
                m = [m]
            if not isinstance(m, list) or not m or not all(isinstance(x, str) and x and "/" not in x for x in m):
                raise ProfileError(f"{path}: files.{kind}.match must be file name patterns")
            opts["match"] = m
        columns[kind] = cols
        file_opts[kind] = opts
    maps: dict[str, dict[str, str]] = {}
    allowed = {"tender": TENDERS, "unit": UNITS, "direction": {"in", "out"}, "reason": REASONS}
    for kind, table in (doc.get("maps") or {}).items():
        if kind not in MAPS:
            raise ProfileError(f"{path}: unknown map {kind}")
        out = {}
        for ours, words in table.items():
            if ours not in allowed[kind]:
                raise ProfileError(f"{path}: maps.{kind}.{ours} is not one of {sorted(allowed[kind])}")
            for w in [ours, *(words or [])]:
                key = re.sub(r"\s+", " ", str(w).strip().lower())
                if key in out and out[key] != ours:
                    raise ProfileError(f"{path}: maps.{kind}: {w!r} maps to both {out[key]} and {ours}")
                out[key] = ours
        maps[kind] = out
    till_default = str(doc.get("tillDefault", "till-1"))
    if not re.fullmatch(r"[a-z0-9-]{1,64}", till_default):
        raise ProfileError(f"{path}: tillDefault must match [a-z0-9-]{{1,64}}")
    ts_format = str(doc.get("timestampFormat", "iso8601"))
    if ts_format not in TIMESTAMP_FORMATS and "%" not in ts_format:
        raise ProfileError(f"{path}: timestampFormat must be one of {sorted(TIMESTAMP_FORMATS)} or a strftime pattern")
    money_unit = str(doc.get("moneyUnit", "major"))
    if money_unit not in ("major", "minor"):
        raise ProfileError(f"{path}: moneyUnit must be major (rupees) or minor (paise)")
    csv_opts = doc.get("csv") or {}
    if not isinstance(csv_opts, dict) or set(csv_opts) - CSV_KEYS:
        raise ProfileError(f"{path}: csv keys must be among {sorted(CSV_KEYS)}")
    delimiter = str(csv_opts.get("delimiter", ","))
    if len(delimiter) != 1:
        raise ProfileError(f"{path}: csv.delimiter must be one character")
    currency = str(doc.get("currency", "INR"))
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ProfileError(f"{path}: currency must be an ISO 4217 code")
    if not doc.get("name"):
        raise ProfileError(f"{path}: name is required")
    try:
        tz = ZoneInfo(doc.get("timezone", "Asia/Kolkata"))
    except Exception as e:  # ZoneInfoNotFoundError, ValueError
        raise ProfileError(f"{path}: unknown timezone {doc.get('timezone')!r}") from e
    return Profile(str(doc["name"]), system, tz,
                   doc.get("dateFormat", "%Y-%m-%d"), doc.get("timeFormat", "%H:%M:%S"), currency,
                   doc.get("decimalSeparator", "."), doc.get("thousandsSeparator", ","), till_default, columns,
                   tenders_columns, maps, ts_format, money_unit, delimiter, str(csv_opts.get("encoding", "utf-8-sig")),
                   file_opts)
