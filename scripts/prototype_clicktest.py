"""Real Chromium click test of the standalone link-first mock prototype.

All jobs and media metadata are simulated in the single HTML file. This is
interaction/design evidence only, not a Core backend or download acceptance gate.
"""
from pathlib import Path
import os
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "prototypes/standalone-link-first.html"
OUT = Path(os.getenv("PROTOTYPE_SHOTS", "prototype-evidence"))
OUT.mkdir(parents=True, exist_ok=True)


def snap(page, name: str) -> None:
    page.screenshot(path=str(OUT / name), full_page=True, animations="disabled")


def main() -> None:
    assert PROTO.is_file(), "Standalone clickable prototype is missing"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1360, "height": 900}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(PROTO.as_uri(), wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="What would you like to download?")).to_be_visible()
            expect(page.get_by_role("tab", name="New download")).to_have_attribute("aria-selected", "true")
            snap(page, "01-first-use-desktop.png")
            url = page.get_by_label("Link to the file, video or collection")
            url.fill("https://example.com/playlist/summer")
            page.get_by_role("button", name="Check link").click()
            preview = page.locator("#preview")
            expect(preview).to_be_visible(timeout=15_000)
            expect(page.locator("#media-kind")).to_have_text("PLAYLIST FOUND")
            expect(page.locator("#selection-summary")).to_contain_text("3 selected")
            expect(page.locator("#selection-summary")).to_contain_text("1 size unknown")
            snap(page, "02-playlist-detected.png")

            items = page.locator(".pick-item")
            expect(items).to_have_count(3)
            items.nth(1).uncheck()
            expect(page.locator("#selection-summary")).to_contain_text("2 selected")
            expect(page.locator("#selection-summary")).not_to_contain_text("size unknown")
            items.nth(0).uncheck()
            items.nth(2).uncheck()
            expect(page.get_by_role("button", name="Download", exact=True)).to_be_disabled()
            items.nth(0).check()
            expect(page.get_by_role("button", name="Download", exact=True)).to_be_enabled()

            page.get_by_label("Save to").select_option(label="Videos")
            page.get_by_role("button", name="Download", exact=True).click()
            expect(page.get_by_role("tab", name="My downloads")).to_have_attribute("aria-selected", "true")
            expect(page.locator("#library-list .list-job")).to_have_count(1)
            expect(page.locator("#library-list")).to_contain_text("Videos · Playlist")
            snap(page, "03-library-queued.png")

            page.get_by_role("searchbox", name="Search downloads").fill("nonexistent")
            expect(page.locator("#library-list")).to_contain_text("Nothing matches")
            page.get_by_role("searchbox", name="Search downloads").fill("")
            page.get_by_role("combobox", name="Filter downloads").select_option("done")
            expect(page.locator("#library-list")).to_contain_text("Nothing matches")
            page.get_by_role("combobox", name="Filter downloads").select_option("all")
            expect(page.locator("#library-list .list-job")).to_have_count(1)
            page.get_by_role("tab", name="Settings").click()
            expect(page.get_by_role("heading", name="My download settings")).to_be_visible()
            page.get_by_label("Default save location").select_option(label="Music")
            page.get_by_role("tab", name="New download").click()
            expect(page.locator("#save-folder")).to_have_value("Music")
            snap(page, "04-settings-and-return.png")

            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_role("heading", name="What would you like to download?")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"), "Horizontal overflow on mobile"
            snap(page, "05-first-use-mobile.png")
            assert not errors, f"Prototype browser errors: {errors}"
        except Exception:
            snap(page, "failure.png")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
