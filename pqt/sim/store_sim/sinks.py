"""Output sinks. Determinism is asserted on the file sink; Kafka and HTTP are transports
of the same ordered sequence."""
from __future__ import annotations

import logging
import os
import time

from .events import canonical, parse_iso_ms

log = logging.getLogger("store_sim.sinks")


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise SystemExit(f"missing required environment variable {name}")
    return v


def _pace(events: list[dict], speed: float):
    """Yield events; when speed > 0 sleep so that emission follows event time / speed."""
    if speed <= 0:
        yield from events
        return
    start_wall = time.monotonic()
    start_ev = None
    for ev in events:
        t = parse_iso_ms(ev["time"]) if "time" in ev else None
        if t is not None:
            if start_ev is None:
                start_ev = t
            target = (t - start_ev) / 1000.0 / speed
            wait = target - (time.monotonic() - start_wall)
            if wait > 0:
                time.sleep(min(wait, 5.0))
        yield ev


def kafka_sink(events: list[dict], speed: float = 0.0) -> int:
    from confluent_kafka import Producer

    producer = Producer({
        "bootstrap.servers": _env("PQT_KAFKA_BOOTSTRAP"),
        "security.protocol": os.environ.get("PQT_KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT"),
        "sasl.mechanisms": "SCRAM-SHA-256",
        "sasl.username": _env("PQT_KAFKA_USER"),
        "sasl.password": _env("PQT_KAFKA_PASSWORD"),
        "enable.idempotence": True,
        "acks": "all",
        "linger.ms": 5,
        "compression.type": "zstd",
    })
    topic = os.environ.get("PQT_TOPIC_RAW", "store.events.raw")
    errors: list[str] = []

    def on_delivery(err, _msg):
        if err is not None:
            errors.append(str(err))

    n = 0
    for ev in _pace(events, speed):
        key = ev.get("storeid") or ev.get("partitionkey") or "unknown"
        while True:
            try:
                producer.produce(topic, key=key.encode(), value=canonical(ev).encode(), on_delivery=on_delivery)
                break
            except BufferError:
                producer.poll(0.5)
        producer.poll(0)
        n += 1
    remaining = producer.flush(60)
    if remaining or errors:
        raise SystemExit(f"kafka delivery failed: {remaining} undelivered, errors={errors[:5]}")
    log.info("produced %d events to %s", n, topic)
    return n


def http_sink(events: list[dict], speed: float = 0.0, batch_size: int = 200) -> dict:
    import requests

    token_url = _env("PQT_TOKEN_URL")
    ingest_url = _env("PQT_INGEST_URL")
    session = requests.Session()
    token = {"value": None, "exp": 0.0}

    def bearer() -> str:
        if time.monotonic() > token["exp"] - 30:
            r = session.post(token_url, data={"grant_type": "client_credentials",
                                              "client_id": _env("PQT_CLIENT_ID"),
                                              "client_secret": _env("PQT_CLIENT_SECRET")}, timeout=10)
            r.raise_for_status()
            body = r.json()
            token.update(value=body["access_token"], exp=time.monotonic() + float(body.get("expires_in", 60)))
        return token["value"]

    totals: dict[str, int] = {}
    batch: list[dict] = []

    def flush() -> None:
        if not batch:
            return
        for attempt in range(8):
            r = session.post(f"{ingest_url}/batch", data=canonical(batch).encode(),
                             headers={"Authorization": f"Bearer {bearer()}",
                                      "Content-Type": "application/cloudevents-batch+json"}, timeout=30)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", "1")))
                continue
            if r.status_code >= 500:
                time.sleep(min(2 ** attempt, 15))
                continue
            r.raise_for_status()
            for item in r.json()["results"]:
                totals[item["status"]] = totals.get(item["status"], 0) + 1
            batch.clear()
            return
        raise SystemExit("http ingest kept failing; giving up")

    for ev in _pace(events, speed):
        batch.append(ev)
        if len(batch) >= batch_size:
            flush()
    flush()
    log.info("http ingest results: %s", totals)
    return totals
