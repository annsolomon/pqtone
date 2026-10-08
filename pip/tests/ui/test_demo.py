"""Milestone C2: the Tier 1 demo as a browser test.

A reviewer signs in through Keycloak, watches the floor while a fresh register_delay
simulation streams in at 20x, sees the checkout queue reach the alert threshold, gets the
alert (toast from C1 and the incident in the rail), opens the incident and confirms it.

Runs inside the gateway's network namespace (compose service e2e-ui), so
http://localhost:8080 is exactly the origin a browser uses. `make e2e-ui` starts the
simulation only after this test has signed in and the live stream is connected; the test
signals that by writing READY_FILE.

Everything is recorded to a video in /work/reports/ui (uploaded by CI).
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

BASE = os.environ.get("PIP_BASE_URL", "http://localhost:8080").rstrip("/")
PUBLIC = os.environ.get("PIP_PUBLIC_URL", BASE).rstrip("/")
REPORTS = Path(os.environ.get("PIP_UI_REPORTS", "/work/reports/ui"))
READY_FILE = REPORTS / "ready"
WAIT_MS = int(os.environ.get("PIP_UI_TIMEOUT_S", "300")) * 1000
THRESHOLD = 6  # R-QUEUE-001 threshold in config/rules.yaml
AT_OR_ABOVE_THRESHOLD = re.compile(r"^\s*([6-9]|[1-9]\d+)\b")


def sign_in(page: Page, username: str, password: str) -> None:
    page.goto(f"{BASE}/")
    # The console redirects to Keycloak on the first 401.
    page.locator("#username").fill(username)
    page.locator("#password").fill(password)
    page.locator("#kc-login").click()
    expect(page.get_by_role("navigation", name="Main")).to_be_visible(timeout=30_000)


def test_reviewer_watches_a_queue_build_up_and_confirms_the_alert():
    if PUBLIC != BASE:
        pytest.skip(f"browser test needs PIP_PUBLIC_URL={BASE} (currently {PUBLIC}); run scripts/mode.sh localhost")
    password = os.environ["PIP_REVIEWER_PASSWORD"]
    REPORTS.mkdir(parents=True, exist_ok=True)
    video_dir = REPORTS / "video-raw"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900},
                                      record_video_dir=str(video_dir),
                                      record_video_size={"width": 1440, "height": 900})
        page = context.new_page()
        ok = False
        try:
            sign_in(page, "reviewer", password)

            # Live data connected (the dot in the top bar turns on), then let the simulation start.
            expect(page.locator(".pulse.on")).to_be_visible(timeout=30_000)
            run_label = page.get_by_test_id("sim-run")
            before = run_label.inner_text() if run_label.count() else ""
            READY_FILE.write_text("ready\n")

            # The floor switches to the new simulation run and the checkout queue builds up.
            if before:
                expect(run_label).not_to_have_text(before, timeout=WAIT_MS)
            else:
                expect(run_label).to_be_visible(timeout=WAIT_MS)
            queue = page.get_by_test_id("queue-count")
            expect(queue).to_have_text(AT_OR_ABOVE_THRESHOLD, timeout=WAIT_MS)
            page.screenshot(path=str(REPORTS / "1-queue-at-threshold.png"))

            # The alert: a toast (C1) for the queue incident, and the same incident in the rail.
            toast = page.get_by_test_id("alert-toast").filter(has_text="Long checkout queue").first
            expect(toast).to_be_visible(timeout=WAIT_MS)
            href = toast.get_by_role("link", name="Open incident").get_attribute("href")
            assert href and re.fullmatch(r"/incidents/[0-9a-f-]{36}", href), href
            rail_item = page.locator(f'.rail a[href="{href}"]')
            expect(rail_item).to_be_visible(timeout=30_000)
            expect(page.locator('g[data-zone="checkout"]')).to_have_attribute("data-alert", re.compile(r".+"))
            page.screenshot(path=str(REPORTS / "2-alert.png"))

            # Open it from the rail and confirm it.
            rail_item.click()
            expect(page.get_by_role("heading", level=1, name="Long checkout queue")).to_be_visible()
            page.get_by_role("button", name="Confirm incident").click()
            expect(page.get_by_role("status").filter(has_text="Confirmed.")).to_be_visible(timeout=15_000)
            expect(page.locator(".facts")).to_contain_text("Confirmed")
            expect(page.locator(".history")).to_contain_text("reviewer")
            page.screenshot(path=str(REPORTS / "3-confirmed.png"))
            ok = True
        finally:
            if not ok:
                page.screenshot(path=str(REPORTS / "failure.png"), full_page=True)
            video = page.video
            context.close()
            browser.close()
            if video is not None:
                shutil.move(video.path(), REPORTS / "c2-demo.webm")
            shutil.rmtree(video_dir, ignore_errors=True)
