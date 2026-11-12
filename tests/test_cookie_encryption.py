from __future__ import annotations

import asyncio
import base64
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.adapters import gallery_dl, you_get, yt_dlp
from app.cookies import encrypt_cookie, read_cookie_file
from app.main import create_app


COOKIE_TEXT = b"# Netscape HTTP Cookie File\n.example.test\tTRUE\t/\tTRUE\t0\tid\tplaceholder\n"


def _payload() -> dict[str, str]:
    return {"content_base64": base64.b64encode(COOKIE_TEXT).decode("ascii")}


def test_put_encrypts_cookie_and_list_reports_stored_size(client, settings, auth_headers):
    response = client.put("/api/v1/cookies/session.txt", json=_payload(), headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"filename": "session.txt", "size_bytes": len(COOKIE_TEXT), "stored": True}
    path = settings.data_dir / "cookies" / "session.txt"
    ciphertext = path.read_bytes()
    assert ciphertext != COOKIE_TEXT
    assert COOKIE_TEXT not in ciphertext
    assert Fernet(settings.encryption_key.encode("ascii")).decrypt(ciphertext) == COOKIE_TEXT
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    listing = client.get("/api/v1/cookies", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json() == [{"filename": "session.txt", "size_bytes": len(ciphertext)}]


def test_upload_round_trip_through_shared_reader(client, settings, auth_headers):
    assert client.put("/api/v1/cookies/session.cookies", json=_payload(), headers=auth_headers).status_code == 200
    path = settings.data_dir / "cookies" / "session.cookies"
    assert read_cookie_file(path, settings.encryption_key) == COOKIE_TEXT


def test_legacy_plaintext_with_or_without_key(settings):
    path = settings.data_dir / "cookies" / "old.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(COOKIE_TEXT)
    assert read_cookie_file(path, settings.encryption_key) == COOKIE_TEXT
    assert read_cookie_file(path, None) == COOKIE_TEXT
    assert path.read_bytes() == COOKIE_TEXT  # Migrates only on re-upload.


def test_put_missing_key_fails_closed(settings, auth_headers):
    app = create_app(replace(settings, encryption_key=None), start_worker=False)
    with TestClient(app) as client:
        response = client.put("/api/v1/cookies/session.txt", json=_payload(), headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["detail"] == "APP_ENCRYPTION_KEY is required before storing cookie files"
    assert not (settings.data_dir / "cookies" / "session.txt").exists()


def test_put_invalid_key_fails_closed(settings, auth_headers):
    app = create_app(replace(settings, encryption_key="invalid"), start_worker=False)
    with TestClient(app) as client:
        response = client.put("/api/v1/cookies/session.txt", json=_payload(), headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["detail"] == "APP_ENCRYPTION_KEY must be a valid Fernet key"
    assert not (settings.data_dir / "cookies" / "session.txt").exists()


def test_encrypted_cookie_without_key_is_not_passed_through(settings):
    path = settings.data_dir / "cookies" / "session.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_cookie(COOKIE_TEXT, settings.encryption_key))
    with pytest.raises(ValueError, match="APP_ENCRYPTION_KEY is required before reading"):
        read_cookie_file(path, None)


@pytest.mark.parametrize("engine", ["yt-dlp", "gallery-dl", "you-get"])
@pytest.mark.parametrize("mode", ["encrypted", "legacy", "legacy-no-key"])
@pytest.mark.parametrize("failure", [False, True])
def test_adapters_use_ephemeral_plaintext_and_cleanup(tmp_path, monkeypatch, engine, mode, failure):
    key = Fernet.generate_key().decode("ascii")
    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    stored = cookie_dir / "session.txt"
    stored.write_bytes(encrypt_cookie(COOKIE_TEXT, key) if mode == "encrypted" else COOKIE_TEXT)
    workdir = tmp_path / "work"
    seen: list[Path] = []
    real_spawn = asyncio.create_subprocess_exec
    script = (
        "import pathlib,sys\n"
        "out=pathlib.Path(sys.argv[1])\n"
        "(out/'media.bin').write_bytes(b'media')\n"
        "print('__DL_FILE__'+str(out/'media.bin'), flush=True)\n"
        f"sys.exit({7 if failure else 0})\n"
    )

    async def fake_spawn(*argv, **kwargs):
        cookie_argument = Path(argv[argv.index("--cookies") + 1])
        seen.append(cookie_argument)
        assert cookie_argument != stored
        assert cookie_argument.parent != cookie_dir
        assert cookie_argument.read_bytes() == COOKIE_TEXT
        if os.name != "nt":
            assert stat.S_IMODE(cookie_argument.stat().st_mode) == 0o600
        return await real_spawn(sys.executable, "-c", script, str(workdir), **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    async def noop(_):
        return None

    async def not_cancelled():
        return False

    runners = {"yt-dlp": yt_dlp.run_ytdlp, "gallery-dl": gallery_dl.run_gallery_dl,
               "you-get": you_get.run_you_get}
    async def run():
        return await runners[engine](
            executable="fake-engine", url="https://example.test/media", workdir=workdir,
            global_settings={"cookies_file": "session.txt"}, overrides={}, cookie_dir=cookie_dir,
            encryption_key=None if mode == "legacy-no-key" else key,
            on_progress=noop, on_log=noop, is_cancelled=not_cancelled,
        )

    if failure:
        with pytest.raises(RuntimeError, match="exited with status 7"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == [workdir / "media.bin"]
    assert len(seen) == 1
    assert not seen[0].exists()
    assert list(tmp_path.glob(".downloader-cookie-*")) == []
