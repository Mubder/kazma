#!/bin/bash
# Kazma's filtered egress for members' code (Spaces S4-05b): installed in the
# kazma-engine distro by setup (kazma_core/container_engine_setup.py, the
# engine:egress step), run as root. Idempotent.
#
#   bash -s -- [this computer's own addresses, as Windows lists them...]
#
# A container on the kazma-egress network (its bridge is kz-egress) reaches
# the public internet and nothing else. Installed here:
#
# * /usr/local/sbin/kazma-egress-rules, which puts the rules in;
# * kazma-egress.service, which runs it before Docker starts, at every start
#   of the distro (the kernel keeps no rule across one). Docker evaluates the
#   DOCKER-USER chain before its own rules and never changes it; made before
#   Docker starts, it is there before any container can run;
# * /etc/kazma/egress-host-addresses: the addresses Windows has (in WSL's NAT
#   mode the distro cannot see them; a public one would reach this
#   computer's own services).
#
# The rules, for a packet a container on the bridge sends:
# * to this computer (the distro itself, INPUT): refused. Docker's resolver
#   answers inside the container's own namespace, so DNS still works;
# * on to anywhere (FORWARD, DOCKER-USER -> KAZMA-EGRESS): refused to a
#   private range (RFC 1918), the shared space (100.64/10), link-local
#   (169.254/16: the cloud metadata address), loopback, "this network",
#   the documentation, benchmark, IETF, multicast and reserved ranges, this
#   computer's own addresses (the distro's, Windows'), and every broadcast,
#   multicast and local destination; else it goes on (the public internet);
# * IPv6: the network has none, and every IPv6 packet from the bridge is
#   refused, both ways in.
# A refusal is a reset (TCP) or an ICMP unreachable: a connection that may
# not be made fails at once, never by a timeout.
set -euo pipefail

install -d -m 755 /etc/kazma
: > /etc/kazma/egress-host-addresses.new
for addr in "$@"; do
  if [[ "$addr" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    echo "$addr" >> /etc/kazma/egress-host-addresses.new
  fi
done
mv -f /etc/kazma/egress-host-addresses.new /etc/kazma/egress-host-addresses

cat > /usr/local/sbin/kazma-egress-rules <<'RULES'
#!/bin/bash
# kazma-egress-rules [bridge] [chain] [remove] -- see /etc/systemd/system/kazma-egress.service.
set -euo pipefail
IF="${1:-kz-egress}"
CHAIN="${2:-KAZMA-EGRESS}"
MODE="${3:-apply}"
HOSTS="${KAZMA_EGRESS_HOSTS:-/etc/kazma/egress-host-addresses}"
REFUSED_V4="0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12
192.0.0.0/24 192.0.2.0/24 192.88.99.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24
203.0.113.0/24 224.0.0.0/4 240.0.0.0/4"

hooks_off() {
  while iptables -w -C DOCKER-USER -i "$IF" -j "$CHAIN" 2>/dev/null; do
    iptables -w -D DOCKER-USER -i "$IF" -j "$CHAIN"
  done
  while iptables -w -C INPUT -i "$IF" -j REJECT 2>/dev/null; do
    iptables -w -D INPUT -i "$IF" -j REJECT
  done
  for c in DOCKER-USER FORWARD INPUT; do
    while ip6tables -w -C "$c" -i "$IF" -j REJECT 2>/dev/null; do
      ip6tables -w -D "$c" -i "$IF" -j REJECT
    done
  done
}

if [[ "$MODE" == remove ]]; then
  hooks_off
  iptables -w -F "$CHAIN" 2>/dev/null || true
  iptables -w -X "$CHAIN" 2>/dev/null || true
  echo "EGRESS_REMOVED $IF $CHAIN"
  exit 0
fi

iptables -w -N DOCKER-USER 2>/dev/null || true
ip6tables -w -N DOCKER-USER 2>/dev/null || true
iptables -w -N "$CHAIN" 2>/dev/null || iptables -w -F "$CHAIN"
refuse() {
  iptables -w -A "$CHAIN" "$@" -p tcp -j REJECT --reject-with tcp-reset
  iptables -w -A "$CHAIN" "$@" -j REJECT --reject-with icmp-net-prohibited
}
for net in $REFUSED_V4; do
  refuse -d "$net"
done
if [[ -f "$HOSTS" ]]; then
  while IFS= read -r addr; do
    if [[ -n "$addr" ]]; then
      refuse -d "$addr"
    fi
  done < "$HOSTS"
fi
for kind in LOCAL BROADCAST MULTICAST ANYCAST; do
  refuse -m addrtype --dst-type "$kind"
done
iptables -w -A "$CHAIN" -j RETURN

hooks_off
iptables -w -I DOCKER-USER 1 -i "$IF" -j "$CHAIN"
iptables -w -I INPUT 1 -i "$IF" -j REJECT
for c in DOCKER-USER FORWARD INPUT; do
  ip6tables -w -I "$c" 1 -i "$IF" -j REJECT
done
echo "EGRESS_RULES $IF $CHAIN"
RULES
chmod 755 /usr/local/sbin/kazma-egress-rules

cat > /etc/systemd/system/kazma-egress.service <<'UNIT'
[Unit]
Description=Kazma's filtered egress for members' code (the kz-egress bridge reaches the public internet only)
Before=docker.service
After=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/kazma-egress-rules

[Install]
WantedBy=docker.service multi-user.target
UNIT
systemctl daemon-reload
systemctl enable kazma-egress.service >/dev/null 2>&1
/usr/local/sbin/kazma-egress-rules
echo "EGRESS_READY"
