"""Milestone C3: accessibility pass.

1. axe-core finds 0 serious or critical WCAG 2.2 A/AA violations on every console page an admin can
   open, including an incident detail page and the floor's table alternative.
2. A reviewer can review an incident with the keyboard only: skip link, navigation, incident list,
   detail page, dismiss with a reason and a note, confirmation message, with a visible focus ring.

Runs after test_demo.py (pytest orders files by name), so the stack has incidents to look at.
Same origin rules as the demo test: needs PQT_PUBLIC_URL == http://localhost:8080.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from axe_report import AxeReport
from test_demo import BASE, PUBLIC, REPORTS, sign_in

pytestmark = pytest.mark.skipif(
    PUBLIC != BASE, reason=f"browser tests need PQT_PUBLIC_URL={BASE} (currently {PUBLIC}); run scripts/mode.sh localhost")

REPORT = AxeReport()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module", autouse=True)
def write_report():
    yield
    REPORT.write(Path(REPORTS))


def _settle(page: Page) -> None:
    # Not "networkidle": the console keeps a server-sent-events connection open.
    expect(page.locator("main").first).to_be_visible(timeout=30_000)
    expect(page.get_by_text(re.compile(r"^Loading"))).to_have_count(0, timeout=30_000)


def test_console_pages_have_no_serious_accessibility_violations(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    AxeReport.install(context)
    page = context.new_page()
    try:
        sign_in(page, "admin", os.environ["PQT_ADMIN_PASSWORD"])
        nav = page.get_by_role("navigation", name="Main")
        routes = [("floor", "/"), ("review queue", "/review"), ("all incidents", "/incidents"),
                  ("shadow rules", "/shadow"), ("audit log", "/audit")]
        if nav.get_by_role("link", name="Review metrics").count():
            routes.append(("review metrics", "/metrics"))
        for name, path in routes:
            page.goto(f"{BASE}{path}")
            _settle(page)
            REPORT.scan(page, name)

        # The floor's table alternative, expanded.
        page.goto(f"{BASE}/")
        _settle(page)
        summary = page.get_by_text("Show the floor as a table")
        expect(summary).to_be_visible(timeout=30_000)
        summary.click()
        expect(page.get_by_role("table", name="Checkout queues")).to_be_visible()
        REPORT.scan(page, "floor table")

        # One incident detail page (with the timeline, when C4 is present).
        page.goto(f"{BASE}/incidents")
        _settle(page)
        first = page.locator("a.row-link").first
        if first.count():
            first.click()
            expect(page.get_by_role("heading", level=1)).to_be_visible()
            _settle(page)
            REPORT.scan(page, "incident detail")
    finally:
        context.close()

    blocking = REPORT.blocking()
    assert not blocking, "serious/critical accessibility violations:\n" + "\n".join(
        f"  {p}: {v['id']} ({v['impact']}) x{v['count']} - {v['help']} - {[n['target'] for n in v['nodes']]}"
        for p, v in blocking)


def _tab_to(page: Page, matches, limit: int = 80) -> str:
    """Press Tab until the focused element satisfies `matches(info)`; returns its accessible text."""
    for _ in range(limit):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => {
            const e = document.activeElement;
            if (!e) return null;
            return { tag: e.tagName.toLowerCase(), text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim(),
                     cls: e.className || '', href: e.getAttribute('href') || '' };
        }""")
        if info and matches(info):
            return info["text"]
    raise AssertionError("never reached the expected element with the Tab key")


def _focus_ring_visible(page: Page) -> bool:
    return page.evaluate("""() => {
        const s = getComputedStyle(document.activeElement);
        return (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2) || s.boxShadow !== 'none';
    }""")


def test_reviewer_can_review_an_incident_with_the_keyboard_only(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, "reviewer", os.environ["PQT_REVIEWER_PASSWORD"])
        page.goto(f"{BASE}/")
        _settle(page)

        # The first Tab reaches the skip link.
        page.keyboard.press("Tab")
        expect(page.locator(":focus")).to_have_text("Skip to main content")
        assert _focus_ring_visible(page), "focused elements show a focus ring"

        _tab_to(page, lambda i: i["tag"] == "a" and i["text"] == "Review queue")
        page.keyboard.press("Enter")
        expect(page.get_by_role("heading", level=1, name="Review queue")).to_be_visible()
        _settle(page)
        if page.locator("a.row-link").count() == 0:
            pytest.skip("no reviewable incidents left in this run")

        _tab_to(page, lambda i: i["tag"] == "a" and "row-link" in i["cls"])
        assert _focus_ring_visible(page)
        page.keyboard.press("Enter")
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        expect(page.get_by_role("region", name="Review actions")).to_be_visible(timeout=15_000)

        _tab_to(page, lambda i: i["tag"] == "button" and i["text"].startswith("Dismiss"))
        page.keyboard.press("Enter")
        reason = page.get_by_label("Reason")
        expect(reason).to_be_focused()  # opening the form moves focus to its first field
        page.keyboard.press("ArrowDown")  # False alarm -> Duplicate of another incident
        expect(reason).to_have_value("DUPLICATE")
        _tab_to(page, lambda i: i["tag"] == "textarea")
        page.keyboard.type("Keyboard-only review check (C3).")
        _tab_to(page, lambda i: i["tag"] == "button" and i["text"] == "Dismiss incident")
        page.keyboard.press("Enter")
        expect(page.get_by_role("status").filter(has_text="Dismissed.")).to_be_visible(timeout=15_000)
        expect(page.locator(".history")).to_contain_text(re.compile(r"Duplicate"))
        page.screenshot(path=str(REPORTS / "c3-keyboard-review.png"))
    finally:
        context.close()
