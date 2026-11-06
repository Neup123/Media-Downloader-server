"""Opt-in real P2 download and Nextcloud WebDAV content acceptance.

Use only lawful publicly accessible source URLs with no secrets. No P2 URL
is checked into the repository. The normal P0 CI acceptance is separate.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.security import preflight_source_dns
from scripts.p2_smoke_support import validate_site_smoke_source, verify_output_sets
from e2e_live_webdav import (\n    api_client, configure_webdav, wait_for_services, restart_downloader_compose,\n    check_persisted_job, NC_USER, NC_PASSWORD,\n)

INSPECT_COMPLETED = r"""
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from app.completion import _file_sha256, load_completed_files

workdir = Path("/data/jobs") / sys.argv[1]
files = load_completed_files(workdir)
if not files:
    raise SystemExit("No verified completed-download manifest")
output = []
for path in files:
    relative = path.relative_to(workdir).as_posix()
    suffix = path.suffix.lower()
    media_verified = False
    if suffix in {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".mov"}:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, timeout=60, check=False,
        )
        if probe.returncode or not json.loads(probe.stdout or b"{}").get("format"):
            raise SystemExit("Video/audio output is not parseable")
        media_verified = True
    elif suffix in {".zip", ".cbz"}:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise SystemExit("Archive output is corrupted")
        media_verified = True
    elif suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        with path.open("rb") as stream:
            header = stream.read(16)
        media_verified = (
            header.startswith(b"\xff\xd8\xff") if suffix in {".jpg", ".jpeg"}
            else header.startswith(b"\x89PNG\r\n\x1a\n") if suffix == ".png"
            else header.startswith((b"GIF87a", b"GIF89a")) if suffix == ".gif"
            else header[:4] == b"RIFF" and header[8:12] == b"WEBP"
        )
        if not media_verified:
            raise SystemExit("Image output lacks a valid file signature")
    output.append({
        "relative": relative,
        "size": path.stat().st_size,
        "sha256": _file_sha256(path),
        "media_verified": media_verified,
    })
print(json.dumps(output))
"""


def submit_and_wait_p2(client: httpx.Client, engine: str, url: str) -> dict:
    response = client.post("/jobs", json={"url": url, "engine": engine})
    if response.status_code != 202:
        raise RuntimeError(f"P2 job submission failed: HTTP {response.status_code}")
    job_id = response.json()["id"]
    return wait_for_p2_completion(client, job_id)


def wait_for_p2_completion(client: httpx.Client, job_id: str) -> dict:
    # A site extractor may put source URLs/tokens in its logs; never print logs,
    # error payloads, response bodies or credentials on CI failure.
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}")
        if response.status_code != 200:
            raise RuntimeError("P2 job status endpoint failed")
        job = response.json()
        if job["status"] == "completed":
            return job
        if job["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"P2 job {job_id} ended {job['status']}; inspect private diagnostics")
        time.sleep(5)
    raise RuntimeError("P2 site job did not complete within 900 seconds")


def assert_same_completed_manifest(before: list[dict], after: list[dict]) -> None:
    def exact_files(items: list[dict]) -> list[tuple[str, int, str]]:
        return sorted(
            (file["relative"], file["size"], file["sha256"])
            for file in items
        )
    if not before or exact_files(before) != exact_files(after):
        raise RuntimeError("P2 completed-download files changed across restart/re-delivery")


def inspect_original_files(job_id: str) -> list[dict]:
    result = subprocess.run(
        ["docker", "exec", "downloader-server", "python", "-c", INSPECT_COMPLETED, job_id],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Completed files could not be verified inside the downloader container")
    try:
        manifest = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("Job inspection did not provide valid JSON") from exc
    if not isinstance(manifest, list):
        raise RuntimeError("Job inspection did not return a list")
    return manifest


def verify_remote_files(job: dict, manifest: list[dict]) -> None:
    output = job.get("output_files") or []
    source = verify_output_sets(manifest, output, job["id"])
    for item in output:
        parsed = urlsplit(item["path"])
        # Localhost is the CI-published disposable Nextcloud port, never an
        # arbitrary URL controlled by the source website.
        remote_url = urlunsplit(("http", "127.0.0.1:8081", parsed.path, "", ""))
        from scripts.p2_smoke_support import relative_webdav_output
        relative = relative_webdav_output(item["path"], job["id"])
        expected = source[relative]
        digest = hashlib.sha256()
        transferred = 0
        with httpx.Client(auth=(NC_USER, NC_PASSWORD), timeout=60.0, trust_env=False) as remote:
            with remote.stream("GET", remote_url) as response:
                if response.status_code != 200:
                    raise RuntimeError("A delivered P2 file could not be read back from Nextcloud")
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    transferred += len(chunk)
                    digest.update(chunk)
        if transferred != expected["size"] or digest.hexdigest() != expected["sha256"]:
            raise RuntimeError("P2 remote content does not match the completed local file")
    recognized = sum(1 for entry in manifest if entry["media_verified"])
    print(f"Verified {len(manifest)} P2 WebDAV files by remote SHA-256; "
          f"{recognized} recognized media files parsed or signature-checked.")


def main() -> None:
    engine = os.environ.get("P2_SMOKE_ENGINE", "")
    source = validate_site_smoke_source(engine, os.environ.get("P2_SMOKE_URL", ""))
    preflight_source_dns(source)
    wait_for_services()
    with api_client() as client:
        configure_webdav(client)
        job = submit_and_wait_p2(client, engine, source)
    manifest = inspect_original_files(job["id"])
    verify_remote_files(job, manifest)

    # A real container restart must preserve source content, delivery audit,
    # job logs and encrypted WebDAV credentials. Reuse the already verified
    # delivery manifest instead of re-downloading the licensed media.
    restart_downloader_compose()
    wait_for_services()
    with api_client() as client:
        current = client.get(f"/jobs/{job['id']}")
        if current.status_code != 200:
            raise RuntimeError("Verified P2 job vanished across downloader restart")
        restored = current.json()
        check_persisted_job(job, restored)
        restored_manifest = inspect_original_files(job["id"])
        assert_same_completed_manifest(manifest, restored_manifest)
        verify_remote_files(restored, restored_manifest)

        delivery = client.get("/delivery")
        if delivery.status_code != 200 or delivery.json().get("password_configured") is not True:
            raise RuntimeError("WebDAV credentials were not restored after P2 restart")
        if NC_PASSWORD in delivery.text:
            raise RuntimeError("WebDAV delivery API unexpectedly returned its secret")

        retry = client.post(f"/jobs/{job['id']}/redeliver")
        if retry.status_code != 202:
            raise RuntimeError("Completed P2 job could not request audited re-delivery")
        redelivered = wait_for_p2_completion(client, job["id"])
        if redelivered["id"] != job["id"]:
            raise RuntimeError("P2 re-delivery changed the durable job identity")
        final_manifest = inspect_original_files(job["id"])
        assert_same_completed_manifest(manifest, final_manifest)
        verify_remote_files(redelivered, final_manifest)

    print(f"Real {engine} P2 site -> Nextcloud WebDAV, restart and re-delivery: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("P2 site smoke FAILED; examine private job diagnostics without publishing source credentials", file=sys.stderr)
        raise SystemExit(1)
