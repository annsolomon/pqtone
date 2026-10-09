"""Milestone E5: the API served by event-core matches services/event-core/openapi.yaml.

springdoc generates the OpenAPI document from the controllers and serves it on the management
port only (never routed by the gateway). The comparison is the contract surface: operations,
path and query parameters, request media types (scripts/openapi_surface.py says what is left
out and why). Any drift fails the build: change the code and the spec together.
"""
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import openapi_surface  # noqa: E402

MGMT = os.environ.get("PIP_EVENT_CORE_MGMT", "http://event-core:8081")


def generated() -> dict:
    r = requests.get(f"{MGMT}/actuator/openapi", timeout=15)
    r.raise_for_status()
    return r.json()


def test_openapi_is_generated_from_the_code():
    doc = generated()
    assert doc["openapi"].startswith("3.1")
    assert "/v1/events" in doc["paths"]


def test_no_drift_between_the_code_and_openapi_yaml():
    committed = openapi_surface.surface(openapi_surface.load(str(ROOT / "services/event-core/openapi.yaml")))
    problems = openapi_surface.drift(committed, openapi_surface.surface(generated()))
    assert problems == [], "\n".join(problems)


def test_the_generated_document_is_not_public():
    base = os.environ.get("PIP_BASE_URL", "http://localhost:8080")
    for path in ("/v3/api-docs", "/actuator/openapi", "/swagger-ui.html"):
        r = requests.get(f"{base}{path}", timeout=10)
        # The gateway answers unknown paths with the console's index.html (SPA fallback), so
        # "not public" means: no OpenAPI document comes back, whatever the status.
        assert '"openapi"' not in r.text and "swagger" not in r.text.lower(), path
