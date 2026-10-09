"""Milestone R4 end to end: change a threshold in the running stack, no restart, versions kept.

Run inside the tools container (it has the scorer and the read-only database DSN). The Makefile
target `e2e-hot-reload` strings the steps together:

  make-rules  -> write a copy of rules.yaml with R-QUEUE-001 threshold 4 at version 1.1.0
  pick-seed   -> the first seed from a fresh start whose run the threshold change actually affects
  snapshot    -> remember every existing incident's version
  (publish the copy with rules-admin)
  wait-rules  -> every rules-engine task's heartbeat reports the new version, from the topic
  (simulate register_delay with the NEW rules as ground truth)
  verify      -> the live incidents match the new-rules ground truth (pqt-scorer e2e), that ground
                 truth differs from the old rules' one, new incidents carry 1.1.0, old ones are unchanged
  (reset with rules-admin) -> wait-rules back to the file's 1.0.0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from pqt_scorer.cli import _connect, main as scorer_main
from store_sim.config import load_rules
from store_sim.events import parse_iso_ms
from store_sim.reference import RefEngine

Q_HEAD = "  - id: R-QUEUE-001\n    version: 1.0.0\n"


def make_rules(a) -> int:
    text = Path(a.input).read_text()
    for needle in (Q_HEAD, "      threshold: 6 "):
        if needle not in text:
            print(f"rules file no longer contains {needle!r}; update this test", file=sys.stderr)
            return 2
    text = text.replace("      threshold: 6 ", f"      threshold: {a.threshold} ")
    text = text.replace(Q_HEAD, f"  - id: R-QUEUE-001\n    version: {a.version}\n")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text)
    print(f"wrote {a.out}: R-QUEUE-001 threshold {a.threshold}, version {a.version}")
    return 0


def pick_seed(a) -> int:
    """A random seed can give a run where thresholds 4 and 6 open the same number of queue episodes;
    the test would then prove nothing (and verify() rightly fails). Search forward from a fresh seed
    (fresh, so event ids never collide with earlier runs) for one where they differ. Deterministic for
    a given start; prints only the seed."""
    from store_sim.generate import simulate

    for seed in range(a.start, a.start + a.tries):
        counts = []
        for rules in (a.rules, a.baseline_rules):
            out = simulate(seed=seed, layout_path=a.layout, scenario_path=a.scenario, rules_path=rules)
            counts.append(sum(1 for g in out.ground_truth if g["ruleId"] == "R-QUEUE-001"))
        if counts[0] != counts[1]:
            print(seed)
            print(f"seed {seed}: R-QUEUE-001 episodes {counts[0]} (published) vs {counts[1]} (file)", file=sys.stderr)
            return 0
    print(f"no seed in [{a.start}, {a.start + a.tries}) separates the two rule sets", file=sys.stderr)
    return 1


def snapshot(a) -> int:
    with _connect() as conn:
        rows = conn.execute("SELECT incident_id::text, rule_version FROM pqt.incident").fetchall()
    Path(a.out).write_text(json.dumps(dict(rows), sort_keys=True))
    print(f"snapshot of {len(rows)} incidents")
    return 0


def wait_rules(a) -> int:
    deadline = time.monotonic() + a.timeout
    seen = None
    while time.monotonic() < deadline:
        with _connect() as conn:
            rows = conn.execute("""
                SELECT payload -> 'rules' FROM pqt.pipeline_heartbeat
                 WHERE received_at > now() - interval '30 seconds'""").fetchall()
        seen = [r[0] for r in rows if r[0]]
        if seen and all(r.get("versions", {}).get(a.rule) == a.version and r.get("source") == a.source for r in seen):
            print(f"all {len(seen)} rules-engine tasks run {a.rule} {a.version} from the {a.source}")
            return 0
        time.sleep(2)
    print(f"timed out waiting for {a.rule} {a.version} from the {a.source}; last heartbeats: {seen}", file=sys.stderr)
    return 1


def reference_counts(case: Path, rules_path: str) -> Counter:
    manifest = json.loads((case / "manifest.json").read_text())
    rules = load_rules(rules_path)
    ref = RefEngine(rules)
    events = [json.loads(line) for line in (case / "events.jsonl").read_text().splitlines() if line.strip()]
    clean = {e["id"]: e for e in events if not e["id"].startswith("malformed-") and "sequence" in e}
    for ev in sorted(clean.values(), key=lambda e: e["sequence"]):   # the clean, in-order stream
        ref.process({"t": parse_iso_ms(ev["time"]), "type": ev["type"], "storeId": manifest["storeId"],
                     "simRunId": manifest["runId"], "id": ev["id"], "data": ev["data"]})
    ref.finish(manifest["horizonMs"])
    return Counter(i["ruleId"] for i in ref.out if i["kind"] == "OPENED")


def verify(a) -> int:
    case = Path(a.case_dir)
    run_id = json.loads((case / "manifest.json").read_text())["runId"]
    problems = []

    if scorer_main(["e2e", "--case-dir", str(case), "--out", a.report, "--timeout", "600"]) != 0:
        problems.append("live incidents do not match the ground truth computed with the published rules")

    new, old = reference_counts(case, a.rules), reference_counts(case, a.baseline_rules)
    if new["R-QUEUE-001"] == old["R-QUEUE-001"]:
        problems.append(f"the threshold change makes no difference on this run ({new['R-QUEUE-001']} episodes); "
                        "the test would prove nothing")

    with _connect() as conn:
        versions = {v for (v,) in conn.execute(
            "SELECT DISTINCT rule_version FROM pqt.incident WHERE sim_run_id = %s AND rule_id = 'R-QUEUE-001'",
            (run_id,)).fetchall()}
        now = dict(conn.execute("SELECT incident_id::text, rule_version FROM pqt.incident").fetchall())
    if versions != {a.version}:
        problems.append(f"new R-QUEUE-001 incidents carry versions {sorted(versions)}, expected {{{a.version}}}")
    before = json.loads(Path(a.snapshot).read_text())
    changed = {k: (v, now.get(k)) for k, v in before.items() if now.get(k) != v}
    if changed:
        problems.append(f"{len(changed)} existing incidents changed version, e.g. {list(changed.items())[:3]}")

    print(json.dumps({"runId": run_id, "queueEpisodes": {"publishedRules": new["R-QUEUE-001"],
                                                          "fileRules": old["R-QUEUE-001"]},
                      "newIncidentVersions": sorted(versions), "existingIncidentsChecked": len(before),
                      "problems": problems}))
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hot-reload-check")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make-rules")
    m.add_argument("--input", default="config/rules.yaml")
    m.add_argument("--out", required=True)
    m.add_argument("--threshold", type=int, default=4)
    m.add_argument("--version", default="1.1.0")
    m.set_defaults(fn=make_rules)
    k = sub.add_parser("pick-seed")
    k.add_argument("--start", type=int, required=True)
    k.add_argument("--tries", type=int, default=50)
    k.add_argument("--rules", required=True)
    k.add_argument("--baseline-rules", default="config/rules.yaml")
    k.add_argument("--layout", default="config/layouts/store-001.json")
    k.add_argument("--scenario", default="sim/scenarios/register_delay.yaml")
    k.set_defaults(fn=pick_seed)
    s = sub.add_parser("snapshot")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=snapshot)
    w = sub.add_parser("wait-rules")
    w.add_argument("--rule", default="R-QUEUE-001")
    w.add_argument("--version", required=True)
    w.add_argument("--source", choices=["topic", "file"], required=True)
    w.add_argument("--timeout", type=int, default=120)
    w.set_defaults(fn=wait_rules)
    v = sub.add_parser("verify")
    v.add_argument("--case-dir", required=True)
    v.add_argument("--rules", required=True)
    v.add_argument("--baseline-rules", default="config/rules.yaml")
    v.add_argument("--version", default="1.1.0")
    v.add_argument("--snapshot", required=True)
    v.add_argument("--report", default="/work/reports/hot-reload")
    v.set_defaults(fn=verify)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
