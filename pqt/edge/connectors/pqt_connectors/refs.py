"""Pseudonymous document references (ADR-033).

`HMAC-SHA256(store key, source system | document number)`, hex, first 32 characters, with a kind prefix
(`bill-`, `doc-`, `pay-`). The same number always gives the same reference in one store; nothing outside
the shop can reverse it. The key is generated on the edge box at install (`pqt-connectors init-key`) and
never leaves it; unlike track ids it does not rotate, so a refund can join its sale weeks later.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

KEY_BYTES = 32


class KeyError_(RuntimeError):
    """The store key is missing, malformed or readable by others."""


def new_key(path: str | Path) -> None:
    p = Path(path)
    if p.exists():
        raise KeyError_(f"{p} exists: a new key would orphan every reference made with the old one")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(secrets.token_hex(KEY_BYTES) + "\n")


def load_key(path: str | Path) -> bytes:
    p = Path(path)
    try:
        st = p.stat()
    except OSError as e:
        raise KeyError_(f"store key {p} is not readable") from e
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise KeyError_(f"store key {p} must not be readable by group or others (chmod 600)")
    text = p.read_text(encoding="ascii").strip()
    try:
        key = bytes.fromhex(text)
    except ValueError as e:
        raise KeyError_(f"store key {p} is not hex") from e
    if len(key) != KEY_BYTES:
        raise KeyError_(f"store key {p} must be {KEY_BYTES} bytes")
    return key


class Refs:
    def __init__(self, key: bytes, system: str) -> None:
        if len(key) != KEY_BYTES:
            raise KeyError_("store key must be 32 bytes")
        self._key, self.system = key, system

    def hex(self, number: str) -> str:
        n = str(number).strip()
        if not n:
            raise ValueError("empty document number")
        return hmac.new(self._key, f"{self.system}|{n}".encode(), hashlib.sha256).hexdigest()[:32]

    def bill(self, number: str) -> str:
        return f"bill-{self.hex(number)}"

    def doc(self, number: str) -> str:
        return f"doc-{self.hex(number)}"

    def pay(self, reference: str) -> str:
        return f"pay-{self.hex('payment:' + str(reference).strip())}"
