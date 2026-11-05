"""A multi-status 207 containing an unrelated size is not proof of delivery."""
import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet

from app.db import DeliverySetting, initialize_database, make_engine, make_session_factory
from app.delivery import DeliveryError, deliver_files


def test_webdav_upload_rejects_unrelated_success(settings):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    source = settings.data_dir / "jobs" / "verify-job" / "image.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"payload")
    secret = Fernet(settings.encryption_key.encode("ascii")).encrypt(b"secret").decode("ascii")
    with sessions() as session:
        session.add(DeliverySetting(id=1, mode="webdav",
            webdav_url="https://cloud.example.test/remote.php/dav/files/u",
            username="u", password_encrypted=secret, folder="Downloader"))
        session.commit()
    requests = []

    async def handler(request):
        requests.append(request.method)
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            assert await request.aread() == b"payload"
            return httpx.Response(201)
        if request.method == "PROPFIND":
            href = request.url.path
            xml = (
                "<d:multistatus xmlns:d='DAV:'>"
                "<d:response><d:href>" + href + "-other</d:href><d:propstat>"
                "<d:prop><d:getcontentlength>7</d:getcontentlength></d:prop>"
                "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                "<d:response><d:href>" + href + "</d:href><d:propstat>"
                "<d:prop><d:getcontentlength>7</d:getcontentlength></d:prop>"
                "<d:status>HTTP/1.1 404 Not Found</d:status></d:propstat></d:response>"
                "</d:multistatus>"
            )
            return httpx.Response(207, content=xml)
        return httpx.Response(405)

    try:
        with pytest.raises(DeliveryError, match="size verification failed"):
            asyncio.run(deliver_files([source], "verify-job", sessions, settings,
                transport=httpx.MockTransport(handler)))
        assert requests[-2:] == ["PUT", "PROPFIND"]
    finally:
        engine.dispose()
