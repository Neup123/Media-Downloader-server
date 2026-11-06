#!/usr/bin/env python3
"""Read-only negative egress acceptance against an ALREADY RUNNING guarded core.

Run on the Docker host (including WSL) after starting compose.egress.yaml.
No credentials, download URLs or app passwords are printed. The script
connects only to localhost and reserved/blocked addresses; it performs no
state-changing core API requests, and never starts or stops containers.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


class AcceptanceError(RuntimeError):
    pass


def reject_packets(table: str, cidr: str) -> int:
    """Extract the actual IPv4 REJECT rule packet counter from iptables -nvxL."""
    for line in table.splitlines():
        columns = line.split()
        # pkts bytes target prot opt in out source destination ...
        if len(columns) >= 9 and columns[2] == "REJECT" and columns[8] == cidr:
            return int(columns[0])
    raise AcceptanceError("Missing REJECT counter for required destination policy")


def run_on_host(compose: list[str], args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        compose + args, capture_output=True, text=True, check=False,
        timeout=30,
    )


def require_ok(compose: list[str], args: list[str], label: str) -> str:
    result = run_on_host(compose, args)
    if result.returncode:
        # Do not echo commands or stderr, which may contain host details.
        raise AcceptanceError(f"{label}: failed (see local Docker logs)")
    return result.stdout.strip()


def reject_probe(compose: list[str], target: str, *, redirect: bool = False) -> None:
    curl = [
        "exec", "-T", "downloader-server", "curl",
        "--noproxy", "*", "--silent", "--output", "/dev/null",
        "--connect-timeout", "2", "--max-time", "5",
    ]
    if redirect:
        curl += [
            "--location", "--header", f"Host: {target}",
            "http://127.0.0.1:8080/healthz/",
        ]
    else:
        curl += [
            "--resolve", f"probe.invalid:80:{target}",
            "http://probe.invalid/",
        ]
    result = run_on_host(compose, curl)
    if result.returncode == 0:
        raise AcceptanceError("A forbidden network destination was reachable")


def verify(compose: list[str]) -> dict[str, object]:
    require_ok(compose, ["exec", "-T", "egress-guard", "iptables",
                         "-w", "-C", "OUTPUT", "-j", "DOWNLOADER_EGRESS"],
               "IPv4 kernel chain")
    require_ok(compose, ["exec", "-T", "egress-guard", "ip6tables",
                         "-w", "-C", "OUTPUT", "-j", "DOWNLOADER_EGRESS"],
               "IPv6 kernel chain")
    require_ok(compose, ["exec", "-T", "egress-guard", "ip6tables",
                         "-w", "-C", "DOWNLOADER_EGRESS", "!", "-d", "2000::/3",
                         "-j", "REJECT"], "IPv6 non-global destination rule")
    require_ok(compose, [
        "exec", "-T", "downloader-server", "curl",
        "--fail", "--silent", "--noproxy", "*",
        "http://127.0.0.1:8080/healthz",
    ], "Core health inside guarded namespace")

    table_command = [
        "exec", "-T", "egress-guard", "iptables",
        "-w", "-n", "-v", "-x", "-L", "DOWNLOADER_EGRESS",
    ]
    before = require_ok(compose, table_command, "IPv4 rule counters")
    metadata_before = reject_packets(before, "169.254.0.0/16")
    loopback_before = reject_packets(before, "127.0.0.0/8")

    reject_probe(compose, "169.254.169.254")
    reject_probe(compose, "127.0.0.2")
    after = require_ok(compose, table_command, "IPv4 rule counters")
    if reject_packets(after, "169.254.0.0/16") <= metadata_before:
        raise AcceptanceError("Metadata probe did not hit the kernel REJECT rule")
    if reject_packets(after, "127.0.0.0/8") <= loopback_before:
        raise AcceptanceError("Loopback probe did not hit the kernel REJECT rule")

    # Actual API 307 redirect, then follow it from the downloader namespace.
    target = "169.254.169.254"
    location = require_ok(compose, [
        "exec", "-T", "downloader-server", "curl",
        "--noproxy", "*", "--silent", "--show-error",
        "--output", "/dev/null", "--max-time", "8",
        "--header", f"Host: {target}", "--write-out", "%{redirect_url}",
        "http://127.0.0.1:8080/healthz/",
    ], "Controlled local redirect")
    if location != f"http://{target}/healthz":
        raise AcceptanceError("Controlled redirect was not emitted as expected")
    redirect_before = reject_packets(after, "169.254.0.0/16")
    reject_probe(compose, target, redirect=True)
    redirected = require_ok(compose, table_command, "IPv4 rule counters")
    if reject_packets(redirected, "169.254.0.0/16") <= redirect_before:
        raise AcceptanceError("Redirect did not reach the kernel REJECT policy")

    for target6 in ("[::1]", "[fc00::1]", "[fe80::1]", "[2001:db8::1]"):
        denied = run_on_host(compose, [
            "exec", "-T", "downloader-server", "curl",
            "--noproxy", "*", "--silent", "--output", "/dev/null",
            "--connect-timeout", "2", "--max-time", "4",
            f"http://{target6}/",
        ])
        if denied.returncode == 0:
            raise AcceptanceError("Forbidden IPv6 address was reachable")

    return {
        "guarded_namespace_health": "passed",
        "ipv4_and_ipv6_chains": "present",
        "private_dns_override": "blocked_by_ipv4_reject_counter",
        "metadata_destination": "blocked_by_ipv4_reject_counter",
        "loopback_destination": "blocked_by_ipv4_reject_counter",
        "actual_http_redirect": "blocked_by_ipv4_reject_counter",
        "ipv6_reserved_probe": "denied_or_no_route",
        "production_approved": False,
        "note": "This verifies the current host kernel policy, not end-to-end WebDAV, every public engine, or remote Nextcloud.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", action="append", default=[],
                        help="Compose files in their original order; repeat to add an overlay")
    parser.add_argument("--env-file", type=Path,
                        help="Optional Compose env file; never printed")
    args = parser.parse_args(argv)
    files = args.compose_file or ["compose.egress.yaml"]
    compose = ["docker", "compose"]
    if args.env_file:
        compose += ["--env-file", str(args.env_file)]
    for file in files:
        compose += ["-f", file]
    try:
        print(json.dumps(verify(compose), sort_keys=True, indent=2))
    except (AcceptanceError, OSError, subprocess.TimeoutExpired) as exc:
        # Only the failure class, never any Docker subprocess stdout/stderr.
        print(json.dumps({
            "production_approved": False, "acceptance": "failed",
            "reason": str(exc) if isinstance(exc, AcceptanceError) else type(exc).__name__,
        }, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
