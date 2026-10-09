#!/usr/bin/env python3
"""Fail when a new version of a data schema would reject data an older version accepted.

The contract rule (CLAUDE.md) is "additive only": every new version must accept everything the
previous version accepted, so a producer can keep sending the old shape and a consumer can be
upgraded independently.

Usage, from the project root:
  scripts/schema-compat.py                     check every type in schemas/catalog.json, version by version
  scripts/schema-compat.py --schemas DIR       same, for another schemas folder
  scripts/schema-compat.py OLD.json NEW.json   compare two schema files

Exit codes: 0 compatible, 1 a breaking change or a catalog problem, 2 usage error.

Reported as breaking:
  - a field removed or renamed (a rename looks exactly like a removal);
  - a required field added;
  - a type narrowed (integer -> string, or a type dropped from a list; integer -> number is a widening);
  - a bound tightened: minimum/exclusiveMinimum raised, maximum/exclusiveMaximum lowered, minLength or
    minItems or minProperties raised, maxLength or maxItems or maxProperties lowered, any of them added;
  - pattern, format, const or multipleOf added or changed; enum values removed or an enum added;
  - additionalProperties closed, or constraints added to a field an open object already let through;
  - uniqueItems turned on;
  - any change to a composition or reference keyword (allOf, anyOf, oneOf, not, if/then/else, $ref,
    dependentRequired, ...). Those can't be proven compatible here, so the check fails closed.
Annotations (title, description, examples, $comment, default, deprecated) never matter.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

Schema = dict[str, Any]

ANNOTATIONS = {"title", "description", "examples", "$comment", "default", "deprecated", "readOnly",
               "writeOnly", "$schema", "$id"}
HANDLED = {"type", "properties", "required", "additionalProperties", "enum", "const", "pattern", "format",
           "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength",
           "maxLength", "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties", "items"}
LOWER_BOUNDS = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")
UPPER_BOUNDS = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")
SEMVER = re.compile(r"/(\d+)\.(\d+)\.(\d+)$")


def _types(s: Schema) -> set[str] | None:
    t = s.get("type")
    if t is None:
        return None
    return {t} if isinstance(t, str) else set(t)


def _covers(new: set[str], t: str) -> bool:
    return t in new or (t == "integer" and "number" in new)


def compare(old: Schema, new: Schema, path: str = "") -> list[str]:
    """Problems that make `new` reject some instance `old` accepted. Empty list = compatible."""
    where = path or "(root)"
    out: list[str] = []

    old_t, new_t = _types(old), _types(new)
    if new_t is not None and (old_t is None or not all(_covers(new_t, t) for t in old_t)):
        out.append(f"{where}: type narrowed from {sorted(old_t) if old_t else 'any'} to {sorted(new_t)}")

    for k in LOWER_BOUNDS:
        if k in new and (k not in old or new[k] > old[k]):
            out.append(f"{where}: {k} added" if k not in old else f"{where}: {k} raised from {old[k]} to {new[k]}")
    for k in UPPER_BOUNDS:
        if k in new and (k not in old or new[k] < old[k]):
            out.append(f"{where}: {k} added" if k not in old else f"{where}: {k} lowered from {old[k]} to {new[k]}")

    for k in ("pattern", "format", "const"):
        if k in new and k not in old:
            out.append(f"{where}: {k} added")
        elif k in new and new[k] != old[k]:
            out.append(f"{where}: {k} changed from {old[k]!r} to {new[k]!r}")
    if "multipleOf" in new:
        if "multipleOf" not in old:
            out.append(f"{where}: multipleOf added")
        elif old["multipleOf"] % new["multipleOf"] != 0:
            out.append(f"{where}: multipleOf changed from {old['multipleOf']} to {new['multipleOf']}")
    if "enum" in new:
        if "enum" not in old:
            out.append(f"{where}: enum added")
        else:
            gone = [v for v in old["enum"] if v not in new["enum"]]
            if gone:
                out.append(f"{where}: enum values removed: {', '.join(map(str, gone))}")
    if new.get("uniqueItems") and not old.get("uniqueItems"):
        out.append(f"{where}: uniqueItems added")

    added_required = set(new.get("required", [])) - set(old.get("required", []))
    for name in sorted(added_required):
        out.append(f"{_join(path, name)}: required field added")

    old_props: dict[str, Schema] = old.get("properties", {})
    new_props: dict[str, Schema] = new.get("properties", {})
    old_open = old.get("additionalProperties", True) is not False
    new_open = new.get("additionalProperties", True) is not False
    if old_open and not new_open:
        out.append(f"{where}: additionalProperties closed")
    for name, old_sub in old_props.items():
        if name not in new_props:
            # Even on an open object, a field that disappears from the schema is gone from the contract.
            out.append(f"{_join(path, name)}: field removed or renamed")
            continue
        out += compare(old_sub, new_props[name], _join(path, name))
    for name, new_sub in new_props.items():
        if name in old_props or not old_open:
            continue
        if isinstance(old.get("additionalProperties"), dict):
            out += compare(old["additionalProperties"], new_sub, _join(path, name))
        elif _constrains(new_sub):
            out.append(f"{_join(path, name)}: field was free-form in the old version and now has constraints")
    if isinstance(old.get("additionalProperties"), dict) and isinstance(new.get("additionalProperties"), dict):
        out += compare(old["additionalProperties"], new["additionalProperties"], f"{where}.*")
    elif old.get("additionalProperties", True) is True and isinstance(new.get("additionalProperties"), dict):
        if _constrains(new["additionalProperties"]):
            out.append(f"{where}: additionalProperties constrained")

    if isinstance(old.get("items"), dict) and isinstance(new.get("items"), dict):
        out += compare(old["items"], new["items"], f"{path}[]" if path else "[]")
    elif "items" in new and "items" not in old and _constrains(new["items"]):
        out.append(f"{where}: items constrained")

    for k in sorted((set(old) | set(new)) - HANDLED - ANNOTATIONS):
        if old.get(k) != new.get(k):
            out.append(f"{where}: {k} changed (not checked automatically; fails closed)")
    return out


def _constrains(s: Any) -> bool:
    return s is False or (isinstance(s, dict) and bool(set(s) - ANNOTATIONS))


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _semver(dataschema: str) -> tuple[int, int, int] | None:
    m = SEMVER.search(dataschema)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _short(dataschema: str) -> str:
    # https://schemas.pqt.local/store/queue.length/1.1.0 -> queue.length
    parts = dataschema.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 else dataschema


def check_catalog(schemas: Path) -> int:
    catalog = json.loads((schemas / "catalog.json").read_text(encoding="utf-8"))
    failed = False
    for t in catalog.get("types", []):
        versions = t.get("versions", [])
        loaded: list[tuple[str, Schema]] = []
        for v in versions:
            ds, f = v.get("dataschema", ""), v.get("file", "")
            p = schemas / f
            if not p.is_file():
                print(f"{t['type']}: {f} does not exist")
                failed = True
                continue
            s = json.loads(p.read_text(encoding="utf-8"))
            if s.get("$id") != ds:
                print(f"{t['type']}: $id {s.get('$id')} in {f} does not match dataschema {ds}")
                failed = True
            if _semver(ds) is None:
                print(f"{t['type']}: dataschema {ds} does not end in a MAJOR.MINOR.PATCH version")
                failed = True
            loaded.append((ds, s))
        order = [_semver(ds) for ds, _ in loaded]
        if None not in order and order != sorted(order):
            print(f"{t['type']}: versions must be listed oldest first, without repeats")
            failed = True
        if len(set(order)) != len(order):
            print(f"{t['type']}: a version is listed twice")
            failed = True
        for (ods, old), (nds, new) in zip(loaded, loaded[1:]):
            name = f"{_short(nds)} {ods.rsplit('/', 1)[-1]} -> {nds.rsplit('/', 1)[-1]}"
            problems = compare(old, new)
            if problems:
                failed = True
                print(f"{name}: BREAKING")
                for p in problems:
                    print(f"  - {p}")
            else:
                print(f"{name}: compatible")
    if not failed:
        print(f"schema-compat: all {len(catalog.get('types', []))} types compatible")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--schemas", default="schemas", help="schemas folder holding catalog.json")
    ap.add_argument("files", nargs="*", help="OLD.json NEW.json to compare two files")
    a = ap.parse_args(argv)
    if a.files:
        if len(a.files) != 2:
            ap.print_usage(sys.stderr)
            return 2
        old, new = (json.loads(Path(f).read_text(encoding="utf-8")) for f in a.files)
        problems = compare(old, new)
        label = f"{Path(a.files[0]).name} -> {Path(a.files[1]).name}"
        print(f"{label}: {'BREAKING' if problems else 'compatible'}")
        for p in problems:
            print(f"  - {p}")
        return 1 if problems else 0
    return check_catalog(Path(a.schemas))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
