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
  out (the real-engine test removes its own this way);
* the check before every run finds a rule put in front of a jump and a chain
  that is not exactly the one made (the S4-06/S4-05b second review: asking
  ``iptables -C`` rule by rule found neither), and a rule for another
  interface in front is no one's concern;
* a kernel with no IPv6 is whole without ip6tables, and rules that cannot be
  put in take the members' network away, never Docker.

**Control: without the host's addresses** a public address of this computer
is not refused (it would reach this computer's own services).
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kazma_core import container_engine_setup as ces

from tests._bash import find_bash

pytestmark = pytest.mark.skipif(find_bash() is None, reason="bash is not on this machine")

_FAKE = r'''
import json, os, sys, time
from pathlib import Path

sys.stdout.reconfigure(newline=chr(10))


def told_to_wait(act):
    # Windows refuses a file another copy is opening or replacing at that moment.
    for _ in range(100):
        try:
            return act()
        except PermissionError:
            time.sleep(0.02)
    return act()


family, state_file, args = sys.argv[1], Path(sys.argv[2]), [a for a in sys.argv[3:] if a != "-w"]
if family == "v6" and state_file.with_name("no-ip6tables").exists():
    sys.exit(3)  # IPv6 off in the kernel: ip6tables can do nothing
state = json.loads(told_to_wait(state_file.read_text)) if state_file.exists() else {}
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
    if len(rest) == 1 and rest[0].isdigit():
        del tables[chain][int(rest[0]) - 1]
    elif rest in tables.get(chain, []):
        tables[chain].remove(rest)
    else:
        code = 1
elif op == "-S":
    if chain not in tables:
        code = 1
    else:
        print(("-P %s ACCEPT" if chain in ("INPUT", "FORWARD", "OUTPUT") else "-N %s") % chain)
        for rule in tables[chain]:
            print(" ".join(["-A", chain, *rule]))
elif op == "-E":
    # A rename keeps every jump to the chain (iptables(8): "cosmetic").
    new = rest[0]
    if chain not in tables or new in tables:
        code = 1
    else:
        tables[new] = tables.pop(chain)
        for rules in tables.values():
            for rule in rules:
                if "-j" in rule and rule[rule.index("-j") + 1] == chain:
                    rule[rule.index("-j") + 1] = new
log = state_file.with_name("ops.log")
with log.open("a") as ops:
    ops.write(json.dumps([family, *args]) + "\n")
    if family == "v4" and "DOCKER-USER" in tables:
        jumps = [[r[r.index("-j") + 1], len(tables.get(r[r.index("-j") + 1], []))]
                 for r in tables["DOCKER-USER"] if "-j" in r]
        ops.write(json.dumps(["jumps", jumps]) + "\n")
if op not in ("-S", "-C"):
    # Copies run beside one another (a pipeline lists while its reader changes):
    # one that only reads writes nothing, and the rules are never half written.
    whole = state_file.with_name(f"state.{os.getpid()}")
    whole.write_text(json.dumps(state))
    told_to_wait(lambda: os.replace(whole, state_file))
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
    (tmp_path / "if_inet6").write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = Path(sys.executable).as_posix()
    for name, family in (("iptables", "v4"), ("ip6tables", "v6")):
        wrapper = bin_dir / name
        wrapper.write_bytes(
            f'#!/bin/bash\nexec "{python}" "{fake.as_posix()}" {family} "{state.as_posix()}" "$@"\n'.encode())
        # Not executable, it is passed over on Linux and the machine's own iptables
        # answers: run by root, the test would write this computer's firewall.
        wrapper.chmod(0o755)
        assert os.name == "nt" or shutil.which(name, path=str(bin_dir)) == str(wrapper)
    script = tmp_path / "kazma-egress-rules"
    script.write_bytes(_rules_script().encode("utf-8"))

    def call(*args: str, hosts: list[str] | None = None, shell: str | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = bin_dir.as_posix() + os.pathsep + env.get("PATH", "")
        # Whether the kernel carries IPv6: /proc/net/if_inet6, here a file of the test's own.
        env["KAZMA_EGRESS_INET6"] = (tmp_path / "if_inet6").as_posix()
        if hosts is not None:
            listed = tmp_path / "hosts"
            listed.write_text("".join(f"{h}\n" for h in hosts), encoding="utf-8", newline="\n")
            env["KAZMA_EGRESS_HOSTS"] = listed.as_posix()
        else:
            env["KAZMA_EGRESS_HOSTS"] = (tmp_path / "no-such-file").as_posix()
        command = shell if shell is not None else f'bash "{script.as_posix()}" "$@"'
        return subprocess.run([find_bash(), "-c", f'PATH="{bin_dir.as_posix()}:$PATH" {command}', "bash", *args],
                              capture_output=True, text=True, env=env, timeout=120, check=False)

    def run(*args: str, hosts: list[str] | None = None, expect_code: int = 0) -> str:
        done = call(*args, hosts=hosts)
        assert done.returncode == expect_code, done.stderr + done.stdout
        return done.stdout

    def rules() -> dict:
        return json.loads(state.read_text()) if state.exists() else {}

    def save(changed: dict) -> None:
        state.write_text(json.dumps(changed))

    def jumps_seen() -> list[list[str]]:
        log = state.with_name("ops.log")
        lines = log.read_text().splitlines() if log.exists() else []
        return [json.loads(line)[1] for line in lines if json.loads(line)[0] == "jumps"]

    run.save = save
    run.folder = tmp_path
    run.jumps_seen = jumps_seen
    run.call = call
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


INPUT_V4 = ["-i", "kz-egress", "-j", "REJECT", "--reject-with", "icmp-port-unreachable"]
REFUSE_V6 = ["-i", "kz-egress", "-j", "REJECT", "--reject-with", "icmp6-port-unreachable"]
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
        of_kind = [r for r in chain if kind in r and "addrtype" in r]
        assert len(of_kind) == 2 and any("tcp" in r for r in of_kind), kind
    # TCP is reset at once, everything else answered unreachable: no timeout.
    assert any("tcp-reset" in r for r in chain) and any("icmp-net-prohibited" in r for r in chain)
    assert v4["DOCKER-USER"][0] == ["-i", "kz-egress", "-j", "KAZMA-EGRESS"]
    assert v4["INPUT"][0] == INPUT_V4
    for name in ("DOCKER-USER", "FORWARD", "INPUT"):
        assert v6[name][0] == REFUSE_V6, name


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
    # Docker puts the rules in again before it starts, and never fails to
    # start for them (the second review: an engine whose kernel could not
    # take them lost SearXNG, Neo4j and the document worker, with nobody
    # given the network): the members' network goes instead, once it runs.
    assert "/etc/systemd/system/docker.service.d/kazma-egress.conf" in text
    drop_in = text[text.index("<<'DROPIN'"):text.index("\nDROPIN\n")]
    assert "Requires=" not in drop_in and "After=kazma-egress.service" in drop_in
    starts = [line for line in drop_in.splitlines() if line.startswith("Exec")]
    assert starts == ["ExecStartPre=-/usr/local/sbin/kazma-egress-rules",
                      "ExecStartPost=-/usr/local/sbin/kazma-egress-guard"]
    assert text.index("DROPIN") < text.index("systemctl daemon-reload")
    assert "{1,3}" in text.split("for addr in")[1].split("done")[0]  # only an IPv4 address is kept
    assert text.rstrip().endswith('echo "EGRESS_READY"')


# ── the S4-05b review: rules in effect, checked before every run ───────────


def _as_docker_starts(run, rules) -> dict:
    """Docker's own jump from FORWARD to DOCKER-USER (made at its start)."""
    state = rules()
    state["v4"]["FORWARD"].insert(0, ["-j", "DOCKER-USER"])
    run.save(state)
    return state


_TCP_RESET = ["-p", "tcp", "-j", "REJECT", "--reject-with", "tcp-reset"]
_UNREACHABLE = ["-j", "REJECT", "--reject-with", "icmp-net-prohibited"]

#: Each way the rules stop being in effect after they were put in.
_BREAKS = {
    "the bridge's jump": lambda st: st["v4"]["DOCKER-USER"].clear(),
    "Docker's jump": lambda st: st["v4"]["FORWARD"].remove(["-j", "DOCKER-USER"]),
    "a refused range": lambda st: st["v4"]["KAZMA-EGRESS"].remove(["-d", "169.254.0.0/16", *_TCP_RESET]),
    "this computer's address": lambda st: st["v4"]["KAZMA-EGRESS"].remove(["-d", f"{HOST}/32", *_TCP_RESET]),
    "a range's UDP and ICMP": lambda st: st["v4"]["KAZMA-EGRESS"].remove(["-d", "169.254.0.0/16", *_UNREACHABLE]),
    "a local destination's UDP and ICMP": lambda st: st["v4"]["KAZMA-EGRESS"].remove(
        ["-m", "addrtype", "--dst-type", "LOCAL", *_UNREACHABLE]),
    "an accept at the chain's top": lambda st: st["v4"]["KAZMA-EGRESS"].insert(0, ["-j", "ACCEPT"]),
    "an accept before the bridge's jump": lambda st: st["v4"]["DOCKER-USER"].insert(
        0, ["-i", "kz-egress", "-j", "ACCEPT"]),
    "an accept for every bridge before it": lambda st: st["v4"]["DOCKER-USER"].insert(0, ["-i", "kz+", "-j", "ACCEPT"]),
    "an accept for all but another interface": lambda st: st["v4"]["DOCKER-USER"].insert(
        0, ["!", "-i", "eth0", "-j", "ACCEPT"]),
    "an accept before Docker's jump": lambda st: st["v4"]["FORWARD"].insert(0, ["-j", "ACCEPT"]),
    "an accept before INPUT's refusal": lambda st: st["v4"]["INPUT"].insert(0, ["-p", "tcp", "-j", "ACCEPT"]),
    "an accept whose comment names another interface": lambda st: st["v4"]["DOCKER-USER"].insert(
        0, ["-m", "comment", "--comment", '"for', "-i", "eth0", 'only"', "-j", "ACCEPT"]),
    "an accept of TCP whose comment names one": lambda st: st["v4"]["INPUT"].insert(
        0, ["-p", "tcp", "-m", "comment", "--comment", '"not', "!", "-i", "lo", "-i", 'eth0"', "-j", "ACCEPT"]),
    "a rule with no condition in front": lambda st: st["v4"]["FORWARD"].insert(0, []),
    "an accept before IPv6's refusal": lambda st: st["v6"]["DOCKER-USER"].insert(0, ["-j", "ACCEPT"]),
    "an accept before IPv6's INPUT": lambda st: st["v6"]["INPUT"].insert(0, ["-j", "ACCEPT"]),
    "the chain flushed": lambda st: st["v4"]["KAZMA-EGRESS"].clear(),
    "INPUT": lambda st: st["v4"]["INPUT"].clear(),
    "IPv6": lambda st: st["v6"]["FORWARD"].clear(),
    "iptables -F": lambda st: [chain.clear() for family in st.values() for chain in family.values()],
}


def test_check_finds_the_rules_in_effect_and_each_way_they_are_not(stand_in) -> None:
    import copy

    run, rules = stand_in
    run(hosts=[HOST])
    good = _as_docker_starts(run, rules)
    assert "EGRESS_IN_EFFECT kz-egress KAZMA-EGRESS" in run("kz-egress", "KAZMA-EGRESS", "check", hosts=[HOST])
    for name, broken in _BREAKS.items():
        state = copy.deepcopy(good)
        broken(state)
        run.save(state)
        out = run("kz-egress", "KAZMA-EGRESS", "check", hosts=[HOST], expect_code=3)
        assert "EGRESS_NOT_IN_EFFECT" in out, name
        if " before " in name or " in front" in name or "comment" in name:
            assert "EGRESS_IN_FRONT " in out, (name, out)
    state = copy.deepcopy(good)
    _BREAKS["an accept before INPUT's refusal"](state)
    run.save(state)
    said = run("kz-egress", "KAZMA-EGRESS", "check", hosts=[HOST], expect_code=3)
    assert "EGRESS_NOT_IN_EFFECT INPUT\n" in said and "EGRESS_IN_FRONT INPUT: -p tcp -j ACCEPT\n" in said
    run.save(good)
    assert "EGRESS_IN_EFFECT" in run("kz-egress", "KAZMA-EGRESS", "check", hosts=[HOST])
    # Rules the bridge's packets never meet stand in front of nothing, and
    # Docker's own jump in front of IPv6's FORWARD leads to the refusal.
    good["v4"]["DOCKER-USER"].insert(0, ["-i", "kzet1234", "-j", "KAZMA-EGRESS-T1234"])
    good["v4"]["INPUT"].insert(0, ["-i", "lo", "-j", "ACCEPT"])
    good["v4"]["INPUT"].insert(0, ["!", "-s", "10.0.0.0/8", "-d", "10.1.0.0/16", "-i", "eth0", "-p", "udp", "-j", "ACCEPT"])
    good["v6"]["FORWARD"].insert(0, ["-j", "DOCKER-USER"])
    run.save(good)
    assert "EGRESS_IN_EFFECT" in run("kz-egress", "KAZMA-EGRESS", "check", hosts=[HOST])


def test_control_asking_rule_by_rule_misses_what_stands_in_front(stand_in) -> None:
    """The first check (46f200bcb: ``iptables -C`` for each jump and each
    range's TCP refusal) answers "there" for an accept in front of the jump
    and for a range's UDP refusal taken out."""
    run, rules = stand_in
    run(hosts=[HOST])
    _as_docker_starts(run, rules)
    asked = ("iptables -w -C DOCKER-USER -i kz-egress -j KAZMA-EGRESS && "
             "iptables -w -C KAZMA-EGRESS -d 169.254.0.0/16 -p tcp -j REJECT --reject-with tcp-reset")
    for name in ("an accept before the bridge's jump", "a range's UDP and ICMP"):
        state = rules()
        _BREAKS[name](state)
        run.save(state)
        assert run.call(shell=asked).returncode == 0, name
        run("kz-egress", "KAZMA-EGRESS", "check", hosts=[HOST], expect_code=3)


def test_a_reapply_puts_a_refusal_that_lost_its_place_in_front_again(stand_in) -> None:
    run, rules = stand_in
    run(hosts=[HOST])
    _as_docker_starts(run, rules)
    state = rules()
    state["v4"]["INPUT"].insert(0, ["-p", "tcp", "-j", "ACCEPT"])
    state["v4"]["DOCKER-USER"].insert(0, ["-i", "kz-egress", "-j", "ACCEPT"])
    state["v6"]["INPUT"].insert(0, ["-j", "ACCEPT"])
    run.save(state)
    run(hosts=[HOST])
    assert "EGRESS_IN_EFFECT" in run("kz-egress", "KAZMA-EGRESS", "check", hosts=[HOST])
    assert rules()["v4"]["INPUT"].count(INPUT_V4) == 1 and rules()["v6"]["INPUT"].count(REFUSE_V6) == 1


def test_a_reapply_takes_out_no_rule_of_anyone_elses(stand_in) -> None:
    """A later copy of the refusal goes by its number, counted over every
    rule of the chain, one with no condition too (b55865291: not counted, the
    rule before the copy was deleted instead)."""
    run, rules = stand_in
    run(hosts=[HOST])
    _as_docker_starts(run, rules)
    state = rules()
    state["v4"]["INPUT"] = [[], ["-p", "tcp", "-j", "ACCEPT"], INPUT_V4, ["-p", "udp", "-j", "ACCEPT"]]
    run.save(state)
    run(hosts=[HOST])
    assert rules()["v4"]["INPUT"] == [INPUT_V4, [], ["-p", "tcp", "-j", "ACCEPT"], ["-p", "udp", "-j", "ACCEPT"]]
    assert "EGRESS_IN_EFFECT" in run("kz-egress", "KAZMA-EGRESS", "check", hosts=[HOST])


# ── rules that cannot be put in ─────────────────────────────────────────────


def test_a_kernel_with_no_ipv6_is_filtered_without_it(stand_in) -> None:
    """IPv6 off in the kernel: no packet of it, nothing to put in.
    **Control:** with the kernel's IPv6 there and ip6tables failing, the
    rules fail, as they must."""
    run, rules = stand_in
    (run.folder / "no-ip6tables").write_text("", encoding="utf-8")
    run(hosts=[HOST], expect_code=3)
    (run.folder / "if_inet6").unlink()
    assert "EGRESS_RULES" in run(hosts=[HOST])
    _as_docker_starts(run, rules)
    assert "EGRESS_IN_EFFECT" in run("kz-egress", "KAZMA-EGRESS", "check", hosts=[HOST])
    assert "EGRESS_REMOVED" in run("kz-egress", "KAZMA-EGRESS", "remove")


def _guard_script() -> str:
    text = ces._asset("egress-filter.sh")
    start = text.index("<<'GUARD'\n") + len("<<'GUARD'\n")
    return text[start:text.index("\nGUARD\n", start) + 1]


@pytest.mark.parametrize("in_effect, rm_code, listed, said", [
    (True, 0, "", ""),
    (False, 0, "", "EGRESS_NETWORK_REMOVED kazma-egress"),
    (False, 1, "", "EGRESS_NETWORK_NOT_THERE kazma-egress"),
    (False, 1, "echo 0a1b2c", "EGRESS_NETWORK_STILL_THERE kazma-egress: docker network rm answered 1: in use"),
    (False, 1, "exit 1", "EGRESS_NETWORK_UNKNOWN kazma-egress: docker network rm answered 1: in use"),
])
def test_rules_not_in_effect_take_the_network_away_and_leave_docker(tmp_path, in_effect, rm_code, listed, said) -> None:
    """``kazma-egress-guard`` (Docker's ExecStartPost): the members' network
    goes when the rules are not in effect, it says what happened to it
    (b55865291 said "removed" whatever ``docker network rm`` answered), and
    it always ends well, so Docker stays."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    asked = tmp_path / "asked"
    (bin_dir / "docker").write_bytes((
        f'#!/bin/bash\necho "$@" >> "{asked.as_posix()}"\n'
        f'if [[ "$2" == rm ]]; then echo "in use" >&2; exit {rm_code}; fi\n{listed}\nexit 0\n').encode())
    rules = bin_dir / "rules"
    rules.write_bytes(f"#!/bin/bash\nexit {0 if in_effect else 3}\n".encode())
    (bin_dir / "timeout").write_bytes(b'#!/bin/bash\nshift\nexec "$@"\n')
    for program in bin_dir.iterdir():
        program.chmod(0o755)
    script = tmp_path / "guard"
    script.write_bytes(_guard_script().replace("/usr/local/sbin/kazma-egress-rules", f'"{rules.as_posix()}"')
                       .encode("utf-8"))
    env = {**os.environ, "PATH": bin_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")}
    done = subprocess.run([find_bash(), "-c", f'PATH="{bin_dir.as_posix()}:$PATH" bash "{script.as_posix()}"'],
                          capture_output=True, text=True, env=env, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert asked.exists() is not in_effect
    assert done.stdout.strip() == said, done.stdout
    if not in_effect:
        calls = asked.read_text().splitlines()
        assert calls[0] == "network rm kazma-egress"
        assert calls[1:] == ([] if rm_code == 0 else ["network ls --quiet --filter name=^kazma-egress$"])


def test_kazma_asks_the_rules_themselves_before_a_run(stand_in) -> None:
    """``egress_rules_in_effect`` runs the distro's check as root, and is
    False for a break above. **Control:** a stamp and a network of the
    name -- the checks before the review -- say nothing of any of them."""
    import copy

    run, rules = stand_in
    run(hosts=[HOST])
    good = _as_docker_starts(run, rules)
    asked: list[list[str]] = []

    def wsl(argv, **_kw):
        asked.append(list(argv))
        assert argv[:6] == ["wsl.exe", "-d", ces.DISTRO, "-u", "root", "--"], argv
        assert argv[6] == "/usr/local/sbin/kazma-egress-rules", argv
        done = run.call(*argv[7:], hosts=[HOST])
        return subprocess.CompletedProcess(argv, done.returncode, done.stdout.encode(), done.stderr.encode())

    assert ces.egress_rules_in_effect(wsl) is True
    assert asked[-1][7:] == ["kz-egress", "KAZMA-EGRESS", "check"]
    for name in ("the bridge's jump", "a range's UDP and ICMP", "an accept before the bridge's jump", "iptables -F"):
        broken = _BREAKS[name]
        state = copy.deepcopy(good)
        broken(state)
        run.save(state)
        assert ces.egress_rules_in_effect(wsl) is False, name


def test_a_reapply_keeps_the_bridge_filtered_throughout(stand_in) -> None:
    """Put in again, the new chain is built under its own name and swapped
    in: after every iptables call, the bridge's packets jump to a chain that
    holds all its refusals. **Control:** the re-apply before the review
    (flush, then append) leaves them jumping to an empty chain."""
    run, rules = stand_in
    run(hosts=[HOST])
    full = len(rules()["v4"]["KAZMA-EGRESS"])
    seen = len(run.jumps_seen())
    run(hosts=[HOST])
    during = run.jumps_seen()[seen:]
    assert during and full > 20
    for snapshot in during:
        assert any(count == full for _chain, count in snapshot), snapshot
    assert rules()["v4"]["DOCKER-USER"] == [["-i", "kz-egress", "-j", "KAZMA-EGRESS"]]
    assert "KAZMA-EGRESS-NEW" not in rules()["v4"]
    # Control: the old order.
    seen = len(run.jumps_seen())
    done = run.call(shell="iptables -w -F KAZMA-EGRESS && iptables -w -A KAZMA-EGRESS -j RETURN")
    assert done.returncode == 0, done.stderr
    old = run.jumps_seen()[seen:]
    assert any(all(count < full for _chain, count in snapshot) for snapshot in old), old


def test_a_reapply_that_stopped_half_way_is_finished(stand_in) -> None:
    """A run killed between its new chain's jump and the swap leaves the new
    chain jumped and the old gone: the next run adopts it, nothing doubled."""
    run, rules = stand_in
    run(hosts=[HOST])
    once = rules()
    state = rules()
    state["v4"]["KAZMA-EGRESS-NEW"] = state["v4"].pop("KAZMA-EGRESS")
    state["v4"]["DOCKER-USER"] = [["-i", "kz-egress", "-j", "KAZMA-EGRESS-NEW"]]
    run.save(state)
    run(hosts=[HOST])
    assert rules() == once
