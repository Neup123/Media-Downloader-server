"""Single public-domain Wikimedia file diagnostic; no retries or raw logs."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from app.security import preflight_source_dns
from e2e_live_webdav import api_client, configure_webdav, wait_for_services
from e2e_p2_site_webdav import inspect_original_files, verify_remote_files

SOURCE = "https://commons.wikimedia.org/wiki/File:Example.jpg"
# Wikimedia User-Agent policy: identify the automated client and its project.
USER_AGENT = "DownloaderServerDiagnosticBot/1.0 (https://github.com/Neup123/Media-Downloader-server)"
OPTIONS = {
    "extractor.wikimedia.useragent": USER_AGENT,
    # The upstream extractor may enumerate image revisions; explicitly request
    # the first file rather than assuming a File: page yields exactly one.
    "extractor.file-range": "1",
    "extractor.retries": "0",
    "extractor.wikimedia.retries": "0",
    "downloader.http.retries": "0",
    "extractor.timeout": "20",
}
RESULT = Path("commons-smoke-attestation.json")


def safe_failure_category(job: dict) -> str:
    # Only fixed diagnostic categories leave this runner. Never echo logs.
    detail = str(job.get("error") or "") + "\n" + str(job.get("log_text") or "")
    if re.search(r"\b429\b|too many requests|rate.limit", detail, re.I):
        return "source_rate_limited"
    if re.search(r"\b403\b|forbidden", detail, re.I):
        return "source_access_denied"
    if re.search(r"no output file|no results|no files", detail, re.I):
        return "no_extractor_output"
    if re.search(r"timed? out|timeout", detail, re.I):
        return "timeout"
    return "download_or_delivery_failed"


def fixture_output_summary(manifest: list[dict]) -> dict:
    # Fixed aggregate fields only. Never expose source names, paths or URLs.
    return {
        "output_count": len(manifest),
        "jpeg_named_count": sum(
            item["relative"].lower().endswith((".jpg", ".jpeg")) for item in manifest
        ),
        "media_signature_checked_count": sum(
            item["media_verified"] is True for item in manifest
        ),
        "total_output_bytes": sum(item["size"] for item in manifest),
        "all_outputs_within_fixture_size_limit": all(
            0 < item["size"] <= 1024 * 1024 for item in manifest
        ),
    }


def report(category: str, **details) -> None:
    RESULT.write_text(json.dumps({
        "schema_version": 1,
        "source_revision": os.environ.get("GITHUB_SHA", "local"),
        "fixture": "Wikimedia Commons public-domain Example.jpg",
        "engine": "gallery-dl",
        "outcome": category,
        "production_target_verified": False,
        **details,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Commons single-file diagnostic: " + category, flush=True)
    if category == "unexpected_fixture_output":
        print("Safe fixture output counts: " + json.dumps(details, sort_keys=True), flush=True)


def main() -> None:
    report("not_completed")
    preflight_source_dns(SOURCE)
    wait_for_services()
    with api_client() as client:
        configure_webdav(client)
        response = client.post("/jobs", json={
            "url": SOURCE, "engine": "gallery-dl",
            "overrides": {"options": OPTIONS},
        })
        if response.status_code != 202:
            report("submission_rejected", http_status=response.status_code)
            raise RuntimeError("Fixture submission rejected")
        identifier = response.json()["id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status = client.get(f"/jobs/{identifier}")
            if status.status_code != 200:
                report("status_unavailable")
                raise RuntimeError("Fixture status unavailable")
            job = status.json()
            if job["status"] == "completed":
                break
            if job["status"] in {"failed", "cancelled", "skipped"}:
                report(safe_failure_category(job))
                raise RuntimeError("Fixture did not complete")
            time.sleep(3)
        else:
            report("job_timeout")
            raise RuntimeError("Fixture job timed out")
    manifest = inspect_original_files(identifier)
    if (
        len(manifest) != 1
        or not manifest[0]["relative"].lower().endswith((".jpg", ".jpeg"))
        or manifest[0]["media_verified"] is not True
        or not 0 < manifest[0]["size"] <= 1024 * 1024
    ):
        report("unexpected_fixture_output", **fixture_output_summary(manifest))
        raise RuntimeError("Expected one small JPEG")
    verify_remote_files(job, manifest)
    report("verified", file_count=1, size_bytes=manifest[0]["size"],
           sha256=manifest[0]["sha256"], webdav_sha256_verified=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Diagnostic did not pass; see bounded attestation, not raw logs.", file=sys.stderr)
        raise SystemExit(1)
