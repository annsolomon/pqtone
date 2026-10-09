"""Milestone S2: the console handles more than one store.

The store picker lists every layout, switching stores reloads the floor, and store 002 shows both of
its checkout lines with their own registers. Runs after test_demo.py (pytest orders files by name).
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import expect, sync_playwright

from test_demo import BASE, PUBLIC, REPORTS, sign_in

pytestmark = pytest.mark.skipif(
    PUBLIC != BASE, reason=f"browser tests need PQT_PUBLIC_URL={BASE} (currently {PUBLIC}); run scripts/mode.sh localhost")


def test_store_picker_switches_the_floor_between_stores():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            sign_in(page, "reviewer", os.environ["PQT_REVIEWER_PASSWORD"])
            picker = page.locator(".store-pick select")  # get_by_label("Store") also matches the floor plan's name
            expect(picker).to_be_visible(timeout=30_000)
            expect(picker.locator("option")).to_have_count(2)
            expect(page.get_by_role("heading", level=1)).to_have_text("Demo store 001")
            expect(page.get_by_test_id("queue-count")).to_have_count(1)

            picker.select_option("store-002")
            expect(page.get_by_role("heading", level=1)).to_have_text("Demo store 002")
            for zone in ("checkout-a", "checkout-b", "bakery"):
                expect(page.locator(f'g[data-zone="{zone}"]')).to_be_visible()
            expect(page.get_by_test_id("queue-count")).to_have_count(2)
            expect(page.locator('g.queue[data-queue="checkout-2"] rect.register')).to_have_count(2)
            page.screenshot(path=str(REPORTS / "s2-store-002.png"))

            picker.select_option("store-001")
            expect(page.get_by_role("heading", level=1)).to_have_text("Demo store 001")
            expect(page.locator('g[data-zone="checkout"]')).to_be_visible()
        finally:
            context.close()
            browser.close()
