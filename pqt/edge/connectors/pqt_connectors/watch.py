"""A watched drop folder (X1 B5a, ADR-037): files the shop's software (or a person) saves into a folder are
imported once and moved away.

Each poll takes the files that match a kind's patterns (`files.<kind>.match`, default names starting with the
kind) and have not changed for `min_age_s` (so a file still being written is left for the next poll); hidden
files, temporary names (`.part`, `.tmp`, `~`) and symbolic links (which could point anywhere on the box) are
ignored. All ready files are imported in one batch, bills
before payments, and handed to the sink together; only then are they moved: to `done/<date>/`, or to
`failed/` with `<name>.report.json` beside them when any of their rows were reported. The good rows of a
failed file were sent; a corrected file dropped again sends only what is still missing. If the sink fails
nothing moves, and the next poll imports the same files again (re-importing adds nothing).
"""
from __future__ import annotations

import fnmatch
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .profile import ORDER, Profile

TEMPORARY = (".part", ".tmp", ".crdownload", ".partial", "~")


def _wall() -> float:
    return time.time()


def classify(drop: Path, profile: Profile, *, min_age_s: float, now: float) -> tuple[list[tuple[str, Path]], list[str]]:
    ready: list[tuple[str, Path]] = []
    unknown: list[str] = []
    for f in sorted(p for p in drop.iterdir() if p.is_file() and not p.is_symlink()):
        if f.name.startswith(".") or f.name.lower().endswith(TEMPORARY) or f.name.endswith(".report.json"):
            continue
        if now - f.stat().st_mtime < min_age_s:
            continue
        kind = next((k for k in ORDER if k in profile.columns
                     and any(fnmatch.fnmatch(f.name, pat) for pat in profile.patterns(k))), None)
        if kind is None:
            unknown.append(f.name)
        else:
            ready.append((kind, f))
    ready.sort(key=lambda kf: (ORDER.index(kf[0]), kf[1].name))
    return ready, unknown


def _move(src: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / src.name
    n = 1
    while dest.exists():
        dest = folder / f"{src.stem}.{n}{src.suffix}"
        n += 1
    shutil.move(str(src), dest)
    return dest


def run_once(drop: str | Path, profile: Profile, make_importer: Callable, sink: Callable[[list[dict]], None], *,
             min_age_s: float = 10, now: Callable[[], float] = _wall) -> dict:
    drop = Path(drop)
    ready, unknown = classify(drop, profile, min_age_s=min_age_s, now=now())
    summary: dict = {"imported": [], "failed": [], "unknown": unknown, "events": 0}
    if not ready:
        return summary
    imp = make_importer()
    try:
        for kind, f in ready:
            imp.ingest(kind, f)
        report = imp.commit(sink)
    finally:
        imp.state.close()
    doc = report.as_dict()
    summary["events"] = doc["events"]
    day = datetime.fromtimestamp(now(), tz=timezone.utc).strftime("%Y-%m-%d")
    for _kind, f in ready:
        errors = [e for e in doc["errors"] if e["file"] == f.name]
        if errors:
            dest = _move(f, drop / "failed")
            (dest.parent / f"{dest.name}.report.json").write_text(
                json.dumps({"file": f.name, "errors": errors}, indent=2) + "\n", encoding="utf-8")
            summary["failed"].append(f.name)
        else:
            _move(f, drop / "done" / day)
            summary["imported"].append(f.name)
    return summary
