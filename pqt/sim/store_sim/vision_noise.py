"""faults.vision: perception noise from a measured profile (milestone S5 hook, filled in Tier 2 N2).

A scenario opts in with

    faults:
      vision: {profile: ../../config/noise/none.yaml}   # relative to the scenario file

The profile is validated against noise_profile.schema.json and its hash is recorded in the run
manifest and folded into the run id, so two runs with different profiles never share an id.
Tier 1 applies nothing: `apply` returns the events unchanged. Tier 2 (N2) replaces its body with
drops, phantom tracks, split tracks and timing jitter, deterministic from the fault RNG.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import jsonschema
import numpy as np
import yaml

from .config import sha256_bytes


class NoiseProfileError(ValueError):
    """The profile is missing or does not match the noise-profile schema."""


@dataclass(frozen=True)
class NoiseProfile:
    name: str
    path: Path
    sha256: str
    doc: dict


def _schema() -> dict:
    return json.loads(resources.files("store_sim").joinpath("noise_profile.schema.json").read_text(encoding="utf-8"))


def validate(doc: object) -> list[str]:
    """Schema errors for a parsed profile, as readable strings. Empty means valid."""
    v = jsonschema.Draft202012Validator(_schema())
    errors = sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors]


def load(scenario_path: str | Path, vision: dict | None) -> NoiseProfile | None:
    """The profile named by a scenario's faults.vision section, or None when the section is absent."""
    if not vision:
        return None
    if set(vision) != {"profile"}:
        raise NoiseProfileError(f"faults.vision takes exactly one key, 'profile'; got {sorted(vision)}")
    rel = Path(str(vision["profile"]))
    path = rel if rel.is_absolute() else (Path(scenario_path).parent / rel)
    if not path.is_file():
        raise NoiseProfileError(f"noise profile not found: {path}")
    raw = path.read_bytes()
    doc = yaml.safe_load(raw)
    errors = validate(doc)
    if errors:
        raise NoiseProfileError(f"{path} is not a valid noise profile:\n  " + "\n  ".join(errors))
    return NoiseProfile(name=doc["name"], path=path, sha256=sha256_bytes(raw), doc=doc)


def apply(events: list[dict], profile: NoiseProfile | None,
          rng: np.random.Generator) -> tuple[list[dict], dict]:
    """Apply perception noise to clean events. Tier 1: a validated no-op that draws nothing from rng."""
    if profile is None:
        return events, {}
    return events, {"visionNoiseApplied": 0}
