#!/usr/bin/env python3
"""Milestone R5: scale test of the live pipeline.

Simulates N copies of store-001 (store ids store-s001 ... store-sNNN) in ONE run with the S2 `stores:`
feature, streams the interleaved events to Kafka at SPEED x real time, and measures while it runs:

  * throughput: events/s produced, stored by event-core (Postgres), received by rules-engine (metrics);
  * processing lag: rules-engine consumer records-lag-max over time, and the wall-clock latency from
    the decision event's arrival to the incident row (same measure as the Q2 gate), p50/p95/max;
  * completeness: every clean event stored once; incidents vs ground truth (pip-scorer matching).

Rules-engine state size is measured by `make scale` from the outside (du in the container) and merged
into the result. Runs inside the `tools` container (Kafka, Postgres and rules-engine on its network).

  python scripts/scale.py --stores 50 --speed 10 --duration PT30M --seed 7 --label s50-x10 --out /work/reports/scale
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

import psycopg
import yaml

from pip_scorer.cli import RULES
from pip_scorer.latency import measure as measure_latency
from pip_scorer.match import score
from store_sim.config import load_rules, parse_duration_ms
from store_sim.generate import simulate, write_files
from store_sim.sinks import kafka_sink

METRICS_URL = os.environ.get("PIP_RULES_METRICS_URL", "http://rules-engine:8082/metrics")
RECEIVED = re.compile(r"^pip_rules_events_received_total(?:\{[^}]*\})?\s+([0-9.eE+-]+)$")
LAG = re.compile(r"^kafka_consumer_fetch_manager_records_lag_max(?:\{[^}]*\})?\s+([0-9.eE+-]+|NaN)$")


def scrape() -> dict:
    """rules-engine counters: events received (sum over series) and the worst consumer lag."""
    text = urllib.request.urlopen(METRICS_URL, timeout=5).read().decode()
    received, lag = 0.0, 0.0
    for line in text.splitlines():
        if m := RECEIVED.match(line):
            received += float(m.group(1))
        elif (m := LAG.match(line)) and m.group(1) != "NaN":
            lag = max(lag, float(m.group(1)))
    return {"received": received, "lagMax": lag}


def stored(conn, run_id: str) -> int:
    return conn.execute("SELECT count(*) FROM pip.event WHERE sim_run_id = %s", (run_id,)).fetchone()[0]


def make_inputs(work: Path, stores: int, duration: str) -> tuple[str, str]:
    """N layouts cloned from store-001, and a scenario that runs all of them (rush-hour traffic)."""
    lay_dir = work / "layouts"
    lay_dir.mkdir(parents=True, exist_ok=True)
    base = json.loads(Path("config/layouts/store-001.json").read_text())
    ids = [f"store-s{i:03d}" for i in range(1, stores + 1)]
    for sid in ids:
        lay = dict(base, storeId=sid, name=f"Scale store {sid[-3:]}")
        (lay_dir / f"{sid}.json").write_text(json.dumps(lay, indent=1) + "\n")
    sc = {"name": f"scale_{stores}", "description": "R5 scale test: rush-hour traffic in every store",
          "stores": ids, "duration": duration,
          "arrivals": [{"from": "PT0S", "to": duration, "perHour": 180}],
          "staffing": {"initialOpen": 1, "reactionMin": "PT120S", "reactionMax": "PT300S"}}
    sc_path = work / "scenario.yaml"
    sc_path.write_text(yaml.safe_dump(sc, sort_keys=False))
    return str(lay_dir / f"{ids[0]}.json"), str(sc_path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", type=int, default=50)
    ap.add_argument("--speed", type=float, default=10.0)
    ap.add_argument("--duration", default="PT30M")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", default="/work/reports/scale")
    ap.add_argument("--drain-timeout", type=int, default=600)
    a = ap.parse_args()
    if not 1 <= a.stores <= 500 or a.speed <= 0:
        raise SystemExit("--stores must be 1..500 and --speed > 0")
    if not re.fullmatch(r"[a-z0-9-]{1,40}", a.label):
        raise SystemExit("--label: lowercase letters, digits and dashes")
    out = Path(a.out) / a.label
    out.mkdir(parents=True, exist_ok=True)
    work = Path("/work/scale") / a.label

    layout, scenario = make_inputs(work, a.stores, a.duration)
    t0 = time.monotonic()
    run = simulate(seed=a.seed, layout_path=layout, scenario_path=scenario, rules_path="config/rules.yaml")
    write_files(run, work / "case")
    sim_s = time.monotonic() - t0
    n = len(run.emitted)
    print(json.dumps({"label": a.label, "runId": run.run_id, "events": n, "stores": a.stores,
                      "groundTruth": len(run.ground_truth), "simulateSeconds": round(sim_s, 1)}), flush=True)

    conn = psycopg.connect(os.environ["PIP_SCORER_DSN"], autocommit=True)
    samples: list[dict] = []
    stop = threading.Event()
    base = scrape()
    sent = [0]

    def counting(events):
        """Counts events handed to the producer, so backlog = produced - processed can be sampled."""
        for ev in events:
            sent[0] += 1
            yield ev

    def sampler():
        start = time.monotonic()
        while not stop.is_set():
            try:
                m = scrape()
                st = stored(conn, run.run_id)
                rec = m["received"] - base["received"]
                samples.append({"t": round(time.monotonic() - start, 1), "produced": sent[0], "stored": st,
                                "received": rec, "backlog": max(0, sent[0] - rec), "lagMax": m["lagMax"]})
            except Exception as e:  # keep sampling through a transient scrape error, but record it
                samples.append({"t": round(time.monotonic() - start, 1), "error": str(e)[:200]})
            stop.wait(5)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    send_start = time.monotonic()
    kafka_sink(counting(run.emitted), a.speed)
    send_s = time.monotonic() - send_start

    # Drain: every event stored by event-core and seen by rules-engine, then every store's watermark at
    # its horizon (the rules engine has decided everything it will decide), then incidents stop changing.
    horizons = {f"{m['storeId']}|{run.run_id}": m["horizonMs"] for m in run.manifest.get("stores", [])} or \
        {f"{run.manifest['storeId']}|{run.run_id}": run.manifest["horizonMs"]}
    deadline = time.monotonic() + a.drain_timeout
    timed_out = True
    while time.monotonic() < deadline:
        if stored(conn, run.run_id) >= len(run.clean) and scrape()["received"] - base["received"] >= len(run.clean):
            wms = {}
            for (payload,) in conn.execute("SELECT payload FROM pip.pipeline_heartbeat").fetchall():
                for k in payload.get("keys", []):
                    if k.get("key") in horizons:
                        wms[k["key"]] = max(wms.get(k["key"], 0), k.get("watermarkMs", 0))
            if all(wms.get(key, -1) >= h for key, h in horizons.items()):
                timed_out = False
                break
        time.sleep(2)
    drained_s = time.monotonic() - send_start
    last = -1
    while True:
        n_inc = conn.execute("SELECT count(*) FROM pip.incident WHERE sim_run_id = %s", (run.run_id,)).fetchone()[0]
        if n_inc == last:
            break
        last = n_inc
        time.sleep(4)
    stop.set()
    th.join(10)

    # Incidents vs ground truth, and the Q2 wall-clock processing latency.
    rules = load_rules("config/rules.yaml")
    incidents = [{"ruleId": r, "mode": mo, "storeId": s, "key": k, "detectedMs": int(d), "kind": "OPENED"}
                 for r, mo, s, k, d in conn.execute(
                     "SELECT rule_id, mode, store_id, incident_key, extract(epoch FROM detected_at) * 1000 "
                     "FROM pip.incident WHERE sim_run_id = %s", (run.run_id,)).fetchall()]
    m = yaml.safe_load(Path("scorer/thresholds.yaml").read_text())["matching"]
    scores = score(run.ground_truth, incidents, tolerance_before_ms=parse_duration_ms(m["toleranceBefore"]),
                   max_latency_ms=parse_duration_ms(m["maxLatency"]), rule_ids=RULES)
    latency = measure_latency(conn, run.run_id, rules.grace_ms)
    total_stored = stored(conn, run.run_id)

    ok = [s for s in samples if "error" not in s]
    peak = lambda key: max((b[key] - a_[key]) / (b["t"] - a_["t"]) for a_, b in zip(ok, ok[1:]) if b["t"] > a_["t"]) if len(ok) > 1 else None
    result = {
        "label": a.label, "runId": run.run_id, "stores": a.stores, "speed": a.speed, "duration": a.duration,
        "host": {"cpus": os.cpu_count(), "memGb": round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)},
        "simulatedHours": parse_duration_ms(a.duration) / 3_600_000, "streamThreads": os.environ.get("PIP_STREAM_THREADS_REPORTED"),
        "events": {"clean": len(run.clean), "emitted": n, "stored": total_stored, "complete": total_stored == len(run.clean)},
        "seconds": {"send": round(send_s, 1), "drained": round(drained_s, 1)}, "drainTimedOut": timed_out,
        "throughput": {"producedAvg": round(n / send_s, 1) if send_s else None,
                       "storedAvg": round(total_stored / drained_s, 1) if drained_s else None,
                       "storedPeak": round(peak("stored"), 1) if peak("stored") is not None else None,
                       "rulesPeak": round(peak("received"), 1) if peak("received") is not None else None},
        # backlog = events handed to the producer but not yet processed by rules-engine (the end-to-end queue).
        # recordsLagMax is Kafka's client gauge: a windowed maximum over every consumer of the app (restore and
        # global-store consumers included), so it can stay high while nothing is queued. Kept for reference.
        "lag": {"backlogMax": max((s.get("backlog", 0) for s in ok), default=None),
                "recordsLagMax": max((s["lagMax"] for s in ok), default=None)},
        "processingLatencyMs": latency,
        "quality": {rule: {"precision": round(s.precision, 4), "recall": round(s.recall, 4), "gt": s.tp + s.fn}
                    for rule, s in sorted(scores.items())},
        "samples": samples,
    }
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}), flush=True)
    return 0 if result["events"]["complete"] and not timed_out else 1


if __name__ == "__main__":
    raise SystemExit(main())
