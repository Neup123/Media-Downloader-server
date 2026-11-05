"""Cookie uploads must never follow links or expose partially-written secrets."""

from __future__ import annotations

import base64
import stat

from app.main import create_app


def _upload(client, auth_headers, filename: str, data: bytes):
    return client.put(
        "/api/v1/cookies/" + filename,
        headers=auth_headers,
        json={"content_base64": base64.b64encode(data).decode("ascii")},
    )


def test_overwriting_symlink_does_not_touch_external_file(settings, client, auth_headers):
    cookie_dir = settings.data_dir / "cookies"
    outside = settings.data_dir / "unrelated-secret.txt"
    outside.write_bytes(b"must not change")
    target = cookie_dir / "browser.txt"
    target.symlink_to(outside)
    original = outside.read_bytes()
    content = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tSID\tnew-secret\n"

    response = _upload(client, auth_headers, "browser.txt", content)
    assert response.status_code == 200, response.text
    assert outside.read_bytes() == original
    assert target.read_bytes() == content
    assert not target.is_symlink()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(cookie_dir.glob(".downloader-cookie-*.part"))


def test_cookie_listing_never_follows_symlinks(settings, client, auth_headers):
    cookie_dir = settings.data_dir / "cookies"
    outside = settings.data_dir / "unrelated.txt"
    outside.write_bytes(b"other material")
    (cookie_dir / "external.txt").symlink_to(outside)
    response = client.get("/api/v1/cookies", headers=auth_headers)
    assert response.status_code == 200
    assert not response.json()


def test_cookie_write_failure_keeps_previous_file_and_removes_temp(settings, client, auth_headers, monkeypatch):
    cookie_dir = settings.data_dir / "cookies"
    target = cookie_dir / "browser.txt"
    target.write_bytes(b"previous private cookie")

    def fail_replace(*_args):
        raise OSError("simulated replacement failure")
    monkeypatch.setattr("app.main.os.replace", fail_replace)

    response = _upload(client, auth_headers, "browser.txt", b"# Netscape HTTP Cookie File\nchanged")
    assert response.status_code == 500
    assert "could not be stored securely" in response.text
    assert target.read_bytes() == b"previous private cookie"
    assert not list(cookie_dir.glob(".downloader-cookie-*.part"))


def test_cookie_directory_symlink_is_refused(settings, client, auth_headers):
    cookie_dir = settings.data_dir / "cookies"
    cookie_dir.rmdir()
    other = settings.data_dir / "unrelated"
    other.mkdir()
    cookie_dir.symlink_to(other, target_is_directory=True)
    response = _upload(client, auth_headers, "browser.txt", b"# Netscape HTTP Cookie File\n")
    assert response.status_code == 422
    assert "must not be a symlink" in response.text
    assert not list(other.iterdir())
