#!/usr/bin/env python3
"""Mark a milestone row done in a plan file: scripts/tick.py docs/plans/<file>.md <ID> [note]

The row's first cell gets a ✅. A note, if given, is appended to the table's "Done when"
column (found from the table header), or to the last cell when the table has none.
"""
import sys
from pathlib import Path


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        sys.exit("usage: scripts/tick.py <plan.md> <milestone id> [note]")
    path, mid = Path(argv[1]), argv[2]
    note = argv[3] if len(argv) > 3 else ""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    hits = [i for i, l in enumerate(lines) if l.startswith(f"| {mid} |") or l.startswith(f"| ✅ {mid} |")]
    if len(hits) != 1:
        sys.exit(f"expected one row for {mid} in {path}, found {len(hits)}")
    i = hits[0]
    if lines[i].startswith(f"| ✅ {mid} |"):
        print(f"{mid} already ticked")
        return 0
    row = cells(lines[i])
    row[0] = f"✅ {mid}"
    if note:
        header_at = i
        while header_at > 0 and lines[header_at - 1].lstrip().startswith("|"):
            header_at -= 1
        header = [h.lower() for h in cells(lines[header_at])]
        col = header.index("done when") if "done when" in header else len(row) - 1
        row[col] = f"{row[col]} Note: {note}".strip()
    lines[i] = "| " + " | ".join(row) + " |\n"
    path.write_text("".join(lines), encoding="utf-8")
    print(lines[i].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
