"""A starting profile from a sample export (X1 B5a): `pqt-connectors suggest`.

Matches the sample's column names (or JSON keys) against the names billing systems commonly use for each
canonical field, and prints a profile to review. Columns it cannot place, personal data among them, are
listed as not mapped, so whoever reviews the profile sees what is left out. A suggestion is never imported
as it is: `pqt-connectors check` the reviewed profile on the sample first.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .profile import KINDS, TIMED

# Normalised names (lower case, letters and digits only) per canonical field, most specific first.
SYNONYMS: dict[str, list[str]] = {
    "billNo": ["billno", "billnumber", "invoiceno", "invoicenumber", "invno", "voucherno", "vouchernumber", "receiptno",
               "receiptnumber", "billid", "invoiceid", "orderno", "ordernumber", "orderid", "docno", "transactionno",
               "invoice", "bill"],
    "docNo": ["docno", "documentno", "documentnumber", "grnno", "grnnumber", "voucherno", "vouchernumber", "refno",
              "referenceno", "challanno", "purchaseno", "invoiceno"],
    "timestamp": ["timestamp", "datetime", "createdat", "createdon", "billedat", "invoicedat", "paidat", "transactiontime",
                  "transactiondatetime", "billdatetime", "time_stamp"],
    "date": ["date", "billdate", "invoicedate", "invdate", "voucherdate", "transactiondate", "txndate", "docdate", "grndate",
             "businessdate", "day"],
    "time": ["time", "billtime", "invoicetime", "vouchertime", "transactiontime", "txntime"],
    "till": ["counter", "counterno", "till", "tillno", "tillid", "register", "registerno", "pos", "posno", "terminal",
             "terminalid", "lane", "cashcounter"],
    "lineNo": ["line", "lineno", "linenumber", "sno", "slno", "srno", "serialno", "itemno"],
    "sku": ["itemcode", "sku", "productcode", "barcode", "articlecode", "ean", "code", "itemid", "productid", "partno"],
    "qty": ["qty", "quantity", "qnty", "units", "billedqty", "soldqty", "pcs"],
    "unit": ["unit", "uom", "units_of_measure", "unitofmeasure", "measure"],
    "amount": ["amount", "netamount", "lineamount", "total", "linetotal", "nettotal", "value", "netvalue", "amt",
               "billedamount", "grossamount"],
    "tax": ["tax", "taxamount", "gst", "gstamount", "vat", "taxamt", "totaltax"],
    "tender": ["mode", "paymentmode", "paymode", "tender", "paymenttype", "paymentmethod", "method", "instrument"],
    "direction": ["type", "direction", "txntype", "transactiontype", "drcr", "inout", "kind"],
    "reference": ["txnref", "transactionref", "reference", "referenceno", "utr", "rrn", "paymentref", "upiref", "ref"],
    "reason": ["reason", "vouchertype", "movementtype", "doctype", "transactiontype"],
    "sales": ["sales", "totalsales", "netsales", "grosssales", "salesamount"],
    "refunds": ["refunds", "totalrefunds", "returns", "salesreturn", "refundamount"],
    "bills": ["bills", "billcount", "noofbills", "invoices", "invoicecount", "transactions"],
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower().split(".")[-1])


def _guess(fields: list[str], names: list[str], used: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in fields:
        for syn in SYNONYMS.get(f, []):
            hit = next((n for n in names if n not in used and _norm(n) == _norm(syn)), None)
            if hit:
                out[f] = hit
                used.add(hit)
                break
    return out


def _flat_keys(obj: Any, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                keys += _flat_keys(v, f"{prefix}{k}.")
            elif not isinstance(v, list):
                keys.append(f"{prefix}{k}")
    return keys


def sample_names(kind: str, sample: str | Path) -> tuple[list[str], dict]:
    """Column names (or JSON paths) in a sample, and file options (path, each) for nested JSON."""
    p = Path(sample)
    ext = p.suffix.lower()
    if ext in (".csv", ".txt"):
        import csv

        with open(p, newline="", encoding="utf-8-sig") as f:
            head = f.readline()
        delim = max([",", ";", "\t", "|"], key=head.count)
        return [h.strip() for h in next(csv.reader([head], delimiter=delim))], ({"_delimiter": delim} if delim != "," else {})
    if ext == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(p, read_only=True, data_only=True)
        try:
            header = next(wb.worksheets[0].iter_rows(values_only=True), ()) or ()
        finally:
            wb.close()
        return [str(h).strip() for h in header if h is not None], {}
    if ext in (".ndjson", ".jsonl"):
        first = next((json.loads(line) for line in p.read_text(encoding="utf-8-sig").splitlines() if line.strip()), {})
        records, opts = [first], {}
    elif ext == ".json":
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
        opts = {}
        if isinstance(doc, list):
            records = doc
        else:
            key = kind if isinstance(doc.get(kind), list) else next((k for k, v in doc.items() if isinstance(v, list)), None)
            if key is None:
                raise ValueError("no list of records found in the JSON sample")
            records = doc[key]
            if key != kind:
                opts["path"] = key
    else:
        raise ValueError(f"unsupported sample type {ext!r}")
    first = records[0] if records else {}
    if kind == "bills" and isinstance(first, dict):
        each = next((k for k, v in first.items() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
        if each:
            opts["each"] = each
            inner = _flat_keys(first[each][0])
            outer = [f"$.{k}" for k in _flat_keys(first)]
            return inner + outer, opts
    return _flat_keys(first), opts


def suggest(kind: str, sample: str | Path, *, system: str = "my-system", base: dict | None = None) -> str:
    names, opts = sample_names(kind, sample)
    fields = sorted(KINDS[kind]["required"]) + sorted(KINDS[kind]["optional"])
    used: set[str] = set()
    # Timed kinds: a single timestamp field wins when the sample has one; otherwise date (+ time).
    timing = _guess(["timestamp"], names, used) if kind in TIMED else {}
    if kind in TIMED and not timing:
        timing = _guess(["date", "time"], names, used)
    cols = {**_guess(fields, names, used), **timing}
    order = ["billNo", "docNo", "timestamp", "date", "time", "till", "lineNo", "sku", "qty", "unit", "amount", "tax",
             "tender", "direction", "reference", "reason", "sales", "refunds", "bills"]
    cols = {f: cols[f] for f in order if f in cols}
    doc = dict(base or {})
    doc.update({"apiVersion": "connector.pqt/v1", "name": f"{system}-suggested", "system": system,
                "description": f"Suggested from {Path(sample).name}; formats and word maps copied from the generic profile."})
    if "timestamp" in cols:
        doc["timestampFormat"] = "iso8601"
    if "_delimiter" in opts:
        doc["csv"] = {"delimiter": opts.pop("_delimiter")}
    spec = {k: opts[k] for k in ("path", "each") if k in opts}
    spec["columns"] = cols
    doc["files"] = {kind: spec}
    unmapped = [n for n in names if n not in used]
    missing = sorted((KINDS[kind]["required"] | ({"timestamp"} if kind in TIMED else set())) - set(cols)
                     - ({"timestamp"} if "date" in cols else set()))
    lines = ["# Suggested by pqt-connectors suggest: review every mapping, then `pqt-connectors check` it on the sample.",
             f"# Not mapped (never read): {', '.join(unmapped) if unmapped else 'none'}"]
    if missing:
        lines.append(f"# Still to map by hand: {', '.join(missing)}")
    return "\n".join(lines) + "\n" + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
