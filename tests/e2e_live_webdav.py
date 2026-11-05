from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx


API_URL = os.getenv("DOWNLOADER_API_URL", "http://127.0.0.1:8080/api/v1")
API_TOKEN = os.environ["APP_API_TOKEN"]
NC_URL = os.getenv("NEXTCLOUD_HTTP_URL", "http://127.0.0.1:8081")
NC_USER = "admin"
NC_PASSWORD = os.environ["NEXTCLOUD_ADMIN_PASSWORD"]
SOURCE_URL = os.getenv("SOURCE_URL", "http://media-source:9000/sample.mp4")
WEBDAV_URL = os.getenv(
    "WEBDAV_BASE_URL",
    "http://nextcloud/remote.php/dav/files/admin",
)
FIXTURE = Path(os.getenv("MEDIA_FIXTURE", "fixture/sample.mp4"))


def fail(message: str) -> None:
    raise RuntimeError(message)


def wait_for_services() -> None:
    deadline = time.monotonic() + 240
    with httpx.Client(timeout=10.0) as client:
        nextcloud_ready = False
        downloader_ready = False
        while time.monotonic() < deadline:
            try:
                response = client.get(NC_URL + "/status.php")
                data = response.json()
                nextcloud_ready = response.status_code == 200 and data.get("installed") is True
            except (httpx.HTTPError, ValueError):
                nextcloud_ready = False
            try:
                downloader_ready = client.get(
                    "http://127.0.0.1:8080/healthz"
                ).status_code == 200
            except httpx.HTTPError:
                downloader_ready = False
            if nextcloud_ready and downloader_ready:
                return
            time.sleep(3)
    fail("Nextcloud and Downloader Server did not both become ready within 240 seconds")


def api_client() -> httpx.Client:
    return httpx.Client(
        base_url=API_URL,
        timeout=20.0,
        headers={"X-API-Key": API_TOKEN, "Content-Type": "application/json"},
    )


def configure_webdav(client: httpx.Client) -> None:
    response = client.put("/delivery", json={
        "mode": "webdav",
        "webdav_url": WEBDAV_URL,
        "username": NC_USER,
        "app_password": NC_PASSWORD,
        "folder": "Downloader-E2E",
    })
    if response.status_code != 200:
        fail(f"Could not configure WebDAV delivery: {response.status_code} {response.text}")
    if response.json().get("password_configured") is not True:
        fail("The WebDAV credential was not stored")


def submit_and_wait(client: httpx.Client, engine: str) -> dict:
    response = client.post("/jobs", json={"url": SOURCE_URL, "engine": engine})
    if response.status_code != 202:
        fail(f"{engine} job submission failed: {response.status_code} {response.text}")
    job_id = response.json()["id"]
    print(f"Submitted {engine} job {job_id}", flush=True)

    deadline = time.monotonic() + 240
    last = None
    while time.monotonic() < deadline:
        current = client.get(f"/jobs/{job_id}")
        if current.status_code != 200:
            fail(f"Could not read {engine} job: {current.status_code} {current.text}")
        last = current.json()
        status = last["status"]
        progress = last.get("progress") or {}
        print(
            f"{engine} job {job_id}: {status} "
            f"{progress.get('percent', 'unknown')}%",
            flush=True,
        )
        if status == "completed":
            return last
        if status in {"failed", "cancelled"}:
            fail(f"{engine} job {job_id} ended {status}: {last.get('error')}\n{last.get('log_text')}")
        time.sleep(2)
    fail(
        f"{engine} job {job_id} did not finish within 240 seconds: "
        f"{json.dumps(last, default=str)[:3000]}"
    )


def verify_downloaded_files(job: dict) -> None:
    outputs = job.get("output_files") or []
    if not outputs:
        fail(f"Job {job['id']} completed without output files")
    for item in outputs:
        if not item.get("verified"):
            fail(f"Delivery did not mark {item.get('filename')} as verified")
        expected_size = int(item["size_bytes"])
        source_hash = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()

        parsed = urlsplit(item["path"])
        if parsed.scheme not in {"http", "https"}:
            fail(f"WebDAV result is not an HTTP(S) URL: {item['path']}")
        # The container-only DNS alias is replaced by the host-published port
        # so the CI runner can independently read the delivered Nextcloud file.
        netloc = parsed.netloc.replace("nextcloud", "127.0.0.1:8081")
        download_url = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        with httpx.Client(auth=(NC_USER, NC_PASSWORD), timeout=30.0) as remote:
            response = remote.get(download_url)
        if response.status_code != 200:
            fail(
                f"Could not fetch delivered file {item.get('filename')} "
                f"from Nextcloud: HTTP {response.status_code}"
            )
        if len(response.content) != expected_size:
            fail(
                f"Nextcloud file size mismatch for {item.get('filename')}: "
                f"expected {expected_size}, got {len(response.content)}"
            )
        remote_hash = hashlib.sha256(response.content).hexdigest()
        if remote_hash != source_hash:
            fail(f"Nextcloud file content hash mismatch for {item.get('filename')}")
        print(
            f"Verified {item['filename']} in Nextcloud: "
            f"{expected_size} bytes, SHA-256 {remote_hash}",
            flush=True,
        )


def main() -> None:
    if not FIXTURE.is_file():
        fail(f"Media fixture not found: {FIXTURE}")
    wait_for_services()
    with api_client() as client:
        configure_webdav(client)
        for engine in ("yt-dlp", "aria2c", "gallery-dl", "generic", "you-get"):
            job = submit_and_wait(client, engine)
            verify_downloaded_files(job)
            print(f"{engine} -> Nextcloud WebDAV: PASS", flush=True)
    print("REAL NEXTCLOUD WEBDAV E2E: PASS", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"E2E FAILURE: {exc}", file=sys.stderr, flush=True)
        raise
