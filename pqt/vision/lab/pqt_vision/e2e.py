"""V8 end to end: synthetic vision events through the gateway, event-core and the rules engine.

`post` (runs where the gateway is reachable, with the edge-demo OAuth client):
  synthetic tracks → zone engine → CloudEvents (source urn:pqt:edge:store-001:<camera>, times from now)
  → HTTP batch ingest, paced so no event is further in the future than event-core allows.
  Writes manifest.json and events.jsonl to the case directory.

`check` (runs where PostgreSQL is reachable, read-only role):
  every posted event is stored exactly once (proves event-core accepts vision events, V5), and the
  R-QUEUE-001 incidents for this run's queue match the episodes expected from the *true* queue length.

A unique queue id per run keeps repeated runs on a long-lived stack apart; the store is the one the
edge-demo client may publish for (store-001), so the rules engine processes it as live traffic.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

import yaml

from . import camera as cam
from .emitter import parse_iso, write_jsonl
from .pipeline import cloud_events
from .synthetic import DEFAULT, camera_doc, expected_queue_incidents, tracks
from .uplink import OAuthClient, Uplink

STORE = "store-001"
CAMERA = "cam-vision-e2e"
TRAILING_MS = 60_000
MAX_FUTURE_MS = 240_000  # event-core refuses events more than 5 minutes ahead; keep a minute in hand


def _rules(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    q = next(r for r in doc["rules"] if r["id"] == "R-QUEUE-001")

    def ms(iso: str) -> int:
        assert iso.startswith("PT") and iso.endswith("S"), iso
        return int(float(iso[2:-1]) * 1000)

    p = q["params"]
    return {"threshold": int(p["threshold"]), "sustain_ms": ms(p["sustain"]), "hysteresis": int(p["hysteresis"]),
            "clear_sustain_ms": ms(p["clearSustain"]), "version": q["version"], "mode": q["mode"]}


def post(out: Path, *, rules_path: Path, speed: float = 4.0) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    queue_id = f"q-vis-{int(time.time()):x}"
    camera = cam.from_doc(camera_doc(store=STORE, camera=CAMERA, queue_id=queue_id))
    trk, times = tracks(DEFAULT)
    start_ms = int(time.time()) * 1000
    events = cloud_events(camera, trk, times, start_ms=start_ms, run_key=queue_id, secret=secrets.token_bytes(32),
                          trailing_ms=TRAILING_MS)
    rules = _rules(rules_path)
    end_ms = int(DEFAULT.end_s * 1000) + TRAILING_MS
    expected = expected_queue_incidents(DEFAULT.true_lengths(), threshold=rules["threshold"],
                                        sustain_ms=rules["sustain_ms"], hysteresis=rules["hysteresis"],
                                        clear_sustain_ms=rules["clear_sustain_ms"], end_ms=end_ms)
    write_jsonl(out / "events.jsonl", events)
    uplink = Uplink(os.environ["PQT_INGEST_URL"], OAuthClient(os.environ["PQT_TOKEN_URL"], os.environ["PQT_CLIENT_ID"],
                                                              os.environ["PQT_CLIENT_SECRET"]))
    totals: dict[str, int] = {}
    wall0 = time.monotonic()
    batch: list[dict] = []
    for e in events:
        due = (parse_iso(e["time"]) - start_ms) / 1000.0 / speed
        if batch and (due > time.monotonic() - wall0 or len(batch) >= 100):
            for k, v in uplink.send(batch).items():
                totals[k] = totals.get(k, 0) + v
            batch = []
        wait = due - (time.monotonic() - wall0)
        if wait > 0:
            time.sleep(wait)
        if parse_iso(e["time"]) - int(time.time() * 1000) > MAX_FUTURE_MS:
            raise RuntimeError("pacing fell behind the future-skew limit; lower --speed")
        batch.append(e)
    if batch:
        for k, v in uplink.send(batch).items():
            totals[k] = totals.get(k, 0) + v
    manifest = {"source": events[0]["source"], "storeId": STORE, "queueId": queue_id, "startMs": start_ms,
                "endMs": start_ms + end_ms, "events": len(events), "ingest": totals,
                "expected": [{**x, "onsetMs": x["onsetMs"] + start_ms, "detectedMs": x["detectedMs"] + start_ms,
                              **({"resolvedMs": x["resolvedMs"] + start_ms} if "resolvedMs" in x else {})}
                             for x in expected],
                "rule": {"id": "R-QUEUE-001", **{k: v for k, v in rules.items() if k in ("version", "mode", "threshold")}}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def check(case: Path, *, timeout_s: int = 240, tolerance_before_ms: int = 30_000, max_latency_ms: int = 120_000) -> dict:
    import psycopg

    m = json.loads((case / "manifest.json").read_text(encoding="utf-8"))
    ids = [json.loads(line)["id"] for line in (case / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]
    deadline = time.monotonic() + timeout_s
    problems: list[str] = []
    with psycopg.connect(os.environ["PQT_SCORER_DSN"]) as conn:
        while True:
            stored, distinct = conn.execute(
                "SELECT count(*), count(DISTINCT id) FROM pqt.event WHERE source = %s AND id = ANY(%s)",
                (m["source"], ids)).fetchone()
            rows = conn.execute(
                "SELECT (extract(epoch FROM onset_at) * 1000)::bigint, (extract(epoch FROM detected_at) * 1000)::bigint, "
                "mode, rule_version FROM pqt.incident WHERE rule_id = 'R-QUEUE-001' AND store_id = %s "
                "AND incident_key = %s AND sim_run_id IS NULL ORDER BY detected_at", (m["storeId"], m["queueId"])).fetchall()
            if stored >= len(ids) and len(rows) >= len(m["expected"]):
                time.sleep(10)  # let a late duplicate incident show up before judging
                rows = conn.execute(
                    "SELECT (extract(epoch FROM onset_at) * 1000)::bigint, (extract(epoch FROM detected_at) * 1000)::bigint, "
                    "mode, rule_version FROM pqt.incident WHERE rule_id = 'R-QUEUE-001' AND store_id = %s "
                    "AND incident_key = %s AND sim_run_id IS NULL ORDER BY detected_at", (m["storeId"], m["queueId"])).fetchall()
                break
            if time.monotonic() > deadline:
                break
            time.sleep(3)
    if stored != len(ids) or distinct != len(ids):
        problems.append(f"stored {stored} rows ({distinct} distinct ids) for {len(ids)} posted events")
    if len(rows) != len(m["expected"]):
        problems.append(f"{len(rows)} R-QUEUE-001 incidents for {m['queueId']}, expected {len(m['expected'])}")
    matches = []
    for exp, row in zip(m["expected"], rows):
        onset, detected, mode, version = row
        lag = detected - exp["detectedMs"]
        ok = -tolerance_before_ms <= lag <= max_latency_ms
        matches.append({"expectedDetectedMs": exp["detectedMs"], "detectedMs": detected, "lagMs": lag,
                        "onsetLagMs": onset - exp["onsetMs"], "mode": mode, "ruleVersion": version, "ok": ok})
        if not ok:
            problems.append(f"incident detected {lag / 1000:.1f} s from the expected time (allowed "
                            f"-{tolerance_before_ms / 1000:.0f} s .. +{max_latency_ms / 1000:.0f} s)")
    result = {"queueId": m["queueId"], "posted": len(ids), "ingest": m["ingest"], "stored": stored,
              "distinct": distinct, "incidents": matches, "problems": problems}
    print(json.dumps(result, indent=2))
    return result


def main_post(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="pqt-vision e2e-post")
    p.add_argument("--out", required=True)
    p.add_argument("--rules", default="config/rules.yaml")
    p.add_argument("--speed", type=float, default=4.0)
    a = p.parse_args(argv)
    m = post(Path(a.out), rules_path=Path(a.rules), speed=a.speed)
    print(json.dumps({k: m[k] for k in ("source", "queueId", "events", "ingest", "expected")}, indent=2))
    bad = {k: v for k, v in m["ingest"].items() if k not in ("accepted", "duplicate")}
    return 1 if bad else 0


def main_check(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="pqt-vision e2e-check")
    p.add_argument("--case-dir", required=True)
    p.add_argument("--timeout", type=int, default=240)
    a = p.parse_args(argv)
    return 1 if check(Path(a.case_dir), timeout_s=a.timeout)["problems"] else 0


if __name__ == "__main__":
    sys.exit(main_post(sys.argv[1:]))
