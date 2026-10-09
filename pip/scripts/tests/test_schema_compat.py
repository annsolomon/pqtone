"""Tests for scripts/schema-compat.py. Run from the project root (pytest scripts/tests)."""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("schema_compat", ROOT / "scripts" / "schema-compat.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sc = _load()

OBJ: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["a"],
    "properties": {
        "a": {"type": "string", "pattern": "^[a-z]+$", "maxLength": 10},
        "n": {"type": "integer", "minimum": 0, "maximum": 100},
        "kind": {"type": "string", "enum": ["x", "y"]},
        "inner": {"type": "object", "properties": {"z": {"type": "boolean"}}},
    },
}


def changed(**edits) -> dict:
    s = copy.deepcopy(OBJ)
    for path, value in edits.items():
        node = s
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        if value is DELETE:
            del node[keys[-1]]
        else:
            node[keys[-1]] = value
    return s


DELETE = object()


# --- the real repository -------------------------------------------------------------------------

def test_repository_catalog_is_compatible(capsys):
    assert sc.main(["--schemas", str(ROOT / "schemas")]) == 0
    assert "queue.length 1.0.0 -> 1.1.0: compatible" in capsys.readouterr().out


def test_queue_length_1_1_0_only_adds_an_optional_field():
    old = json.loads((ROOT / "schemas/data/store.queue.length-1.0.0.schema.json").read_text())
    new = json.loads((ROOT / "schemas/data/store.queue.length-1.1.0.schema.json").read_text())
    assert sc.compare(old, new) == []
    assert "estimatedWaitSeconds" in new["properties"]
    assert "estimatedWaitSeconds" not in new["required"]


def test_the_breaking_1_2_0_fixture_fails_with_every_reason(capsys):
    new = ROOT / "schemas/data/store.queue.length-1.1.0.schema.json"
    bad = FIXTURES / "store.queue.length-1.2.0-breaking.schema.json"
    assert sc.main([str(new), str(bad)]) == 1
    out = capsys.readouterr().out
    assert "openRegisters: field removed or renamed" in out
    assert "length: maximum lowered from 10000 to 100" in out
    assert "estimatedWaitSeconds: required field added" in out


def test_registering_the_breaking_version_fails_the_catalog_check(tmp_path, capsys):
    schemas = tmp_path / "schemas"
    shutil.copytree(ROOT / "schemas", schemas)
    shutil.copy(FIXTURES / "store.queue.length-1.2.0-breaking.schema.json",
                schemas / "data/store.queue.length-1.2.0.schema.json")
    cat = json.loads((schemas / "catalog.json").read_text())
    for t in cat["types"]:
        if t["type"] == "com.pip.store.queue.length":
            t["versions"].append({"dataschema": "https://schemas.pip.local/store/queue.length/1.2.0",
                                  "file": "data/store.queue.length-1.2.0.schema.json"})
    (schemas / "catalog.json").write_text(json.dumps(cat))
    assert sc.main(["--schemas", str(schemas)]) == 1
    assert "queue.length 1.1.0 -> 1.2.0: BREAKING" in capsys.readouterr().out


# --- catalog hygiene -------------------------------------------------------------------------------

def test_id_must_match_dataschema(tmp_path):
    schemas = tmp_path / "schemas"
    shutil.copytree(ROOT / "schemas", schemas)
    f = schemas / "data/store.queue.length-1.1.0.schema.json"
    s = json.loads(f.read_text())
    s["$id"] = "https://schemas.pip.local/store/queue.length/9.9.9"
    f.write_text(json.dumps(s))
    assert sc.main(["--schemas", str(schemas)]) == 1


def test_versions_must_be_listed_in_ascending_order(tmp_path):
    schemas = tmp_path / "schemas"
    shutil.copytree(ROOT / "schemas", schemas)
    cat = json.loads((schemas / "catalog.json").read_text())
    for t in cat["types"]:
        if t["type"] == "com.pip.store.queue.length":
            t["versions"].reverse()
    (schemas / "catalog.json").write_text(json.dumps(cat))
    assert sc.main(["--schemas", str(schemas)]) == 1


# --- one rule at a time ------------------------------------------------------------------------------

@pytest.mark.parametrize("new, reason", [
    (changed(properties__n=DELETE), "n: field removed or renamed"),
    (changed(properties__inner__properties__z=DELETE), "inner.z: field removed or renamed"),
    (changed(required=["a", "n"]), "n: required field added"),
    (changed(properties__n__type="string"), "n: type narrowed"),
    (changed(properties__n__maximum=50), "n: maximum lowered from 100 to 50"),
    (changed(properties__n__minimum=1), "n: minimum raised from 0 to 1"),
    (changed(properties__a__maxLength=5), "a: maxLength lowered from 10 to 5"),
    (changed(properties__a__minLength=1), "a: minLength added"),
    (changed(properties__a__pattern="^[a-c]+$"), "a: pattern changed"),
    (changed(properties__kind__enum=["x"]), "kind: enum values removed: y"),
    (changed(properties__inner__additionalProperties=False), "inner: additionalProperties closed"),
    (changed(properties__inner__properties__z__format="date"), "inner.z: format added"),
    (changed(properties__n__multipleOf=5), "n: multipleOf added"),
])
def test_each_tightening_is_reported(new, reason):
    problems = sc.compare(OBJ, new)
    assert any(reason in p for p in problems), problems


@pytest.mark.parametrize("new", [
    changed(properties__extra={"type": "string"}),           # optional field added to a closed object
    changed(properties__n__maximum=1000),                    # loosened
    changed(properties__n__minimum=DELETE),                  # constraint removed
    changed(properties__n__type="number"),                   # integer widened to number
    changed(properties__n__type=["integer", "null"]),        # nullable added
    changed(properties__kind__enum=["x", "y", "z"]),         # enum value added
    changed(properties__a__pattern=DELETE),                  # pattern removed
    changed(additionalProperties=True),                      # object opened
    changed(required=[]),                                    # requirement dropped
    changed(description="docs only", title="t"),             # annotations only
])
def test_compatible_changes_pass(new):
    assert sc.compare(OBJ, new) == []


def test_new_constraints_on_a_field_old_data_could_already_send_are_breaking():
    open_old = {"type": "object", "properties": {"a": {"type": "string"}}}
    new = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}
    assert any("b: field was free-form" in p for p in sc.compare(open_old, new))


def test_unsupported_keywords_fail_closed():
    old = {"type": "object", "oneOf": [{"required": ["a"]}]}
    new = {"type": "object", "oneOf": [{"required": ["b"]}]}
    assert any("oneOf changed" in p for p in sc.compare(old, new))
