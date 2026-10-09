import time
from datetime import datetime

import pytest
import requests

from conftest import BASE, login


def test_api_requires_a_session():
    assert requests.get(f"{BASE}/api/me", timeout=10).status_code == 401
    assert requests.get(f"{BASE}/api/incidents", timeout=10).status_code == 401


def test_unknown_paths_are_denied():
    r = requests.get(f"{BASE}/actuator/env", timeout=10)
    assert r.status_code == 404
    assert "propertySources" not in r.text


def test_roles_are_expanded_by_hierarchy():
    assert login("viewer").me["roles"] == ["viewer"]
    assert login("admin").me["roles"] == ["admin", "operator", "reviewer", "viewer"]


def test_viewer_can_read_but_not_act_or_see_shadow():
    v = login("viewer")
    assert v.get("/api/incidents?mode=enforce").status_code == 200
    assert v.get("/api/stores").status_code == 200
    assert v.get("/api/pipeline/health").json()["status"] in ("ok", "degraded")
    assert v.get("/api/incidents?mode=shadow").status_code == 403
    assert v.get("/api/admin/audit/verify").status_code == 403
    r = v.post("/api/incidents/00000000-0000-0000-0000-000000000000/actions", json={"action": "ack"},
               headers={"If-Match": '"1"'})
    assert r.status_code == 403


def test_every_store_layout_is_listed_and_served():
    """Milestone S2: a second store with two checkout lines."""
    v = login("viewer")
    stores = v.get("/api/stores").json()
    assert [s["storeId"] for s in stores] == ["store-001", "store-002"]
    layout = v.get("/api/stores/store-002/layout").json()
    assert [q["id"] for q in layout["queues"]] == ["checkout-1", "checkout-2"]
    assert {r["queueId"] for r in layout["registers"]} == {"checkout-1", "checkout-2"}
    assert v.get("/api/stores/store-002/state").status_code == 200
    assert v.get("/api/stores/store-999/layout").status_code == 404


def test_state_changing_requests_need_csrf():
    r = login("reviewer").post("/api/incidents/00000000-0000-0000-0000-000000000000/actions",
                               json={"action": "confirm"}, headers={"If-Match": '"1"'}, csrf=False)
    assert r.status_code == 403


def _reviewable(console):
    deadline = time.time() + 60
    while time.time() < deadline:
        items = console.get("/api/incidents?mode=enforce&status=OPEN,ACKNOWLEDGED,AUTO_RESOLVED&limit=50").json()
        if items:
            return items
        time.sleep(3)
    pytest.skip("no reviewable incidents; run make e2e-pipeline first")


def test_review_flow_with_optimistic_concurrency():
    r = login("reviewer")
    inc = _reviewable(r)[0]
    path = f"/api/incidents/{inc['incidentId']}"
    detail = r.get(path)
    etag = detail.headers["ETag"]
    assert r.post(f"{path}/actions", json={"action": "confirm"}).status_code == 428
    assert r.post(f"{path}/actions", json={"action": "confirm"}, headers={"If-Match": '"999999"'}).status_code == 412
    ok = r.post(f"{path}/actions", json={"action": "confirm"}, headers={"If-Match": etag})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "CONFIRMED"
    # the old ETag is now stale
    assert r.post(f"{path}/actions", json={"action": "close"}, headers={"If-Match": etag}).status_code == 412
    history = r.get(path).json()["reviews"]
    assert history[-1]["actor"] == "reviewer" and history[-1]["to"] == "CONFIRMED"


def test_dismiss_requires_a_reason_and_operator_cannot_confirm():
    items = _reviewable(login("reviewer"))
    if len(items) < 2:
        pytest.skip("need a second reviewable incident")
    inc = items[-1]
    path = f"/api/incidents/{inc['incidentId']}"
    op = login("operator")
    etag = op.get(path).headers["ETag"]
    assert op.post(f"{path}/actions", json={"action": "confirm"}, headers={"If-Match": etag}).status_code == 403
    rv = login("reviewer")
    etag = rv.get(path).headers["ETag"]
    assert rv.post(f"{path}/actions", json={"action": "dismiss"}, headers={"If-Match": etag}).status_code == 400
    r = rv.post(f"{path}/actions", json={"action": "dismiss", "reasonCode": "FALSE_POSITIVE"}, headers={"If-Match": etag})
    assert r.status_code == 200 and r.json()["status"] == "DISMISSED"


def test_review_metrics_are_admin_only_and_count_decisions():
    """Milestone C5. Runs after the review-flow tests above, which confirm and dismiss incidents."""
    assert login("reviewer").get("/api/admin/review-metrics").status_code == 403
    a = login("admin")
    assert a.get("/api/admin/review-metrics?days=0").status_code == 400
    assert a.get("/api/admin/review-metrics?days=91").status_code == 400
    r = a.get("/api/admin/review-metrics?days=7")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["days"] == 7
    rules = {x["ruleId"]: x for x in m["rules"]}
    assert "R-QUEUE-001" in rules, "the pipeline run raised queue incidents"
    q = rules["R-QUEUE-001"]
    assert q["incidents"] >= q["acted"] >= q["decided"] == q["confirmed"] + q["dismissed"]
    assert q["undecided"] == q["incidents"] - q["decided"]
    if q["decided"]:
        assert q["confirmRateLow"] <= q["confirmRate"] <= q["confirmRateHigh"]
        assert q["timeToActionP50Seconds"] is not None and q["timeToActionP50Seconds"] >= 0
    assert "reviewer" not in r.text, "reviewer names are not part of the metrics"


def test_audit_chain_is_intact_and_admin_only():
    assert login("reviewer").get("/api/admin/audit/verify").status_code == 403
    verdict = login("admin").get("/api/admin/audit/verify").json()
    assert verdict["valid"] is True


def test_admin_sees_shadow_incidents_and_they_are_read_only():
    a = login("admin")
    items = a.get("/api/incidents?mode=shadow").json()
    if not items:
        pytest.skip("no shadow incidents in this run")
    path = f"/api/incidents/{items[0]['incidentId']}"
    etag = a.get(path).headers["ETag"]
    r = a.post(f"{path}/actions", json={"action": "confirm"}, headers={"If-Match": etag})
    assert r.status_code == 409 and r.json()["code"] == "shadow-read-only"


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def test_incident_timeline_shows_the_breach_and_no_track_ids():
    """Milestone C4: the detail page's evidence comes from the stored events, in event time."""
    v = login("viewer")
    deadline = time.time() + 60
    queue_incidents = []
    while time.time() < deadline and not queue_incidents:
        items = v.get("/api/incidents?mode=enforce&ruleId=R-QUEUE-001&limit=20").json()
        queue_incidents = [i for i in items if (i.get("subject") or "").startswith("queue:")]
        if not queue_incidents:
            time.sleep(3)
    if not queue_incidents:
        pytest.skip("no queue incidents; run make e2e-pipeline first")
    inc = queue_incidents[0]
    r = v.get(f"/api/incidents/{inc['incidentId']}/timeline")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["kind"] == "queue" and t["target"] == inc["subject"].split(":", 1)[1]
    at = {k: _ts(t[k]) for k in ("from", "onsetAt", "detectedAt", "to")}
    assert at["from"] < at["onsetAt"] <= at["detectedAt"] <= at["to"]
    assert t["series"], "a queue incident has queue-length events around it"
    assert max(p["length"] for p in t["series"]) >= t["threshold"], "the breach is visible in the evidence"
    times = [_ts(p["t"]) for p in t["series"]]
    assert times == sorted(times)
    assert "trk-" not in r.text
    assert v.get("/api/incidents/00000000-0000-0000-0000-000000000000/timeline").status_code == 404


def test_shadow_timeline_is_admin_only():
    a = login("admin")
    items = a.get("/api/incidents?mode=shadow").json()
    if not items:
        pytest.skip("no shadow incidents in this run")
    path = f"/api/incidents/{items[0]['incidentId']}/timeline"
    assert a.get(path).status_code == 200
    assert login("reviewer").get(path).status_code == 404


def test_rule_scores_are_recorded_after_the_live_run_and_admin_only():
    """Milestone R6: make e2e-pipeline records the run's score per rule (pip-scorer e2e --record)."""
    assert login("reviewer").get("/api/admin/rule-scores").status_code == 403
    r = login("admin").get("/api/admin/rule-scores")
    assert r.status_code == 200, r.text
    rules = {x["ruleId"]: x for x in r.json()["rules"]}
    assert "R-QUEUE-001" in rules and rules["R-QUEUE-001"]["mode"] == "enforce"
    assert rules["R-QUEUE-001"]["ready"] is False and "already enforce" in rules["R-QUEUE-001"]["reasons"]
    shadow = [x for x in rules.values() if x["mode"] == "shadow"]
    assert shadow, "the shadow rule R-ABS-001 is scored too"
    for x in rules.values():
        assert x["runs"] >= 1 and x["tp"] >= 0 and x["fp"] >= 0 and x["fn"] >= 0
        if x["precision"] is not None and x["precisionLow"] is not None:
            assert x["precisionLow"] <= x["precision"]


def test_live_stream_says_hello():
    v = login("viewer")
    with v.s.get(f"{BASE}/api/stream", stream=True, timeout=15, headers={"Accept": "text/event-stream"}) as r:
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/event-stream")
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("event:"):
                assert line.split(":", 1)[1].strip() == "hello"
                break


def test_logout_returns_the_idp_end_session_url():
    v = login("viewer")
    r = v.post("/logout")
    assert r.status_code == 200
    assert "/protocol/openid-connect/logout" in r.json()["logoutUrl"]
    assert v.get("/api/me").status_code == 401
