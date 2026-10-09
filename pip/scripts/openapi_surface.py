#!/usr/bin/env python3
"""Milestone E5: the API contract surface of an OpenAPI document, and the drift between two.

The committed spec (services/event-core/openapi.yaml) is written by hand and carries prose,
schemas and response codes that code annotations do not. What must never drift is the surface
a client depends on, which springdoc can derive from the controllers:

  operation (METHOD path) -> path parameters, query parameters (name, required),
                             request body media types (and whether a body is required)

Left out on purpose, with the reason:
  - headers: CSRF (X-XSRF-TOKEN) and If-Match are enforced by Spring Security and the review
    flow, not all declared on controller methods;
  - response codes and schemas: springdoc cannot infer 202/409/412 without annotations;
  - SECURITY_FILTER_PATHS: endpoints served by Spring Security filters, not controllers.

Usage: openapi_surface.py COMMITTED.yaml GENERATED.json   (exit 1 and a diff on drift)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
SECURITY_FILTER_PATHS = {("post", "/logout")}

Surface = dict[str, dict[str, Any]]


def surface(doc: dict) -> Surface:
    out: Surface = {}
    for path, item in (doc.get("paths") or {}).items():
        shared = item.get("parameters", [])
        for method in METHODS:
            op = item.get(method)
            if op is None or (method, path) in SECURITY_FILTER_PATHS:
                continue
            params = {"path": set(), "query": set()}
            for p in [*shared, *op.get("parameters", [])]:
                p = _resolve(doc, p)
                where = p.get("in")
                if where in params:
                    params[where].add((p["name"], bool(p.get("required", where == "path"))))
            body = _resolve(doc, op.get("requestBody")) if op.get("requestBody") else None
            out[f"{method.upper()} {path}"] = {
                "path": sorted(params["path"]),
                "query": sorted(params["query"]),
                "body": None if body is None else {
                    "required": bool(body.get("required", False)),
                    "mediaTypes": sorted((body.get("content") or {}).keys()),
                },
            }
    return out


def _resolve(doc: dict, node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node and str(node["$ref"]).startswith("#/"):
        cur: Any = doc
        for part in node["$ref"][2:].split("/"):
            cur = cur[part]
        node = cur
    return node


def drift(committed: Surface, generated: Surface) -> list[str]:
    out = []
    for op in sorted(set(committed) | set(generated)):
        if op not in generated:
            out.append(f"{op}: in openapi.yaml but not served by the code")
        elif op not in committed:
            out.append(f"{op}: served by the code but missing from openapi.yaml")
        else:
            for part in ("path", "query", "body"):
                if committed[op][part] != generated[op][part]:
                    out.append(f"{op}: {part} differs: openapi.yaml {committed[op][part]} vs code {generated[op][part]}")
    return out


def load(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text) if path.endswith(".json") else yaml.safe_load(text)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    problems = drift(surface(load(argv[1])), surface(load(argv[2])))
    for p in problems:
        print(p)
    if not problems:
        print("openapi: no drift between openapi.yaml and the code")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
