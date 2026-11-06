"""Nextcloud source provenance must bind stable file ID to its owner."""
import asyncio
from pathlib import Path
from xml.etree import ElementTree

import httpx

from app.delivery import _nextcloud_file_id


def dav_xml(url_path: str, *, file_id="12345", status="200 OK") -> bytes:
    return (f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
<d:response><d:href>{url_path}</d:href>
<d:propstat><d:prop><d:getcontentlength>42</d:getcontentlength>
<oc:fileid>{file_id}</oc:fileid></d:prop>
<d:status>HTTP/1.1 {status}</d:status></d:propstat></d:response>
</d:multistatus>""").encode()


def test_file_id_is_from_correct_resource_and_success_propstat():
    target = "https://cloud.test/remote.php/dav/files/alice/Downloads/job-a/test.mp4"
    assert _nextcloud_file_id(dav_xml("/remote.php/dav/files/alice/Downloads/job-a/test.mp4"), target) == "12345"
    assert _nextcloud_file_id(dav_xml("/remote.php/dav/files/bob/Downloads/job-a/test.mp4"), target) is None
    assert _nextcloud_file_id(dav_xml("/remote.php/dav/files/alice/Downloads/job-a/test.mp4", status="404 Not Found"), target) is None
    assert _nextcloud_file_id(dav_xml("/remote.php/dav/files/alice/Downloads/job-a/test.mp4", file_id="../evil"), target) is None


def test_provenance_endpoint_owner_isolation(client, auth_headers):
    from app.db import Job
    jobs = [
        Job(id="10000000-0000-0000-0000-000000000001", url="https://origin.test/alice",
            engine="generic", owner_id="alice", status="completed",
            overrides={}, progress={}, log_text="",
            output_files=[{"verified": True, "file_id": "12345", "filename": "alice.mp4"}]),
        Job(id="10000000-0000-0000-0000-000000000002", url="https://origin.test/bob",
            engine="generic", owner_id="bob", status="completed",
            overrides={}, progress={}, log_text="",
            output_files=[{"verified": True, "file_id": "12345", "filename": "bob.mp4"}]),
    ]
    with client.app.state.session_factory() as session:
        session.add_all(jobs)
        session.commit()
    for owner in ("alice", "bob"):
        response = client.get("/api/v1/provenance",
            params={"owner_id": owner, "file_id": "12345"}, headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["source_url"] == f"https://origin.test/{owner}"
        assert response.json()["owner_id"] == owner
    assert client.get("/api/v1/provenance", params={"owner_id": "eve", "file_id": "12345"},
                      headers=auth_headers).status_code == 404
    assert client.get("/api/v1/provenance", params={"owner_id": "alice", "file_id": "../bad"},
                      headers=auth_headers).status_code == 422
    assert client.get("/api/v1/provenance", params={"owner_id": "alice", "file_id": "12345"}).status_code == 401
