"""Install Kazma's own container engine on Windows: Docker as a service.

Docker Desktop starts only at a sign-in, so after an unattended restart
(a Windows update, a power cut) every container waits until someone signs in
(2026-11-12). Kazma's engine is Docker Engine in a WSL distro of its own,
``kazma-engine``, kept up from boot by the ``KazmaEngine`` scheduled task
(S4U: it runs with nobody signed in), reached from Windows as the Docker
context ``kazma-engine`` over mutual TLS on ``127.0.0.1:2376``. Plan,
measurements and the independent review:
``docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md``; AGENTS.md section 63.

``kazma setup install`` installs it on Windows whenever a container part is
chosen (Docker Desktop only with ``--docker desktop``); ``kazma setup engine``
installs, repairs, checks or removes it. Express installs no container part.

At a Windows shutdown or restart the ``KazmaEngineShutdown`` service (it takes
Windows' preshutdown notice, which WslService does not) stops Docker in the
distro the way a daemon shutdown does, writes its disks and powers it off
before WSL's VM ends; Windows waits for it. It does so through the distro's
holder, which keep-engine.ps1 starts through WMI with its stdin and stdout on
the service's pipes: once a shutdown has begun, Task Scheduler has ended its
tasks and starts none, and WSL refuses LocalSystem. Without it the VM ended
like a power cut, and a container's last writes came back as NUL bytes
(2026-11-13, measured on the drill VM).

A person's code that the admin allowed the network (Spaces S4-05b) runs on
the ``kazma-egress`` network, which reaches the public internet only: the
engine:egress step puts in the distro the rules that hold its bridge there
(``egress-filter.sh``, applied before Docker at every start), then makes the
network (:data:`EGRESS_NETWORK`). Only a member's network needs it: until the
admin has given one the network (``spaces.member_code.network_given``) the
filter is not missing from the engine (:attr:`EngineCheck.waiting`), so an
install without it -- the live one -- is whole to ``kazma setup install``;
``kazma setup engine`` puts it in all the same, after the record.

Every download is pinned by URL, size and SHA-256, and nothing is kept that
does not match. Each step is idempotent: a second run repairs what is missing
and changes nothing that is there.
"""

from __future__ import annotations

import configparser
import hashlib
import ipaddress
import json
import logging
import os
import shutil
import subprocess
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from kazma_core.docker_cli import KAZMA_CONTEXT, KAZMA_DISTRO

logger = logging.getLogger(__name__)

__all__ = [
    "DISTRO",
    "DOWNLOADS",
    "EGRESS_BRIDGE",
    "EGRESS_CHAIN",
    "EGRESS_LABEL",
    "EGRESS_NETWORK",
    "RESTART",
    "SHUTDOWN_SERVICE",
    "TASK_NAME",
    "EngineCheck",
    "check",
    "egress_installed",
    "egress_network_ok",
    "egress_rules_in_effect",
    "install_steps",
    "preflight",
    "remove",
    "remove_shutdown_service",
    "run_action",
    "shutdown_service_current",
    "shutdown_service_step",
]

Runner = Callable[..., subprocess.CompletedProcess]

DISTRO = KAZMA_DISTRO
CONTEXT = KAZMA_CONTEXT
TASK_NAME = "KazmaEngine"
#: The service that takes Windows' preshutdown notice and holds the shutdown
#: while Kazma's servers stop (``kazma_core.shutdown_notice``) and then
#: keep-engine.ps1 stops the engine (``engine-shutdown.cs``). Installed with
#: Kazma's engine and with Kazma's own service (``kazma setup install``'s
#: service part), so a Windows restart stops Kazma cleanly on any engine.
SHUTDOWN_SERVICE = "KazmaEngineShutdown"
#: How long the service holds a Windows shutdown for the engine's stop: the
#: slowest container's own stop timeout (10 s unless it sets one), with room.
SHUTDOWN_BUDGET_S = 120
#: How long it holds the shutdown for Kazma's servers to stop first: the
#: server's own stop budget (the guard's grace less 10 s, 50 s) plus its exit
#: margin, with room.
SHUTDOWN_KAZMA_BUDGET_S = 75
PORT = 2376
#: What an action answers when Windows must restart before the next step
#: (the WSL features were just turned on).
RESTART = "restart Windows to finish WSL, then run setup again"
#: The network a person's code reaches the public internet through (Spaces
#: S4-05b, ``egress-filter.sh``): its bridge, which the distro's rules name,
#: and the label setup gives it. A network of the name made any other way is
#: not used (:func:`egress_network_ok`).
EGRESS_NETWORK = "kazma-egress"
EGRESS_BRIDGE = "kz-egress"
EGRESS_LABEL = "io.kazma.egress"


@dataclass(frozen=True)
class Download:
    url: str
    size: int
    sha256: str
    name: str


#: Pinned downloads (2026-11-12). Ubuntu's image is the dated 24.04 release
#: (``current`` is the same file today; a dated URL never moves); setup
#: upgrades its packages inside the distro.
DOWNLOADS: dict[str, Download] = {
    "wsl": Download(
        "https://github.com/microsoft/WSL/releases/download/3.0.5/wsl.3.0.5.0.x64.msi",
        367788032, "2313efc5873e3f8552f42e77f950fabd913c0289078fa89d2100f3421a525eaa", "wsl.msi"),
    "ubuntu": Download(
        "https://cloud-images.ubuntu.com/wsl/releases/24.04/20240423/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz",
        0, "8251e27ffff381a4af5f41dcb94d867de3e0d9774a9241908ab34555d99315ea", "ubuntu-noble-wsl.rootfs.tar.gz"),
    "docker_cli": Download(
        "https://download.docker.com/win/static/stable/x86_64/docker-29.9.3.zip",
        49715375, "7c4ff7bbb31579046cd34dd8b91f7cbd3ab3482a1a85330170be726ef792d751", "docker-29.9.3.zip"),
    "compose": Download(
        "https://github.com/docker/compose/releases/download/v5.7.1/docker-compose-windows-x86_64.exe",
        34616320, "e4316597452650ae92fe9719a2a7c2fce8f56c5ceafed88d2aed6e39b2b2b2d0", "docker-compose.exe"),
}

#: Bridge ranges tried in order; the first that overlaps no route of this
#: machine (its LAN, WSL's own NAT, Hyper-V's switch) is used.
_RANGES = (("10.213.0.1/24", "10.213.128.0/17"), ("10.231.0.1/24", "10.231.128.0/17"),
           ("172.29.0.1/24", "172.29.128.0/17"))
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ── where things live ─────────────────────────────────────────────────────


def _engine_dir() -> Path:
    """``%LOCALAPPDATA%\\Kazma\\engine``: the distro's disk, its scripts,
    downloads. One definition, :func:`kazma_core.docker_cli.kazma_engine_dir`:
    the guard's disk watch reads the same folder."""
    from kazma_core.docker_cli import kazma_engine_dir

    return kazma_engine_dir()


def bin_dir() -> Path:
    """Docker's own Windows CLI, installed by setup (no Docker Desktop needed)."""
    return _engine_dir() / "bin"


def _own_cli_installed_check() -> bool:
    """Kazma's own Docker CLI and compose plugin, where the ``engine:cli``
    step puts them.

    Any docker CLI on the machine is not enough: Docker Desktop's goes away
    with Docker Desktop. On the owner's PC (2026-11-14) the check took Docker
    Desktop's for Kazma's, so setup never installed its own, and uninstalling
    Docker Desktop would have cut Kazma off from its engine."""
    return (bin_dir() / "docker.exe").is_file() and (_plugins_dir() / "docker-compose.exe").is_file()


def _client_dir() -> Path:
    """The client certificate Windows presents to the engine (account only)."""
    raw = (os.environ.get("DOCKER_CONFIG") or "").strip()
    return (Path(raw) if raw else Path.home() / ".docker") / "kazma-engine"


def _shutdown_service_dir() -> Path:
    """The shutdown service's program: under Program Files, which only
    administrators can write (the service runs as LocalSystem)."""
    base = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(base) / "Kazma" / "engine-shutdown"


def _csc() -> Path:
    """The .NET Framework's C# compiler, part of every Windows 10 and 11."""
    windows = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    return Path(windows) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"


def _plugins_dir() -> Path:
    raw = (os.environ.get("DOCKER_CONFIG") or "").strip()
    return (Path(raw) if raw else Path.home() / ".docker") / "cli-plugins"


def _current_context() -> str:
    """The docker CLI's current context, from its ``config.json``."""
    raw = (os.environ.get("DOCKER_CONFIG") or "").strip()
    path = (Path(raw) if raw else Path.home() / ".docker") / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "default"
    return str(data.get("currentContext") or "default") if isinstance(data, dict) else "default"


def _asset(name: str) -> str:
    text = resources.files("kazma_core").joinpath("container_engine_assets", name).read_text(encoding="utf-8")
    return text.replace("\r\n", "\n")


# ── running things ────────────────────────────────────────────────────────


def _out(run: Runner, argv: list[str], *, timeout: float = 60, stdin: str | None = None,
         env: dict[str, str] | None = None) -> tuple[int, str]:
    # Bytes both ways: in text mode Windows writes every "\n" of stdin as
    # "\r\n", and bash then reads `set -o pipefail\r` (the clean VM,
    # 2026-11-12: the engine's setup script failed on its first line).
    # No stdin of the step's own is no input at all, never setup's console:
    # Windows' WSL stub answers `wsl.exe --version` with "press any key to
    # install WSL" and waited on the console until the 60 s timeout, for each
    # WSL call setup makes (the boot drill, 2026-11-12); a key pressed at
    # that moment would have installed WSL from the Store.
    feed: dict[str, Any] = ({"stdin": subprocess.DEVNULL} if stdin is None
                            else {"input": stdin.encode("utf-8")})
    try:
        done = run(argv, **feed, capture_output=True, timeout=timeout, check=False, env=env,
                   creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, (_text(done.stdout) + _text(done.stderr)).replace("\x00", "")


def _text(data: bytes | str | None) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _wsl_env() -> dict[str, str]:
    # WSL answers in UTF-16 unless asked for UTF-8.
    return {**os.environ, "WSL_UTF8": "1"}


def _wsl_installed(run: Runner) -> bool:
    """WSL itself (its MSI or Store package), not Windows' stub ``wsl.exe``:
    the stub answers ``--version`` with an error (measured on the clean VM,
    2026-11-12). The distro list cannot tell them apart: a WSL with no
    distro yet answers ``-l`` with an error too."""
    code, _ = _out(run, ["wsl.exe", "--version"], env=_wsl_env())
    return code == 0


def _distros(run: Runner, *, running: bool = False) -> list[str] | None:
    """The WSL distros (only those running, with *running*); None when WSL is
    not installed."""
    if not running and not _wsl_installed(run):
        return None
    argv = ["wsl.exe", "-l", "-q"] + (["--running"] if running else [])
    code, text = _out(run, argv, env=_wsl_env())
    if code != 0:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _docker(run: Runner, *args: str, timeout: float = 30) -> tuple[int, str]:
    from kazma_core.docker_cli import find_docker_cli

    cli = find_docker_cli()
    if not cli:
        return 127, "the docker CLI is not installed"
    return _out(run, [cli, "--context", CONTEXT, *args], timeout=timeout)


# ── checking ──────────────────────────────────────────────────────────────


@dataclass
class EngineCheck:
    """Kazma's engine as found: ``ok`` (True: all of it is there), what is
    ``missing`` (wsl, distro, docker, cli, context, task, shutdown, egress), a
    ``detail``; and what is not there and not needed yet (``waiting``: the
    egress filter, until someone's code has the network)."""

    ok: bool | None
    detail: str
    missing: list[str] = field(default_factory=list)
    version: str = ""
    waiting: list[str] = field(default_factory=list)


def check(run: Runner = subprocess.run) -> EngineCheck:
    """What of Kazma's engine is on this machine (Windows)."""
    from kazma_core.docker_cli import engine_installed, find_docker_cli

    missing: list[str] = []
    distros = _distros(run)
    if distros is None:
        missing += ["wsl", "distro"]  # no WSL, so no distro either
    elif DISTRO not in distros:
        missing.append("distro")
    if not _own_cli_installed_check():
        missing.append("cli")
    if not engine_installed("kazma"):
        missing.append("context")
    code, _ = _out(run, ["schtasks", "/Query", "/TN", TASK_NAME])
    if code != 0:
        missing.append("task")
    if not (shutdown_service_current(run) and _keep_engine_current() and _stop_helpers_current()):
        missing.append("shutdown")
    version = ""
    # The engine is asked through any docker CLI there is (Docker Desktop's
    # too): Kazma's own copy missing is the "cli" step's, never a reason to
    # set up a Docker that answers again.
    code, text = (_docker(run, "version", "--format", "{{.Server.Version}}")
                  if not {"distro", "context"} & set(missing) and find_docker_cli() else (1, ""))
    waiting: list[str] = []
    if code == 0 and text.strip():
        version = text.strip().splitlines()[-1]
        if not (egress_installed() and egress_network_ok(run)):
            # Only a member's network needs the filter: before one has it,
            # an engine without it is whole (nothing to repair, nothing red).
            (missing if _network_given() else waiting).append("egress")
    else:
        missing.append("docker")  # no distro: nothing there to answer
    if not missing:
        return EngineCheck(True, f"Kazma's engine, Docker {version}", [], version, waiting)
    words = {"wsl": "WSL is not installed", "distro": "the kazma-engine distro is not there",
             "docker": "Docker in it does not answer", "cli": "Docker's CLI is not installed",
             "context": "the kazma-engine Docker context is not set up",
             "task": "the KazmaEngine task that starts it at boot is not registered",
             "shutdown": "nothing stops it cleanly when Windows shuts down (the KazmaEngineShutdown service)",
             "egress": "members' code has no network limited to the public internet (the kazma-egress network)"}
    return EngineCheck(False, "; ".join(words[m] for m in missing), missing, version, waiting)


def _network_given() -> bool:
    """Whether the admin gave someone's code the network (Spaces S4-05b):
    what makes the egress filter part of the engine. Read only when the
    filter is not there; an identity store that cannot be read counts as
    nobody."""
    from kazma_core.spaces.member_code import network_given

    return network_given()


def _keep_engine_current() -> bool:
    """keep-engine.ps1 is this build's: it makes the stop at a shutdown, and
    an older copy has none (and starts the distro again mid-shutdown)."""
    keep = _engine_dir() / "keep-engine.ps1"
    try:
        return keep.read_text(encoding="utf-8").replace("\r\n", "\n") == _asset("keep-engine.ps1")
    except OSError:
        return False


def _stop_helpers_stamp() -> Path:
    """The SHA-256 of the ``engine-stop.sh`` last installed in the distro
    (written by :func:`_install_stop_script`; read without starting WSL)."""
    return _engine_dir() / "engine-stop.sha256"


def _stop_helpers_current() -> bool:
    try:
        stamp = _stop_helpers_stamp().read_text(encoding="ascii").strip()
    except OSError:
        return False
    return stamp == hashlib.sha256(_asset("engine-stop.sh").encode("utf-8")).hexdigest()


def _egress_stamp() -> Path:
    """The SHA-256 of the ``egress-filter.sh`` last put in the distro (written
    by the engine:egress step once the network is made)."""
    return _engine_dir() / "egress-filter.sha256"


def egress_installed() -> bool:
    """This build's egress filter was put in the distro (Spaces S4-05b): read
    from the stamp alone, no call to WSL or Docker. The network itself is
    :func:`egress_network_ok`'s."""
    try:
        stamp = _egress_stamp().read_text(encoding="ascii").strip()
    except OSError:
        return False
    return stamp == hashlib.sha256(_asset("egress-filter.sh").encode("utf-8")).hexdigest()


def egress_network_ok(run: Runner = subprocess.run) -> bool:
    """The ``kazma-egress`` network on Kazma's engine is setup's: its bridge
    is the one the distro's rules name and it carries setup's label. A
    network of the name made any other way would not be filtered."""
    code, text = _docker(run, "network", "inspect", EGRESS_NETWORK, "--format",
                         '{{index .Options "com.docker.network.bridge.name"}}|{{index .Labels "'
                         + EGRESS_LABEL + '"}}')
    lines = text.strip().splitlines()
    return code == 0 and bool(lines) and lines[-1].strip() == f"{EGRESS_BRIDGE}|1"


#: The chain the distro's rules jump the bridge's packets to.
EGRESS_CHAIN = "KAZMA-EGRESS"


def egress_rules_in_effect(run: Runner = subprocess.run) -> bool:
    """The distro's rules for the ``kz-egress`` bridge are in effect now
    (``kazma-egress-rules ... check``: Docker's jump to ``DOCKER-USER``, the
    jump to the filtering chain, its refusals, the ``INPUT`` and IPv6
    refusals). Asked before every run of a member's code on the network
    (the S4-05b review): a stamp says the filter was put in, not that its
    rules are still there -- another distro sharing the network namespace,
    an ``iptables -F`` or a firewall backend change takes them out, and the
    bridge would then reach everything."""
    code, text = _out(run, ["wsl.exe", "-d", DISTRO, "-u", "root", "--", "/usr/local/sbin/kazma-egress-rules",
                            EGRESS_BRIDGE, EGRESS_CHAIN, "check"], timeout=30, env=_wsl_env())
    if code == 0 and f"EGRESS_IN_EFFECT {EGRESS_BRIDGE} {EGRESS_CHAIN}" in text:
        return True
    logger.warning("[engine] the egress rules are not in effect (exit %s): %s", code, text.strip()[-300:])
    return False


def _service_state(run: Runner) -> str | None:
    """The shutdown service's state (``RUNNING``, ``STOPPED``...), None when
    it is not installed."""
    code, text = _out(run, ["sc.exe", "query", SHUTDOWN_SERVICE])
    if code != 0:
        return None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "STATE" and value.split():
            return value.split()[-1]
    return None


def _service_command_line(run: Runner) -> str | None:
    """The command line the service is registered with (``sc.exe qc``)."""
    code, text = _out(run, ["sc.exe", "qc", SHUTDOWN_SERVICE])
    if code != 0:
        return None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "BINARY_PATH_NAME":
            return value.strip()
    return None


def shutdown_service_current(run: Runner = subprocess.run) -> bool:
    """The shutdown service runs, built from this build's ``engine-shutdown.cs``
    and with this build's command line. An older copy runs without stopping
    Kazma first (before 2026-11-20 it stopped the engine only): read from the
    source the build left beside the program, without starting anything."""
    if os.name != "nt" and run is subprocess.run:
        return False
    if _service_state(run) != "RUNNING":
        return False
    try:
        built = (_shutdown_service_dir() / "kazma-engine-shutdown.cs").read_text(encoding="utf-8")
    except OSError:
        return False
    if built.replace("\r\n", "\n") != _asset("engine-shutdown.cs"):
        return False
    return (_service_command_line(run) or "").split() == _service_command().split()


def preflight(run: Runner = subprocess.run, *, wslconfig: Path | None = None) -> str:
    """What would stop Kazma's engine on this machine, said before anything is
    installed; ``""`` when nothing does. (The CPU's virtualization is
    ``system_deps.docker_readiness``.)"""
    problems = []
    path = wslconfig or Path.home() / ".wslconfig"
    parser = configparser.ConfigParser()
    try:
        parser.read_string(path.read_text(encoding="utf-8-sig"))
    except (OSError, configparser.Error, UnicodeDecodeError):
        parser = configparser.ConfigParser()
    mode = parser.get("wsl2", "networkingMode", fallback="nat").strip().lower()
    forwarding = parser.get("wsl2", "localhostForwarding", fallback="true").strip().lower()
    if mode != "mirrored" and forwarding in ("false", "0", "no", "off"):
        problems.append(f"WSL's localhost forwarding is off in {path}: Kazma's engine is reached on "
                        "127.0.0.1. Set localhostForwarding=true there, or choose --docker desktop")
    for name in _distros(run, running=True) or []:
        if name == DISTRO or name.lower().startswith("docker-desktop"):
            continue
        code, _ = _out(run, ["wsl.exe", "-d", name, "-e", "pgrep", "-x", "dockerd"], env=_wsl_env())
        if code == 0:
            problems.append(f"Docker already runs in the WSL distro {name!r}: all WSL distros share one network, "
                            "and a second engine would collide with it. Stop that Docker, or choose "
                            "--docker desktop")
    return "; ".join(problems)


# ── installing ────────────────────────────────────────────────────────────


_TITLES = {
    "engine:wsl": "install WSL 2 (pinned release) for Kazma's container engine",
    "engine:distro": "create the kazma-engine WSL distro (Ubuntu 24.04, pinned image)",
    "engine:docker": "install Docker Engine in it, with its TLS keys",
    "engine:cli": "install Docker's command-line tools for Windows (pinned)",
    "engine:context": "connect Windows to it (the kazma-engine Docker context)",
    "engine:task": "start it at boot with nobody signed in (the KazmaEngine task)",
    "engine:shutdown": "stop it cleanly when Windows shuts down (the KazmaEngineShutdown service)",
    "engine:shutdown_service": "stop Kazma cleanly when Windows shuts down (the KazmaEngineShutdown service)",
    "engine:egress": "limit members' code to the public internet (the kazma-egress network)",
    "engine:record": "check it answers, and record it",
}


def shutdown_service_step() -> Any:
    """The step that puts the ``KazmaEngineShutdown`` service in alone: Kazma's
    own service part's, on any engine (administrators only)."""
    from kazma_core.postgres_service import Step

    return Step(_TITLES["engine:shutdown_service"], action="engine:shutdown_service", admin=True)


def install_steps(found: EngineCheck, *, with_waiting: bool = False) -> list[Any]:
    """The steps that put in what *found* says is missing, in order; the
    record last but for the egress filter (the record also checks that the
    engine answers: a filter that fails never leaves the engine unrecorded).

    The filter is a step when it is missing (someone's code has the network),
    or with *with_waiting* (``kazma setup engine``) when it is not there yet
    or the engine is being set up: ``kazma setup install`` never puts in what
    no one needs."""
    from kazma_core.postgres_service import Step

    wanted = {
        "engine:wsl": "wsl" in found.missing,
        "engine:distro": bool({"wsl", "distro"} & set(found.missing)),
        "engine:docker": bool({"wsl", "distro", "docker"} & set(found.missing)),
        "engine:cli": "cli" in found.missing,
        "engine:context": bool({"wsl", "distro", "docker", "context"} & set(found.missing)),
        "engine:task": "task" in found.missing,
        "engine:shutdown": "shutdown" in found.missing,
        "engine:record": True,
        "engine:egress": "egress" in found.missing or (with_waiting and (
            "egress" in found.waiting or bool({"wsl", "distro", "docker"} & set(found.missing)))),
    }
    admin = ("engine:wsl", "engine:task", "engine:shutdown")
    return [Step(_TITLES[action], action=action, admin=action in admin, dep="docker")
            for action, needed in wanted.items() if needed]


def _fetch(item: Download, folder: Path, *, opener: Callable[..., Any] | None = None) -> Path:
    """Download *item* into *folder*, kept only when its SHA-256 (and size,
    when pinned) match."""
    if opener is None:
        from kazma_core.http_tls import open_download as opener
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / item.name
    if target.is_file() and _sha256(target) == item.sha256:
        return target
    partial = target.with_name(target.name + ".partial")
    hasher, got = hashlib.sha256(), 0
    with opener(item.url, timeout=300) as response, open(partial, "wb") as out:  # noqa: S310 -- pinned https
        while chunk := response.read(1 << 20):
            got += len(chunk)
            if item.size and got > item.size:
                break
            hasher.update(chunk)
            out.write(chunk)
    if (item.size and got != item.size) or hasher.hexdigest() != item.sha256:
        partial.unlink(missing_ok=True)
        raise ValueError(f"{item.name} did not match its pinned SHA-256; nothing was kept")
    os.replace(partial, target)
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _free_range(run: Runner) -> tuple[str, str]:
    """A bridge range overlapping no route of this machine."""
    code, text = _out(run, ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-NetRoute -AddressFamily IPv4 | ForEach-Object DestinationPrefix"])
    routes = []
    for line in text.splitlines() if code == 0 else []:
        try:
            net = ipaddress.ip_network(line.strip(), strict=False)
        except ValueError:
            continue
        if net.prefixlen and not net.is_loopback and not net.is_multicast and str(net) != "255.255.255.255/32":
            routes.append(net)
    for bip, pool in _RANGES:
        nets = (ipaddress.ip_network(bip, strict=False), ipaddress.ip_network(pool, strict=False))
        if not any(n.overlaps(r) for n in nets for r in routes):
            return bip, pool
    return _RANGES[-1]


def _action_wsl(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    if _distros(run) is not None:
        return True, "WSL is here"
    restart = False
    for feature in ("VirtualMachinePlatform", "Microsoft-Windows-Subsystem-Linux"):
        code, text = _out(run, ["dism.exe", "/online", "/enable-feature", f"/featurename:{feature}", "/all",
                                "/norestart"], timeout=900)
        if code == 3010:
            restart = True
        elif code != 0:
            return False, f"turning on {feature} failed: {text.strip()[-300:]}"
    msi = _fetch(DOWNLOADS["wsl"], _engine_dir() / "downloads", opener=opener)
    code, text = _out(run, ["msiexec.exe", "/i", str(msi), "/qn", "/norestart"], timeout=900)
    if code not in (0, 3010, 1641):
        return False, f"the WSL installer failed (exit {code})"
    if restart or code in (3010, 1641) or _distros(run) is None:
        log("WSL is installed; Windows must restart before the engine can be created")
        return True, RESTART
    return True, "WSL installed"


#: Windows' features WSL 2 runs on; turned on by ``engine:wsl``, working only
#: after Windows restarts.
WSL_FEATURES = ("VirtualMachinePlatform", "Microsoft-Windows-Subsystem-Linux")

#: What WSL answers when the virtual machine service has not started: its
#: features were turned on and Windows has not restarted since.
_NO_VM_SERVICE = "HCS_E_SERVICE_NOT_AVAILABLE"


def _distro_dir() -> tuple[Path, bool]:
    """Where the distro's disk goes, and whether the owner chose the place:
    the data folder's ``engine`` folder (``data_folder``: off Windows' own
    drive, so a reset keeps it), else ``%LOCALAPPDATA%\\Kazma\\engine``."""
    from kazma_core import data_folder

    folder = data_folder.chosen()
    if folder is not None:
        return data_folder.Layout(folder).engine, True
    return _engine_dir() / DISTRO, False


def _secure_distro_dir(target: Path, run: Runner, *, kept: Path | None = None) -> tuple[bool, str]:
    """The disk's folder restricted to this account, SYSTEM and the
    administrators (a second drive's root lets every signed-in user change
    files), and a kept disk given to this account: after a reset of Windows
    the account is a new one, and WSL, which attaches the disk as this
    account, would be refused."""
    from kazma_core.data_folder_move import current_account, secure_folder

    user = current_account(run)
    ok, why = secure_folder(target, ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
                                     *([f"{user}:(OI)(CI)F"] if user else [])], run)
    if not ok:
        return False, why
    if kept is not None and user:
        code, text = _out(run, ["icacls", str(kept), "/grant", f"{user}:F"])
        if code != 0:
            return False, f"icacls refused {kept}: {text.strip()[-300:]}"
    return True, ""


def _features_pending(run: Runner) -> bool:
    """Whether a feature WSL needs waits for Windows to restart (state
    ``EnablePending``, read as the enum's name: the same in every language)."""
    names = ",".join(WSL_FEATURES)
    code, text = _out(run, ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            f"(Get-WindowsOptionalFeature -Online -FeatureName {names}).State | "
                            "ForEach-Object { \"$_\" }"], timeout=120)
    return code == 0 and "EnablePending" in text


def _action_distro(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    """The distro, imported from the pinned image. While WSL's features wait
    for Windows to restart, it answers :data:`RESTART` as the WSL step does:
    setup run a second time before that restart tried ``wsl --import`` and
    failed with ``HCS_E_SERVICE_NOT_AVAILABLE`` (the installer's VM drill,
    2026-11-17), leaving the container parts "not installed" instead of
    "after Windows restarts"."""
    distros = _distros(run)
    if distros is None:
        return True, RESTART
    if DISTRO in distros:
        return True, "the distro is here"
    if _features_pending(run):
        log("WSL's features wait for Windows to restart")
        return True, RESTART
    target, chosen = _distro_dir()
    kept = target / "ext4.vhdx"
    if kept.is_file():
        # A disk kept in the data folder from before (a reset of Windows
        # wiped the registration, not the disk): registered where it is, with
        # every image, container and volume in it. A fresh import over it
        # would start Kazma's containers empty.
        ok, why = _secure_distro_dir(target, run, kept=kept)
        if not ok:
            return False, why
        code, text = _out(run, ["wsl.exe", "--import-in-place", DISTRO, str(kept)], timeout=900, env=_wsl_env())
        if code == 0:
            return True, f"registered the kept disk {kept}: its images, containers and volumes are back"
        if _NO_VM_SERVICE in text:
            log("WSL's virtual machine service is not running yet: Windows must restart")
            return True, RESTART
        return False, f"wsl --import-in-place of the kept disk {kept} failed: {text.strip()[-300:]}"
    image = _fetch(DOWNLOADS["ubuntu"], _engine_dir() / "downloads", opener=opener)
    if chosen:
        ok, why = _secure_distro_dir(target, run)
        if not ok:
            return False, why
    target.mkdir(parents=True, exist_ok=True)
    code, text = _out(run, ["wsl.exe", "--import", DISTRO, str(target), str(image), "--version", "2"],
                      timeout=900, env=_wsl_env())
    if code == 0:
        return True, "created" + (f" in {target}" if chosen else "")
    if _NO_VM_SERVICE in text:
        log("WSL's virtual machine service is not running yet: Windows must restart")
        return True, RESTART
    return False, f"wsl --import failed: {text.strip()[-300:]}"


def _action_docker(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    bip, pool = _free_range(run)
    log(f"the engine's networks: {bip}, pool {pool}")
    code, text = _out(run, ["wsl.exe", "-d", DISTRO, "-u", "root", "--", "bash", "-s", "--", bip, pool],
                      stdin=_asset("setup-engine.sh"), timeout=1800, env=_wsl_env())
    if code != 0 or "ENGINE_READY" not in text:
        return False, f"setting Docker up in the distro failed: {text.strip()[-400:]}"
    ok, detail = _install_stop_script(run)
    if not ok:
        return False, detail
    # wsl.conf (systemd, drives at /c) applies from the distro's next start.
    _out(run, ["wsl.exe", "--terminate", DISTRO], env=_wsl_env())
    return True, text.strip().splitlines()[-1]


def _install_stop_script(run: Runner) -> tuple[bool, str]:
    """``/usr/local/sbin/kazma-engine-stop`` in the distro: what the stop task
    runs at a Windows shutdown (``engine-stop.sh``)."""
    script = _asset("engine-stop.sh")
    code, text = _out(run, ["wsl.exe", "-d", DISTRO, "-u", "root", "--", "bash", "-s"],
                      stdin=script, timeout=120, env=_wsl_env())
    if code != 0 or "STOP_READY" not in text:
        return False, f"installing the engine's shutdown stop in the distro failed: {text.strip()[-300:]}"
    _engine_dir().mkdir(parents=True, exist_ok=True)
    _stop_helpers_stamp().write_text(hashlib.sha256(script.encode("utf-8")).hexdigest() + "\n", encoding="ascii")
    return True, ""


def _action_cli(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    downloads = _engine_dir() / "downloads"
    archive = _fetch(DOWNLOADS["docker_cli"], downloads, opener=opener)
    bin_dir().mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        data = zf.read("docker/docker.exe")
    tmp = bin_dir() / "docker.exe.partial"
    tmp.write_bytes(data)
    os.replace(tmp, bin_dir() / "docker.exe")
    compose = _fetch(DOWNLOADS["compose"], downloads, opener=opener)
    _plugins_dir().mkdir(parents=True, exist_ok=True)
    shutil.copyfile(compose, _plugins_dir() / "docker-compose.exe")
    _on_user_path(run, bin_dir())
    return True, f"docker.exe in {bin_dir()}, compose plugin in {_plugins_dir()}"


def _on_user_path(run: Runner, folder: Path) -> None:
    """*folder* on this account's PATH (new terminals; this process now), so
    the owner's own ``docker`` reaches the engine too. Through the .NET call,
    never ``setx`` (it cuts PATH at 1,024 characters)."""
    entries = [e for e in (os.environ.get("PATH") or "").split(os.pathsep) if e]
    if str(folder) not in entries:
        os.environ["PATH"] = os.pathsep.join([*entries, str(folder)])
    quoted = str(folder).replace("'", "''")
    script = ("$p=[string][Environment]::GetEnvironmentVariable('Path','User'); "
              f"$f='{quoted}'; "
              "if (-not (($p -split ';') -contains $f)) { "
              "[Environment]::SetEnvironmentVariable('Path', (($p.TrimEnd(';'), $f) -join ';').TrimStart(';'), 'User') }")
    _out(run, ["powershell", "-NoProfile", "-NonInteractive", "-Command", script])


def _action_context(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    from kazma_core.docker_cli import engine_installed, find_docker_cli
    from kazma_core.security.file_access import restrict_to_owner, write_private

    folder = _client_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if not restrict_to_owner(folder):
        return False, f"{folder} could not be made readable by this account only"
    for name in ("ca.pem", "cert.pem", "key.pem"):
        code, text = _out(run, ["wsl.exe", "-d", DISTRO, "-u", "root", "--", "cat",
                                f"/root/kazma-engine-client/{name}"], env=_wsl_env())
        if code != 0 or "BEGIN" not in text:
            return False, f"the engine's client {name} could not be read"
        if not write_private(folder / name, text):
            return False, f"{folder / name} could not be made readable by this account only"
    cli = find_docker_cli()
    if not cli:
        return False, "the docker CLI is not installed"
    spec = (f"host=tcp://127.0.0.1:{PORT},ca={folder / 'ca.pem'},cert={folder / 'cert.pem'},"
            f"key={folder / 'key.pem'}")
    verb = "update" if engine_installed("kazma") else "create"
    code, text = _out(run, [cli, "context", verb, CONTEXT,
                            "--description", "Kazma's container engine (WSL, starts at boot)",
                            "--docker", spec])
    if code != 0:
        return False, f"docker context {verb} failed: {text.strip()[-300:]}"
    # The owner's own `docker` reaches the engine when nothing else is theirs:
    # a Docker Desktop install keeps its own default (Kazma pins its context).
    if not engine_installed("desktop"):
        _out(run, [cli, "context", "use", CONTEXT])
    return True, f"context {CONTEXT} ({verb}d)"


def _task_script() -> str:
    keep = _engine_dir() / "keep-engine.ps1"
    return (
        "$ErrorActionPreference='Stop';"
        f"$a=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "
        f"'-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{keep}\"';"
        "$boot=New-ScheduledTaskTrigger -AtStartup;"
        "$every=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        "-RepetitionInterval (New-TimeSpan -Minutes 5);"
        "$who=[Security.Principal.WindowsIdentity]::GetCurrent().Name;"
        "$p=New-ScheduledTaskPrincipal -UserId $who -LogonType S4U -RunLevel Limited;"
        "$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew "
        "-RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -StartWhenAvailable;"
        f"Register-ScheduledTask -TaskName {TASK_NAME} -Force -Action $a -Trigger $boot,$every -Principal $p "
        "-Settings $s -Description \"Keeps Kazma's container engine (the kazma-engine WSL distro) running from "
        "boot, with nobody signed in.\" | Out-Null;"
        f"Start-ScheduledTask -TaskName {TASK_NAME}"
    )


def _action_task(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    _engine_dir().mkdir(parents=True, exist_ok=True)
    (_engine_dir() / "keep-engine.ps1").write_text(_asset("keep-engine.ps1"), encoding="utf-8")
    code, text = _out(run, ["powershell", "-NoProfile", "-NonInteractive", "-Command", _task_script()],
                      timeout=120)
    if code != 0:
        return False, f"registering the {TASK_NAME} task failed: {text.strip()[-300:]}"
    return True, f"{TASK_NAME} registered and started"


def _service_command() -> str:
    """The service's command line: its program and how long it may hold a
    shutdown for the engine's stop."""
    exe = _shutdown_service_dir() / "kazma-engine-shutdown.exe"
    return f'"{exe}" --budget {SHUTDOWN_BUDGET_S} --kazma-budget {SHUTDOWN_KAZMA_BUDGET_S}'


def _service_script() -> str:
    """Creates the shutdown service, or points it at this build; restarts on a
    crash; starts it. ``Win32_Service.Change`` sets the command line whole
    (Windows PowerShell 5.1 would mangle its quotes on ``sc.exe``'s)."""
    command = _service_command().replace("'", "''")
    return (
        "$ErrorActionPreference='Stop';"
        f"$name='{SHUTDOWN_SERVICE}'; $bin='{command}';"
        "$svc=Get-CimInstance Win32_Service -Filter \"Name='$name'\";"
        "if ($svc) { $r=Invoke-CimMethod -InputObject $svc -MethodName Change "
        "-Arguments @{PathName=$bin; StartMode='Automatic'; DisplayName='Kazma: clean stop at shutdown'}; "
        "if ($r.ReturnValue -ne 0) { throw \"changing the service failed ($($r.ReturnValue))\" } } "
        "else { New-Service -Name $name -BinaryPathName $bin -StartupType Automatic "
        "-DisplayName 'Kazma: clean stop at shutdown' "
        "-Description \"Stops Kazma, then its container engine (the kazma-engine WSL distro), before Windows "
        "shuts down, so nothing stops mid-write.\" | Out-Null };"
        "sc.exe failure $name reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null;"
        "Start-Service -Name $name"
    )


def _action_shutdown(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    """The clean stop at a Windows shutdown: the helpers in the distro,
    keep-engine.ps1 (which starts the holder the stop goes through), and the
    ``KazmaEngineShutdown`` service that makes the stop and holds Windows
    meanwhile (built here from ``engine-shutdown.cs`` with the .NET
    Framework's compiler)."""
    ok, detail = _install_stop_script(run)
    if not ok:
        return False, detail
    _engine_dir().mkdir(parents=True, exist_ok=True)
    # An older keep-engine.ps1 holds the distro with no channel to the
    # service: it gets this one, and the running copy (which holds the old
    # loop) is replaced by running the task again; the distro is held
    # meanwhile, so no container restarts.
    keep = _engine_dir() / "keep-engine.ps1"
    there = keep.is_file()
    if not _keep_engine_current():
        keep.write_text(_asset("keep-engine.ps1"), encoding="utf-8")
        if there:
            _out(run, ["schtasks", "/End", "/TN", TASK_NAME])
            _out(run, ["schtasks", "/Run", "/TN", TASK_NAME])
    if shutdown_service_current(run):
        return True, f"{SHUTDOWN_SERVICE} runs this build: at a shutdown it stops Kazma, then the engine"
    return _action_shutdown_service(run, log, opener)


def _action_shutdown_service(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    """The ``KazmaEngineShutdown`` service alone, built from this build's
    ``engine-shutdown.cs`` and started: what stops Kazma's servers at a
    Windows shutdown (and the engine, where Kazma's engine is set up). The
    step of Kazma's own service part (``kazma setup install``), on any engine."""
    if shutdown_service_current(run):
        return True, f"{SHUTDOWN_SERVICE} already runs this build"
    csc = _csc()
    if not csc.is_file():
        return False, f"the .NET Framework's C# compiler is not at {csc}"
    # The running service holds its program open: stop it before replacing it.
    if _service_state(run) not in (None, "STOPPED"):
        _out(run, ["sc.exe", "stop", SHUTDOWN_SERVICE])
        for _ in range(40):
            if _service_state(run) in (None, "STOPPED"):
                break
            time.sleep(0.25)
    folder = _shutdown_service_dir()
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "kazma-engine-shutdown.cs"
    source.write_text(_asset("engine-shutdown.cs"), encoding="utf-8")
    code, text = _out(run, [str(csc), "-nologo", "-optimize+", "-target:exe",
                            f"-out:{folder / 'kazma-engine-shutdown.exe'}", str(source)], timeout=120)
    if code != 0:
        return False, f"building the {SHUTDOWN_SERVICE} service failed: {text.strip()[-300:]}"
    code, text = _out(run, ["powershell", "-NoProfile", "-NonInteractive", "-Command", _service_script()],
                      timeout=120)
    if code != 0 or _service_state(run) != "RUNNING":
        return False, f"installing the {SHUTDOWN_SERVICE} service failed: {text.strip()[-300:]}"
    return True, (f"{SHUTDOWN_SERVICE} runs: at a shutdown it stops Kazma first (up to "
                  f"{SHUTDOWN_KAZMA_BUDGET_S} s), then the engine's containers (up to {SHUTDOWN_BUDGET_S} s)")


def _engine_answers(run: Runner, wait_s: float) -> tuple[bool, str]:
    """Start the engine's task and wait for Docker in it: ``(True, version)``,
    or ``(False, why)`` past *wait_s*."""
    _out(run, ["schtasks", "/Run", "/TN", TASK_NAME])  # running it while it runs does nothing
    deadline = time.monotonic() + wait_s
    while True:
        code, text = _docker(run, "version", "--format", "{{.Server.Version}}")
        if code == 0 and text.strip():
            return True, text.strip().splitlines()[-1]
        if time.monotonic() >= deadline:
            return False, f"Kazma's engine did not answer within {int(wait_s)} s: {text.strip()[-200:]}"
        time.sleep(3)


def _windows_addresses(run: Runner) -> list[str]:
    """This computer's IPv4 addresses as Windows lists them: in WSL's NAT
    mode the distro cannot see them, and a public one would reach this
    computer's own services from a container."""
    code, text = _out(run, ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $_.IPAddress }"])
    if code != 0:
        return []
    found: list[str] = []
    for line in text.splitlines():
        parts = line.strip().split(".")
        if len(parts) == 4 and all(p.isdigit() and int(p) < 256 for p in parts):
            found.append(line.strip())
    return found


def _action_egress(run: Runner, log: Callable[[str], None], opener: Any,
                   *, wait_s: float = 120.0) -> tuple[bool, str]:
    """Spaces S4-05b: the rules first (``egress-filter.sh``, before Docker at
    every start), then the network they hold to the public internet; the
    stamp last, so a step that stopped half way is run again."""
    answered, detail = _engine_answers(run, wait_s)
    if not answered:
        return False, detail
    addresses = _windows_addresses(run)
    code, text = _out(run, ["wsl.exe", "-d", DISTRO, "-u", "root", "--", "bash", "-s", "--", *addresses],
                      stdin=_asset("egress-filter.sh"), timeout=120, env=_wsl_env())
    if code != 0 or "EGRESS_READY" not in text:
        return False, f"putting the egress filter in the distro failed: {text.strip()[-300:]}"
    if not egress_network_ok(run):
        # A network of the name made any other way is not filtered: replaced.
        _docker(run, "network", "rm", EGRESS_NETWORK)
        code, text = _docker(run, "network", "create", "--driver", "bridge",
                             "-o", f"com.docker.network.bridge.name={EGRESS_BRIDGE}",
                             "-o", "com.docker.network.bridge.enable_icc=false",
                             "--label", f"{EGRESS_LABEL}=1", EGRESS_NETWORK)
        if code != 0 or not egress_network_ok(run):
            return False, f"the {EGRESS_NETWORK} network could not be made: {text.strip()[-300:]}"
    _engine_dir().mkdir(parents=True, exist_ok=True)
    _egress_stamp().write_text(
        hashlib.sha256(_asset("egress-filter.sh").encode("utf-8")).hexdigest() + "\n", encoding="ascii")
    log(f"members' code: the public internet only ({len(addresses)} of this computer's addresses refused)")
    return True, f"the {EGRESS_NETWORK} network reaches the public internet only"


def _action_record(run: Runner, log: Callable[[str], None], opener: Any,
                   *, wait_s: float = 120.0) -> tuple[bool, str]:
    from kazma_core.atomic_files import atomic_write_text
    from kazma_core.container_engine import RECORD_KEY
    from kazma_core.docker_cli import apply_engine_choice
    from kazma_core.postgres_service import _record_path, read_record

    answered, version = _engine_answers(run, wait_s)
    if not answered:
        return False, version
    target = _record_path()
    data = read_record(target)
    data.update({"version": 1, RECORD_KEY: {"kind": "kazma", "distro": DISTRO, "context": CONTEXT,
                                            "task": TASK_NAME, "docker": version, "at": time.time()}})
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(data, indent=2))
    # This setup run and every Kazma process after it use the engine.
    apply_engine_choice("kazma")
    # Compose sends a Windows folder as /g/x, the engine's view of it.
    os.environ["COMPOSE_CONVERT_WINDOWS_PATHS"] = "1"
    _out(run, ["setx", "COMPOSE_CONVERT_WINDOWS_PATHS", "1"])
    return True, f"Kazma's engine answers (Docker {version})"


_ACTIONS: dict[str, Callable[..., tuple[bool, str]]] = {
    "engine:wsl": _action_wsl,
    "engine:distro": _action_distro,
    "engine:docker": _action_docker,
    "engine:cli": _action_cli,
    "engine:context": _action_context,
    "engine:task": _action_task,
    "engine:shutdown": _action_shutdown,
    "engine:shutdown_service": _action_shutdown_service,
    "engine:egress": _action_egress,
    "engine:record": _action_record,
}


def run_action(action: str, *, run: Runner = subprocess.run, log: Callable[[str], None] | None = None,
               opener: Any = None) -> tuple[bool, str]:
    """Carry out one ``engine:*`` step. (ok, what happened)."""
    say = log or (lambda _m: None)
    fn = _ACTIONS.get(action)
    if fn is None:
        return False, f"unknown step {action}"
    try:
        return fn(run, say, opener)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return False, str(exc)


# ── removing ──────────────────────────────────────────────────────────────


def _foreign_containers(run: Runner = subprocess.run) -> list[str] | None:
    """Containers on the engine that are not Kazma's (another project's data
    lives in them): their names, or None when the engine cannot be asked."""
    code, text = _docker(run, "ps", "-a", "--format", "{{.Names}}")
    if code != 0:
        return None
    return sorted(n for n in text.split() if n and not n.startswith("kazma-"))


def remove_shutdown_service(run: Runner = subprocess.run) -> tuple[bool, str]:
    """Stop and delete the ``KazmaEngineShutdown`` service and its program
    (administrators only)."""
    _out(run, ["sc.exe", "stop", SHUTDOWN_SERVICE])
    _out(run, ["sc.exe", "delete", SHUTDOWN_SERVICE])
    shutil.rmtree(_shutdown_service_dir(), ignore_errors=True)
    for _ in range(20):  # a delete waits for open handles to close
        if _service_state(run) is None:
            break
        time.sleep(0.25)
    else:
        return False, f"the {SHUTDOWN_SERVICE} service is still registered"
    return True, f"the {SHUTDOWN_SERVICE} service is removed"


def remove(run: Runner = subprocess.run, log: Callable[[str], None] | None = None,
           *, force: bool = False) -> tuple[bool, str]:
    """Remove Kazma's engine: its tasks, its shutdown service, its distro (every
    image, container and volume in it), its context, client keys and CLI. Refused while another
    project's containers live in it, or while it cannot be asked, unless
    *force*."""
    say = log or (lambda _m: None)
    if not force:
        foreign = _foreign_containers(run)
        if foreign is None and DISTRO in (_distros(run) or []):
            return False, "the engine does not answer, so what lives in it cannot be checked (--force removes it)"
        if foreign:
            return False, ("these containers are not Kazma's and live in the engine: " + ", ".join(foreign)
                           + ". Move them first, or remove with --force (their data goes with it)")
    _out(run, ["schtasks", "/End", "/TN", TASK_NAME])
    _out(run, ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    remove_shutdown_service(run)
    code, text = _out(run, ["wsl.exe", "--unregister", DISTRO], timeout=300, env=_wsl_env())
    if code != 0 and DISTRO in (_distros(run) or []):
        return False, f"wsl --unregister failed: {text.strip()[-300:]}"
    from kazma_core.docker_cli import find_docker_cli

    cli = find_docker_cli()
    if cli:
        if _current_context() == CONTEXT:
            _out(run, [cli, "context", "use", "default"])
        _out(run, [cli, "context", "rm", "-f", CONTEXT])
    shutil.rmtree(_client_dir(), ignore_errors=True)
    shutil.rmtree(bin_dir(), ignore_errors=True)
    plugin = _plugins_dir() / "docker-compose.exe"
    # The compose plugin setup put there, never one the owner installed.
    if plugin.is_file() and _sha256(plugin) == DOWNLOADS["compose"].sha256:
        plugin.unlink(missing_ok=True)
    say("Kazma's engine is removed")
    from kazma_core.atomic_files import atomic_write_text
    from kazma_core.container_engine import RECORD_KEY
    from kazma_core.postgres_service import _record_path, read_record

    target = _record_path()
    data = read_record(target)
    if data.pop(RECORD_KEY, None) is not None:
        atomic_write_text(target, json.dumps(data, indent=2))
    return True, "removed"
