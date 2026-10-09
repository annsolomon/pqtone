"""Milestone E5: the drift check itself."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import openapi_surface as o  # noqa: E402

SPEC = o.load(str(ROOT / "services/event-core/openapi.yaml"))


def test_the_committed_spec_has_every_controller_operation():
    ops = set(o.surface(SPEC))
    assert {"POST /v1/events", "POST /v1/events/batch", "GET /api/incidents", "POST /api/incidents/{id}/actions",
            "GET /api/admin/audit", "GET /api/admin/audit/verify", "GET /api/stream"} <= ops
    assert "POST /logout" not in ops, "served by a Spring Security filter, not a controller"


def test_identical_documents_have_no_drift():
    assert o.drift(o.surface(SPEC), o.surface(copy.deepcopy(SPEC))) == []


def test_each_kind_of_drift_is_reported():
    gen = copy.deepcopy(SPEC)
    del gen["paths"]["/api/pipeline/health"]                                         # gone from code
    gen["paths"]["/api/new"] = {"get": {"responses": {}}}                            # new in code
    gen["paths"]["/api/events/recent"]["get"]["parameters"][1]["name"] = "max"       # renamed query param
    gen["paths"]["/v1/events"]["post"]["requestBody"]["content"] = {"application/json": {}}  # media type
    problems = o.drift(o.surface(SPEC), o.surface(gen))
    assert "GET /api/pipeline/health: in openapi.yaml but not served by the code" in problems
    assert "GET /api/new: served by the code but missing from openapi.yaml" in problems
    assert any(p.startswith("GET /api/events/recent: query differs") for p in problems)
    assert any(p.startswith("POST /v1/events: body differs") for p in problems)


def test_refs_and_path_level_parameters_are_resolved():
    doc = {"components": {"parameters": {"Id": {"name": "id", "in": "path", "required": True}}},
           "paths": {"/x/{id}": {"parameters": [{"$ref": "#/components/parameters/Id"}],
                                 "get": {"parameters": [{"name": "q", "in": "query"}]}}}}
    assert o.surface(doc)["GET /x/{id}"] == {"path": [("id", True)], "query": [("q", False)], "body": None}


def test_cli(tmp_path, capsys):
    import json
    (tmp_path / "gen.json").write_text(json.dumps(SPEC))
    assert o.main(["x", str(ROOT / "services/event-core/openapi.yaml"), str(tmp_path / "gen.json")]) == 0
    assert "no drift" in capsys.readouterr().out
