"""Milestone Q2: wall-clock processing latency of the live pipeline.

For each incident of a run: incident row created_at minus the received_at of its *decision event*,
the first stored event of the same store and run whose event time is at or after
detected_at + grace. That is the event that moved the rules-engine watermark past the detection
point, so the measure covers event-core -> outbox -> Kafka -> rules-engine -> incidents topic ->
incident row, and excludes the grace wait, which is by design (docs/learn/watermarks.md).
"""
from __future__ import annotations

SQL = """
SELECT (extract(epoch FROM (i.created_at - d.received_at)) * 1000)::bigint
  FROM pip.incident i
  LEFT JOIN LATERAL (
        SELECT min(e.received_at) AS received_at
          FROM pip.event e
         WHERE e.store_id = i.store_id
           AND e.sim_run_id IS NOT DISTINCT FROM i.sim_run_id
           AND e.event_time >= i.detected_at + make_interval(secs => %s)
       ) d ON true
 WHERE i.sim_run_id = %s
"""


def _pct(xs: list[int], pct: float) -> int:
    idx = min(len(xs) - 1, max(0, int(round(pct / 100.0 * (len(xs) - 1)))))
    return xs[idx]


def summarise(latencies_ms: list[int], unmeasured: int) -> dict:
    xs = sorted(max(0, int(x)) for x in latencies_ms)
    if not xs:
        return {"n": 0, "unmeasured": unmeasured, "p50": None, "p95": None, "max": None}
    return {"n": len(xs), "unmeasured": unmeasured, "p50": _pct(xs, 50), "p95": _pct(xs, 95), "max": xs[-1]}


def processing_gate(metrics: dict, p95_limit_ms: int | None) -> list[str]:
    out = []
    if metrics["unmeasured"]:
        out.append(f"processing latency: {metrics['unmeasured']} incident(s) had no decision event to measure from")
    if p95_limit_ms is not None and metrics["p95"] is not None and metrics["p95"] > p95_limit_ms:
        out.append(f"processing latency p95 {metrics['p95']} ms > {p95_limit_ms} ms (ingest -> incident row)")
    return out


def measure(conn, run_id: str, grace_ms: int) -> dict:
    rows = conn.execute(SQL, (grace_ms / 1000.0, run_id)).fetchall()
    lat = [r[0] for r in rows if r[0] is not None]
    return summarise(lat, unmeasured=sum(1 for r in rows if r[0] is None))
