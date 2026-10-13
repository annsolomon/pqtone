"""Column-mapping profiles: how one billing system's CSV exports map onto the X1 contracts (B4).

A profile names, for each kind of export, which source column holds each contract field, how dates and
numbers are written, and how the system's words for tenders, units and directions map onto ours. Only
the mapped columns are ever read: whatever else an export carries (customer names, phone numbers, notes)
is never looked at (ADR-033).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

KINDS = {
    "bills": {"required": {"billNo", "date", "time", "sku", "qty", "amount"},
              "optional": {"till", "lineNo", "unit", "tax"}},
    "payments": {"required": {"billNo", "date", "time", "tender", "amount"},
                 "optional": {"till", "direction", "reference"}},
    "movements": {"required": {"docNo", "date", "time", "sku", "qty", "direction", "reason"},
                  "optional": {"unit", "lineNo"}},
    "stock": {"required": {"sku", "qty", "date", "time"}, "optional": {"unit"}},
    "dayTotals": {"required": {"date", "sales", "refunds", "bills"}, "optional": set()},
}
TOP = {"apiVersion", "name", "system", "description", "timezone", "dateFormat", "timeFormat", "currency",
       "decimalSeparator", "thousandsSeparator", "tillDefault", "files", "maps"}
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
    columns, tenders_columns = {}, {}
    for kind, spec in files.items():
        cols = dict((spec or {}).get("columns") or {})
        if kind == "dayTotals":
            tenders_columns = dict(cols.pop("tenders", {}) or {})
            if set(tenders_columns) - TENDERS:
                raise ProfileError(f"{path}: dayTotals tenders must be among {sorted(TENDERS)}")
        missing = KINDS[kind]["required"] - set(cols)
        extra = set(cols) - KINDS[kind]["required"] - KINDS[kind]["optional"]
        if missing or extra:
            raise ProfileError(f"{path}: files.{kind}.columns missing {sorted(missing)} / unknown {sorted(extra)}")
        columns[kind] = cols
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
    return Profile(str(doc["name"]), system, ZoneInfo(doc.get("timezone", "Asia/Kolkata")),
                   doc.get("dateFormat", "%Y-%m-%d"), doc.get("timeFormat", "%H:%M:%S"), doc.get("currency", "INR"),
                   doc.get("decimalSeparator", "."), doc.get("thousandsSeparator", ","), till_default, columns,
                   tenders_columns, maps)
