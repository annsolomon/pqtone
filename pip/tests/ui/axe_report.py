"""Milestone C3: run axe-core in a Playwright page and keep a report.

axe-core (MPL-2.0, test-only) is copied into the e2e-ui image at build time (tests/ui/Dockerfile)
and injected into each page; nothing is fetched at test time and nothing ships to users.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page

AXE_JS = Path(os.environ.get("PIP_AXE_JS", "/opt/axe/axe.min.js"))
# WCAG 2.2 A and AA plus axe best practices. Only serious and critical findings fail the gate;
# everything is reported.
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]
BLOCKING = {"serious", "critical"}


@dataclass
class AxeReport:
    pages: dict[str, list[dict]] = field(default_factory=dict)

    def scan(self, page: Page, name: str) -> list[dict]:
        if not page.evaluate("() => typeof window.axe !== 'undefined'"):
            page.add_script_tag(content=AXE_JS.read_text(encoding="utf-8"))
        result = page.evaluate(
            """async (tags) => {
                const r = await window.axe.run(document, { runOnly: { type: "tag", values: tags }, resultTypes: ["violations"] });
                return r.violations.map(v => ({
                    id: v.id, impact: v.impact, help: v.help, helpUrl: v.helpUrl,
                    nodes: v.nodes.slice(0, 10).map(n => ({ target: n.target.join(" "), summary: n.failureSummary })),
                    count: v.nodes.length,
                }));
            }""",
            TAGS,
        )
        self.pages[name] = result
        return result

    def blocking(self) -> list[tuple[str, dict]]:
        return [(p, v) for p, vs in self.pages.items() for v in vs if v.get("impact") in BLOCKING]

    def write(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "axe.json").write_text(json.dumps({"tags": TAGS, "pages": self.pages}, indent=2) + "\n")
        lines = ["### Accessibility (axe-core, WCAG 2.2 A/AA + best practices)", "",
                 "| Page | Critical | Serious | Moderate | Minor |", "| --- | ---: | ---: | ---: | ---: |"]
        for name, vs in self.pages.items():
            c = {k: sum(v["count"] for v in vs if v.get("impact") == k) for k in ("critical", "serious", "moderate", "minor")}
            lines.append(f"| {name} | {c['critical']} | {c['serious']} | {c['moderate']} | {c['minor']} |")
        details = [f"- **{p}** `{v['id']}` ({v['impact']}, {v['count']} nodes): {v['help']}"
                   for p, vs in self.pages.items() for v in vs]
        if details:
            lines += ["", "Findings:", *details]
        (out_dir / "axe.md").write_text("\n".join(lines) + "\n")
