"""Fault injection on the emission sequence. Event times are never altered."""
from __future__ import annotations

import copy

import numpy as np


def inject(events: list[dict], faults: dict, rng: np.random.Generator) -> tuple[list[dict], dict]:
    """Return (emission-ordered events, counts).

    * outOfOrder: delay emission by up to maxDelay (kept below rules grace).
    * late:       delay emission beyond grace, so rules-engine must drop them.
    * duplicates: emit an exact copy again a little later (event-core dedups).
    * malformed:  add invalid events that event-core must reject to the DLQ.
    Heartbeat ticks are infrastructure and never faulted.
    """
    from .config import parse_duration_ms

    ooo = faults.get("outOfOrder", {})
    late = faults.get("late", {})
    dup = faults.get("duplicates", {})
    bad = faults.get("malformed", {})
    ooo_rate, ooo_max = float(ooo.get("rate", 0)), parse_duration_ms(ooo.get("maxDelay", "PT1S"))
    late_rate = float(late.get("rate", 0))
    late_min, late_max = parse_duration_ms(late.get("minDelay", "PT60S")), parse_duration_ms(late.get("maxDelay", "PT120S"))
    dup_rate = float(dup.get("rate", 0))
    bad_rate = float(bad.get("rate", 0))

    counts = {"outOfOrder": 0, "late": 0, "duplicates": 0, "malformed": 0}
    staged: list[tuple[int, int, dict]] = []
    from .events import parse_iso_ms

    for i, ev in enumerate(events):
        t = parse_iso_ms(ev["time"])
        delay = 0
        if not ev["type"].endswith("clock.tick"):
            u = float(rng.random())
            if u < late_rate:
                delay = int(float(rng.uniform(late_min, late_max)))
                counts["late"] += 1
            elif u < late_rate + ooo_rate:
                delay = int(float(rng.uniform(1, ooo_max)))
                counts["outOfOrder"] += 1
            if float(rng.random()) < dup_rate:
                staged.append((t + delay + int(float(rng.uniform(0, 30_000))), i, ev))
                counts["duplicates"] += 1
            if float(rng.random()) < bad_rate:
                staged.append((t + delay, i, _malformed(ev, int(rng.integers(0, 3)), counts["malformed"])))
                counts["malformed"] += 1
        staged.append((t + delay, i, ev))
    staged.sort(key=lambda x: (x[0], x[1]))
    return [e for _, _, e in staged], counts


def _malformed(ev: dict, variant: int, n: int) -> dict:
    bad = copy.deepcopy(ev)
    bad["id"] = f"malformed-{n:06d}"
    if variant == 0:
        del bad["time"]
    elif variant == 1:
        bad["type"] = "com.pqt.store.teleport"
    else:
        bad["data"] = {"unexpected": "field"}
    return bad
