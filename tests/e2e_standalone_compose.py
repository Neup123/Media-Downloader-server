"""Clean, standalone Docker Compose smoke acceptance without Nextcloud.

This script runs on the GitHub Actions host after starting ONLY the downloader
service from compose.yaml and checks health, key enforcement and core APIs.
"""
from __future__ import annotations

import os
import sys
import time

import httpx


BASE_URL = os.getenv("STANDALONE_API_URL", "http://127.0.0.1:8080")
API_TOKEN = os.environ["APP_API_TOKEN"]


def assert_standalone_service() -> None:
    deadline = time.monotonic() + 240
    with httpx.Client(base_url=BASE_URL, timeout=6.0, follow_redirects=False) as public:
        while time.monotonic() < deadline:
            try:
                health = public.get("/healthz")
                if health.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            raise AssertionError("Standalone Compose service did not become healthy")

        anonymous = public.get("/api/v1/jobs")
        assert anonymous.status_code == 401, (
            f"Anonymous jobs endpoint returned {anonymous.status_code}, expected 401"
        )

    with httpx.Client(
        base_url=BASE_URL + "/api/v1",
        headers={"X-API-Key": API_TOKEN},
        timeout=45.0,
        follow_redirects=False,
    ) as authorized:
        engines = authorized.get("/engines")
        assert engines.status_code == 200, (
            f"Standalone engine discovery failed: HTTP {engines.status_code}"
        )
        engine_names = engines.json()
        assert isinstance(engine_names, list) and engine_names, (
            "Standalone API did not return installed engines"
        )
        installed = {item["name"]: item for item in engine_names}
        # Round 1 found an empty get-sauce version despite the pinned Go
        # executable being installed. Exercise the real production binary,
        # not a mocked subprocess response.
        assert installed["get-sauce"]["installed"] is True
        version = installed["get-sauce"]["version"]
        assert isinstance(version, str) and version.strip() and "1.10.0" in version, (
            "Installed get-sauce did not expose its pinned 1.10.0 build version"
        )
        jobs = authorized.get("/jobs")
        assert jobs.status_code == 200 and jobs.json() == [], (
            "New standalone Compose installation does not have a clean queue"
        )
        delivery = authorized.get("/delivery")
        assert delivery.status_code == 200, (
            f"Standalone delivery API unavailable: HTTP {delivery.status_code}"
        )
        assert "app_password" not in delivery.json(), (
            "Standalone delivery settings exposed a secret key"
        )
    print("CLEAN STANDALONE DOCKER COMPOSE ACCEPTANCE: PASS", flush=True)


if __name__ == "__main__":
    try:
        assert_standalone_service()
    except Exception as exc:
        print(f"STANDALONE COMPOSE FAILURE: {exc}", file=sys.stderr, flush=True)
        raise
