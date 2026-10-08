#!/usr/bin/env python3
"""Mark a milestone row done in a plan file: scripts/tick.py docs/plans/<file>.md <ID> [note]"""
import sys
from pathlib import Path

if len(sys.argv) < 3:
    sys.exit("usage: scripts/tick.py <plan.md> <milestone id> [note]")
path, mid = Path(sys.argv[1]), sys.argv[2]
note = sys.argv[3] if len(sys.argv) > 3 else ""
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
hits = [i for i, l in enumerate(lines) if l.startswith(f"| {mid} |") or l.startswith(f"| ✅ {mid} |")]
if len(hits) != 1:
    sys.exit(f"expected one row for {mid} in {path}, found {len(hits)}")
i = hits[0]
if lines[i].startswith(f"| ✅ {mid} |"):
    print(f"{mid} already ticked"); sys.exit(0)
row = lines[i].replace(f"| {mid} |", f"| ✅ {mid} |", 1)
if note:
    row = row.rstrip("\n").rstrip()
    row = row[:-1].rstrip() + f" Note: {note} |\n" if row.endswith("|") else row + f" {note}\n"
lines[i] = row
path.write_text("".join(lines), encoding="utf-8")
print(row.strip())
