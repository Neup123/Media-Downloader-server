"""Static invariants complement live kernel egress acceptance in GitHub Actions."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_guarded_compose_requires_healthy_firewall_before_downloader_starts():
    config = yaml.safe_load((ROOT / "compose.egress.yaml").read_text())
    guard = config["services"]["egress-guard"]
    downloader = config["services"]["downloader-server"]
    assert "NET_ADMIN" in guard["cap_add"]
    assert "NET_ADMIN" not in downloader.get("cap_add", [])
    assert "ALL" in downloader["cap_drop"]
    assert downloader["network_mode"] == "service:egress-guard"
    assert downloader["depends_on"]["egress-guard"]["condition"] == "service_healthy"
    assert "ports" not in downloader
    assert "ports" in guard
    assert downloader["environment"]["ALLOW_PRIVATE_TARGETS"] == "false"
    assert downloader["environment"]["ALLOW_PRIVATE_WEBDAV_TARGETS"] == "false"
    assert downloader["environment"]["ALLOW_INSECURE_WEBDAV"] == "false"


def test_kernel_rules_cover_metadata_docker_bridge_and_ipv6():
    rules = (ROOT / "scripts/egress_guard.sh").read_text()
    for cidr in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10",
        "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
        "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/4",
        "240.0.0.0/4", "2001:db8::/32",
    ):
        assert cidr in rules
    assert "ip6tables -w -A DOWNLOADER_EGRESS ! -d 2000::/3 -j REJECT" in rules
    assert "iptables -w -I OUTPUT 1 -j DOWNLOADER_EGRESS" in rules
    assert "ip6tables -w -I OUTPUT 1 -j DOWNLOADER_EGRESS" in rules
    assert "iptables -w -A DOWNLOADER_EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT" in rules
    assert "ip6tables -w -A DOWNLOADER_EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT" in rules
    assert "exec sleep infinity" in rules
    assert "127.0.0.11/32 -p udp --dport 53" in rules
    assert "127.0.0.11/32 -p tcp --dport 53" in rules


def test_ci_probes_real_downloader_namespace_and_fails_on_reachable_targets():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/egress-guard-e2e.yml").read_text()
    )
    steps = workflow["jobs"]["kernel-egress"]["steps"]
    script = "\n".join(step.get("run", "") for step in steps)
    assert "up -d --build --wait" in script
    assert "exec -T downloader-server curl" in script
    assert "--resolve" in script
    assert "169.254.169.254" in script
    assert "192.168.0.1" in script
    assert "2001:db8::1" in script
    assert "down --volumes --remove-orphans" in script
