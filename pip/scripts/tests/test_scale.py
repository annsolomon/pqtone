"""Milestone R5: the parts of the scale harness that don't need a running stack."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "sim"))
import scale  # noqa: E402


def test_metric_lines_are_parsed_and_lag_takes_the_worst_partition(monkeypatch):
    text = "\n".join([
        "# HELP pip_rules_events_received_total Events received",
        'pip_rules_events_received_total{service="rules-engine"} 1234.0',
        'kafka_consumer_fetch_manager_records_lag_max{client_id="a",service="rules-engine"} 7.0',
        'kafka_consumer_fetch_manager_records_lag_max{client_id="b",service="rules-engine"} 42.0',
        'kafka_consumer_fetch_manager_records_lag_max{client_id="c",service="rules-engine"} NaN',
        'pip_rules_events_late_total{store="s"} 3.0',
    ])

    class R:
        def read(self):
            return text.encode()

    monkeypatch.setattr(scale.urllib.request, "urlopen", lambda *a, **k: R())
    assert scale.scrape() == {"received": 1234.0, "lagMax": 42.0}


def test_inputs_clone_store_001_into_n_stores_run_by_one_scenario(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    layout, scenario = scale.make_inputs(tmp_path, 3, "PT10M")
    names = sorted(p.name for p in (tmp_path / "layouts").glob("*.json"))
    assert names == ["store-s001.json", "store-s002.json", "store-s003.json"]
    base = json.loads((ROOT / "config/layouts/store-001.json").read_text())
    s2 = json.loads((tmp_path / "layouts" / "store-s002.json").read_text())
    assert s2["storeId"] == "store-s002" and s2["zones"] == base["zones"]
    from store_sim.generate import simulate
    run = simulate(seed=1, layout_path=layout, scenario_path=scenario, rules_path="config/rules.yaml")
    assert {e["storeid"] for e in run.clean} == {"store-s001", "store-s002", "store-s003"}
    assert len(run.manifest["stores"]) == 3


def test_report_writes_tables_and_three_charts(tmp_path):
    import scale_report as sr
    for label, speed in (("s50-x10-t2", 10), ("s50-x40-t2", 40)):
        (tmp_path / label).mkdir()
        samples = [{"t": i * 5.0, "produced": i * 101 * speed, "stored": i * 100.0 * speed, "received": i * 99.0 * speed,
                    "backlog": 2.0 * i * speed, "lagMax": 10.0 * i} for i in range(6)]
        samples.insert(3, {"t": 14.0, "error": "timeout"})
        result = {"label": label, "stores": 50, "speed": speed, "streamThreads": "2",
                  "events": {"clean": 1000, "complete": True}, "seconds": {"send": 10, "drained": 12}, "drainTimedOut": False,
                  "throughput": {"producedAvg": 100, "storedPeak": 110, "rulesPeak": 105}, "lag": {"backlogMax": 50, "recordsLagMax": 50},
                  "processingLatencyMs": {"p50": 100, "p95": 300, "max": 400},
                  "quality": {"R-QUEUE-001": {"precision": 1.0, "recall": 1.0, "gt": 5}}, "samples": samples}
        (tmp_path / label / "result.json").write_text(json.dumps(result))
        sr.merge(tmp_path / label / "result.json", "2048", "300MiB")
    out = tmp_path / "doc" / "rules-engine.md"
    sr.report(tmp_path, out)
    md = out.read_text()
    assert "| s50-x10-t2 | 50 | 10x | 2 | 1,000 | yes |" in md and "2.0 MB | 300MiB |" in md
    assert "Backlog max" in md
    for name in ("stored-rate", "rules-rate", "lag"):
        svg = (tmp_path / "doc" / f"rules-engine-{name}.svg").read_text()
        assert svg.startswith("<svg") and "s50-x40-t2" in svg
    # sampling errors are skipped, never plotted as zero
    assert sr.rates(json.loads((tmp_path / "s50-x10-t2" / "result.json").read_text())["samples"], "stored")[0][1] == 200.0  # 1,000 events per 5 s sample
