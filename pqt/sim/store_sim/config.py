"""Configuration loading: layout, rules and scenarios. No wall-clock access here."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_DUR = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def parse_duration_ms(value: str) -> int:
    """Parse the ISO-8601 duration subset used in configs (PTnHnMnS)."""
    m = _DUR.match(value)
    if not m or not any(m.groups()):
        raise ValueError(f"unsupported duration: {value!r}")
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return ((h * 60 + mi) * 60 + se) * 1000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


@dataclass(frozen=True)
class RuleSet:
    grace_ms: int
    modes: dict[str, str]
    versions: dict[str, str]
    queue_threshold: int
    queue_sustain_ms: int
    queue_hysteresis: int
    queue_clear_ms: int
    dwell_zones: frozenset[str]
    dwell_limit_ms: int
    dwell_ttl_ms: int
    abs_within_ms: int
    abs_expect: str
    foot_zones: tuple[str, ...] = ()
    foot_window_ms: int = 300_000
    foot_history_windows: int = 12
    foot_factor: float = 2.0
    foot_min_count: int = 10

    @property
    def foot_on(self) -> bool:
        return self.modes.get("R-FOOT-001", "off") != "off"


def load_rules(path: str | Path) -> RuleSet:
    doc = yaml.safe_load(Path(path).read_text())
    by_id = {r["id"]: r for r in doc["rules"]}
    q = by_id["R-QUEUE-001"]["params"]
    d = by_id["R-DWELL-001"]["params"]
    a = by_id["R-ABS-001"]["params"]
    f = by_id.get("R-FOOT-001", {}).get("params")
    foot = {}
    if f is not None:
        window_ms, history_ms = parse_duration_ms(f["window"]), parse_duration_ms(f["history"])
        if window_ms <= 0 or history_ms % window_ms != 0:
            raise ValueError("R-FOOT-001: history must be a whole number of windows")
        foot = dict(foot_zones=tuple(f["zones"]), foot_window_ms=window_ms,
                    foot_history_windows=history_ms // window_ms, foot_factor=float(f["factor"]),
                    foot_min_count=int(f["minCount"]))
    return RuleSet(
        grace_ms=parse_duration_ms(doc["grace"]),
        modes={k: v["mode"] for k, v in by_id.items()},
        versions={k: str(v["version"]) for k, v in by_id.items()},
        queue_threshold=int(q["threshold"]),
        queue_sustain_ms=parse_duration_ms(q["sustain"]),
        queue_hysteresis=int(q["hysteresis"]),
        queue_clear_ms=parse_duration_ms(q["clearSustain"]),
        dwell_zones=frozenset(d["zones"]),
        dwell_limit_ms=parse_duration_ms(d["limit"]),
        dwell_ttl_ms=parse_duration_ms(d["sessionTtl"]),
        abs_within_ms=parse_duration_ms(a["within"]),
        abs_expect=a["expect"],
        **foot,
    )


def load_layout(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


SCENARIO_KEYS = {
    None: {"name", "description", "duration", "epoch", "stores", "arrivals", "shoppers", "staffing", "faults"},
    "shoppers": {"checkoutProbability", "fittingRoomProbability", "fittingRoomMedianSeconds",
                 "fittingRoomSigma", "serviceMedianSeconds"},
    "staffing": {"initialOpen", "minOpen", "maxOpen", "openAtQueueLength", "reactionMin", "reactionMax",
                 "closeAfterIdle"},
    "faults": {"outOfOrder", "late", "duplicates", "malformed", "vision"},
}


def check_scenario(sc: dict) -> None:
    """Reject unknown keys: a typo ("reactionMax" as "reactonMax") would otherwise be ignored silently."""
    if not isinstance(sc, dict):
        raise ValueError("a scenario is a YAML mapping")
    problems = [f"unknown key {k!r}" for k in sc if k not in SCENARIO_KEYS[None]]
    for section in ("shoppers", "staffing", "faults"):
        for k in (sc.get(section) or {}):
            if k not in SCENARIO_KEYS[section]:
                problems.append(f"unknown key {section}.{k}")
    for k in ("duration", "arrivals"):
        if k not in sc:
            problems.append(f"missing {k}")
    if "stores" in sc and (not isinstance(sc["stores"], list) or not sc["stores"]
                           or not all(isinstance(x, str) for x in sc["stores"])):
        problems.append("stores: a non-empty list of store ids")
    rates = []
    for i, seg in enumerate(sc.get("arrivals") or []):
        if not isinstance(seg, dict) or set(seg) != {"from", "to", "perHour"} or float(seg["perHour"]) < 0:
            problems.append(f"arrivals[{i}] needs from, to and a perHour >= 0")
        else:
            rates.append(float(seg["perHour"]))
    if rates and max(rates) <= 0:
        problems.append("arrivals: at least one segment needs perHour > 0")
    if problems:
        raise ValueError("invalid scenario: " + "; ".join(problems))


def load_scenario(path: str | Path) -> dict:
    sc = yaml.safe_load(Path(path).read_text())
    check_scenario(sc)
    sc.setdefault("faults", {})
    return sc
