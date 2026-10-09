"""CloudEvent construction and canonical serialisation."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

NAMESPACE = uuid.UUID("6b1f3f2a-6c1e-4f6e-9d1a-5f0c2b7e9a10")
TYPE_PREFIX = "com.pqt.store."
SCHEMA_BASE = "https://schemas.pqt.local/store/"
SCHEMA_VERSION = "1.0.0"


def iso_ms(epoch_ms: int) -> str:
    dt = datetime.fromtimestamp(epoch_ms // 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{epoch_ms % 1000:03d}Z"


def parse_iso_ms(value: str) -> int:
    dt = datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")
    return int(dt.timestamp() * 1000)


def canonical(obj) -> str:
    """Deterministic JSON: sorted keys, no whitespace, integers only in payloads."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def source_for(store_id: str) -> str:
    return f"urn:pqt:sim:store-sim:{store_id}"


def make_event(*, order: int, short_type: str, t_ms: int, store_id: str, run_id: str,
               subject: str, data: dict) -> dict:
    return {
        "specversion": "1.0",
        "id": str(uuid.uuid5(NAMESPACE, f"{run_id}:{order}")),
        "source": source_for(store_id),
        "type": TYPE_PREFIX + short_type,
        "time": iso_ms(t_ms),
        "subject": subject,
        "dataschema": f"{SCHEMA_BASE}{short_type}/{SCHEMA_VERSION}",
        "datacontenttype": "application/json",
        "storeid": store_id,
        "partitionkey": store_id,
        "simrunid": run_id,
        "sequence": f"{order:010d}",
        "data": data,
    }
