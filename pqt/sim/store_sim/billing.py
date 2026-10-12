"""Billing and stock simulation with ground truth (X1, milestone B2).

A scenario opts in with a `billing:` section. Then every shopper served at a register gets a bill at
the end of service: basket lines drawn from a catalogue, payments by tender, stock leaving the shelf.
Around the bills: legitimate refunds at a staffed till, voided and re-issued bills, no-sale drawer
openings, deliveries, stock snapshots, and the billing system's day totals at closing. Contracts:
`schemas/data/pos.*` and `stock.*` (B1); data rules: ADR-033 (integer money, HMAC references, revisions).

Planted anomalies, recorded in the billing ground truth so later milestones can be scored:

- `emptyTillRefunds`: refunds keyed at a till while nobody is being served there;
- `dayTotalSkewBp`: the billing system's day total disagrees with its own bills by this many basis
  points (the reconcile job, B6, must flag more than 50 bp);
- `stockOverrides`: low opening stock, so popular items run out (`stock_out` truth records).

Determinism: one RNG stream of its own (SeedSequence([store seed, 0xB111])), drawn only when billing is
on, so a scenario without `billing:` produces exactly the events it produced before X1.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import numpy as np

from .config import parse_duration_ms

KEYS = {"catalogue", "basketMeanLines", "tenders", "splitTenderProbability", "refundProbability", "voidProbability",
        "noSalePerHour", "priceOverrideProbability", "stockSnapshotEvery", "deliveries", "stockOverrides", "anomalies"}
ANOMALY_KEYS = {"emptyTillRefunds", "dayTotalSkewBp"}
TENDERS = ("cash", "card", "upi", "wallet", "credit", "voucher", "other")
SIM_SOURCE = "sim"


def load(scenario_path: str | Path, cfg: dict | None) -> tuple[dict, dict, str] | None:
    """(billing config, catalogue, catalogue sha256) for a scenario with `billing:`, else None."""
    if not cfg:
        return None
    unknown = set(cfg) - KEYS
    if unknown:
        raise ValueError(f"invalid scenario: unknown key(s) billing.{', billing.'.join(sorted(unknown))}")
    if set(cfg.get("anomalies") or {}) - ANOMALY_KEYS:
        raise ValueError(f"invalid scenario: unknown billing.anomalies key(s) {sorted(set(cfg['anomalies']) - ANOMALY_KEYS)}")
    tenders = cfg.get("tenders") or {"cash": 0.4, "upi": 0.45, "card": 0.15}
    if set(tenders) - set(TENDERS) or abs(sum(tenders.values()) - 1.0) > 1e-9 or min(tenders.values()) < 0:
        raise ValueError("invalid scenario: billing.tenders must be known tenders with probabilities summing to 1")
    path = Path(scenario_path).parent / cfg.get("catalogue", "../../config/catalogue/store-001.json")
    raw = path.read_bytes()
    catalogue = json.loads(raw)
    skus = [i["sku"] for i in catalogue["items"]]
    if len(set(skus)) != len(skus):
        raise ValueError(f"{path}: duplicate sku")
    for sku in (cfg.get("stockOverrides") or {}):
        if sku not in skus:
            raise ValueError(f"invalid scenario: billing.stockOverrides names unknown sku {sku}")
    for d in cfg.get("deliveries") or []:
        for line in d["lines"]:
            if line["sku"] not in skus:
                raise ValueError(f"invalid scenario: delivery {d.get('docNo')} names unknown sku {line['sku']}")
    return {**cfg, "tenders": tenders}, catalogue, hashlib.sha256(raw).hexdigest()


def round_minor(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def line_amounts(price_minor: int, qty_milli: int, tax_rate_bp: int) -> tuple[int, int]:
    """Tax-inclusive line amount and the tax inside it, exact to the paisa (ADR-033)."""
    amount = round_minor(Decimal(price_minor) * Decimal(qty_milli) / Decimal(1000))
    tax = round_minor(Decimal(amount) * Decimal(tax_rate_bp) / Decimal(10000 + tax_rate_bp))
    return amount, tax


@dataclass
class Line:
    sku: str
    qty_milli: int
    unit: str
    amount: int
    tax: int


@dataclass
class Bill:
    no: int
    till: str
    hexref: str
    lines: list[Line]
    revision: int = 0
    payments: list[tuple[str, int]] = field(default_factory=list)  # (tender, amount) in
    refunded: bool = False

    @property
    def total(self) -> int:
        return sum(ln.amount for ln in self.lines)


Rec = tuple[int, str, str, dict]  # (t offset ms, short type, subject, data)


class BillingModel:
    def __init__(self, cfg: dict, catalogue: dict, *, store_id: str, seed: int, epoch_ms: int) -> None:
        self.cfg = cfg
        self.store = store_id
        self.currency = catalogue.get("currency", "INR")
        self.rng = np.random.default_rng(np.random.SeedSequence([seed, 0xB111]))
        self.key = hashlib.sha256(f"pqt-sim-billing:{seed}:{store_id}".encode()).digest()
        self.items = {i["sku"]: i for i in catalogue["items"]}
        self.skus = [i["sku"] for i in catalogue["items"]]
        overrides = cfg.get("stockOverrides") or {}
        self.stock = {s: int(overrides.get(s, self.items[s]["initialStockMilli"])) for s in self.skus}
        self.tender_names = list(cfg["tenders"])
        self.tender_p = np.array([cfg["tenders"][k] for k in self.tender_names], dtype=float)
        self.epoch_ms = epoch_ms
        self.bills: list[Bill] = []
        self.out_by_tender: dict[str, int] = {}
        self.in_by_tender: dict[str, int] = {}
        self.refunds = 0
        self.anomalies: list[dict] = []
        self.stocked_out: set[str] = set()
        self.events = 0

    # -- helpers ------------------------------------------------------------------------------------
    def _hex(self, kind: str, number: str) -> str:
        return hmac.new(self.key, f"{SIM_SOURCE}|{kind}|{number}".encode(), hashlib.sha256).hexdigest()[:32]

    def _tender(self) -> str:
        return str(self.rng.choice(self.tender_names, p=self.tender_p))

    def _rec(self, out: list[Rec], t: int, short: str, subject: str, data: dict) -> None:
        out.append((t, short, subject, {"storeId": self.store, **data}))
        self.events += 1

    def _sale_line(self, out: list[Rec], t: int, b: Bill, n: int, ln: Line) -> None:
        self._rec(out, t, "pos.sale.line", f"till:{b.till}",
                  {"tillId": b.till, "billRef": f"bill-{b.hexref}", "revision": b.revision, "lineNo": n, "sku": ln.sku,
                   "qtyMilli": ln.qty_milli, "unit": ln.unit, "amountMinor": ln.amount, "taxMinor": ln.tax,
                   "currency": self.currency})

    def _payment(self, out: list[Rec], t: int, b: Bill, no: int, tender: str, direction: str, amount: int) -> None:
        data = {"tillId": b.till, "billRef": f"bill-{b.hexref}", "paymentNo": no, "tender": tender, "direction": direction,
                "amountMinor": amount, "currency": self.currency}
        if tender in ("card", "upi", "wallet"):
            data["providerRef"] = f"pay-{self._hex('pay', f'{b.no}:{no}')}"
        self._rec(out, t, "pos.payment", f"till:{b.till}", data)
        book = self.in_by_tender if direction == "in" else self.out_by_tender
        book[tender] = book.get(tender, 0) + amount

    def _movement(self, out: list[Rec], t: int, sku: str, direction: str, reason: str, qty: int, doc_hex: str,
                  line_no: int) -> None:
        self._rec(out, t, "stock.movement", f"sku:{sku}",
                  {"sku": sku, "direction": direction, "reason": reason, "qtyMilli": qty, "unit": self.items[sku]["unit"],
                   "docRef": f"doc-{doc_hex}", "lineNo": line_no})
        self.stock[sku] += qty if direction == "in" else -qty

    def _draw_qty(self, item: dict) -> int:
        lo, hi = item["qtyMilli"]
        if item["unit"] == "kg" or item["unit"] == "l":
            steps = max(0, (hi - lo) // 50)
            return lo + 50 * int(self.rng.integers(0, steps + 1))
        return 1000 * int(self.rng.integers(lo // 1000, hi // 1000 + 1))

    def _basket(self, t: int) -> list[Line]:
        n = min(12, 1 + int(self.rng.poisson(max(0.0, float(self.cfg.get("basketMeanLines", 4)) - 1))))
        avail = [s for s in self.skus if self.stock[s] > 0]
        if not avail:
            return []
        w = np.array([self.items[s]["popularity"] for s in avail], dtype=float)
        picks = self.rng.choice(len(avail), size=min(n, len(avail)), replace=False, p=w / w.sum())
        lines = []
        for k in sorted(int(p) for p in picks):
            item = self.items[avail[k]]
            qty = min(self._draw_qty(item), self.stock[item["sku"]])
            if qty <= 0:
                continue
            price = item["priceMinor"]
            amount, tax = line_amounts(price, qty, item["taxRateBp"])
            lines.append(Line(item["sku"], qty, item["unit"], amount, tax))
        return lines

    # -- events driven by the store model -------------------------------------------------------------
    def bill(self, t: int, till: str) -> list[Rec]:
        """A served shopper's bill at the end of service, at register `till`."""
        out: list[Rec] = []
        lines = self._basket(t)
        if not lines:
            return out
        b = Bill(len(self.bills) + 1, till, "", lines)
        b.hexref = self._hex("bill", str(b.no))
        tt = t
        for n, ln in enumerate(b.lines, 1):
            if float(self.rng.random()) < float(self.cfg.get("priceOverrideProbability", 0.0)):
                discounted = round_minor(Decimal(ln.amount) * Decimal("0.9"))
                self._rec(out, tt, "pos.till.event", f"till:{till}",
                          {"tillId": till, "kind": "price_override", "billRef": f"bill-{b.hexref}",
                           "amountMinor": ln.amount - discounted, "currency": self.currency})
                ln.amount = discounted
                ln.tax = round_minor(Decimal(discounted) * Decimal(self.items[ln.sku]["taxRateBp"])
                                     / Decimal(10000 + self.items[ln.sku]["taxRateBp"]))
            self._sale_line(out, tt, b, n, ln)
            tt += 1
        if float(self.rng.random()) < float(self.cfg.get("voidProbability", 0.0)):
            self._rec(out, tt, "pos.till.event", f"till:{till}",
                      {"tillId": till, "kind": "void", "billRef": f"bill-{b.hexref}", "revision": b.revision,
                       "amountMinor": b.total, "currency": self.currency})
            tt += 1
            if len(b.lines) == 1:  # the whole bill cancelled: nothing sold
                return out
            b.revision += 1
            b.lines = b.lines[:-1]  # the customer put the last item back
            for n, ln in enumerate(b.lines, 1):
                self._sale_line(out, tt, b, n, ln)
                tt += 1
        total = b.total
        if float(self.rng.random()) < float(self.cfg.get("splitTenderProbability", 0.0)) and total >= 2:
            first = int(self.rng.integers(1, total))
            parts = [("cash", first), (self._tender(), total - first)]
        else:
            parts = [(self._tender(), total)]
        for no, (tender, amount) in enumerate(parts, 1):
            self._payment(out, tt, b, no, tender, "in", amount)
            b.payments.append((tender, amount))
            tt += 1
        for n, ln in enumerate(b.lines, 1):
            self._movement(out, tt, ln.sku, "out", "sale", ln.qty_milli, b.hexref, n)
            if self.stock[ln.sku] <= 0 and ln.sku not in self.stocked_out:
                self.stocked_out.add(ln.sku)
                self.anomalies.append({"kind": "stock_out", "sku": ln.sku, "atMs": self.epoch_ms + tt})
        self.bills.append(b)
        if float(self.rng.random()) < float(self.cfg.get("refundProbability", 0.0)):
            out += self.refund(tt + 1000, till)
        return out

    def _refundable(self) -> Bill | None:
        cands = [b for b in self.bills if not b.refunded and b.payments]
        if not cands:
            return None
        return cands[int(self.rng.integers(0, len(cands)))]

    def refund(self, t: int, till: str, *, planted: bool = False) -> list[Rec]:
        """Refund of an earlier bill's first line at `till` (a customer returning an item)."""
        out: list[Rec] = []
        b = self._refundable()
        if b is None:
            return out
        b.refunded = True
        ln = b.lines[0]
        tender = b.payments[0][0]
        self._rec(out, t, "pos.till.event", f"till:{till}",
                  {"tillId": till, "kind": "refund", "billRef": f"bill-{b.hexref}", "amountMinor": ln.amount,
                   "currency": self.currency})
        # The refund is keyed at `till`, against the original bill's reference.
        data_bill = Bill(b.no, till, b.hexref, b.lines, b.revision)
        self._payment(out, t + 1, data_bill, len(b.payments) + 1, tender, "out", ln.amount)
        self._movement(out, t + 2, ln.sku, "in", "customer_return", ln.qty_milli, b.hexref, 1)
        self.refunds += ln.amount
        if planted:
            self.anomalies.append({"kind": "empty_till_refund", "tillId": till, "atMs": self.epoch_ms + t,
                                   "billRef": f"bill-{b.hexref}", "amountMinor": ln.amount})
        return out

    def no_sale(self, t: int, till: str) -> list[Rec]:
        out: list[Rec] = []
        self._rec(out, t, "pos.till.event", f"till:{till}", {"tillId": till, "kind": "no_sale"})
        return out

    def delivery(self, t: int, doc_no: str, lines: list[dict]) -> list[Rec]:
        out: list[Rec] = []
        hexref = self._hex("doc", f"delivery:{doc_no}")
        for n, line in enumerate(lines, 1):
            sku = line["sku"]
            qty = int(round(float(line["qty"]) * 1000))
            self._movement(out, t + n - 1, sku, "in", "purchase_receipt", qty, hexref, n)
            if self.stock[sku] > 0:
                self.stocked_out.discard(sku)
        return out

    def snapshot(self, t: int) -> list[Rec]:
        out: list[Rec] = []
        for k, sku in enumerate(self.skus):
            self._rec(out, t + k, "stock.level", f"sku:{sku}",
                      {"sku": sku, "qtyMilli": self.stock[sku], "unit": self.items[sku]["unit"], "source": SIM_SOURCE})
        return out

    def close(self, t: int) -> list[Rec]:
        """The billing system's day totals at closing, then a last stock snapshot."""
        out: list[Rec] = []
        truth = self.true_totals()
        reported = dict(truth)
        skew = int((self.cfg.get("anomalies") or {}).get("dayTotalSkewBp", 0))
        if skew:
            reported["salesMinor"] = round_minor(Decimal(truth["salesMinor"]) * (Decimal(10000 + skew) / Decimal(10000)))
            self.anomalies.append({"kind": "day_total_mismatch", "atMs": self.epoch_ms + t, "skewBp": skew,
                                   "trueSalesMinor": truth["salesMinor"], "reportedSalesMinor": reported["salesMinor"]})
        business_date = np.datetime64(self.epoch_ms, "ms").astype("datetime64[D]").astype(str)
        self._rec(out, t, "pos.day.total", f"store:{self.store}",
                  {"businessDate": business_date, "salesMinor": reported["salesMinor"],
                   "refundsMinor": reported["refundsMinor"], "bills": reported["bills"],
                   "paymentsByTender": reported["paymentsByTender"], "currency": self.currency, "source": SIM_SOURCE})
        out += self.snapshot(t + 1)
        return out

    # -- ground truth ---------------------------------------------------------------------------------
    def true_totals(self) -> dict:
        net = {k: self.in_by_tender.get(k, 0) - self.out_by_tender.get(k, 0)
               for k in sorted(set(self.in_by_tender) | set(self.out_by_tender))}
        return {"salesMinor": sum(b.total for b in self.bills), "refundsMinor": self.refunds, "bills": len(self.bills),
                "paymentsByTender": net}

    def truth(self) -> dict:
        return {"storeId": self.store, "currency": self.currency, "dayTotals": self.true_totals(),
                "anomalies": self.anomalies, "events": self.events,
                "closingStockMilli": dict(sorted(self.stock.items()))}
