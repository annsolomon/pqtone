"""Count mode (Tier 2, V6): how close are counts derived from vision events to ground-truth counts?

Ground truth is a CSV of samples, one row per (time, zone):

    offset_s,zone,count
    0,checkout-1,0
    10,checkout-1,3

`offset_s` is seconds from the clip's first frame (manual counts every 10 s for our own footage; one
sample per second derived from MOT17 annotations on the open-dataset track). `zone` is a zone id
(metric "occupancy", from zone.entered / zone.exited) or a queue id (metric "queue", from queue.length).

The predicted count at a sample is the state after every event with time ≤ the sample time, exactly as a
downstream consumer replaying the event stream would see it. The clip start defaults to the time of the
earliest event (the emitter's first clock tick is at t = 0).

The metric the plan gates on: per clip, at least 95% of samples within ±1 and MAE ≤ 0.5.
"""
from __future__ import annotations

import csv
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def parse_time_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


@dataclass(frozen=True)
class Sample:
    offset_ms: int
    zone: str
    count: int


def read_gt(path: str | Path) -> list[Sample]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows and set(rows[0]) != {"offset_s", "zone", "count"}:
        raise ValueError(f"{path}: columns must be offset_s,zone,count")
    out = []
    for r in rows:
        c = int(r["count"])
        if c < 0:
            raise ValueError(f"{path}: negative count")
        out.append(Sample(int(round(float(r["offset_s"]) * 1000)), r["zone"], c))
    return out


def read_events(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def timelines(events: list[dict], metric: str) -> dict[str, tuple[list[int], list[int]]]:
    """zone → (times, counts after each change), from the event stream."""
    evs = sorted(events, key=lambda e: (parse_time_ms(e["time"]), int(e.get("sequence", 0) or 0)))
    out: dict[str, tuple[list[int], list[int]]] = {}
    if metric == "queue":
        for e in evs:
            if e["type"].endswith(".queue.length"):
                t, c = out.setdefault(e["data"]["queueId"], ([], []))
                t.append(parse_time_ms(e["time"]))
                c.append(int(e["data"]["length"]))
        return out
    if metric != "occupancy":
        raise ValueError("metric must be occupancy or queue")
    inside: dict[str, set[str]] = {}
    for e in evs:
        kind = e["type"].rsplit(".", 2)[-2:]
        if kind not in (["zone", "entered"], ["zone", "exited"]):
            continue
        z, trk = e["data"]["zoneId"], e["data"]["trackId"]
        s = inside.setdefault(z, set())
        if kind[1] == "entered":
            s.add(trk)
        else:
            s.discard(trk)
        t, c = out.setdefault(z, ([], []))
        t.append(parse_time_ms(e["time"]))
        c.append(len(s))
    return out


def count_at(tl: tuple[list[int], list[int]] | None, t_ms: int) -> int:
    if not tl:
        return 0
    i = bisect_right(tl[0], t_ms)
    return tl[1][i - 1] if i else 0


def score(gt: list[Sample], events: list[dict], *, metric: str = "occupancy", start_ms: int | None = None,
          min_within: float = 0.95, max_mae: float = 0.5) -> dict:
    if not events:
        raise ValueError("no events")
    start = start_ms if start_ms is not None else min(parse_time_ms(e["time"]) for e in events)
    tls = timelines(events, metric)
    per_zone: dict[str, dict] = {}
    errors_all: list[int] = []
    for zone in sorted({s.zone for s in gt}):
        errs = [count_at(tls.get(zone), start + s.offset_ms) - s.count for s in gt if s.zone == zone]
        errors_all += errs
        per_zone[zone] = _summary(errs, [s.count for s in gt if s.zone == zone], min_within, max_mae)
    overall = _summary(errors_all, [s.count for s in gt], min_within, max_mae)
    return {"metric": metric, "startMs": start, "zones": per_zone, "overall": overall,
            "gate": {"minWithin1": min_within, "maxMae": max_mae}, "pass": all(z["pass"] for z in per_zone.values())}


def _summary(errs: list[int], truth: list[int], min_within: float, max_mae: float) -> dict:
    n = len(errs)
    if n == 0:
        return {"n": 0, "pass": False}
    mae = sum(abs(e) for e in errs) / n
    within = sum(1 for e in errs if abs(e) <= 1) / n
    return {"n": n, "mae": round(mae, 4), "within1": round(within, 4), "bias": round(sum(errs) / n, 4),
            "maxAbsError": max(abs(e) for e in errs), "meanTruth": round(sum(truth) / n, 3),
            "pass": within >= min_within and mae <= max_mae}


def render(result: dict, title: str = "Count accuracy") -> str:
    lines = [f"## {title} ({result['metric']})", "",
             "| Zone | Samples | Mean truth | MAE | Within ±1 | Bias | Max error | Gate |", "|---|---|---|---|---|---|---|---|"]
    for z, s in result["zones"].items():
        if s["n"]:
            lines.append(f"| {z} | {s['n']} | {s['meanTruth']} | {s['mae']:.3f} | {s['within1']:.1%} | {s['bias']:+.3f} | "
                         f"{s['maxAbsError']} | {'ok' if s['pass'] else 'FAIL'} |")
    g = result["gate"]
    lines += ["", f"Gate: at least {g['minWithin1']:.0%} of samples within ±1 and MAE ≤ {g['maxMae']} per zone."]
    return "\n".join(lines) + "\n"
