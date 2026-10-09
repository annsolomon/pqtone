"""Threshold and regression gates."""
from __future__ import annotations

import math
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


def wilson(successes: int, n: int, z: float) -> tuple[float, float]:
    """Wilson score interval for a proportion. n == 0 says nothing: (0, 1)."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class Confidence:
    """thresholds.yaml `confidence:` (milestone Q4, ADR-027). None disables the interval gate."""
    min_n: int | None
    z: float
    min_lower_bound: float | None


def confidence_for(thresholds: dict) -> Confidence:
    c = thresholds.get("confidence") or {}
    return Confidence(int(c["minN"]) if "minN" in c else None, float(c.get("z", 1.96)),
                      float(c["minLowerBound"]) if "minLowerBound" in c else None)


def check(scenario: str, score: dict, gate: Gate, conf: Confidence) -> tuple[list[str], list[str]]:
    """(failures, warnings). The point gate always applies, unchanged. On top of it:

    * n >= minN: the Wilson lower bound must reach minLowerBound, or the row fails;
    * 0 < n < minN: if the interval's lower bound is under the point threshold, a warning says the
      sample is too small to certify the threshold. A warning never fails the build.
    """
    failures = evaluate(scenario, score, gate)
    warnings: list[str] = []
    if conf.min_n is None or "nPrecision" not in score:
        return failures, warnings
    for metric, threshold in (("precision", gate.precision), ("recall", gate.recall)):
        n = score[f"n{metric.capitalize()}"]
        lo, hi = score[f"{metric}CI"]
        if n == 0:
            continue
        if n >= conf.min_n:
            if conf.min_lower_bound is not None and lo < conf.min_lower_bound:
                failures.append(f"{metric} lower bound {lo:.3f} < {conf.min_lower_bound:.2f} (n={n})")
        elif lo < threshold:
            warnings.append(f"{metric}: n={n}, 95% interval [{lo:.3f}, {hi:.3f}]; "
                            f"too few samples to certify >= {threshold:.2f}")
    return failures, warnings
