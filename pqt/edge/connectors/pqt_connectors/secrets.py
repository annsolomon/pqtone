"""Secrets by reference (ADR-037 §4): `env:NAME` or `file:/path` (mode 600), never the secret itself in a
profile or config file."""
from __future__ import annotations

import os
import stat
from pathlib import Path


class SecretError(ValueError):
    pass


def resolve(ref: str) -> str:
    if not isinstance(ref, str):
        raise SecretError("a secret must be given as env:NAME or file:/path")
    if ref.startswith("env:"):
        value = os.environ.get(ref[4:])
        if not value:
            raise SecretError(f"environment variable {ref[4:]} is not set")
        return value
    if ref.startswith("file:"):
        p = Path(ref[5:])
        try:
            mode = stat.S_IMODE(p.stat().st_mode)
        except OSError as e:
            raise SecretError(f"cannot read secret file {p}: {e.strerror}") from e
        if mode & 0o077:
            raise SecretError(f"secret file {p} is readable by others: chmod 600 it")
        value = p.read_text(encoding="utf-8").strip()
        if not value:
            raise SecretError(f"secret file {p} is empty")
        return value
    raise SecretError("a secret must be given as env:NAME or file:/path, never written into the config")
