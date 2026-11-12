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

logger = logging.getLogger(__name__)

__all__ = [
    "DISTRO",
    "DOWNLOADS",
    "RESTART",
    "TASK_NAME",
    "EngineCheck",
    "check",
    "install_steps",
    "preflight",
    "remove",
    "run_action",
]

Runner = Callable[..., subprocess.CompletedProcess]

DISTRO = "kazma-engine"
CONTEXT = "kazma-engine"
TASK_NAME = "KazmaEngine"
PORT = 2376
#: What an action answers when Windows must restart before the next step
#: (the WSL features were just turned on).
RESTART = "restart Windows to finish WSL, then run setup again"


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
    """``%LOCALAPPDATA%\\Kazma\\engine``: the distro's disk, its scripts, downloads."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Kazma" / "engine"


def bin_dir() -> Path:
    """Docker's own Windows CLI, installed by setup (no Docker Desktop needed)."""
    return _engine_dir() / "bin"


def _client_dir() -> Path:
    """The client certificate Windows presents to the engine (account only)."""
    raw = (os.environ.get("DOCKER_CONFIG") or "").strip()
    return (Path(raw) if raw else Path.home() / ".docker") / "kazma-engine"


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
    try:
        done = run(argv, input=None if stdin is None else stdin.encode("utf-8"), capture_output=True,
                   timeout=timeout, check=False, env=env, creationflags=_NO_WINDOW)
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
    ``missing`` (wsl, distro, docker, cli, context, task), a ``detail``."""

    ok: bool | None
    detail: str
    missing: list[str] = field(default_factory=list)
    version: str = ""


def check(run: Runner = subprocess.run) -> EngineCheck:
    """What of Kazma's engine is on this machine (Windows)."""
    from kazma_core.docker_cli import engine_installed, find_docker_cli

    missing: list[str] = []
    distros = _distros(run)
    if distros is None:
        missing += ["wsl", "distro"]  # no WSL, so no distro either
    elif DISTRO not in distros:
        missing.append("distro")
    if not find_docker_cli():
        missing.append("cli")
    if not engine_installed("kazma"):
        missing.append("context")
    code, _ = _out(run, ["schtasks", "/Query", "/TN", TASK_NAME])
    if code != 0:
        missing.append("task")
    version = ""
    code, text = (_docker(run, "version", "--format", "{{.Server.Version}}")
                  if not {"distro", "cli", "context"} & set(missing) else (1, ""))
    if code == 0 and text.strip():
        version = text.strip().splitlines()[-1]
    else:
        missing.append("docker")  # no distro: nothing there to answer
    if not missing:
        return EngineCheck(True, f"Kazma's engine, Docker {version}", [], version)
    words = {"wsl": "WSL is not installed", "distro": "the kazma-engine distro is not there",
             "docker": "Docker in it does not answer", "cli": "Docker's CLI is not installed",
             "context": "the kazma-engine Docker context is not set up",
             "task": "the KazmaEngine task that starts it at boot is not registered"}
    return EngineCheck(False, "; ".join(words[m] for m in missing), missing, version)


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
    "engine:record": "check it answers, and record it",
}


def install_steps(found: EngineCheck) -> list[Any]:
    """The steps that put in what *found* says is missing, in order; the
    record always last (it also checks that the engine answers)."""
    from kazma_core.postgres_service import Step

    wanted = {
        "engine:wsl": "wsl" in found.missing,
        "engine:distro": bool({"wsl", "distro"} & set(found.missing)),
        "engine:docker": bool({"wsl", "distro", "docker"} & set(found.missing)),
        "engine:cli": "cli" in found.missing,
        "engine:context": bool({"wsl", "distro", "docker", "context"} & set(found.missing)),
        "engine:task": "task" in found.missing,
        "engine:record": True,
    }
    return [Step(_TITLES[action], action=action, admin=action in ("engine:wsl", "engine:task"), dep="docker")
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


def _action_distro(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    distros = _distros(run)
    if distros is None:
        return False, RESTART
    if DISTRO in distros:
        return True, "the distro is here"
    image = _fetch(DOWNLOADS["ubuntu"], _engine_dir() / "downloads", opener=opener)
    target = _engine_dir() / DISTRO
    target.mkdir(parents=True, exist_ok=True)
    code, text = _out(run, ["wsl.exe", "--import", DISTRO, str(target), str(image), "--version", "2"],
                      timeout=900, env=_wsl_env())
    return (True, "created") if code == 0 else (False, f"wsl --import failed: {text.strip()[-300:]}")


def _action_docker(run: Runner, log: Callable[[str], None], opener: Any) -> tuple[bool, str]:
    bip, pool = _free_range(run)
    log(f"the engine's networks: {bip}, pool {pool}")
    code, text = _out(run, ["wsl.exe", "-d", DISTRO, "-u", "root", "--", "bash", "-s", "--", bip, pool],
                      stdin=_asset("setup-engine.sh"), timeout=1800, env=_wsl_env())
    if code != 0 or "ENGINE_READY" not in text:
        return False, f"setting Docker up in the distro failed: {text.strip()[-400:]}"
    # wsl.conf (systemd, drives at /c) applies from the distro's next start.
    _out(run, ["wsl.exe", "--terminate", DISTRO], env=_wsl_env())
    return True, text.strip().splitlines()[-1]


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
    from kazma_core.security.file_access import restrict_to_owner

    folder = _client_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if not restrict_to_owner(folder):
        return False, f"{folder} could not be made readable by this account only"
    for name in ("ca.pem", "cert.pem", "key.pem"):
        code, text = _out(run, ["wsl.exe", "-d", DISTRO, "-u", "root", "--", "cat",
                                f"/root/kazma-engine-client/{name}"], env=_wsl_env())
        if code != 0 or "BEGIN" not in text:
            return False, f"the engine's client {name} could not be read"
        target = folder / name
        target.write_text(text, encoding="utf-8")
        restrict_to_owner(target)
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


def _action_record(run: Runner, log: Callable[[str], None], opener: Any,
                   *, wait_s: float = 120.0) -> tuple[bool, str]:
    from kazma_core.atomic_files import atomic_write_text
    from kazma_core.container_engine import RECORD_KEY
    from kazma_core.docker_cli import apply_engine_choice
    from kazma_core.postgres_service import _record_path, read_record

    _out(run, ["schtasks", "/Run", "/TN", TASK_NAME])  # running it while it runs does nothing
    deadline = time.monotonic() + wait_s
    version = ""
    while True:
        code, text = _docker(run, "version", "--format", "{{.Server.Version}}")
        if code == 0 and text.strip():
            version = text.strip().splitlines()[-1]
            break
        if time.monotonic() >= deadline:
            return False, f"Kazma's engine did not answer within {int(wait_s)} s: {text.strip()[-200:]}"
        time.sleep(3)
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


def remove(run: Runner = subprocess.run, log: Callable[[str], None] | None = None,
           *, force: bool = False) -> tuple[bool, str]:
    """Remove Kazma's engine: its task, its distro (every image, container and
    volume in it), its context, client keys and CLI. Refused while another
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
