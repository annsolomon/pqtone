"""Tier 2 N3/N4: how rule quality degrades under vision noise.

Input: the offline score.json of the same scenario x seed matrix run under several noise profiles
(clean, measured, 2x measured). Output: precision and recall per (scenario, rule) for each profile, the
drop from clean in percentage points, and the gate: under the measured profile every enforce-mode rule
must still pass its thresholds (thresholds.yaml), or an ADR must revise them (N4).
"""
from __future__ import annotations


def compare(runs: list[tuple[str, dict]], *, gate_label: str = "measured") -> dict:
    labels = [label for label, _ in runs]
    rows: dict[tuple[str, str], dict] = {}
    for label, doc in runs:
        for r in doc["results"]:
            key = (r["scenario"], r["ruleId"])
            row = rows.setdefault(key, {"scenario": r["scenario"], "ruleId": r["ruleId"], "mode": r.get("mode", ""),
                                        "by": {}})
            s = r["score"]
            row["by"][label] = {"precision": s["precision"], "recall": s["recall"], "tp": s["tp"], "fp": s["fp"],
                                "fn": s["fn"], "failures": r["failures"]}
    base = labels[0]
    out_rows = []
    for (sc, rule), row in sorted(rows.items()):
        b = row["by"].get(base)
        for label in labels[1:]:
            x = row["by"].get(label)
            if b and x:
                x["precisionDropPp"] = round(100 * (b["precision"] - x["precision"]), 1)
                x["recallDropPp"] = round(100 * (b["recall"] - x["recall"]), 1)
        out_rows.append(row)
    gated = [r for r in out_rows if r["mode"] == "enforce" and gate_label in r["by"]]
    failing = [f"{r['scenario']} / {r['ruleId']}: {'; '.join(r['by'][gate_label]['failures'])}"
               for r in gated if r["by"][gate_label]["failures"]]
    worst = sorted(((r["by"][l].get("recallDropPp", 0) + r["by"][l].get("precisionDropPp", 0), r["scenario"], r["ruleId"], l)
                    for r in out_rows for l in labels[1:] if l in r["by"]), reverse=True)[:5]
    return {"labels": labels, "rows": out_rows, "gate": {"label": gate_label, "passed": not failing, "failing": failing},
            "worst": [{"scenario": s, "ruleId": rr, "label": l, "dropPp": d} for d, s, rr, l in worst]}


def render(doc: dict) -> str:
    labels = doc["labels"]
    head = "| Scenario | Rule | Mode | " + " | ".join(f"{l} P / R" for l in labels) + " | Gate |"
    lines = ["## Rule quality under vision noise (Tier 2 N3)", "", head, "|" + "---|" * (len(labels) + 4)]
    g = doc["gate"]["label"]
    for r in doc["rows"]:
        cells = []
        for l in labels:
            x = r["by"].get(l)
            if not x:
                cells.append("-")
                continue
            drop = ""
            if "recallDropPp" in x and (x["recallDropPp"] or x["precisionDropPp"]):
                drop = f" (-{x['precisionDropPp']:g} / -{x['recallDropPp']:g} pp)"
            cells.append(f"{x['precision']:.3f} / {x['recall']:.3f}{drop}")
        gx = r["by"].get(g, {})
        gate = "-" if r["mode"] != "enforce" else ("ok" if not gx.get("failures") else "FAIL")
        lines.append(f"| {r['scenario']} | {r['ruleId']} | {r['mode']} | " + " | ".join(cells) + f" | {gate} |")
    lines += ["", f"Gate (N4): every enforce-mode rule meets thresholds.yaml under **{g}** noise: "
                  f"{'PASS' if doc['gate']['passed'] else 'FAIL'}."]
    if doc["gate"]["failing"]:
        lines += [""] + [f"- {f}" for f in doc["gate"]["failing"]]
    if doc["worst"]:
        lines += ["", "Largest drops (precision + recall, percentage points): " +
                  "; ".join(f"{w['scenario']} / {w['ruleId']} under {w['label']}: {w['dropPp']:g}" for w in doc["worst"])]
    return "\n".join(lines) + "\n"
