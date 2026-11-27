"""Synthetic finite HLS through real Core worker and disposable Nextcloud.

No provider requests, public activation, production target, or user credentials.
Reuses the existing verified completion manifest and streaming WebDAV checker.
"""
from e2e_live_webdav import api_client, SOURCE_URL, wait_for_job
from e2e_p2_site_webdav import inspect_original_files, verify_remote_files


def main():
    source = SOURCE_URL.rsplit("/", 1)[0] + "/recording-finite.m3u8"
    with api_client() as client:
        policy = "/engines/streamlink/policy"
        previous = client.get(policy)
        previous.raise_for_status()
        enabled = previous.json()["enabled"]
        client.put(policy, json={"enabled": True}).raise_for_status()
        job_id = None
        try:
            response = client.post("/jobs", json={"engine": "streamlink", "url": source})
            assert response.status_code == 202, "Finite HLS submission failed"
            job_id = response.json()["id"]
            # Never send Stop; the finite playlist must end by itself.
            job = wait_for_job(client, job_id, "streamlink", "natural end")
            assert job["engine"] == "streamlink"
            assert job["status"] == "completed"
            assert job["stop_requested"] is False and job["can_stop"] is False
            manifest = inspect_original_files(job_id)
            assert len(manifest) == 1
            item = manifest[0]
            assert item["relative"].endswith(".ts") and item["media_verified"] is True
            assert 7.5 <= item["duration"] <= 8.5, "Recording did not cover the finite 8-second fixture"
            assert item["size"] > 0
            verify_remote_files(job, manifest, label="Streamlink finite HLS")
            print("Streamlink natural end: completed without Stop; decoded frame, "
                  f"{item['duration']:.3f}s, {item['size']} bytes, "
                  f"SHA-256 {item['sha256']}, verified WebDAV readback PASS", flush=True)
        finally:
            try:
                if job_id:
                    current = client.get(f"/jobs/{job_id}")
                    current.raise_for_status()
                    if current.json()["status"] not in {"completed", "failed", "cancelled"}:
                        client.post(f"/jobs/{job_id}/cancel").raise_for_status()
            finally:
                client.put(policy, json={"enabled": enabled}).raise_for_status()


if __name__ == "__main__":
    main()
