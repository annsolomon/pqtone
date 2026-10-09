#!/usr/bin/env python3
"""Milestone 0.7: one mechanical rename of the placeholder product name.

  python3 pip/scripts/rename-product.py --old pip --new pqt [--apply]     (run from the repository root)

Without --apply it prints what would change. With --apply it rewrites tracked text files and moves
paths with `git mv`. It is deliberately explicit about what it leaves alone:

  * Python's package manager: "pip install", "python -m pip", "pip3", ".venv/bin/pip", and pip's own
    environment variables (PIP_NO_CACHE_DIR, PIP_DISABLE_PIP_VERSION_CHECK, ...);
  * words that merely contain the letters (pipeline, pipefail, pipeInput, Pipe, PipelineHealth);
  * lines that tell the history of the name (they mention the placeholder or the package-manager clash);
  * the audit advisory-lock constant (its value spells the old name; changing it changes behaviour).

Rules (old = pip, new = pqt; case kept):
  pip (word), pip_*, pip-*  -> pqt, pqt_*, pqt-*       e.g. com.pip, urn:pip, pip_scorer, pip-scorer
  PIP_* (env vars)          -> PQT_*                   except pip's own variables
  PIP (word), PIPSESSION    -> PQT, PQTSESSION
  Pip<Upper>... (CamelCase) -> Pqt...                  PipProperties; not Pipeline/Pipe
  paths: pip/ (project), com/pip/ (Java), pip_scorer/, pip-realm.json.tmpl, PipProperties.java
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PIP_TOOL_VARS = {"PIP_NO_CACHE_DIR", "PIP_DISABLE_PIP_VERSION_CHECK", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
                 "PIP_CERT", "PIP_REQUIRE_VIRTUALENV", "PIP_BREAK_SYSTEM_PACKAGES", "PIP_TRUSTED_HOST",
                 "PIP_CONSTRAINT", "PIP_ROOT_USER_ACTION", "PIP_PROGRESS_BAR", "PIP_NO_INPUT"}
# Lines about the name itself (its history, or the rename procedure in the runbook) keep the old name.
HISTORY = re.compile(r"package manager|collides|placeholder name|placeholder [`\"]|<name>|<NAME>|\\\.pip\|", re.I)
# pip the tool, protected before the general rule and restored after it.
TOOL = re.compile(r"(?:-m |bin/)pip(?![A-Za-z0-9_-])|(?<![A-Za-z0-9_-])pip(?=\s+(?:install|uninstall|wheel|download|freeze|list|show)\b)|\bpip3\b")
KEEP_LINE = re.compile(r'0x50495041554449L')  # audit advisory lock key ("PIPAUDI")
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webm", ".ico", ".woff", ".woff2", ".zip", ".jar", ".pdf"}


def rules(old: str, new: str):
    o, n, O, N = old, new, old.upper(), new.upper()
    oc, nc = old.capitalize(), new.capitalize()
    tool_vars = "|".join(sorted(v[len(O) + 1:] for v in PIP_TOOL_VARS))
    return [
        # pip's own command lines and paths stay: "pip install", "-m pip", "bin/pip", "pip3", "pip wheel".
        (re.compile(rf"(?<![A-Za-z0-9_]){o}(?![A-Za-z0-9])"), n),
        (re.compile(rf"\b{O}_(?!(?:{tool_vars})\b)(?=[A-Z0-9{{$])"), f"{N}_"),
        (re.compile(rf"\b{O}SESSION\b"), f"{N}SESSION"),
        (re.compile(rf"\b{O}\b"), N),
        (re.compile(rf"\b{oc}(?=[A-Z])(?!eline|e[A-Z])"), nc),
    ]


def rewrite(text: str, rs) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if HISTORY.search(line) or KEEP_LINE.search(line):
            out.append(line)
            continue
        kept: list[str] = []

        def protect(m: re.Match) -> str:
            kept.append(m.group(0))
            return f"\0{len(kept) - 1}\0"

        line = TOOL.sub(protect, line)
        for pat, rep in rs:
            line = pat.sub(rep, line)
        line = re.sub(r"\0(\d+)\0", lambda m: kept[int(m.group(1))], line)
        out.append(line)
    return "".join(out)


def tracked(root: Path) -> list[Path]:
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    return [root / n for n in names if n]


def path_moves(root: Path, old: str, new: str) -> list[tuple[str, str]]:
    """Directories and files to move, deepest first so parents move last."""
    moves = []
    for p in sorted({q.relative_to(root) for q in tracked(root)}):
        parts = list(p.parts)
        for i, part in enumerate(parts):
            renamed = part
            cap = old.capitalize()
            if part == old or part == f"{old}_scorer" or part.startswith(f"{old}-"):
                renamed = new + part[len(old):]
            elif part.startswith(cap) and len(part) > len(cap) and part[len(cap)].isupper() \
                    and not part[len(cap):].startswith(("Eline", "E")):
                renamed = new.capitalize() + part[len(cap):]   # PipProperties.java -> PqtProperties.java
            if renamed != part:
                moves.append(("/".join(parts[: i + 1]), "/".join(parts[:i] + [renamed])))
    uniq = sorted(set(moves), key=lambda m: -m[0].count("/"))
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default="pip")
    ap.add_argument("--new", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9]{1,9}", a.new):
        raise SystemExit("--new: 2-10 lowercase letters/digits, starting with a letter (valid Java and Python package)")
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).decode().strip())
    rs = rules(a.old, a.new)
    changed = 0
    for f in tracked(root):
        if f.suffix.lower() in BINARY_EXT or not f.is_file() or f.name == "rename-product.py":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new_text = rewrite(text, rs)
        if new_text != text:
            changed += 1
            if a.apply:
                f.write_text(new_text, encoding="utf-8")
            else:
                print(f"edit {f.relative_to(root)}")
    moves = path_moves(root, a.old, a.new)
    for src, dst in moves:
        if a.apply:
            if (root / src).exists():
                (root / dst).parent.mkdir(parents=True, exist_ok=True)
                subprocess.check_call(["git", "mv", src, dst], cwd=root)
        else:
            print(f"move {src} -> {dst}")
    print(f"{'changed' if a.apply else 'would change'} {changed} files, {len(moves)} path moves", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
