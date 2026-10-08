"""Threshold and regression gates."""
from __future__ import annotations

import re
from dataclasses import dataclass

_DUR = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def dur_ms(value: str) -> int:
    m = _DUR.match(value)
    if not m or not any(m.groups()):
        raise ValueError(f"unsupported duration {value!r}")
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((h * 60 + mi) * 60 + s) * 1000


@dataclass
class Gate:
    precision: float
    recall: float
    latency_p95_ms: int | None


def gate_for(thresholds: dict, scenario: str, rule: str) -> Gate:
    merged: dict = dict(thresholds.get("defaults", {}).get(rule, {}))
    sc = thresholds.get("scenarios", {}).get(scenario, {})
    merged.update(sc.get("*", {}))
    merged.update(sc.get(rule, {}))
    lat = merged.get("latencyP95")
    return Gate(float(merged.get("precision", 0.0)), float(merged.get("recall", 0.0)),
                dur_ms(lat) if lat else None)


def evaluate(scenario: str, score: dict, gate: Gate) -> list[str]:
    failures = []
    if score["precision"] < gate.precision:
        failures.append(f"precision {score['precision']:.3f} < {gate.precision:.2f}")
    if score["recall"] < gate.recall:
        failures.append(f"recall {score['recall']:.3f} < {gate.recall:.2f}")
    p95 = score["latencyMs"]["p95"]
    if gate.latency_p95_ms is not None and p95 is not None and p95 > gate.latency_p95_ms:
        failures.append(f"latency p95 {p95} ms > {gate.latency_p95_ms} ms")
    return failures


def regressions(current: dict, baseline: dict, max_drop_pp: float) -> list[str]:
    out = []
    for key, rules in current.items():
        for rule, s in rules.items():
            b = baseline.get(key, {}).get(rule)
            if not b:
                continue
            for metric in ("precision", "recall"):
                drop = (b[metric] - s[metric]) * 100
                if drop > max_drop_pp:
                    out.append(f"{key}/{rule}: {metric} dropped {drop:.1f}pp vs baseline")
    return out
