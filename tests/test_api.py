from __future__ import annotations


def test_health_is_public(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_requires_key(client):
    response = client.get("/api/v1/engines")
    assert response.status_code == 401


def test_submit_and_read_job(client, auth_headers):
    response = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/video",
        "engine": "yt-dlp",
        "overrides": {"format": "best"},
    })
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued"
    assert job["engine"] == "yt-dlp"
    assert job["overrides"]["format"] == "best"
    fetched = client.get(f"/api/v1/jobs/{job['id']}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == job["id"]


def test_url_safety(client, auth_headers):
    for url in ["file:///etc/passwd", "http://127.0.0.1/admin", "http://localhost/private", "http://user:pass@example.com/video"]:
        response = client.post("/api/v1/jobs", headers=auth_headers, json={"url": url})
        assert response.status_code == 422, (url, response.text)


def test_aria2_accepts_magnet_and_rejects_invalid_engine_option(client, auth_headers):
    response = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "engine": "aria2c",
    })
    assert response.status_code == 202
    invalid = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/file.iso",
        "engine": "aria2c",
        "overrides": {"options": {"rpc-listen-all": "true"}},
    })
    assert invalid.status_code == 422


def test_cancel_queued_job_and_retry(client, auth_headers):
    submitted = client.post("/api/v1/jobs", headers=auth_headers, json={"url": "https://example.com/file"}).json()
    cancelled = client.post(f"/api/v1/jobs/{submitted['id']}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    retried = client.post(f"/api/v1/jobs/{submitted['id']}/retry", headers=auth_headers)
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"


def test_engine_settings_are_persisted_and_validated(client, auth_headers):
    response = client.put("/api/v1/engines/yt-dlp/settings", headers=auth_headers, json={
        "settings": {"format": "bestvideo+bestaudio/best", "write_subtitles": True, "extra_args": ["--no-warnings"]},
    })
    assert response.status_code == 200
    read = client.get("/api/v1/engines/yt-dlp/settings", headers=auth_headers)
    assert read.json()["settings"]["format"] == "bestvideo+bestaudio/best"
    assert read.json()["settings"]["write_subtitles"] is True
    blocked = client.put("/api/v1/engines/yt-dlp/settings", headers=auth_headers, json={
        "settings": {"extra_args": ["--exec=touch /tmp/pwned"]},
    })
    assert blocked.status_code == 422
    sensitive = client.put("/api/v1/engines/gallery-dl/settings", headers=auth_headers, json={
        "settings": {"options": {"extractor.pixiv.refresh-token": "do-not-store"}},
    })
    assert sensitive.status_code == 422
    credential_proxy = client.put("/api/v1/engines/gallery-dl/settings", headers=auth_headers, json={
        "settings": {"proxy": "http://user:password@proxy.example:8080"},
    })
    assert credential_proxy.status_code == 422


def test_delivery_secret_is_write_only(client, auth_headers):
    response = client.put("/api/v1/delivery", headers=auth_headers, json={
        "mode": "webdav",
        "webdav_url": "https://cloud.example.com/remote.php/dav/files/downloader",
        "username": "downloader",
        "app_password": "sensitive-password",
        "folder": "Media/Downloads",
    })
    assert response.status_code == 200
    assert response.json()["password_configured"] is True
    assert "sensitive-password" not in response.text
    read = client.get("/api/v1/delivery", headers=auth_headers)
    assert read.status_code == 200
    assert read.json()["password_configured"] is True
    assert "sensitive-password" not in read.text


def test_api_contract_is_available(client):
    response = client.get("/openapi.yaml")
    assert response.status_code == 200
    assert "openapi: 3.1.0" in response.text
    assert client.get("/api-reference").status_code == 200


def test_cookie_files_are_uploadable_but_never_returned(client, auth_headers):
    import base64
    content = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tSID\tsecret-value\n"
    response = client.put(
        "/api/v1/cookies/browser-export.txt",
        headers=auth_headers,
        json={"content_base64": base64.b64encode(content).decode("ascii")},
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "browser-export.txt"
    assert "secret-value" not in response.text
    listing = client.get("/api/v1/cookies", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()[0]["filename"] == "browser-export.txt"
    assert "secret-value" not in listing.text
    traversal = client.put(
        "/api/v1/cookies/%2E%2E%2Fescape.txt",
        headers=auth_headers,
        json={"content_base64": base64.b64encode(content).decode("ascii")},
    )
    assert traversal.status_code in {404, 422}


def test_gallery_dl_engine_settings_and_job_are_supported(client, auth_headers):
    settings = client.put("/api/v1/engines/gallery-dl/settings", headers=auth_headers, json={
        "settings": {
            "options": {"extractor.generic.referer": "https://example.com/"},
            "proxy": "",
            "cookies_file": "",
        },
    })
    assert settings.status_code == 200
    fetched = client.get("/api/v1/engines/gallery-dl/settings", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["settings"]["options"]["extractor.generic.referer"] == "https://example.com/"

    submitted = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/gallery",
        "engine": "gallery-dl",
        "overrides": {"options": {"extractor.generic.referer": "https://override.example/"}},
    })
    assert submitted.status_code == 202
    assert submitted.json()["engine"] == "gallery-dl"

    blocked = client.put("/api/v1/engines/gallery-dl/settings", headers=auth_headers, json={
        "settings": {"options": {"output.directory": "/tmp/escape"}},
    })
    assert blocked.status_code == 422
