"""A watched drop folder (X1 B5a, ADR-037): files the shop's software (or a person) saves into a folder are
imported once and moved away.

Each poll takes the files that match a kind's patterns (`files.<kind>.match`, default names starting with the
kind) and have not changed for `min_age_s` (so a file still being written is left for the next poll); hidden
files, temporary names (`.part`, `.tmp`, `~`) and symbolic links (which could point anywhere on the box) are
ignored. All ready files are imported in one batch, bills
before payments, and handed to the sink together; only then are they moved: to `done/<date>/`, or to
`failed/` with `<name>.report.json` beside them when any of their rows were reported. The good rows of a
failed file were sent; a corrected file dropped again sends only what is still missing. If the sink fails
nothing moves, and the next poll imports the same files again (re-importing adds nothing). A file that cannot be
read at all (any error) goes to `failed/` too, so it never stops the watch.

Whoever can write the drop folder must not be able to make the connector write elsewhere: `done/`, `failed/` and
the day folders must be real folders (a symbolic link stops the poll before anything is imported), and reports
are created with O_EXCL | O_NOFOLLOW, never through an existing name.
"""
from __future__ import annotations

import fnmatch
import json
import os
import stat
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


class Refused(RuntimeError):
    pass


def _real_dir(folder: Path) -> Path:
    """Create `folder` if needed and make sure it (and its parent inside the drop) is a real directory."""
    for part in (folder.parent, folder):
        try:
            st = os.lstat(part)
        except FileNotFoundError:
            os.mkdir(part, 0o750)
            st = os.lstat(part)
        if not stat.S_ISDIR(st.st_mode):
            raise Refused(f"{part.name}/ is not a real folder (a symbolic link or a file): fix the drop folder")
    return folder


def _check(folders: list[Path]) -> None:
    """Before importing: whichever of these already exist must be real folders (nothing is created yet)."""
    for part in folders:
        try:
            st = os.lstat(part)
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise Refused(f"{part.name}/ is not a real folder (a symbolic link or a file): fix the drop folder")


def _free_name(folder: Path, name: str) -> Path:
    stem, dot, ext = name.partition(".")
    dest, n = folder / name, 1
    while os.path.lexists(dest):
        dest = folder / f"{stem}.{n}{dot}{ext}"
        n += 1
    return dest


def _move(src: Path, folder: Path) -> Path:
    dest = _free_name(folder, src.name)
    os.rename(src, dest)  # same file system, never follows a link at the destination (the name is free)
    return dest


def _write_new(path: Path, text: str) -> Path:
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
            break
        except FileExistsError:
            path = _free_name(path.parent, path.name)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run_once(drop: str | Path, profile: Profile, make_importer: Callable, sink: Callable[[list[dict]], None], *,
             min_age_s: float = 10, now: Callable[[], float] = _wall) -> dict:
    drop = Path(drop)
    ready, unknown = classify(drop, profile, min_age_s=min_age_s, now=now())
    summary: dict = {"imported": [], "failed": [], "unknown": unknown, "events": 0, "refused": None}
    if not ready:
        return summary
    day = datetime.fromtimestamp(now(), tz=timezone.utc).strftime("%Y-%m-%d")
    try:
        _check([drop / "done", drop / "done" / day, drop / "failed"])
    except Refused as e:
        summary["refused"] = str(e)
        return summary
    imp = make_importer()
    try:
        for kind, f in ready:
            try:
                imp.ingest(kind, f)
            except Exception as e:  # noqa: BLE001 - one unreadable file must not stop the others or the watch
                imp.report.error(f.name, None, f"could not be read ({type(e).__name__})")
        report = imp.commit(sink)
    finally:
        imp.state.close()
    doc = report.as_dict()
    summary["events"] = doc["events"]
    done_dir, failed_dir = drop / "done" / day, drop / "failed"
    for _kind, f in ready:
        errors = [e for e in doc["errors"] if e["file"] == f.name]
        if errors:
            dest = _move(f, _real_dir(failed_dir))
            _write_new(failed_dir / f"{dest.name}.report.json", json.dumps({"file": f.name, "errors": errors}, indent=2) + "\n")
            summary["failed"].append(f.name)
        else:
            _move(f, _real_dir(done_dir))
            summary["imported"].append(f.name)
    return summary
