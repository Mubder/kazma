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
#   puts the rules in again before it starts (ExecStartPre), and once it has
#   started kazma-egress-guard takes the kazma-egress network away when they
#   are not in effect (ExecStartPost). Rules that cannot be put in cost the
#   members' network, never Docker: SearXNG, Neo4j and the document worker
#   start with it or without it;
# * `kazma-egress-rules [bridge] [chain] check`, which Kazma runs before every
#   run of a member's code on the network: each jump the first rule the
#   bridge's packets meet, and the chain exactly the one made here, or the
#   run is refused, naming the rule in front (rules flushed or put in front
#   later -- another distro sharing the network namespace, `iptables -F`, a
#   firewall backend change -- are found before code runs, not after);
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
#   refused, both ways in. A kernel with no IPv6 carries none: nothing is put
#   in for it, and its missing ip6tables fails nothing.
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
INET6="${KAZMA_EGRESS_INET6:-/proc/net/if_inet6}"
# As iptables -S prints them: a rule is put in and found by the same text.
REFUSE_V4="-i $IF -j REJECT --reject-with icmp-port-unreachable"
REFUSE_V6="-i $IF -j REJECT --reject-with icmp6-port-unreachable"
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
  # shellcheck disable=SC2086
  while iptables -w -C INPUT $REFUSE_V4 2>/dev/null; do
    iptables -w -D INPUT $REFUSE_V4
  done
  if ipv6; then
    for c in DOCKER-USER FORWARD INPUT; do
      # shellcheck disable=SC2086
      while ip6tables -w -C "$c" $REFUSE_V6 2>/dev/null; do
        ip6tables -w -D "$c" $REFUSE_V6
      done
    done
  fi
}

ipv6() {  # whether the kernel carries IPv6 at all
  [[ -e "$INET6" ]]
}

another_interface() {  # another_interface RULE: a rule for another interface, by its whole name
  # -s, -d, -i and -o come first in what iptables prints, before any match:
  # nothing after them is read -- a comment may say anything at all.
  local -a words
  local at=0 not=""
  read -r -a words <<< "$1"
  while (( at < ${#words[@]} )); do
    case "${words[at]}" in
      '!') not=1 at=$((at + 1)) ;;
      -s|-d|-o) not="" at=$((at + 2)) ;;
      -i) [[ -z "$not" && "${words[at + 1]:-}" != "$IF" && "${words[at + 1]:-}" != *+ ]]; return ;;
      *) return 1 ;;
    esac
  done
  return 1
}

rules_of() {  # rules_of TOOL CHAIN: CHAIN's rules, one a line; a rule with no condition is an empty line
  local line
  while IFS= read -r line; do
    if [[ "$line" == "-A $2" || "$line" == "-A $2 "* ]]; then
      line="${line#-A "$2"}"
      echo "${line# }"
    fi
  done < <("$1" -w -S "$2" 2>/dev/null)
}

first_for() {  # first_for TOOL CHAIN: the first rule of CHAIN the bridge's packets meet
  local line
  while IFS= read -r line; do
    if ! another_interface "$line"; then
      echo "${line:-a rule with no condition}"
      return 0
    fi
  done < <(rules_of "$1" "$2")
  return 0
}

put_first() {  # put_first TOOL CHAIN RULE: RULE the first the bridge's packets meet, once
  local tool="$1" chain="$2" rule="$3" at last
  if [[ "$(first_for "$tool" "$chain")" == "$rule" ]]; then
    return 0
  fi
  # In front before any later copy goes: never a moment without it.
  # shellcheck disable=SC2086
  "$tool" -w -I "$chain" 1 $rule
  while true; do
    at=0 last=0
    while IFS= read -r line; do
      at=$((at + 1))
      if [[ "$line" == "$rule" ]]; then
        last=$at
      fi
    done < <(rules_of "$tool" "$chain")
    (( last > 1 )) || break
    "$tool" -w -D "$chain" "$last"
  done
}

hosts() {
  if [[ -f "$HOSTS" ]]; then
    while IFS= read -r addr; do
      if [[ -n "$addr" ]]; then
        echo "$addr/32"
      fi
    done < "$HOSTS"
  fi
}

chain_rules() {  # the chain's rules, one a line, as iptables -S prints them
  local net kind
  for net in $REFUSED_V4 $(hosts); do
    echo "-d $net -p tcp -j REJECT --reject-with tcp-reset"
    echo "-d $net -j REJECT --reject-with icmp-net-prohibited"
  done
  for kind in LOCAL BROADCAST MULTICAST ANYCAST; do
    echo "-p tcp -m addrtype --dst-type $kind -j REJECT --reject-with tcp-reset"
    echo "-m addrtype --dst-type $kind -j REJECT --reject-with icmp-net-prohibited"
  done
  echo "-j RETURN"
}

if [[ "$MODE" == remove ]]; then
  hooks_off
  drop_chain "$NEW"
  drop_chain "$CHAIN"
  echo "EGRESS_REMOVED $IF $CHAIN"
  exit 0
fi

if [[ "$MODE" == check ]]; then
  # Each jump the first rule the bridge's packets meet -- a rule in front of
  # it would let them past -- and the chain exactly the one made here; else
  # exit 3 naming what is not and the rule in front (Kazma then refuses the
  # run).
  missing=() front=()
  must_be_first() {  # must_be_first NAME TOOL CHAIN RULE [OR RULE]
    local met
    met="$(first_for "$2" "$3")"
    if [[ "$met" != "$4" && ( -z "${5:-}" || "$met" != "${5:-}" ) ]]; then
      missing+=("$1")
      [[ -z "$met" ]] || front+=("$1: $met")
    fi
  }
  must_be_first "FORWARD->DOCKER-USER" iptables FORWARD "-j DOCKER-USER"
  must_be_first "DOCKER-USER->$CHAIN" iptables DOCKER-USER "-i $IF -j $CHAIN"
  must_be_first INPUT iptables INPUT "$REFUSE_V4"
  if ipv6; then
    must_be_first ip6:DOCKER-USER ip6tables DOCKER-USER "$REFUSE_V6"
    # Docker puts its own jump to DOCKER-USER in front of FORWARD's.
    must_be_first ip6:FORWARD ip6tables FORWARD "$REFUSE_V6" "-j DOCKER-USER"
    must_be_first ip6:INPUT ip6tables INPUT "$REFUSE_V6"
  fi
  want="$(echo "-N $CHAIN"; while IFS= read -r rule; do echo "-A $CHAIN $rule"; done < <(chain_rules))"
  [[ "$(iptables -w -S "$CHAIN" 2>/dev/null)" == "$want" ]] || missing+=("$CHAIN")
  if (( ${#missing[@]} )); then
    echo "EGRESS_NOT_IN_EFFECT ${missing[*]}"
    for met in ${front[@]+"${front[@]}"}; do
      echo "EGRESS_IN_FRONT $met"
    done
    exit 3
  fi
  echo "EGRESS_IN_EFFECT $IF $CHAIN"
  exit 0
fi

iptables -w -N DOCKER-USER 2>/dev/null || true
if ipv6; then
  ip6tables -w -N DOCKER-USER 2>/dev/null || true
fi
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
while IFS= read -r rule; do
  # shellcheck disable=SC2086
  iptables -w -A "$NEW" $rule
done < <(chain_rules)
iptables -w -I DOCKER-USER 1 -i "$IF" -j "$NEW"
drop_chain "$CHAIN"
iptables -w -E "$NEW" "$CHAIN"
# The fixed refusals: in front, never taken out first.
put_first iptables INPUT "$REFUSE_V4"
if ipv6; then
  for c in DOCKER-USER FORWARD INPUT; do
    put_first ip6tables "$c" "$REFUSE_V6"
  done
fi
echo "EGRESS_RULES $IF $CHAIN"
RULES
chmod 755 /usr/local/sbin/kazma-egress-rules

cat > /usr/local/sbin/kazma-egress-guard <<'GUARD'
#!/bin/bash
# After Docker starts: rules not in effect take the members' network away --
# nothing can run on it -- and leave Docker running. `kazma setup engine`
# puts both back.
set -uo pipefail
NET="${1:-kazma-egress}"
if /usr/local/sbin/kazma-egress-rules kz-egress KAZMA-EGRESS check; then
  exit 0
fi
said="$(timeout 30 docker network rm "$NET" 2>&1)"
code=$?
if (( code == 0 )); then
  echo "EGRESS_NETWORK_REMOVED $NET"
  exit 0
fi
# Not removed: never there (the usual case, nobody given the network), or
# still there, or Docker cannot say.
if held="$(timeout 30 docker network ls --quiet --filter "name=^${NET}\$" 2>&1)"; then
  if [[ -z "$held" ]]; then
    echo "EGRESS_NETWORK_NOT_THERE $NET"
  else
    echo "EGRESS_NETWORK_STILL_THERE $NET: docker network rm answered $code: $said"
  fi
else
  echo "EGRESS_NETWORK_UNKNOWN $NET: docker network rm answered $code: $said"
fi
exit 0
GUARD
chmod 755 /usr/local/sbin/kazma-egress-guard

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
# Docker puts the rules in again each time it starts; rules that cannot be
# put in never stop it (the "-"): they take the network away once it runs.
install -d -m 755 /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/kazma-egress.conf <<'DROPIN'
[Unit]
After=kazma-egress.service

[Service]
ExecStartPre=-/usr/local/sbin/kazma-egress-rules
ExecStartPost=-/usr/local/sbin/kazma-egress-guard
DROPIN
systemctl daemon-reload
systemctl enable kazma-egress.service >/dev/null 2>&1
/usr/local/sbin/kazma-egress-rules
echo "EGRESS_READY"
