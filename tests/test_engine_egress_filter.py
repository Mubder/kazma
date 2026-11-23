"""The rules that hold members' code to the public internet (Spaces S4-05b,
``container_engine_assets/egress-filter.sh``), run by bash.

The rules script the installer puts in the distro
(``/usr/local/sbin/kazma-egress-rules``) runs here against stand-in
``iptables`` and ``ip6tables`` that keep their rules in a file and refuse
what the real ones refuse (a chain made twice, a rule removed that is not
there). Then each destination is walked through the chain the bridge's
packets jump to:

* refused: every private range (RFC 1918), the shared space (100.64/10),
  link-local (169.254/16: the cloud metadata address), loopback, "this
  network", multicast and the reserved and documentation ranges, and this
  computer's own addresses as Windows lists them; on to the public internet
  otherwise;
* the bridge's packets jump to the chain first in Docker's ``DOCKER-USER``
  (evaluated before Docker's own rules), a packet to this computer itself
  is refused (``INPUT``), and every IPv6 packet from the bridge is refused;
* run again, nothing is doubled; ``remove`` takes every rule and the chain
  out (the real-engine test removes its own this way).

**Control: without the host's addresses** a public address of this computer
is not refused (it would reach this computer's own services).
"""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from kazma_core import container_engine_setup as ces

from tests._bash import find_bash

pytestmark = pytest.mark.skipif(find_bash() is None, reason="bash is not on this machine")

_FAKE = r'''
import json, sys
from pathlib import Path

family, state_file, args = sys.argv[1], Path(sys.argv[2]), [a for a in sys.argv[3:] if a != "-w"]
state = json.loads(state_file.read_text()) if state_file.exists() else {}
tables = state.setdefault(family, {"INPUT": [], "FORWARD": [], "OUTPUT": []})
op, chain, rest = args[0], args[1], args[2:]
code = 0
if op == "-N":
    if chain in tables:
        code = 1
    else:
        tables[chain] = []
elif op == "-F":
    if chain not in tables:
        code = 1
    else:
        tables[chain] = []
elif op == "-X":
    tables.pop(chain, None)
elif op == "-A":
    tables[chain].append(rest)
elif op == "-I":
    if rest and rest[0].isdigit():
        tables[chain].insert(int(rest[0]) - 1, rest[1:])
    else:
        tables[chain].insert(0, rest)
elif op == "-C":
    code = 0 if rest in tables.get(chain, []) else 1
elif op == "-D":
    if rest in tables.get(chain, []):
        tables[chain].remove(rest)
    else:
        code = 1
state_file.write_text(json.dumps(state))
sys.exit(code)
'''


def _rules_script() -> str:
    text = ces._asset("egress-filter.sh")
    start = text.index("<<'RULES'\n") + len("<<'RULES'\n")
    return text[start:text.index("\nRULES\n", start) + 1]


@pytest.fixture
def stand_in(tmp_path):
    """bash, the rules script and the stand-in iptables; returns a runner and
    a reader of the rules."""
    fake = tmp_path / "fake_iptables.py"
    fake.write_text(_FAKE, encoding="utf-8")
    state = tmp_path / "state.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = Path(sys.executable).as_posix()
    for name, family in (("iptables", "v4"), ("ip6tables", "v6")):
        wrapper = bin_dir / name
        wrapper.write_bytes(
            f'#!/bin/bash\nexec "{python}" "{fake.as_posix()}" {family} "{state.as_posix()}" "$@"\n'.encode())
    script = tmp_path / "kazma-egress-rules"
    script.write_bytes(_rules_script().encode("utf-8"))

    def run(*args: str, hosts: list[str] | None = None) -> str:
        env = dict(os.environ)
        env["PATH"] = bin_dir.as_posix() + os.pathsep + env.get("PATH", "")
        if hosts is not None:
            listed = tmp_path / "hosts"
            listed.write_text("".join(f"{h}\n" for h in hosts), encoding="utf-8", newline="\n")
            env["KAZMA_EGRESS_HOSTS"] = listed.as_posix()
        else:
            env["KAZMA_EGRESS_HOSTS"] = (tmp_path / "no-such-file").as_posix()
        done = subprocess.run([find_bash(), "-c", f'PATH="{bin_dir.as_posix()}:$PATH" bash "{script.as_posix()}" "$@"',
                               "bash", *args], capture_output=True, text=True, env=env, timeout=120, check=False)
        assert done.returncode == 0, done.stderr + done.stdout
        return done.stdout

    def rules() -> dict:
        return json.loads(state.read_text()) if state.exists() else {}

    return run, rules


def _verdict(chain: list[list[str]], destination: str) -> str:
    """Where *destination* goes through *chain* (the address rules only)."""
    address = ipaddress.ip_address(destination)
    for rule in chain:
        if "-d" in rule:
            if address in ipaddress.ip_network(rule[rule.index("-d") + 1], strict=False):
                return rule[rule.index("-j") + 1]
        elif rule == ["-j", "RETURN"]:
            return "RETURN"
    return "RETURN"


REFUSED = ("10.1.2.3", "172.20.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "127.0.0.1",
           "0.0.0.1", "224.0.0.251", "240.0.0.1", "198.18.0.1", "192.0.2.10")
PUBLIC = ("1.1.1.1", "93.184.216.34", "8.8.8.8")
HOST = "8.8.4.4"  # a public address of this computer, as Windows lists it


def test_only_the_public_internet_is_reached(stand_in) -> None:
    run, rules = stand_in
    assert "EGRESS_RULES kz-egress KAZMA-EGRESS" in run(hosts=[HOST])
    v4, v6 = rules()["v4"], rules()["v6"]
    chain = v4["KAZMA-EGRESS"]
    for destination in REFUSED + (HOST,):
        assert _verdict(chain, destination) == "REJECT", destination
    for destination in PUBLIC:
        assert _verdict(chain, destination) == "RETURN", destination
    assert chain[-1] == ["-j", "RETURN"]
    for kind in ("LOCAL", "BROADCAST", "MULTICAST"):
        assert any(r[:4] == ["-m", "addrtype", "--dst-type", kind] for r in chain), kind
    # TCP is reset at once, everything else answered unreachable: no timeout.
    assert any("tcp-reset" in r for r in chain) and any("icmp-net-prohibited" in r for r in chain)
    assert v4["DOCKER-USER"][0] == ["-i", "kz-egress", "-j", "KAZMA-EGRESS"]
    assert v4["INPUT"][0] == ["-i", "kz-egress", "-j", "REJECT"]
    for name in ("DOCKER-USER", "FORWARD", "INPUT"):
        assert v6[name][0] == ["-i", "kz-egress", "-j", "REJECT"], name


def test_run_again_nothing_is_doubled_and_remove_takes_it_all(stand_in) -> None:
    run, rules = stand_in
    run(hosts=[HOST])
    once = rules()
    run(hosts=[HOST])
    assert rules() == once
    assert "EGRESS_REMOVED" in run("kz-egress", "KAZMA-EGRESS", "remove")
    after = rules()
    assert "KAZMA-EGRESS" not in after["v4"]
    for family in ("v4", "v6"):
        for name, chain in after[family].items():
            assert not any("kz-egress" in r for r in chain), (family, name)


def test_a_bridge_and_chain_of_their_own(stand_in) -> None:
    """The real-engine test's own names: nothing of the installed filter's
    is touched."""
    run, rules = stand_in
    run(hosts=[])
    run("kzet1234", "KAZMA-EGRESS-T1234", hosts=[])
    v4 = rules()["v4"]
    assert {"KAZMA-EGRESS", "KAZMA-EGRESS-T1234"} <= set(v4)
    run("kzet1234", "KAZMA-EGRESS-T1234", "remove")
    v4 = rules()["v4"]
    assert "KAZMA-EGRESS-T1234" not in v4 and v4["DOCKER-USER"][0] == ["-i", "kz-egress", "-j", "KAZMA-EGRESS"]


def test_control_without_the_hosts_addresses_this_computer_is_reached(stand_in) -> None:
    run, rules = stand_in
    run()
    assert _verdict(rules()["v4"]["KAZMA-EGRESS"], HOST) == "RETURN"


def test_the_installer_keeps_only_addresses_and_starts_before_docker() -> None:
    text = ces._asset("egress-filter.sh")
    unit = text[text.index("<<'UNIT'"):text.index("\nUNIT\n")]
    assert "Before=docker.service" in unit and "WantedBy=docker.service" in unit
    assert "Type=oneshot" in unit and "ExecStart=/usr/local/sbin/kazma-egress-rules" in unit
    assert "{1,3}" in text.split("for addr in")[1].split("done")[0]  # only an IPv4 address is kept
    assert text.rstrip().endswith('echo "EGRESS_READY"')
