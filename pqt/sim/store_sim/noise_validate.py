"""Tier 2 N5: does the simulator, with a measured noise profile, reproduce the count errors seen on real clips?

A noise profile measured by N1 carries `queueCountError`: predicted minus true zone occupancy, one sample
per second, from the real camera pipeline on real clips. N2 does not use that distribution; it applies the
profile's *mechanisms* (misses, phantoms, fragments, ID switches, jitter) to clean visits. If the
mechanisms are right, the occupancy errors they cause in the simulator should have the same distribution.

Procedure (fixed before the first run):

1. For each scenario and seed, simulate the clean events, then apply the profile exactly as `generate`
   does (the run's own vision RNG stream).
2. Every 1 s, for every zone of every store, compare the occupancy (tracks entered and not yet exited)
   of the noisy events with that of the clean events. Keep the samples whose true occupancy is 2-8, the
   range the open-dataset zones were chosen for (`pqt_vision.mot_zones`), so crowding is comparable.
3. Two-sample Kolmogorov-Smirnov test of the simulated errors against the measured ones, at α = 0.05.
   The errors are integers, so the test is conservative (ties make the true p-value smaller, not larger:
   a rejection is reliable; a non-rejection with a small measured sample is weak evidence).

The verdict is a report, not a gate: a rejection says which way the simulator is wrong and goes back to N2.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from .events import parse_iso_ms
from .generate import simulate
from .vision_noise import NoiseProfile, apply, validate

DEFAULT_SCENARIOS = ("baseline", "rush_hour", "register_delay", "staff_shortage", "flash_sale", "two_queues")
DEFAULT_SEEDS = (11, 42, 1337)
TRUE_RANGE = (2, 8)
ALPHA = 0.05
SAMPLE_MS = 1000


# -- the statistics ------------------------------------------------------------------------------

def ecdf(sample: list[int], x: float) -> float:
    s = sorted(sample)
    return bisect_right(s, x) / len(s)


def kolmogorov_q(lam: float) -> float:
    """Q_KS(λ) = 2 Σ (-1)^(k-1) exp(-2 k² λ²), the Kolmogorov distribution's survival function."""
    if lam < 0.2:
        return 1.0
    total, sign = 0.0, 1.0
    for k in range(1, 101):
        term = sign * math.exp(-2.0 * k * k * lam * lam)
        total += term
        if abs(term) < 1e-12:
            break
        sign = -sign
    return min(1.0, max(0.0, 2.0 * total))


def ks_2samp(a: list[int], b: list[int]) -> tuple[float, float]:
    """Two-sided two-sample KS statistic D and its asymptotic p-value (Stephens' small-sample correction,
    as in Numerical Recipes): λ = (√nₑ + 0.12 + 0.11/√nₑ)·D with nₑ = n₁n₂/(n₁+n₂)."""
    if not a or not b:
        raise ValueError("both samples need at least one value")
    sa, sb = sorted(a), sorted(b)
    d = 0.0
    for x in sorted(set(sa) | set(sb)):
        d = max(d, abs(bisect_right(sa, x) / len(sa) - bisect_right(sb, x) / len(sb)))
    ne = len(sa) * len(sb) / (len(sa) + len(sb))
    root = math.sqrt(ne)
    return d, kolmogorov_q((root + 0.12 + 0.11 / root) * d)


def describe(sample: list[int]) -> dict:
    n = len(sample)
    arr = np.array(sample, dtype=float)
    c = Counter(sample)
    return {"n": n, "mean": round(float(arr.mean()), 3), "sd": round(float(arr.std()), 3),
            "within1": round(sum(v for k, v in c.items() if abs(k) <= 1) / n, 4),
            "exact": round(c.get(0, 0) / n, 4), "min": int(arr.min()), "max": int(arr.max()),
            "shares": {str(k): round(v / n, 4) for k, v in sorted(c.items())}}


def measured_sample(profile: dict) -> list[int]:
    """The measured errors back from the profile's empirical distribution (values × round(weight × n))."""
    q = profile.get("queueCountError")
    if not q or q.get("kind") != "empirical":
        raise ValueError("the profile has no empirical queueCountError to validate against")
    out: list[int] = []
    for v, w in zip(q["values"], q["weights"]):
        out += [int(v)] * int(round(w * q["n"]))
    if abs(len(out) - q["n"]) > len(q["values"]):  # rounding of the weights, at most one per value
        raise ValueError(f"queueCountError weights do not reproduce n={q['n']} (got {len(out)})")
    return out


# -- the simulation side -------------------------------------------------------------------------

def _timelines(events: list[dict]) -> dict[tuple[str, str], tuple[list[int], list[int]]]:
    """(store, zone) → (times, occupancy after each change)."""
    inside: dict[tuple[str, str], set] = {}
    out: dict[tuple[str, str], tuple[list[int], list[int]]] = {}
    for e in sorted(events, key=lambda e: parse_iso_ms(e["time"])):  # stable: same-time events keep stream order
        kind = e["type"].rsplit(".", 2)[-2:]
        if kind not in (["zone", "entered"], ["zone", "exited"]):
            continue
        key = (e.get("storeid", ""), e["data"]["zoneId"])
        s = inside.setdefault(key, set())
        (s.add if kind[1] == "entered" else s.discard)(e["data"]["trackId"])
        t, c = out.setdefault(key, ([], []))
        t.append(parse_iso_ms(e["time"]))
        c.append(len(s))
    return out


def _at(tl: tuple[list[int], list[int]] | None, t: int) -> int:
    if not tl:
        return 0
    i = bisect_right(tl[0], t)
    return tl[1][i - 1] if i else 0


def occupancy_errors(clean: list[dict], noisy: list[dict], *, start_ms: int, end_ms: int,
                     true_range: tuple[int, int] = TRUE_RANGE) -> list[int]:
    truth, pred = _timelines(clean), _timelines(noisy)
    errors = []
    for key in sorted(truth):
        for t in range(start_ms, end_ms + 1, SAMPLE_MS):
            n = _at(truth[key], t)
            if true_range[0] <= n <= true_range[1]:
                errors.append(_at(pred.get(key), t) - n)
    return errors


def simulated_errors(profile: dict, *, scenarios_dir: str | Path, scenarios: tuple[str, ...], seeds: tuple[int, ...],
                     layout: str, rules: str) -> tuple[list[int], list[dict]]:
    prof = NoiseProfile(profile["name"], Path("<in memory>"), "", profile)
    errors: list[int] = []
    cases = []
    for sc in scenarios:
        for seed in seeds:
            out = simulate(seed=seed, layout_path=layout, scenario_path=str(Path(scenarios_dir) / f"{sc}.yaml"),
                           rules_path=rules)
            rng = np.random.default_rng(np.random.SeedSequence([seed, 0x51D0]))  # generate's vision stream
            noisy, counts = apply(out.clean, prof, rng)
            start = parse_iso_ms(out.manifest["epoch"])
            errs = occupancy_errors(out.clean, noisy, start_ms=start, end_ms=out.manifest["endMs"])
            errors += errs
            cases.append({"scenario": sc, "seed": seed, "samples": len(errs), **{k: v for k, v in counts.items()}})
    return errors, cases


def clip_errors(clean: list[dict], truth: list[tuple[int, str, int]], profile: dict, *,
                seeds: tuple[int, ...] = tuple(range(30))) -> list[int]:
    """The same-clip test (N5, decisive): `clean` are the zone events a perfect tracker gives on a real clip
    (one store), `truth` its (time ms, zone, true count) samples. Apply the profile once per seed and return
    every (noisy count - true count): what the simulator says the camera pipeline's errors on *this* clip
    should look like. Compared with the pipeline's real errors on the same clip, scene differences (how
    often people come and go, how crowded it is) cancel out and only the noise mechanisms are tested."""
    if not clean:
        return []
    prof = NoiseProfile(profile["name"], Path("<in memory>"), "", profile)
    stores = {e.get("storeid", "") for e in clean}
    if len(stores) != 1:
        raise ValueError("clip_errors takes the events of one camera")
    store = stores.pop()
    out: list[int] = []
    for seed in seeds:
        noisy, _ = apply(clean, prof, np.random.default_rng(np.random.SeedSequence([seed, 0x51D0])))
        tl = _timelines(noisy)
        out += [_at(tl.get((store, zone)), t) - n for t, zone, n in truth]
    return out


def compare(simulated: list[int], measured: list[int]) -> dict:
    d, p = ks_2samp(simulated, measured)
    return {"alpha": ALPHA, "ksD": round(d, 4), "pValue": round(p, 4), "rejected": p < ALPHA,
            "measured": describe(measured), "simulated": describe(simulated)}


@dataclass
class Validation:
    profile: str
    measured: dict
    simulated: dict
    d: float
    p: float
    cases: list[dict]

    @property
    def rejected(self) -> bool:
        return self.p < ALPHA

    def as_dict(self) -> dict:
        return {"profile": self.profile, "alpha": ALPHA, "trueRange": list(TRUE_RANGE), "ksD": round(self.d, 4),
                "pValue": round(self.p, 4), "rejected": self.rejected, "measured": self.measured,
                "simulated": self.simulated, "cases": self.cases}


def validate_profile(profile_path: str | Path, *, scenarios_dir: str | Path = "sim/scenarios",
                     scenarios: tuple[str, ...] = DEFAULT_SCENARIOS, seeds: tuple[int, ...] = DEFAULT_SEEDS,
                     layout: str = "config/layouts/store-001.json", rules: str = "config/rules.yaml") -> Validation:
    profile = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8"))
    errs = validate(profile)
    if errs:
        raise ValueError(f"{profile_path}: {errs}")
    meas = measured_sample(profile)
    sim, cases = simulated_errors(profile, scenarios_dir=scenarios_dir, scenarios=scenarios, seeds=seeds,
                                  layout=layout, rules=rules)
    if not sim:
        raise ValueError("no simulated sample had a true occupancy in range")
    d, p = ks_2samp(sim, meas)
    return Validation(profile["name"], describe(meas), describe(sim), d, p, cases)


def render_md(v: Validation, *, profile_path: str) -> str:
    m, s = v.measured, v.simulated
    keys = sorted({int(k) for k in m["shares"]} | {int(k) for k in s["shares"]})
    lines = [
        "# N5: noise-model validation", "",
        f"Profile `{v.profile}` (`{profile_path}`). Question: does store-sim, applying this profile's mechanisms "
        "(misses, phantoms, fragments, ID switches, jitter), reproduce the zone-count errors measured on real "
        "clips? Method: `sim/store_sim/noise_validate.py` (fixed before the first run). Regenerate with "
        "`make noise-validate`.", "",
        f"Samples: zone occupancy once per second where the true occupancy is {TRUE_RANGE[0]}-{TRUE_RANGE[1]}; "
        f"simulation over {', '.join(sorted({c['scenario'] for c in v.cases}))} × seeds "
        f"{', '.join(str(x) for x in sorted({c['seed'] for c in v.cases}))}.", "",
        "| Sample | n | Mean | SD | Exact | Within ±1 | Min | Max |", "|---|---|---|---|---|---|---|---|",
        f"| Measured (real clips) | {m['n']} | {m['mean']:+.3f} | {m['sd']:.3f} | {m['exact']:.1%} | {m['within1']:.1%} | {m['min']} | {m['max']} |",
        f"| Simulated (store-sim + profile) | {s['n']} | {s['mean']:+.3f} | {s['sd']:.3f} | {s['exact']:.1%} | {s['within1']:.1%} | {s['min']} | {s['max']} |",
        "", "| Error | " + " | ".join(f"{k:+d}" for k in keys) + " |", "|---|" + "---|" * len(keys),
        "| Measured | " + " | ".join(f"{m['shares'].get(str(k), 0):.1%}" for k in keys) + " |",
        "| Simulated | " + " | ".join(f"{s['shares'].get(str(k), 0):.1%}" for k in keys) + " |", "",
        f"**Two-sample KS: D = {v.d:.3f}, p = {v.p:.3f} (α = {ALPHA}).** "
        + ("The simulated error distribution **differs** from the measured one: " + _direction(m, s)
           + " This goes back to N2 (the injection mechanisms), not to the rules."
           if v.rejected else
           "The test does not reject that the simulated and measured errors come from the same distribution. "
           f"With {m['n']} measured samples this is weak evidence of agreement, not proof; the measured "
           "sample grows with our own footage (P5)."), "",
        "Caveats: the measured errors come from three street scenes, not checkouts; the errors are integers, "
        "so the asymptotic KS p-value is conservative; simulated samples within one run are correlated in "
        "time, which does not change D but makes the effective simulated sample smaller than its n.", "",
    ]
    return "\n".join(lines)


def _direction(m: dict, s: dict) -> str:
    parts = []
    if s["sd"] > m["sd"] * 1.2:
        parts.append(f"the simulator's errors are more spread out (SD {s['sd']:.2f} vs {m['sd']:.2f})")
    elif s["sd"] < m["sd"] / 1.2:
        parts.append(f"the simulator's errors are too narrow (SD {s['sd']:.2f} vs {m['sd']:.2f})")
    if abs(s["mean"] - m["mean"]) > 0.2:
        parts.append(f"its bias is {s['mean']:+.2f} against {m['mean']:+.2f} measured")
    return ("; ".join(parts) or "the shapes differ at similar mean and spread") + "."
