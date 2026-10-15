"""Exact money and quantity parsing (ADR-033): decimal text in, integers out, never a float."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

_CURRENCY = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)


class ParseError(ValueError):
    """A value that cannot be converted exactly."""


def _decimal(text: str, *, decimal_sep: str = ".", thousands_sep: str = ",") -> Decimal:
    if text is None:
        raise ParseError("empty value")
    s = str(text).strip()
    if not s:
        raise ParseError("empty value")
    negative = False
    if s.startswith("(") and s.endswith(")"):  # accounting style negatives
        negative, s = True, s[1:-1].strip()
    s = _CURRENCY.sub("", s)
    if s.endswith(("Dr", "Cr")):  # Tally-style suffixes are not amounts we understand: refuse, never guess
        raise ParseError(f"ambiguous debit/credit suffix in {text!r}")
    if thousands_sep:
        s = s.replace(thousands_sep, "")
    if decimal_sep != ".":
        s = s.replace(decimal_sep, ".")
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", s):
        raise ParseError(f"not a number: {text!r}")
    try:
        d = Decimal(s)
    except InvalidOperation as e:  # pragma: no cover - the regex above already guards this
        raise ParseError(f"not a number: {text!r}") from e
    return -d if negative else d


def to_minor(text: str, *, decimals: int = 2, decimal_sep: str = ".", thousands_sep: str = ",") -> int:
    """'₹1,234.50' -> 123450. More decimals than the currency has are rounded half-even (ADR-033)."""
    d = _decimal(text, decimal_sep=decimal_sep, thousands_sep=thousands_sep)
    return int((d * (Decimal(10) ** decimals)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def to_milli(text: str, *, decimal_sep: str = ".", thousands_sep: str = ",") -> int:
    """Quantity text -> thousandths ('1.25' -> 1250, '2' -> 2000)."""
    d = _decimal(text, decimal_sep=decimal_sep, thousands_sep=thousands_sep)
    return int((d * 1000).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
