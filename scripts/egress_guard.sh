#!/bin/sh
# This process owns the downloader network namespace and installs egress
# restrictions BEFORE Compose starts the downloader. No dynamic allowlist.
set -eu

# Fail closed on missing kernel/netfilter support, permissions or IPv6 policy.
command -v iptables >/dev/null
command -v ip6tables >/dev/null
iptables -w -N DOWNLOADER_EGRESS
iptables -w -I OUTPUT 1 -j DOWNLOADER_EGRESS
ip6tables -w -N DOWNLOADER_EGRESS
ip6tables -w -I OUTPUT 1 -j DOWNLOADER_EGRESS

# Allow response packets for connections already permitted by this policy.
# Without this, localhost replies use ephemeral destination ports and the
# health check/aria2 RPC fail despite their outbound request being allowed.
iptables -w -A DOWNLOADER_EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -w -A DOWNLOADER_EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# The Docker embedded DNS resolver is reachable only on port 53.
iptables -w -A DOWNLOADER_EGRESS -d 127.0.0.11/32 -p udp --dport 53 -j ACCEPT
iptables -w -A DOWNLOADER_EGRESS -d 127.0.0.11/32 -p tcp --dport 53 -j ACCEPT

# The local health endpoint and loopback-only aria2 JSON-RPC are required.
iptables -w -A DOWNLOADER_EGRESS -d 127.0.0.1/32 -p tcp --dport 8080 -j ACCEPT
iptables -w -A DOWNLOADER_EGRESS -d 127.0.0.1/32 -p tcp --dport 16800 -j ACCEPT

# Block local, private, shared, benchmark, documentation, reserved and
# multicast IPv4 space. This applies to EVERY subprocess in the namespace,
# even if a URL redirects or a hostname's DNS answers change.
for cidr in \
  0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 \
  169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 \
  192.0.2.0/24 192.88.99.0/24 192.168.0.0/16 \
  198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 \
  224.0.0.0/4 240.0.0.0/4
do
  iptables -w -A DOWNLOADER_EGRESS -d "$cidr" -j REJECT
done

# Permit IPv6 only to global-unicast space, excluding reserved/documentation
# and transition/tunnel prefixes. An IPv4-mapped IPv6 destination is NOT in
# 2000::/3 and cannot bypass the IPv4 deny rules.
for cidr in \
  2001:db8::/32 2001::/32 2002::/16 2001:10::/28
do
  ip6tables -w -A DOWNLOADER_EGRESS -d "$cidr" -j REJECT
done
ip6tables -w -A DOWNLOADER_EGRESS ! -d 2000::/3 -j REJECT

# Readiness checks inspect both live kernel chains, not merely this process.
iptables -w -C OUTPUT -j DOWNLOADER_EGRESS
ip6tables -w -C OUTPUT -j DOWNLOADER_EGRESS
iptables -w -C DOWNLOADER_EGRESS -d 169.254.0.0/16 -j REJECT
ip6tables -w -C DOWNLOADER_EGRESS ! -d 2000::/3 -j REJECT
echo "Downloader network egress guard installed"
exec sleep infinity
