#!/usr/bin/env python3
"""Milestone R4: a rule whose definition changed must carry a higher version.

Usage: rules-version-check.py OLD_RULES.yaml NEW_RULES.yaml     (exit 1 on a missing bump)

A definition is the rule's mode, severity and params. Incidents record the rule version, so an
unbumped change would make two different behaviours indistinguishable in the history. The same
check runs in the rules-engine when a document is published for hot reload (VersionCheck.java).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml


def semver(v: str) -> tuple[int, int, int]:
    parts = str(v).split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a MAJOR.MINOR.PATCH version: {v!r}")
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def definitions(doc: dict) -> dict[str, tuple[str, dict]]:
    out = {}
    for r in doc.get("rules", []):
        out[r["id"]] = (str(r["version"]), {"mode": r.get("mode"), "severity": r.get("severity", "medium"),
                                            "params": r.get("params")})
    return out


def problems(old: dict, new: dict) -> list[str]:
    before, after = definitions(old), definitions(new)
    out = []
    for rid, (v_new, d_new) in sorted(after.items()):
        semver(v_new)
        if rid not in before:
            continue
        v_old, d_old = before[rid]
        if d_old != d_new and semver(v_new) <= semver(v_old):
            out.append(f"{rid} changed but its version {v_new} is not higher than {v_old}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    old, new = (yaml.safe_load(Path(p).read_text()) for p in argv[1:])
    try:
        found = problems(old, new)
    except (ValueError, KeyError) as e:
        print(f"rules-version-check: {e}", file=sys.stderr)
        return 1
    for p in found:
        print(p)
    if not found:
        print("rules-version-check: every changed rule has a higher version")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
