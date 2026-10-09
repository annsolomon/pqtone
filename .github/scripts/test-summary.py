#!/usr/bin/env python3
"""Surface test counts (and every skipped test) as a notice annotation, so a green run can be
checked for silently skipped tests through the checks API, without downloading the job log.

Usage: test-summary.py <title> <junit.xml or directory>...   (directories are searched for *.xml)
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def esc(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str]) -> int:
    title, paths = argv[0], argv[1:]
    files: list[Path] = []
    for p in map(Path, paths):
        files += sorted(p.glob("*.xml")) if p.is_dir() else ([p] if p.is_file() else [])
    lines, skipped = [], []
    for f in files:
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        cases = root.iter("testcase")
        n = s = fl = 0
        for c in cases:
            n += 1
            if c.find("skipped") is not None:
                s += 1
                msg = (c.find("skipped").get("message") or "").strip()[:120]
                skipped.append(f"{c.get('classname', '')}.{c.get('name', '')}: {msg}")
            elif c.find("failure") is not None or c.find("error") is not None:
                fl += 1
        if n:
            lines.append(f"{f.name}: {n - s - fl} passed, {s} skipped, {fl} failed")
    if not lines:
        print(f"test-summary: no junit files in {paths}")
        return 0
    body = "\n".join(lines + (["", "Skipped:"] + skipped[:40] if skipped else []))
    print(f"::notice title={esc(title).replace(',', '%2C').replace(':', '%3A')}::{esc(body[-3500:])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
