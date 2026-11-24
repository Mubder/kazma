"""Members' code on the filtered network, on Kazma's own engine (Spaces
S4-05b). Opt-in: ``KAZMA_TEST_EGRESS_ENGINE=1`` on a Windows machine with
Kazma's engine (the ``kazma-engine`` distro and Docker context).

Everything it makes has a name of its own and is removed after: the rules
for a bridge and chain of the test's own (``kazma-egress-rules`` with its
names, never the installed filter's), a network on that bridge, a listener
on the engine's own network (``--network host``: the distro, which is this
computer to a container) and the probes (``--rm``, each with a memory
limit). A container on the filtered network:

* reaches a public address (1.1.1.1:443);
* does not reach the listener on the distro's own address (this computer,
  and a private range: the LAN's), nor the cloud metadata address
  (169.254.169.254:80): each refused at once, never waited for.

**Control: the engine's default network** (Docker's ``bridge``, what the
first build of S4-05 used) reaches the listener, and its packet to the
metadata address goes out and is waited for.

The rules on real ``iptables`` run in a container of their own, whose network
namespace is its own (``--rm``, a memory limit; it installs iptables from
Debian): nothing of the engine's rules is read or changed.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
import uuid

import pytest
from kazma_core import container_engine_setup as ces
from kazma_core.tools import code_exec

pytestmark = [
    pytest.mark.real_docker,
    pytest.mark.skipif(os.environ.get("KAZMA_TEST_EGRESS_ENGINE") != "1" or sys.platform != "win32",
                       reason="opt-in: KAZMA_TEST_EGRESS_ENGINE=1 on a Windows machine with Kazma's engine"),
]

_PROBE = """
import json, socket, time
def probe(host, port, wait=6):
    start = time.monotonic()
    try:
        socket.create_connection((host, port), timeout=wait).close()
        what = "reached"
    except socket.timeout:
        what = "timeout"
    except ConnectionRefusedError:
        what = "refused"
    except OSError as exc:
        what = "error %s" % exc.errno
    return [what, round(time.monotonic() - start, 2)]
print(json.dumps({
    "public": probe("1.1.1.1", 443),
    "this computer": probe(HOST, PORT),
    "metadata": probe("169.254.169.254", 80),
}))
"""


def _docker() -> list[str]:
    cli = ces.bin_dir() / "docker.exe"
    return [str(cli) if cli.is_file() else "docker", "--context", ces.CONTEXT]


def _run(argv: list[str], *, stdin: str | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin.encode("utf-8") if stdin is not None else None,  # noqa: S603
                          capture_output=True, timeout=timeout, check=False)


def _rules_script() -> str:
    text = ces._asset("egress-filter.sh")
    start = text.index("<<'RULES'\n") + len("<<'RULES'\n")
    return text[start:text.index("\nRULES\n", start) + 1]


def _distro_address() -> str:
    done = _run(["wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "ip", "-4", "-o", "addr", "show", "eth0"])
    text = done.stdout.decode("utf-8", "replace")
    return text.split(" inet ")[1].split("/")[0].strip()


def _probe(network: str, host: str, port: int, suffix: str) -> dict:
    snippet = _PROBE.replace("HOST", repr(host)).replace("PORT", str(port))
    done = _run([*_docker(), "run", "--rm", "-i", "--name", f"kazma-egress-test-probe-{suffix}-{network[-4:]}",
                 "--memory", "128m", "--network", network, code_exec.DEFAULT_DOCKER_IMAGE, "python", "-I", "-"],
                stdin=snippet, timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8").strip().splitlines()[-1])


def test_the_filtered_network_reaches_the_public_internet_only(tmp_path) -> None:
    if _run([*_docker(), "image", "inspect", code_exec.DEFAULT_DOCKER_IMAGE]).returncode != 0:
        pytest.skip("Kazma's engine does not answer, or has no sandbox image")
    suffix = uuid.uuid4().hex[:6]
    bridge, chain = f"kzet{suffix}", f"KAZMA-EGRESS-T{suffix}"
    network, listener = f"kazma-egress-test-{suffix}", f"kazma-egress-test-listener-{suffix}"
    port = random.randint(41000, 49000)
    hosts = f"/tmp/kazma-egress-test-hosts-{suffix}"
    rules = ["wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "env", f"KAZMA_EGRESS_HOSTS={hosts}",
             "bash", "-s", "--", bridge, chain]
    try:
        windows = ces._windows_addresses(subprocess.run)
        listed = _run(["wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "bash", "-c", f"cat > {hosts}"],
                      stdin="".join(f"{a}\n" for a in windows))
        assert listed.returncode == 0
        put = _run(rules, stdin=_rules_script())
        assert b"EGRESS_RULES" in put.stdout, put.stderr
        made = _run([*_docker(), "network", "create", "--driver", "bridge",
                     "-o", f"com.docker.network.bridge.name={bridge}",
                     "-o", "com.docker.network.bridge.enable_icc=false", network])
        assert made.returncode == 0, made.stderr
        served = _run([*_docker(), "run", "-d", "--rm", "--name", listener, "--memory", "64m", "--network", "host",
                       code_exec.DEFAULT_DOCKER_IMAGE, "python", "-m", "http.server", str(port), "--bind", "0.0.0.0"])
        assert served.returncode == 0, served.stderr
        time.sleep(3)
        here = _distro_address()

        filtered = _probe(network, here, port, suffix)
        assert filtered["public"][0] == "reached", filtered
        for target in ("this computer", "metadata"):
            what, seconds = filtered[target]
            assert what != "reached" and what != "timeout" and seconds < 3, (target, filtered)

        # Control: the engine's default network.
        default = _probe("bridge", here, port, suffix)
        print("filtered network:", filtered, "| default network:", default)
        assert default["this computer"][0] == "reached", default
        assert default["metadata"][0] != "refused" or default["metadata"][1] >= 3, default
    finally:
        _run([*_docker(), "rm", "-f", listener])
        _run([*_docker(), "network", "rm", network])
        _run(["wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "bash", "-s", "--", bridge, chain, "remove"],
             stdin=_rules_script())
        _run(["wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "rm", "-f", hosts])
    left = _run(["wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "iptables", "-w", "-S"])
    assert chain.encode() not in left.stdout and bridge.encode() not in left.stdout


def test_the_rules_on_real_iptables(tmp_path) -> None:
    """Put in, found in effect, found again after a re-apply; each way they
    stop being in effect found, and put right by the next apply."""
    steps = """set -u
(apt-get update -qq && apt-get install -y -qq iptables) >/dev/null 2>&1
printf '8.8.4.4\n' > /hosts; export KAZMA_EGRESS_HOSTS=/hosts
R="bash /rules kz-test KAZMA-EGRESS-TEST"
$R >/dev/null && iptables -I FORWARD 1 -j DOCKER-USER
say() { $R check >/dev/null; echo "$1 $?"; }
say fresh; $R >/dev/null; say again
broken() { say "$1"; $R >/dev/null; say "$1-put-right"; }
iptables -I DOCKER-USER 1 -i kz-test -j ACCEPT; broken accept-before-the-jump
iptables -D KAZMA-EGRESS-TEST -d 169.254.0.0/16 -j REJECT --reject-with icmp-net-prohibited; broken udp-refusal-gone
iptables -I INPUT 1 -p tcp -j ACCEPT; broken accept-in-input
iptables -I INPUT 1 -m comment --comment 'for -i eth0 only' -j ACCEPT; broken accept-whose-comment-names-another
iptables -I DOCKER-USER 1 -p tcp -m comment --comment 'not ! -i lo -i eth0' -j ACCEPT; broken accept-of-tcp-with-a-comment
iptables -I INPUT 1; broken no-condition
ip6tables -I INPUT 1 -j ACCEPT; broken accept-in-input6
iptables -F KAZMA-EGRESS-TEST; broken flushed
iptables -I DOCKER-USER 1 -i other0 -j ACCEPT; say another-bridge
iptables -I INPUT 1 -s 10.0.0.0/8 -d 10.1.0.0/16 -i eth0 -p udp -j ACCEPT; say another-interfaces-udp
ip6tables -I FORWARD 1 -j DOCKER-USER; say docker-s-own-jump6
$R remove >/dev/null; say removed
"""
    script = "cat > /rules <<'ZZZ'\n" + _rules_script() + "ZZZ\n" + steps
    name = f"kazma-egress-test-iptables-{uuid.uuid4().hex[:6]}"
    done = _run([*_docker(), "run", "--rm", "-i", "--name", name, "--memory", "256m", "--cap-add", "NET_ADMIN",
                 code_exec.DEFAULT_DOCKER_IMAGE, "bash", "-s"], stdin=script, timeout=240)
    said = dict(line.rsplit(" ", 1) for line in done.stdout.decode("utf-8").splitlines() if line.strip())
    if not said or said.get("fresh") == "127":
        pytest.skip("iptables could not be installed in the container")
    broken = {"accept-before-the-jump", "udp-refusal-gone", "accept-in-input", "accept-in-input6", "flushed", "removed",
              "accept-whose-comment-names-another", "accept-of-tcp-with-a-comment", "no-condition"}
    assert {k for k, v in said.items() if v == "3"} == broken, said
    assert {k for k, v in said.items() if v == "0"} == set(said) - broken, said
