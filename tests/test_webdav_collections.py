"""A WebDAV MKCOL 405 does not prove an existing resource is a directory."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet

from app.db import DeliverySetting, initialize_database, make_engine, make_session_factory
from app.delivery import DeliveryError, _webdav_is_collection, deliver_files


def _dav_response(href: str, *, collection: bool = True, status: int = 200) -> bytes:
    resource = "<d:collection/>" if collection else ""
    return (
        "<d:multistatus xmlns:d='DAV:'><d:response>"
        f"<d:href>{href}</d:href><d:propstat><d:prop>"
        f"<d:resourcetype>{resource}</d:resourcetype>"
        f"</d:prop><d:status>HTTP/1.1 {status} Status</d:status>"
        "</d:propstat></d:response></d:multistatus>"
    ).encode()


def _duplicate_dav_response(payload: bytes) -> bytes:
    """Repeat one response inside the same HTTP 207 Multi-Status body."""
    response = payload.split(b"<d:response>", 1)[1].split(b"</d:response>", 1)[0]
    return payload.replace(
        b"</d:multistatus>",
        b"<d:response>" + response + b"</d:response></d:multistatus>",
    )


def _configured_delivery(settings):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    encrypted = Fernet(settings.encryption_key.encode()).encrypt(b"test-secret").decode()
    with sessions() as session:
        session.add(DeliverySetting(
            id=1, mode="webdav",
            webdav_url="https://cloud.example.test/remote.php/dav/files/u",
            username="u", password_encrypted=encrypted,
            folder="Downloader",
        ))
        session.commit()
    source = settings.data_dir / "jobs" / "job-405" / "album" / "file.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"the complete media")
    return engine, sessions, source


def test_collection_property_requires_matching_resource_and_successful_propstat():
    url = "https://cloud.example.test/dav/Folder"
    assert _webdav_is_collection(_dav_response("/dav/Folder/"), url)
    assert not _webdav_is_collection(_duplicate_dav_response(_dav_response("/dav/Folder/")), url)
    unrelated = _dav_response("/dav/Other/", status=404)
    unrelated_body = unrelated.split(b"<d:response>", 1)[1].split(b"</d:response>", 1)[0]
    with_unrelated_failure = _dav_response("/dav/Folder/").replace(
        b"</d:multistatus>",
        b"<d:response>" + unrelated_body + b"</d:response></d:multistatus>",
    )
    assert _webdav_is_collection(with_unrelated_failure, url)
    assert not _webdav_is_collection(_dav_response("/dav/Folder/", collection=False), url)
    assert not _webdav_is_collection(_dav_response("/dav/Folder/", status=404), url)
    assert not _webdav_is_collection(_dav_response("/dav/Other/"), url)
    assert not _webdav_is_collection(_dav_response("https://evil.example/dav/Folder/"), url)
    assert not _webdav_is_collection(b"not valid XML", url)
    assert not _webdav_is_collection(b"x" * 1_000_001, url)


def test_existing_webdav_folders_are_verified_before_upload(settings):
    engine, sessions, source = _configured_delivery(settings)
    folders = []
    uploads = {}
    verified_folders = []

    async def handler(request):
        path = request.url.path
        if request.method == "MKCOL":
            folders.append(path)
            return httpx.Response(405)
        if request.method == "PROPFIND" and path.endswith("/"):
            verified_folders.append(path)
            return httpx.Response(207, content=_dav_response(path))
        if request.method == "PUT":
            uploads[path] = await request.aread()
            return httpx.Response(201)
        if request.method == "PROPFIND":
            assert path in uploads
            return httpx.Response(207, content=(
                "<d:multistatus xmlns:d='DAV:'><d:response>"
                f"<d:href>{path}</d:href><d:propstat><d:prop>"
                f"<d:getcontentlength>{len(uploads[path])}</d:getcontentlength>"
                "</d:prop><d:status>HTTP/1.1 200 OK</d:status>"
                "</d:propstat></d:response></d:multistatus>"
            ))
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    try:
        result = asyncio.run(deliver_files(
            [source], "job-405", sessions, settings,
            transport=httpx.MockTransport(handler),
        ))
        assert len(folders) == 3
        assert verified_folders == [folder + "/" for folder in folders]
        assert uploads == {"/remote.php/dav/files/u/Downloader/job-405/album/file.mp4": source.read_bytes()}
        assert len(result) == 1 and result[0]["verified"] is True
    finally:
        engine.dispose()


@pytest.mark.parametrize("mode", ["not-collection", "property-404", "wrong-href", "bad-xml", "propfind-404", "duplicate-success"])
def test_ambiguous_mkcol_405_never_authorizes_upload(settings, mode):
    engine, sessions, source = _configured_delivery(settings)
    seen = []

    async def handler(request):
        seen.append(request.method)
        if request.method == "MKCOL":
            return httpx.Response(405)
        if request.method == "PROPFIND":
            if mode == "propfind-404":
                return httpx.Response(404)
            if mode == "bad-xml":
                return httpx.Response(207, content=b"<not-xml")
            if mode == "duplicate-success":
                return httpx.Response(
                    207, content=_duplicate_dav_response(_dav_response(request.url.path)),
                )
            return httpx.Response(
                207, content=_dav_response(
                    "/other-resource/" if mode == "wrong-href" else request.url.path,
                    collection=mode != "not-collection",
                    status=404 if mode == "property-404" else 200,
                ),
            )
        raise AssertionError("A resource that is not a collection must not be uploaded to")

    try:
        with pytest.raises(DeliveryError, match="not a verified collection"):
            asyncio.run(deliver_files(
                [source], "job-405", sessions, settings,
                transport=httpx.MockTransport(handler),
            ))
        assert seen == ["MKCOL", "PROPFIND"]
    finally:
        engine.dispose()
