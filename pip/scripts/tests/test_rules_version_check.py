"""Milestone R4: CI refuses a changed rule without a version bump."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rvc", ROOT / "scripts" / "rules-version-check.py")
rvc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rvc)

RULES = yaml.safe_load((ROOT / "config/rules.yaml").read_text())


def queue(doc):
    return next(r for r in doc["rules"] if r["id"] == "R-QUEUE-001")


def test_the_repository_file_against_itself():
    assert rvc.problems(RULES, copy.deepcopy(RULES)) == []


def test_changed_params_without_a_bump_fail_and_with_a_bump_pass():
    new = copy.deepcopy(RULES)
    queue(new)["params"]["threshold"] = 4
    assert rvc.problems(RULES, new) == ["R-QUEUE-001 changed but its version 1.0.0 is not higher than 1.0.0"]
    queue(new)["version"] = "1.1.0"
    assert rvc.problems(RULES, new) == []


def test_mode_and_severity_count_and_versions_must_parse():
    new = copy.deepcopy(RULES)
    queue(new)["mode"] = "shadow"
    assert len(rvc.problems(RULES, new)) == 1
    queue(new)["version"] = "1.1"
    try:
        rvc.problems(RULES, new)
    except ValueError as e:
        assert "MAJOR.MINOR.PATCH" in str(e)
    else:
        raise AssertionError("a bad version must be rejected")


def test_a_new_rule_needs_no_previous_version(tmp_path):
    new = copy.deepcopy(RULES)
    new["rules"].append({"id": "R-NEW-001", "version": "1.0.0", "mode": "shadow", "params": {}})
    assert rvc.problems(RULES, new) == []
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(RULES))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(new))
    assert rvc.main(["x", str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")]) == 0
