"""Initial-host DNS checks before invoking extractors and preview CLI.

These checks do not pin subprocess connections or inspect extracted URLs.
A production egress firewall is mandatory for untrusted submissions.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from app.db import Job, initialize_database, make_engine, make_session_factory
from app.security import UnsafeUrlError, preflight_source_dns
from app.worker import JobWorker


def _dns(*addresses):
    def lookup(host, port, **kwargs):
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET,
             socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
             (address, port))
            for address in addresses
        ]
    return lookup


@pytest.mark.parametrize("url", [
    "https://looks-safe.example/path",
    "http://looks-safe.example/path",
    "ftp://looks-safe.example/pub/file",
    "ftps://looks-safe.example/file",
])
def test_private_dns_answers_are_blocked_for_initial_http_and_ftp(monkeypatch, url):
    monkeypatch.setattr(socket, "getaddrinfo", _dns("1.1.1.1", "169.254.169.254"))
    with pytest.raises(UnsafeUrlError, match="private or reserved"):
        preflight_source_dns(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.10.1.1", "fc00::1", "fe80::123"])
def test_preflight_rejects_single_private_or_reserved_dns_answer(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", _dns(address))
    with pytest.raises(UnsafeUrlError, match="private or reserved"):
        preflight_source_dns("https://public-looking.example/media")


def test_public_and_ipv6_dns_answers_are_accepted(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _dns("1.1.1.1", "2606:4700:4700::1111"))
    preflight_source_dns("https://public.example/video")
    preflight_source_dns("ftp://public.example/file")


def test_unresolvable_ftp_is_rejected_without_leaking_dns_details(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        socket.gaierror("private resolver detail")
    ))
    with pytest.raises(UnsafeUrlError, match="Could not resolve FTP source hostname") as exc:
        preflight_source_dns("ftp://missing.example/file")
    assert "private resolver detail" not in str(exc.value)


def test_magnet_does_not_trigger_http_or_ftp_dns_preflight(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: pytest.fail("Unexpected DNS resolution"))
    preflight_source_dns("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567")


def test_preview_refuses_private_dns_before_starting_yt_dlp(client, auth_headers, monkeypatch):
    def block(_url):
        raise UnsafeUrlError("DNS resolution includes a private or reserved address")

    async def unexpected_preview(*_args, **_kwargs):
        pytest.fail("yt-dlp must not execute after a rejected DNS preflight")

    monkeypatch.setattr("app.main.preflight_source_dns", block)
    monkeypatch.setattr("app.main.inspect_ytdlp", unexpected_preview)
    response = client.post("/api/v1/preview", headers=auth_headers, json={
        "url": "https://public-looking.example/watch?v=1",
    })
    assert response.status_code == 422
    assert "private or reserved" in response.json()["detail"]


def test_worker_fails_before_engine_execution_for_private_dns(settings, monkeypatch):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        job = Job(
            url="https://public-looking.example/media",
            engine="yt-dlp", status="queued",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    def block(_url):
        raise UnsafeUrlError("DNS resolution includes a private or reserved address")

    async def unexpected_engine(**_kwargs):
        pytest.fail("No downloader subprocess should start for private DNS")

    monkeypatch.setattr("app.worker.preflight_source_dns", block)
    monkeypatch.setattr("app.worker.run_ytdlp", unexpected_engine)
    try:
        asyncio.run(JobWorker(sessions, settings)._execute(job_id))
        with sessions() as session:
            current = session.get(Job, job_id)
            assert current.status == "failed"
            assert "private or reserved" in current.error
            assert not current.output_files
    finally:
        engine.dispose()
