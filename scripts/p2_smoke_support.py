"""Pure validation helpers for opt-in legal P2 acceptance fixtures.

Never embed proprietary URLs or credentials in the repository. This harness
runs only on explicit manual dispatch and does not turn P2 auto-updates on.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from app.security import UnsafeUrlError, validate_target_url

P2_ENGINES = frozenset({"svtplay-dl", "get-sauce"})
JOB_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def validate_site_smoke_source(engine: str, url: str) -> str:
    if engine not in P2_ENGINES:
        raise ValueError("P2 smoke supports only svtplay-dl and get-sauce")
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise ValueError("A public source URL of at most 2048 characters is required")
    parsed = urlsplit(url)
    # GitHub workflow inputs are visible to repository administrators. Refuse
    # query/fragment/credentials to avoid accidentally publishing URL tokens.
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("Use a public test URL without query, fragment or credentials")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Public P2 smoke requires an HTTP(S) source")
    try:
        return validate_target_url(url, engine, allow_private=False)
    except UnsafeUrlError as exc:
        raise ValueError("Public smoke URL violates media source policy") from exc


def relative_webdav_output(path: str, job_id: str) -> str:
    """Match the exact per-job WebDAV path to the completion manifest."""
    if JOB_ID.fullmatch(job_id) is None:
        raise ValueError("Invalid job id")
    parsed = urlsplit(path)
    if parsed.scheme != "http" or parsed.hostname != "nextcloud" or parsed.port is not None:
        raise ValueError("Expected a disposable Nextcloud WebDAV URL")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("WebDAV result has unexpected URL fields")
    decoded = unquote(parsed.path)
    prefix = f"/{job_id}/"
    if decoded.count(prefix) != 1:
        raise ValueError("WebDAV result does not contain the expected job folder")
    relative = decoded.split(prefix, 1)[1]
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or any(p in {".", ".."} for p in parts):
        raise ValueError("Unsafe WebDAV output path")
    return "/".join(parts)


def verify_output_sets(manifest: list[dict], deliveries: list[dict], job_id: str) -> dict[str, dict]:
    source = {}
    for entry in manifest:
        if not isinstance(entry, dict):
            raise ValueError("Invalid completion manifest entry")
        relative = entry.get("relative")
        digest = entry.get("sha256")
        size = entry.get("size")
        if (
            not isinstance(relative, str) or not relative
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
            or not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            or type(size) is not int or size < 0
            or relative in source
        ):
            raise ValueError("Invalid or duplicate completion manifest entry")
        source[relative] = entry
    delivered = {}
    for item in deliveries:
        if not isinstance(item, dict) or item.get("verified") is not True:
            raise ValueError("WebDAV delivery was not marked verified")
        relative = relative_webdav_output(str(item.get("path") or ""), job_id)
        if relative in delivered or relative not in source or item.get("size_bytes") != source[relative]["size"]:
            raise ValueError("Delivered paths or file sizes do not match completed source outputs")
        delivered[relative] = item
    if not source or set(source) != set(delivered):
        raise ValueError("Missing or extra delivered file")
    return source


def build_p2_smoke_attestation(engine: str, manifest: list[dict], commit_sha: str) -> dict:
    """Produce a source-URL-free, credential-free acceptance artifact.

    The digest covers path, byte size and content hash of the validated local
    completion files without including potentially sensitive source filenames.
    This record must be written ONLY after live remote readback, restart and
    re-delivery have all succeeded.
    """
    if engine not in P2_ENGINES:
        raise ValueError("Unsupported P2 engine in acceptance attestation")
    if re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None:
        raise ValueError("Acceptance attestation needs an exact source revision")
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("No P2 files to attest")
    records = []
    for item in manifest:
        if not isinstance(item, dict):
            raise ValueError("Malformed P2 attestation manifest")
        relative, size, digest = item.get("relative"), item.get("size"), item.get("sha256")
        if (
            not isinstance(relative, str) or not relative
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
            or type(size) is not int or size < 0
            or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValueError("Malformed P2 attestation file")
        records.append((relative, size, digest))
    if len(set(item[0] for item in records)) != len(records):
        raise ValueError("Duplicate P2 attestation file")
    compact = json.dumps(sorted(records), separators=(",", ":"), ensure_ascii=True).encode()
    return {
        "schema_version": 1,
        "status": "verified",
        "engine": engine,
        "source_revision": commit_sha,
        "source_url_recorded": False,
        "file_count": len(records),
        "total_verified_bytes": sum(item[1] for item in records),
        "completion_manifest_sha256": hashlib.sha256(compact).hexdigest(),
        "media_parsed_or_checked": sum(item.get("media_verified") is True for item in manifest),
        "durable_after_compose_restart": True,
        "webdav_stream_sha256_verified": True,
        "redelivery_from_same_job_verified": True,
        "production_target_verified": False,
        "automatic_p2_releases_authorized": False,
    }
