"""The TallyPrime source (X1 B5e, ADR-037): vouchers from TallyPrime's HTTP server, as canonical records.

Each poll asks Tally (`POST http://<tally pc>:9000`, an Export request for the Day Book of one company) for the
vouchers from the cursor date less `lookbackDays` (Tally users back-date and alter vouchers) to today, and turns
them into records the importer handles like any other source:

- sales vouchers: one bill line per inventory entry; a payment for each entry in a ledger the profile maps as a
  tender (cash, bank, UPI, wallet ledgers); other ledgers (sales, tax, the party) are not payments;
- credit notes: a refund (payment out) for each tender ledger entry, and the returned items as stock movements
  in (customer_return);
- purchases: stock movements in (purchase_receipt) per inventory entry;
- cancelled and optional vouchers, and other voucher types, are skipped (counted in the result).

Re-reading a window adds nothing (bills are compared as a whole and an altered voucher becomes the next
revision). Only voucher numbers, dates, item names, quantities, amounts and tender ledger names are read: party
names, addresses and narrations are never looked at, and voucher numbers leave the box only as HMACs.

The response is parsed with defusedxml (entity expansion and external entities refused) and capped at 50 MB.
Tally's HTTP server has no authentication and speaks plain HTTP: it is reached only on loopback or the shop's
private network (plain HTTP to a public address is refused).
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from . import profile as profile_mod
from .importer import Importer
from .profile import Profile
from .restsource import _OPENER

MAX_RESPONSE = 50 * 1024 * 1024
DEFAULT_TYPES = {"sales": ["Sales"], "creditNote": ["Credit Note"], "purchase": ["Purchase"]}
_QTY = re.compile(r"^\s*(-?[0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Za-z][A-Za-z .]*)?")


class TallyFailure(Exception):
    pass


def _private_or_local(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    addrs = {ipaddress.ip_address(i[4][0]) for i in infos}
    return bool(addrs) and all(a.is_private or a.is_loopback or a.is_link_local for a in addrs)


def _check_url(url: str) -> None:
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("tally url must be http(s)://<host>:<port>, e.g. http://192.168.1.20:9000")
    if u.scheme == "http":
        try:
            ip = ipaddress.ip_address(u.hostname)
            ok = ip.is_private or ip.is_loopback or ip.is_link_local
        except ValueError:
            ok = _private_or_local(u.hostname)
        if not ok:
            raise ValueError("plain HTTP to Tally only on this machine or the shop's private network")


def _text(el, tag: str) -> str:
    found = el.find(tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def _entries(voucher, *tags: str):
    for tag in tags:
        yield from voucher.findall(tag)


def _qty(text_: str) -> tuple[str, str]:
    m = _QTY.match(text_ or "")
    if not m:
        return "", ""
    return m.group(1).replace(",", ""), (m.group(2) or "").strip()


def _amount(text_: str) -> str:
    s = (text_ or "").strip().replace(",", "")
    return s[1:] if s.startswith("-") else s


@dataclass
class TallySource:
    id: str
    profile: Profile
    url: str
    company: str
    start: date
    lookback_days: int = 7
    types: dict = field(default_factory=lambda: dict(DEFAULT_TYPES))
    timeout_s: int = 60
    today: Callable[[], date] = date.today
    opener: Callable = _OPENER.open

    @classmethod
    def from_config(cls, cfg: dict, *, base: str | Path = ".", today: Callable[[], date] | None = None) -> "TallySource":
        base = Path(base)
        unknown = set(cfg) - {"id", "type", "profile", "url", "company", "start", "lookbackDays", "voucherTypes",
                              "interval", "timeoutSeconds"}
        if unknown:
            raise ValueError(f"tally source: unknown key(s) {sorted(unknown)}")
        sid = str(cfg.get("id") or "")
        if not re.fullmatch(r"[a-z0-9-]{1,64}", sid):
            raise ValueError("tally source: id must match [a-z0-9-]{1,64}")
        _check_url(str(cfg.get("url") or ""))
        if not cfg.get("company"):
            raise ValueError(f"tally source {sid}: company (as named in Tally) is required")
        try:
            start = date.fromisoformat(str(cfg.get("start")))
        except ValueError:
            raise ValueError(f"tally source {sid}: start must be a date (YYYY-MM-DD)") from None
        types = dict(DEFAULT_TYPES)
        for k, v in (cfg.get("voucherTypes") or {}).items():
            if k not in DEFAULT_TYPES or not isinstance(v, list) or not v:
                raise ValueError(f"tally source {sid}: voucherTypes keys are sales, creditNote, purchase (lists of names)")
            types[k] = [str(x) for x in v]
        ppath = Path(cfg.get("profile", ""))
        prof = profile_mod.load(ppath if ppath.is_absolute() else base / ppath)
        for kind in ("bills", "payments", "movements"):
            if kind not in prof.columns:
                raise ValueError(f"tally source {sid}: the profile must map {kind} (start from profiles/tallyprime.yaml)")
        lookback = int(cfg.get("lookbackDays", 7))
        if not 0 <= lookback <= 400:
            raise ValueError(f"tally source {sid}: lookbackDays must be 0-400")
        src = cls(sid, prof, str(cfg["url"]).rstrip("/"), str(cfg["company"]), start, lookback, types,
                  int(cfg.get("timeoutSeconds", 60)))
        if today is not None:
            src.today = today
        return src

    # -- request ----------------------------------------------------------------------------------
    def envelope(self, frm: date, to: date) -> bytes:
        return ("<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE>"
                "<ID>Day Book</ID></HEADER><BODY><DESC><STATICVARIABLES>"
                f"<SVCURRENTCOMPANY>{escape(self.company)}</SVCURRENTCOMPANY>"
                f"<SVFROMDATE>{frm:%Y%m%d}</SVFROMDATE><SVTODATE>{to:%Y%m%d}</SVTODATE>"
                "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
                "</STATICVARIABLES></DESC></BODY></ENVELOPE>").encode("utf-8")

    def fetch(self, frm: date, to: date):
        from defusedxml import ElementTree as DET  # PSF-2.0
        from defusedxml.common import DefusedXmlException

        req = urllib.request.Request(self.url, data=self.envelope(frm, to), method="POST",
                                     headers={"Content-Type": "text/xml; charset=utf-8"})
        try:
            with self.opener(req, timeout=self.timeout_s) as r:
                raw = r.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as e:
            raise TallyFailure(f"Tally answered HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise TallyFailure(f"Tally is not reachable ({type(e).__name__}); is its HTTP server on?") from None
        if len(raw) > MAX_RESPONSE:
            raise TallyFailure(f"Tally's reply is larger than {MAX_RESPONSE} bytes; shorten lookbackDays")
        try:
            root = DET.fromstring(raw)
        except DefusedXmlException:
            raise TallyFailure("Tally's reply is XML with entity declarations; refused") from None
        except Exception as e:  # noqa: BLE001 - ParseError and friends
            raise TallyFailure(f"Tally's reply is not XML ({type(e).__name__})") from None
        line_error = _text(root, "./BODY/DATA/LINEERROR") or _text(root, ".//LINEERROR")
        if line_error:
            raise TallyFailure("Tally refused the request (LINEERROR); check the company name")
        return root.iter("VOUCHER")

    # -- vouchers to records ----------------------------------------------------------------------
    def _tender(self, ledger: str) -> str | None:
        try:
            return self.profile.lookup("tender", ledger)
        except ValueError:
            return None

    def records(self, vouchers) -> tuple[dict[str, list[dict]], dict[str, int]]:
        out: dict[str, list[dict]] = {"bills": [], "payments": [], "movements": []}
        counts: dict[str, int] = {}
        kinds = {name.lower(): k for k, names in self.types.items() for name in names}
        till = self.profile.till_default
        for v in vouchers:
            vtype = _text(v, "VOUCHERTYPENAME") or v.get("VCHTYPE", "")
            if _text(v, "ISCANCELLED").lower() == "yes" or _text(v, "ISOPTIONAL").lower() == "yes":
                counts["cancelled"] = counts.get("cancelled", 0) + 1
                continue
            kind = kinds.get(vtype.lower())
            if kind is None:
                counts["other"] = counts.get("other", 0) + 1
                continue
            counts[vtype] = counts.get(vtype, 0) + 1
            number = _text(v, "VOUCHERNUMBER")
            d = _text(v, "DATE")
            try:
                ts = datetime.strptime(d, "%Y%m%d").strftime("%Y-%m-%dT23:59:59")
            except ValueError:
                ts = ""  # reported by the importer as a bad timestamp
            items = list(_entries(v, "ALLINVENTORYENTRIES.LIST", "INVENTORYENTRIES.LIST", "INVENTORYENTRIESIN.LIST"))
            ledgers = list(_entries(v, "ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST"))
            if kind == "sales":
                for n, it in enumerate(items, 1):
                    qty, unit = _qty(_text(it, "BILLEDQTY") or _text(it, "ACTUALQTY"))
                    out["bills"].append({"billNo": number, "timestamp": ts, "till": till, "lineNo": n,
                                         "sku": _text(it, "STOCKITEMNAME"), "qty": qty, "unit": unit,
                                         "amount": _amount(_text(it, "AMOUNT"))})
            for le in ledgers:
                tender = self._tender(_text(le, "LEDGERNAME"))
                if tender is None or kind == "purchase":
                    continue
                out["payments"].append({"billNo": number, "timestamp": ts, "till": till, "tender": tender,
                                        "amount": _amount(_text(le, "AMOUNT")),
                                        "direction": "in" if kind == "sales" else "out"})
            if kind in ("creditNote", "purchase"):
                reason = "customer_return" if kind == "creditNote" else "purchase_receipt"
                for n, it in enumerate(items, 1):
                    qty, unit = _qty(_text(it, "BILLEDQTY") or _text(it, "ACTUALQTY"))
                    out["movements"].append({"docNo": f"{vtype}/{number}", "timestamp": ts, "sku": _text(it, "STOCKITEMNAME"),
                                             "qty": qty.lstrip("-"), "unit": unit, "direction": "in", "reason": reason,
                                             "lineNo": n})
        return out, counts

    # -- polling ------------------------------------------------------------------------------------
    def poll(self, imp: Importer, sink: Callable[[list[dict]], None]) -> dict:
        state = imp.state
        stored = state.cursor(self.id, "vouchers")
        cursor = date.fromisoformat(stored.strip('"')) if stored else self.start
        frm, to = cursor - timedelta(days=self.lookback_days), self.today()
        try:
            recs, counts = self.records(self.fetch(frm, to))
        except TallyFailure as e:
            return {"source": self.id, "records": {}, "events": 0, "vouchers": {},
                    "errors": [{"file": "tally", "row": None, "error": str(e)}], "cursor": None}
        for kind in ("bills", "payments", "movements"):
            if recs[kind]:
                imp.feed(kind, kind, recs[kind])
        report = imp.commit(sink).as_dict()
        new = None
        if not report["errors"] and to > cursor:
            new = to.isoformat()
            state.set_cursors(self.id, {"vouchers": new})
        return {"source": self.id, "records": {k: len(v) for k, v in recs.items()}, "events": report["events"],
                "vouchers": counts, "errors": report["errors"], "cursor": new}
