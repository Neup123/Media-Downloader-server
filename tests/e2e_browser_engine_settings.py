"""Real Chromium UI acceptance for global and per-download engine options.

Runs against the production React dashboard and FastAPI/worker in disposable
Docker Compose. No mocked browser API calls, no public test media.
"""
from __future__ import annotations

import os
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("DOWNLOADER_UI_URL", "http://127.0.0.1:8080")
API_TOKEN = os.environ["APP_API_TOKEN"]
SOURCE_URL = os.getenv("SOURCE_URL", "http://media-source:9000/sample.mp4")
NEXTCLOUD_PASSWORD = os.environ["NEXTCLOUD_ADMIN_PASSWORD"]

YTDLP_GLOBAL = "best[height<=360]/best"
YTDLP_OVERRIDE = "best[height<=240]/best"
ARIA_GLOBAL = "3"
ARIA_OVERRIDE = "2"


def fetch_settings(client: httpx.Client, name: str) -> dict:
    response = client.get(f"/engines/{name}/settings")
    response.raise_for_status()
    return response.json()["settings"]


def await_completion(client: httpx.Client, job_id: str, engine: str) -> dict:
    until = time.monotonic() + 180
    last = None
    while time.monotonic() < until:
        response = client.get(f"/jobs/{job_id}")
        response.raise_for_status()
        last = response.json()
        if last["status"] == "completed":
            assert last.get("output_files"), f"{engine}: no output files"
            assert all(item["verified"] for item in last["output_files"]), (
                f"{engine}: WebDAV delivery not verified"
            )
            return last
        if last["status"] in {"cancelled", "failed"}:
            raise AssertionError(f"{engine} browser-submitted job ended {last['status']}: {last.get('error')}")
        time.sleep(2)
    raise AssertionError(f"{engine} browser-submitted job timed out: {str(last)[:500]}")


def verify_webdav_bytes(job: dict) -> None:
    # The disposable Nextcloud image is on an internal Compose DNS name.
    for item in job["output_files"]:
        parsed = urlsplit(item["path"])
        assert parsed.scheme == "http" and parsed.hostname == "nextcloud"
        target = urlunsplit((
            "http", "127.0.0.1:8081", parsed.path, "", ""
        ))
        with httpx.Client(timeout=30, auth=("admin", NEXTCLOUD_PASSWORD)) as client:
            result = client.get(target)
        result.raise_for_status()
        assert len(result.content) == item["size_bytes"], "WebDAV size mismatch"
        assert result.content, "Empty delivered media"


def submitted_job(page, client: httpx.Client, engine: str, expected_overrides: dict) -> dict:
    # Inspect the exact request created by a *real browser click*.
    with page.expect_response(
        lambda response: response.url.endswith("/api/v1/jobs")
        and response.request.method == "POST", timeout=30000
    ) as submitted:
        page.locator("section.submit-panel form button.primary").click()
    response = submitted.value
    assert response.status == 202, f"UI job request was HTTP {response.status}"
    job = response.json()
    assert job["engine"] == engine, f"Unexpected engine: {job['engine']}"
    for key, expected in expected_overrides.items():
        actual = job["overrides"]
        for part in key.split("."):
            actual = actual[part]
        assert actual == expected, f"{key}: expected {expected!r}, got {actual!r}"
    return await_completion(client, job["id"], engine)


def run_browser_acceptance() -> None:
    with httpx.Client(
        base_url=BASE_URL + "/api/v1",
        headers={"X-API-Key": API_TOKEN}, timeout=20,
    ) as api, sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            assert page.locator("header input[type=password]").count() == 0
            page.get_by_role("button", name="My settings", exact=True).click()
            page.get_by_label("API key").fill(API_TOKEN)
            with page.expect_response(
                lambda r: r.url.endswith("/engines/aria2c/settings")
                and r.request.method == "GET", timeout=30000
            ):
                page.get_by_role("button", name="Connect", exact=True).click()
            page.get_by_role("navigation", name="Main navigation").get_by_role("button", name="Administration", exact=True).click()
            with page.expect_response(
                lambda r: r.url.endswith("/api/v1/delivery") and r.request.method == "GET",
                timeout=30000
            ):
                page.get_by_role("navigation", name="Administration sections").get_by_role("button", name="Delivery").click()
            with page.expect_response(
                lambda r: r.url.endswith("/api/v1/delivery/test")
                and r.request.method == "POST", timeout=30000
            ) as delivery_preflight:
                page.get_by_role("button", name="Test delivery", exact=True).click()
            assert delivery_preflight.value.status == 200, "Destination byte readback/cleanup failed"
            payload = delivery_preflight.value.json()
            assert payload["verified"] and payload["cleaned_up"]
            page.get_by_role("navigation", name="Administration sections").get_by_role("button", name="Engines").click()

            picker = page.get_by_role("navigation", name="Download engines")
            assert picker.get_by_role("button").count() == 7, "Engine list missing"
            assert page.locator("section.engine-editor").count() == 1, "More than one engine editor"
            picker.locator('[data-engine-select="yt-dlp"]').click()
            yt_panel = page.locator('section.engine-editor[data-engine="yt-dlp"]')
            yt_panel.locator(".quality-control summary").click()
            # Autosave is triggered by a real field edit; hydration must not PUT.
            with page.expect_response(
                lambda r: r.url.endswith("/engines/yt-dlp/settings")
                and r.request.method == "PUT", timeout=30000
            ) as saved:
                yt_panel.get_by_label("Format selector", exact=True).fill(YTDLP_GLOBAL)
            assert saved.value.status == 200
            assert fetch_settings(api, "yt-dlp")["format"] == YTDLP_GLOBAL
            assert yt_panel.get_by_role("button", name="Save yt-dlp defaults").count() == 0

            picker.locator('[data-engine-select="aria2c"]').click()
            assert page.locator("section.engine-editor").count() == 1
            aria_panel = page.locator('section.engine-editor[data-engine="aria2c"]')
            with page.expect_response(
                lambda r: r.url.endswith("/engines/aria2c/settings")
                and r.request.method == "PUT", timeout=30000
            ) as saved:
                aria_panel.get_by_label("Connections per server", exact=True).fill(ARIA_GLOBAL)
            assert saved.value.status == 200
            assert fetch_settings(api, "aria2c")["options"]["max-connection-per-server"] == ARIA_GLOBAL
            assert aria_panel.get_by_role("button", name="Save aria2c defaults").count() == 0

            picker.locator('[data-engine-select="gallery-dl"]').click()
            gallery_panel = page.locator('section.engine-editor[data-engine="gallery-dl"]')
            assert gallery_panel.get_by_label("Advanced extractor options (JSON object)").is_hidden()
            gallery_panel.get_by_text("Advanced — site-specific gallery options").click()
            assert gallery_panel.get_by_label("Advanced extractor options (JSON object)").is_visible()

            page.get_by_role("button", name="Download", exact=True).click()
            form = page.locator("section.submit-panel form")
            assert form.count() == 1, "Download screen did not render a unique submission form"
            form.locator("summary").click()
            # Native wrapped <label><select> fields can have an accessible name
            # expanded by the option list; target the real form structure.
            form.locator("select").first.select_option("yt-dlp")
            form.locator('input[type="url"]').fill(SOURCE_URL)
            form.locator(".quality-control summary").click()
            form.get_by_label("Format selector", exact=True).fill(YTDLP_OVERRIDE)
            yt_job = submitted_job(page, api, "yt-dlp", {"format": YTDLP_OVERRIDE})
            verify_webdav_bytes(yt_job)
            print("Real Chromium: yt-dlp global + per-job settings + WebDAV PASS", flush=True)

            form.locator("select").first.select_option("aria2c")
            form.locator('input[type="url"]').fill(SOURCE_URL)
            form.locator(".common-options input[type='number']").first.fill(ARIA_OVERRIDE)
            aria_job = submitted_job(
                page, api, "aria2c",
                {"options.max-connection-per-server": ARIA_OVERRIDE},
            )
            verify_webdav_bytes(aria_job)
            print("Real Chromium: aria2c global + per-job settings + WebDAV PASS", flush=True)
            # The same real browser exercises Automation routing cards, not
            # mocked DOM snapshots. Rule order must persist in the core DB.
            page.get_by_role("navigation", name="Main navigation").get_by_role(
                "button", name="Administration", exact=True
            ).click()
            page.get_by_role("navigation", name="Administration sections").get_by_role(
                "button", name="Automation", exact=True
            ).click()
            routing_form = page.locator("#rule-editor form")
            routing_form.get_by_label("Website").fill("routing.example.org")
            routing_form.get_by_label("Download with").select_option("yt-dlp")
            routing_form.get_by_label("Note (optional)").fill("Preferred video downloader")
            with page.expect_response(
                lambda r: r.url.endswith("/api/v1/engine-rules")
                and r.request.method == "POST", timeout=30000
            ) as first_created:
                routing_form.get_by_role("button", name="Add site rule").click()
            assert first_created.value.status == 201
            first_id = first_created.value.json()["id"]
            page.locator('.rule-card[data-rule-id="' + first_id + '"]').wait_for()

            routing_form.get_by_label("Website").fill("routing.example.org")
            routing_form.get_by_label("Download with").select_option("aria2c")
            with page.expect_response(
                lambda r: r.url.endswith("/api/v1/engine-rules/order")
                and r.request.method == "PUT", timeout=30000
            ) as created_order:
                routing_form.get_by_role("button", name="Add site rule").click()
            assert created_order.value.status == 200
            rows = api.get("/engine-rules").json()
            assert len(rows) == 2 and rows[0]["engine"] == "aria2c"
            assert rows[0]["priority"] > rows[1]["priority"]
            second_id = rows[0]["id"]

            with page.expect_response(
                lambda r: r.url.endswith("/api/v1/engine-rules/order")
                and r.request.method == "PUT", timeout=30000
            ) as reordered:
                page.locator('.rule-card[data-rule-id="' + second_id + '"]').get_by_role(
                    "button", name="Move routing.example.org down"
                ).click()
            assert reordered.value.status == 200
            rows = api.get("/engine-rules").json()
            assert [row["id"] for row in rows] == [first_id, second_id]

            page.locator('.rule-card[data-rule-id="' + second_id + '"]').get_by_text(
                "Advanced", exact=True
            ).click()
            assert page.locator('.rule-card[data-rule-id="' + second_id + '"]').get_by_text(
                "Priority", exact=False
            ).count() >= 1
            with page.expect_response(
                lambda r: r.url.endswith("/api/v1/engine-rules/" + second_id)
                and r.request.method == "PUT", timeout=30000
            ) as toggled:
                page.locator('.rule-card[data-rule-id="' + second_id + '"]').get_by_role(
                    "checkbox"
                ).uncheck()
            assert toggled.value.status == 200
            rows = api.get("/engine-rules").json()
            assert next(row for row in rows if row["id"] == second_id)["enabled"] is False
            print("Real Chromium: site rule creation + order + toggle PASS", flush=True)

            print("BROWSER ENGINE SETTINGS ACCEPTANCE: PASS", flush=True)
        except Exception:
            page.screenshot(path="browser-failure.png", full_page=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        run_browser_acceptance()
    except Exception as exc:
        print(f"BROWSER ENGINE SETTINGS FAILURE: {exc}", file=sys.stderr, flush=True)
        raise
