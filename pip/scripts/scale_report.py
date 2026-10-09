#!/usr/bin/env python3
"""Milestone R5: merge outside measurements into a scale result, and write the report with charts.

  scale_report.py merge  RESULT.json STATE_KB MEMORY      # from `make scale` (du + docker stats)
  scale_report.py report RESULTS_DIR OUT.md                # tables + SVG charts next to OUT.md

Standard library only; the charts are plain SVG so the report renders on GitHub without tooling.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

COLORS = ["#1F5FBF", "#0B7A75", "#B86E00", "#A3243B", "#5B4B8A", "#4A5565"]


def merge(result: Path, state_kb: str, memory: str) -> None:
    r = json.loads(result.read_text())
    r["rulesEngine"] = {"stateKb": int(state_kb) if state_kb.strip().isdigit() else None, "memory": memory.strip() or None}
    result.write_text(json.dumps(r, indent=2) + "\n")
    print(json.dumps({"label": r["label"], **r["rulesEngine"]}))


def rates(samples: list[dict], key: str) -> list[tuple[float, float]]:
    ok = [s for s in samples if "error" not in s]
    return [(b["t"], (b[key] - a[key]) / (b["t"] - a["t"])) for a, b in zip(ok, ok[1:]) if b["t"] > a["t"]]


def svg_chart(series: dict[str, list[tuple[float, float]]], title: str, y_label: str, width=720, height=260) -> str:
    pl, pr, pt, pb = 56, 150, 28, 36
    xs = [x for pts in series.values() for x, _ in pts] or [0, 1]
    ys = [y for pts in series.values() for _, y in pts] or [0, 1]
    x1, y1 = max(xs) or 1, (max(ys) or 1) * 1.1
    sx = lambda x: pl + x / x1 * (width - pl - pr)
    sy = lambda y: height - pb - y / y1 * (height - pt - pb)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{title}" '
           f'font-family="system-ui, sans-serif" font-size="11">',
           f'<rect width="{width}" height="{height}" fill="#fff"/>',
           f'<text x="{pl}" y="16" font-size="13" font-weight="600" fill="#1C2430">{title}</text>']
    for i in range(6):
        v = y1 * i / 5
        out.append(f'<line x1="{pl}" x2="{width - pr}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" stroke="#EEF1F4"/>'
                   f'<text x="{pl - 6}" y="{sy(v) + 4:.1f}" text-anchor="end" fill="#4A5565">{v:,.0f}</text>')
    for i in range(6):
        v = x1 * i / 5
        out.append(f'<text x="{sx(v):.1f}" y="{height - pb + 16}" text-anchor="middle" fill="#4A5565">{v:.0f} s</text>')
    out.append(f'<text x="14" y="{(pt + height - pb) / 2:.0f}" transform="rotate(-90 14 {(pt + height - pb) / 2:.0f})" '
               f'text-anchor="middle" fill="#4A5565">{y_label}</text>')
    for n, (name, pts) in enumerate(series.items()):
        c = COLORS[n % len(COLORS)]
        if pts:
            d = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(pts))
            out.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2"/>')
        ly = pt + 14 + n * 16
        out.append(f'<line x1="{width - pr + 12}" x2="{width - pr + 28}" y1="{ly - 4}" y2="{ly - 4}" stroke="{c}" stroke-width="2"/>'
                   f'<text x="{width - pr + 34}" y="{ly}" fill="#1C2430">{name}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def report(results_dir: Path, out_md: Path) -> None:
    results = sorted((json.loads(p.read_text()) for p in results_dir.glob("*/result.json")),
                     key=lambda r: (r["speed"], int(r.get("streamThreads") or 0)))
    if not results:
        raise SystemExit(f"no results in {results_dir}")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    stem = out_md.stem
    (out_md.parent / f"{stem}-stored-rate.svg").write_text(svg_chart(
        {r["label"]: rates(r["samples"], "stored") for r in results}, "Events stored per second (event-core)", "events/s"))
    (out_md.parent / f"{stem}-rules-rate.svg").write_text(svg_chart(
        {r["label"]: rates(r["samples"], "received") for r in results}, "Events processed per second (rules-engine)", "events/s"))
    (out_md.parent / f"{stem}-lag.svg").write_text(svg_chart(
        {r["label"]: [(s["t"], s["lagMax"]) for s in r["samples"] if "error" not in s] for r in results},
        "Rules-engine consumer lag (records-lag-max)", "records"))

    lines = ["| Run | Stores | Speed | Threads | Events | Complete | Send s | Drained s | Produced avg/s | Stored peak/s | "
             "Rules peak/s | Lag max | Latency p50 / p95 / max ms | State | Memory |",
             "|---|---:|---:|---:|---:|:-:|---:|---:|---:|---:|---:|---:|---|---:|---|"]
    for r in results:
        lat = r["processingLatencyMs"]
        re_ = r.get("rulesEngine", {})
        lines.append(
            f"| {r['label']} | {r['stores']} | {r['speed']:g}x | {r.get('streamThreads') or '-'} | {r['events']['clean']:,} | "
            f"{'yes' if r['events']['complete'] and not r.get('drainTimedOut') else 'NO'} | {r['seconds']['send']} | "
            f"{r['seconds']['drained']} | {r['throughput']['producedAvg']} | {r['throughput']['storedPeak']} | "
            f"{r['throughput']['rulesPeak']} | {r['lag']['recordsLagMax']:,.0f} | {lat['p50']} / {lat['p95']} / {lat['max']} | "
            f"{(re_.get('stateKb') or 0) / 1024:.1f} MB | {re_.get('memory') or '-'} |")
    q = ["| Run | " + " | ".join(sorted(results[0]["quality"])) + " |", "|---|" + "---|" * len(results[0]["quality"])]
    for r in results:
        q.append(f"| {r['label']} | " + " | ".join(
            f"P {v['precision']:.3f} / R {v['recall']:.3f} (n={v['gt']})" for _, v in sorted(r["quality"].items())) + " |")
    body = "\n".join([
        "<!-- generated by scripts/scale_report.py from make scale-ladder; numbers are measured, not estimated -->",
        "## Measured results", "",
        f"Host: {results[0].get('host', {}).get('cpus', '?')} vCPU, {results[0].get('host', {}).get('memGb', '?')} GB RAM "
        "(the whole stack shares it).", "", *lines, "", "### Rule quality under load (incidents vs ground truth)", "", *q, "",
        f"![stored rate]({stem}-stored-rate.svg)", "", f"![rules rate]({stem}-rules-rate.svg)", "",
        f"![lag]({stem}-lag.svg)", ""])
    out_md.write_text(body)
    print(f"wrote {out_md} and 3 charts")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "merge":
        merge(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    elif len(sys.argv) == 4 and sys.argv[1] == "report":
        report(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit(__doc__)
