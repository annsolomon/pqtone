import json

import requests

from conftest import BASE, edge_event, now_iso

CE = {"Content-Type": "application/cloudevents+json"}


def post(event, token, headers=CE, path="/v1/events"):
    h = dict(headers)
    if token:
        h["Authorization"] = f"Bearer {token}"
    body = event if isinstance(event, (bytes, str)) else json.dumps(event)
    return requests.post(f"{BASE}{path}", data=body, headers=h, timeout=15)


def test_gateway_health_and_security_headers():
    assert requests.get(f"{BASE}/healthz", timeout=5).status_code == 200
    r = requests.get(f"{BASE}/", timeout=5)
    assert r.status_code == 200
    csp = r.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "Server" not in r.headers or "nginx/" not in r.headers["Server"]


def test_ingest_requires_a_token():
    assert post(edge_event(), None).status_code == 401


def test_ingest_rejects_a_forged_token():
    assert post(edge_event(), "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln").status_code == 401


def test_accept_then_duplicate_then_conflict(edge_token):
    ev = edge_event()
    first = post(ev, edge_token)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "accepted"
    again = post(ev, edge_token)
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    retried_with_trace = dict(ev, traceparent="00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
    assert post(retried_with_trace, edge_token).json()["status"] == "duplicate"
    changed = dict(ev, data={"queueId": "checkout-1", "length": 9, "openRegisters": 1})
    conflict = post(changed, edge_token)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "conflicting-duplicate"
    assert conflict.headers["Content-Type"].startswith("application/problem+json")


def test_contract_violations_are_reported_together(edge_token):
    bad = edge_event(data={"queueId": "checkout-1", "length": "seven"})
    del bad["subject"]
    r = post(bad, edge_token)
    assert r.status_code == 400
    assert r.json()["code"] == "invalid-event"
    assert len(r.json()["errors"]) >= 2


def test_unknown_type_rejected(edge_token):
    assert post(edge_event(type="com.pip.store.teleport"), edge_token).status_code == 400


def test_future_timestamp_rejected(edge_token):
    assert post(edge_event(time=now_iso(3600)), edge_token).status_code == 400


def test_client_cannot_publish_for_another_source(edge_token):
    ev = edge_event(source="urn:pip:sim:store-sim:store-001", simrunid="run-0123456789ab")
    r = post(ev, edge_token)
    assert r.status_code == 403 and r.json()["code"] == "source-not-allowed"


def test_wrong_content_type_rejected(edge_token):
    assert post(edge_event(), edge_token, headers={"Content-Type": "application/json"}).status_code == 415


def test_oversized_event_rejected(edge_token):
    huge = json.dumps(edge_event(data={"queueId": "checkout-1", "length": 1, "openRegisters": 1, "pad": "x" * 70_000}))
    assert post(huge, edge_token).status_code == 413


def test_malformed_json_rejected(edge_token):
    assert post("{not json", edge_token).status_code == 400


def test_batch_returns_per_item_results(edge_token):
    good = edge_event()
    bad = edge_event(data={})
    foreign = edge_event(source="urn:pip:edge:store-999:gw-1")
    r = post([good, good, bad, foreign], edge_token,
             headers={"Content-Type": "application/cloudevents-batch+json"}, path="/v1/events/batch")
    assert r.status_code == 200, r.text
    statuses = [x["status"] for x in r.json()["results"]]
    assert statuses == ["accepted", "duplicate", "invalid", "forbidden"]


def test_sim_client_can_publish_sim_events(sim_token):
    ev = edge_event(source="urn:pip:sim:store-sim:store-001", simrunid="run-e2e000000000",
                    time="2026-01-01T09:00:00.000Z")
    assert post(ev, sim_token).status_code == 202
