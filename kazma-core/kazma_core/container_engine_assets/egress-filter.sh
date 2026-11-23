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
# * a drop-in for docker.service (docker.service.d/kazma-egress.conf): Docker
#   requires the service, and puts the rules in again before it starts
#   (ExecStartPre). Rules that cannot be put in stop Docker starting: never a
#   Docker whose kz-egress bridge is unfiltered;
# * `kazma-egress-rules [bridge] [chain] check`, which Kazma runs before every
#   run of a member's code on the network: every jump and the chain's
#   refusals there, or the run is refused (rules flushed later -- another
#   distro sharing the network namespace, `iptables -F`, a firewall backend
#   change -- are found before code runs, not after);
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
# kazma-egress-rules [bridge] [chain] [apply|check|remove] -- see /etc/systemd/system/kazma-egress.service.
set -euo pipefail
IF="${1:-kz-egress}"
CHAIN="${2:-KAZMA-EGRESS}"
MODE="${3:-apply}"
NEW="$CHAIN-NEW"
HOSTS="${KAZMA_EGRESS_HOSTS:-/etc/kazma/egress-host-addresses}"
REFUSED_V4="0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12
192.0.0.0/24 192.0.2.0/24 192.88.99.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24
203.0.113.0/24 224.0.0.0/4 240.0.0.0/4"

jumps_off() {  # jumps_off CHAIN: every jump from DOCKER-USER to CHAIN out
  while iptables -w -C DOCKER-USER -i "$IF" -j "$1" 2>/dev/null; do
    iptables -w -D DOCKER-USER -i "$IF" -j "$1"
  done
}

drop_chain() {  # drop_chain CHAIN: its jumps, its rules, the chain
  jumps_off "$1"
  iptables -w -F "$1" 2>/dev/null || true
  iptables -w -X "$1" 2>/dev/null || true
}

hooks_off() {
  jumps_off "$CHAIN"
  while iptables -w -C INPUT -i "$IF" -j REJECT 2>/dev/null; do
    iptables -w -D INPUT -i "$IF" -j REJECT
  done
  for c in DOCKER-USER FORWARD INPUT; do
    while ip6tables -w -C "$c" -i "$IF" -j REJECT 2>/dev/null; do
      ip6tables -w -D "$c" -i "$IF" -j REJECT
    done
  done
}

hosts() {
  if [[ -f "$HOSTS" ]]; then
    while IFS= read -r addr; do
      if [[ -n "$addr" ]]; then
        echo "$addr"
      fi
    done < "$HOSTS"
  fi
}

if [[ "$MODE" == remove ]]; then
  hooks_off
  drop_chain "$NEW"
  drop_chain "$CHAIN"
  echo "EGRESS_REMOVED $IF $CHAIN"
  exit 0
fi

if [[ "$MODE" == check ]]; then
  # Every jump, and the chain's refusals, in effect now; else exit 3 naming
  # what is missing (Kazma then refuses the run).
  missing=()
  iptables -w -C FORWARD -j DOCKER-USER 2>/dev/null || missing+=("FORWARD->DOCKER-USER")
  iptables -w -C DOCKER-USER -i "$IF" -j "$CHAIN" 2>/dev/null || missing+=("DOCKER-USER->$CHAIN")
  iptables -w -C INPUT -i "$IF" -j REJECT 2>/dev/null || missing+=("INPUT")
  for c in DOCKER-USER FORWARD INPUT; do
    ip6tables -w -C "$c" -i "$IF" -j REJECT 2>/dev/null || missing+=("ip6:$c")
  done
  for net in $REFUSED_V4 $(hosts); do
    iptables -w -C "$CHAIN" -d "$net" -p tcp -j REJECT --reject-with tcp-reset 2>/dev/null \
      || missing+=("$CHAIN:$net")
  done
  iptables -w -C "$CHAIN" -m addrtype --dst-type LOCAL -j REJECT --reject-with icmp-net-prohibited 2>/dev/null \
    || missing+=("$CHAIN:LOCAL")
  if (( ${#missing[@]} )); then
    echo "EGRESS_NOT_IN_EFFECT ${missing[*]}"
    exit 3
  fi
  echo "EGRESS_IN_EFFECT $IF $CHAIN"
  exit 0
fi

iptables -w -N DOCKER-USER 2>/dev/null || true
ip6tables -w -N DOCKER-USER 2>/dev/null || true
# A run that stopped half way: its new chain, adopted when the old is gone.
if iptables -w -S "$NEW" >/dev/null 2>&1; then
  if iptables -w -S "$CHAIN" >/dev/null 2>&1; then
    drop_chain "$NEW"
  else
    iptables -w -E "$NEW" "$CHAIN"
  fi
fi
# The new rules are built under a name of their own and swapped in: the
# bridge's packets are filtered by the old chain until the new one's jump is
# first, never by none.
iptables -w -N "$NEW"
refuse() {
  iptables -w -A "$NEW" "$@" -p tcp -j REJECT --reject-with tcp-reset
  iptables -w -A "$NEW" "$@" -j REJECT --reject-with icmp-net-prohibited
}
for net in $REFUSED_V4 $(hosts); do
  refuse -d "$net"
done
for kind in LOCAL BROADCAST MULTICAST ANYCAST; do
  refuse -m addrtype --dst-type "$kind"
done
iptables -w -A "$NEW" -j RETURN
iptables -w -I DOCKER-USER 1 -i "$IF" -j "$NEW"
drop_chain "$CHAIN"
iptables -w -E "$NEW" "$CHAIN"
# The fixed refusals: put in when missing, never taken out first.
iptables -w -C INPUT -i "$IF" -j REJECT 2>/dev/null || iptables -w -I INPUT 1 -i "$IF" -j REJECT
for c in DOCKER-USER FORWARD INPUT; do
  ip6tables -w -C "$c" -i "$IF" -j REJECT 2>/dev/null || ip6tables -w -I "$c" 1 -i "$IF" -j REJECT
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
# Docker requires the rules: it does not start without them, and puts them in
# again each time it starts.
install -d -m 755 /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/kazma-egress.conf <<'DROPIN'
[Unit]
Requires=kazma-egress.service
After=kazma-egress.service

[Service]
ExecStartPre=/usr/local/sbin/kazma-egress-rules
DROPIN
systemctl daemon-reload
systemctl enable kazma-egress.service >/dev/null 2>&1
/usr/local/sbin/kazma-egress-rules
echo "EGRESS_READY"
