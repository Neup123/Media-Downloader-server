"""Pure test doubles for the WSL/Linux guarded-host probe."""
import subprocess
from unittest.mock import patch

import pytest

from scripts.guarded_host_acceptance import (
    AcceptanceError, reject_packets, reject_probe, verify,
)

TABLE_HEAD = (
    "Chain DOWNLOADER_EGRESS (1 references)\n"
    " pkts bytes target prot opt in out source destination\n"
)


def table(meta=0, loop=0):
    return TABLE_HEAD + (
        f" {meta} {meta*50} REJECT all -- * * 0.0.0.0/0 169.254.0.0/16 reject-with icmp-port-unreachable\n"
        f" {loop} {loop*50} REJECT all -- * * 0.0.0.0/0 127.0.0.0/8 reject-with icmp-port-unreachable\n"
    )


def test_reject_packet_counters_are_matched_by_exact_destination():
    assert reject_packets(table(meta=7), "169.254.0.0/16") == 7
    assert reject_packets(table(loop=3), "127.0.0.0/8") == 3
    with pytest.raises(AcceptanceError):
        reject_packets(table(), "192.168.0.0/16")


def test_private_probe_must_fail_closed_if_curl_succeeds():
    with patch("scripts.guarded_host_acceptance.run_on_host",
               return_value=subprocess.CompletedProcess([], 0, "")):
        with pytest.raises(AcceptanceError, match="forbidden"):
            reject_probe(["docker", "compose"], "169.254.169.254")


def test_full_probe_requires_kernel_counter_confirmation():
    tables = iter([table(), table(meta=1, loop=1), table(meta=2, loop=1)])
    seen = []

    def fake_run(compose, args):
        seen.append(args)
        if "-L" in args:
            return subprocess.CompletedProcess(args, 0, next(tables))
        if "--write-out" in args:
            return subprocess.CompletedProcess(args, 0, "http://169.254.169.254/healthz")
        if "curl" in args and "http://127.0.0.1:8080/healthz" not in args:
            return subprocess.CompletedProcess(args, 7, "")
        return subprocess.CompletedProcess(args, 0, "ok")

    with patch("scripts.guarded_host_acceptance.run_on_host", side_effect=fake_run):
        report = verify(["docker", "compose"])
    assert report["metadata_destination"] == "blocked_by_ipv4_reject_counter"
    assert report["actual_http_redirect"] == "blocked_by_ipv4_reject_counter"
    assert report["production_approved"] is False
    assert any("--resolve" in args for args in seen)
    assert any("--location" in args for args in seen)
    assert sum("ip6tables" in args for args in seen) >= 2


def test_curl_error_alone_is_not_accepted_without_kernel_packet_count():
    def fake_run(_compose, args):
        if "-L" in args:
            return subprocess.CompletedProcess(args, 0, table())
        if "curl" in args and "http://127.0.0.1:8080/healthz" not in args:
            return subprocess.CompletedProcess(args, 7, "")
        return subprocess.CompletedProcess(args, 0, "ok")

    with patch("scripts.guarded_host_acceptance.run_on_host", side_effect=fake_run):
        with pytest.raises(AcceptanceError, match="did not hit the kernel REJECT"):
            verify(["docker", "compose"])
