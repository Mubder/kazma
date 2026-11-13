"""Move Kazma's containers from Docker Desktop to Kazma's own engine (Stage 3).

``kazma setup engine --move-from-desktop`` (Windows) runs this once Kazma's
engine is set up (:mod:`kazma_core.container_engine_setup`). Plan and design:
``docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md``, AGENTS.md section 63.
The owner's PC was moved by hand on 2026-11-12 with a one-off tool; this is
that tool made safe for every install, on the pattern of the database's move
(``kazma setup postgres --move-from-container``, section 21 G).

What moves: the containers Kazma's record names on Docker Desktop -- the
rule :mod:`kazma_core.guard_dependencies` uses to tell the guard which
containers Kazma depends on (its memory graph, its private search), read
against Docker Desktop's containers. Never another project's container.
Kazma's database is not moved here: it has a move of its own, to the
Postgres service, and a database container on Docker Desktop refuses this
move with that command.

The order (:func:`move`):

1. **Prepared while Kazma runs** (nothing it uses is touched): each
   container's image copied to the engine (``docker save`` into ``docker
   load``, else pulled by its digest; held to the same layers), the code
   sandbox's image pulled, and the document worker image for this code
   copied (when Docker Desktop holds this code's build) or built on the
   engine -- only when documents run in the container profile.
2. **Kazma held** through its guard once no chat turn runs.
3. **Each container**: stopped on Docker Desktop with its restart policy set
   to ``no`` (a later Docker Desktop start never brings it back to fight for
   its ports), its volumes copied while it is stopped -- never while it runs
   -- and checked (every file's path, type, size, owner, mode and SHA-256 on
   both engines), its networks made, the container created on the engine
   from its own settings (bind mounts as the engine sees Windows' drives),
   files added inside it carried, started, and seen running, healthy and
   answering on its ports from Windows.
4. **Switched**: ``containers.engine`` set to ``kazma``, and the guard told.
5. **Kazma started and seen ready.**
6. The old containers stay stopped on Docker Desktop and kept: they are the
   way back.

A failure at any step leaves Kazma on Docker Desktop as it was: the
containers this run made on the engine are removed (before the switch: they
hold only the copy) or stopped and kept (after it: Kazma may have written to
them), Docker Desktop's originals get their restart policy back and are
started again, and ``containers.engine`` says ``desktop``. Every change is
written to a journal (``setup_record.json``, ``engine_move``) before it is
made, so a run that died half way is rolled back exactly by the next run.

Blocking (the docker CLI, settings): run from the CLI or in a thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import IO, Any

from kazma_core.docker_cli import DESKTOP_CONTEXT, ENGINE_KEY, KAZMA_CONTEXT

logger = logging.getLogger(__name__)

__all__ = [
    "DESKTOP",
    "ENGINE",
    "JOURNAL_KEY",
    "Docker",
    "Journal",
    "MovePlan",
    "MoveSteps",
    "engine_conflicts",
    "keep_desktop",
    "move",
    "plan_move",
    "recover",
    "stored_engine",
]

DESKTOP = DESKTOP_CONTEXT
ENGINE = KAZMA_CONTEXT
#: The setup record's entry for a move in progress (or the last one).
JOURNAL_KEY = "engine_move"
#: Moved by ``kazma setup postgres --move-from-container``, never here.
_DATABASE_PURPOSES = frozenset({"database", "memory mirror"})
#: A published host address Kazma's loopback address reaches.
_REACHING = {"", "0.0.0.0", "127.0.0.1", "::", "::1"}
#: Docker Desktop's views of a Windows drive in its own VM.
_DESKTOP_DRIVE_ROOTS = ("/run/desktop/mnt/host/", "/host_mnt/", "/mnt/host/")
#: Added files never carried: runtime folders and caches.
_SKIP_PREFIXES = ("/tmp", "/run", "/var/run", "/proc", "/dev", "/sys")
#: How long a moved container may take to run, pass its health check and
#: answer on its ports (Neo4j's own start period is 40 s).
START_WAIT_S = 300.0
#: How long a container is given to stop on its own before Docker kills it
#: (a database writes its last checkpoint then).
_STOP_GRACE_S = 120
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

Say = Callable[[str], None]


# ── the docker CLI, one engine per call ──────────────────────────────────


@dataclass
class _Answer:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def why(self) -> str:
        text = (self.err or self.out).strip()
        return text.splitlines()[-1][:300] if text else f"exit {self.code}"


def _text(data: bytes | str | None) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _child_env() -> dict[str, str]:
    """The docker CLI's environment: this one without its secrets, and
    without a context or host of its own (every call names its engine)."""
    from kazma_core.security.child_env import tool_child_env

    env = tool_child_env()
    for name in ("DOCKER_CONTEXT", "DOCKER_HOST"):
        env.pop(name, None)
    return env


class Docker:
    """The docker CLI, asked of one engine at a time (``--context``).

    *prefix* is the program: ``[path to docker.exe]`` in production, a fake
    CLI in the tests. Every call names its engine; the process's own
    ``DOCKER_CONTEXT`` (Kazma's pinned choice) never decides."""

    def __init__(self, prefix: list[str], *, env: dict[str, str] | None = None) -> None:
        self.prefix = list(prefix)
        self.env = env if env is not None else _child_env()

    @classmethod
    def found(cls) -> Docker | None:
        from kazma_core.docker_cli import find_docker_cli

        cli = find_docker_cli()
        return cls([cli]) if cli else None

    def argv(self, context: str, args: Iterable[str]) -> list[str]:
        return [*self.prefix, "--context", context, *[str(a) for a in args]]

    def call(self, context: str, *args: str, input: bytes | None = None, timeout: float = 120.0) -> _Answer:
        try:
            done = subprocess.run(  # nosec B603 - fixed argv, the docker CLI, no shell
                self.argv(context, args), input=input, stdin=None if input is not None else subprocess.DEVNULL,
                capture_output=True, timeout=timeout, check=False, env=self.env, creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return _Answer(124, "", f"timed out after {timeout:.0f} s")
        except OSError as exc:
            return _Answer(127, "", str(exc))
        return _Answer(done.returncode, _text(done.stdout), _text(done.stderr))

    def json(self, context: str, *args: str, timeout: float = 120.0) -> Any:
        """The parsed JSON a call prints, or None when it failed."""
        answer = self.call(context, *args, timeout=timeout)
        if not answer.ok:
            return None
        try:
            return json.loads(answer.out or "null")
        except ValueError:
            return None

    def answers(self, context: str) -> bool:
        return self.call(context, "version", "--format", "{{.Server.Version}}", timeout=30).ok

    def pipe(self, src_context: str, src_args: list[str], dst_context: str, dst_args: list[str], *,
             timeout: float = 3600.0) -> tuple[bool, str, str]:
        """The output of one call fed into another, streamed (a volume or an
        image is never held in memory). ``(ok, why, the second's output)``."""
        with tempfile.TemporaryFile() as src_err, tempfile.TemporaryFile() as dst_err, \
                tempfile.TemporaryFile() as dst_out:
            try:
                src = subprocess.Popen(  # nosec B603 - fixed argv, no shell
                    self.argv(src_context, src_args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=src_err, env=self.env, creationflags=_NO_WINDOW)
            except OSError as exc:
                return False, str(exc), ""
            try:
                dst = subprocess.Popen(  # nosec B603 - fixed argv, no shell
                    self.argv(dst_context, dst_args), stdin=src.stdout, stdout=dst_out, stderr=dst_err,
                    env=self.env, creationflags=_NO_WINDOW)
            except OSError as exc:
                src.kill()
                src.wait()
                return False, str(exc), ""
            assert src.stdout is not None
            src.stdout.close()  # the second holds it now: a reader that ends ends the writer
            try:
                dst.wait(timeout)
                src.wait(60)
            except subprocess.TimeoutExpired:
                for proc in (src, dst):
                    proc.kill()
                    proc.wait()
                return False, f"timed out after {timeout:.0f} s", ""

            def _read(fh: IO[bytes]) -> str:
                fh.seek(0)
                return _text(fh.read())

            if src.returncode != 0:
                return False, f"reading failed: {_last_line(_read(src_err)) or f'exit {src.returncode}'}", ""
            if dst.returncode != 0:
                return False, f"writing failed: {_last_line(_read(dst_err)) or f'exit {dst.returncode}'}", ""
            return True, "", _read(dst_out)

    def manifest(self, context: str, args: list[str], *, timeout: float = 3600.0) -> list[tuple] | str:
        """What a tar stream from one call holds (:func:`_manifest_of`), or why
        it could not be read."""
        with tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(  # nosec B603 - fixed argv, no shell
                    self.argv(context, args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                    env=self.env, creationflags=_NO_WINDOW)
            except OSError as exc:
                return str(exc)
            timer = threading.Timer(timeout, proc.kill)
            timer.start()
            try:
                assert proc.stdout is not None
                entries: list[tuple] | str = _manifest_of(proc.stdout)
            except (tarfile.TarError, OSError, EOFError) as exc:
                entries = f"the archive could not be read ({type(exc).__name__})"
            finally:
                timer.cancel()
                if proc.stdout is not None:
                    proc.stdout.close()
                proc.wait()
            if proc.returncode != 0:
                err.seek(0)
                return _last_line(_text(err.read())) or f"exit {proc.returncode}"
            return entries


def _last_line(text: str) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


# ── what a volume holds ──────────────────────────────────────────────────


def _member_name(name: str) -> str:
    text = name.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/") or "."


def _manifest_of(stream: IO[bytes]) -> list[tuple]:
    """Every entry of a tar stream: ``(path, kind, mode, uid, gid, size,
    sha256, link)``, sorted. A hard link counts as the file it names (which
    of a set comes first in an archive is the folder's listing order, and
    two file systems list differently); the volume's own folder is left out
    (an engine makes it before the copy)."""
    entries: list[tuple] = []
    digests: dict[str, tuple[int, str]] = {}
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            name = _member_name(member.name)
            if name == ".":
                continue
            mode = member.mode & 0o7777
            if member.isfile():
                hasher = hashlib.sha256()
                fh = archive.extractfile(member)
                if fh is not None:
                    while chunk := fh.read(1 << 20):
                        hasher.update(chunk)
                digests[name] = (member.size, hasher.hexdigest())
                entries.append((name, "file", mode, member.uid, member.gid, member.size, hasher.hexdigest(), ""))
            elif member.islnk():
                size, digest = digests.get(_member_name(member.linkname), (0, ""))
                entries.append((name, "file", mode, member.uid, member.gid, size, digest, ""))
            elif member.issym():
                entries.append((name, "symlink", 0, member.uid, member.gid, 0, "", member.linkname))
            elif member.isdir():
                entries.append((name, "dir", mode, member.uid, member.gid, 0, "", ""))
            else:
                kind = ("fifo" if member.isfifo() else "chardev" if member.ischr() else
                        "blockdev" if member.isblk() else f"type-{member.type!r}")
                device = f"{member.devmajor}:{member.devminor}" if member.isdev() and not member.isfifo() else ""
                entries.append((name, kind, mode, member.uid, member.gid, 0, "", device))
    return sorted(entries)


def _manifest_difference(a: list[tuple], b: list[tuple]) -> str:
    """The first entry that differs, said in words (no file content)."""
    left = {e[0]: e for e in a}
    right = {e[0]: e for e in b}
    for name in sorted(set(left) | set(right)):
        if name not in right:
            return f"{name} is missing on Kazma's engine"
        if name not in left:
            return f"{name} is on Kazma's engine only"
        if left[name] != right[name]:
            fields = ("path", "kind", "mode", "owner", "group", "size", "content", "link")
            diff = [f for f, x, y in zip(fields, left[name], right[name], strict=True) if x != y]
            return f"{name} differs ({', '.join(diff)})"
    return ""


# ── what the engine can mount ────────────────────────────────────────────


def _shown_volume(name: str) -> str:
    """A volume as said: an unnamed one (an image's VOLUME) by its first characters."""
    if len(name) == 64 and all(c in "0123456789abcdef" for c in name):
        return f"(unnamed) {name[:12]}"
    return name


def _drive_is_fixed(drive: str) -> bool:
    from kazma_core.docker_cli import _drive_is_fixed as fixed

    return fixed(drive)


def _engine_bind_source(source: str, *, fixed: Callable[[str], bool] | None = None) -> str | None:
    """A bind mount's source as Kazma's engine must be told it, or None when
    it cannot mount it.

    Docker Desktop reports a Windows folder as ``C:\\x`` (what it was given)
    or as its own view ``/run/desktop/mnt/host/c/x``; the engine mounts fixed
    drive ``C:`` at ``/c``. A path inside Docker Desktop's own VM (the
    Docker socket, ``/var/lib/...``) has no equivalent on the engine, and a
    share or a mapped, ``subst`` or removable drive cannot be mounted."""
    is_fixed = fixed or _drive_is_fixed
    text = (source or "").strip()
    win = PureWindowsPath(text)
    if len(win.drive) == 2 and win.drive.endswith(":"):
        if not is_fixed(win.drive):
            return None
        rest = "/".join(win.parts[1:])
        return f"/{win.drive[0].lower()}" + (f"/{rest}" if rest else "")
    for root in _DESKTOP_DRIVE_ROOTS:
        if text.startswith(root):
            tail = text[len(root):]
            letter, _, rest = tail.partition("/")
            if len(letter) == 1 and letter.isalpha():
                if not is_fixed(f"{letter.upper()}:"):
                    return None
                return f"/{letter.lower()}" + (f"/{rest}" if rest else "")
    return None


# ── a container's settings, carried ──────────────────────────────────────


def _name(row: dict[str, Any]) -> str:
    return str(row.get("Name") or "").lstrip("/")


def _restart_arg(policy: dict[str, Any] | None) -> str:
    policy = policy or {}
    name = str(policy.get("Name") or "no") or "no"
    if name == "on-failure" and policy.get("MaximumRetryCount"):
        return f"on-failure:{policy['MaximumRetryCount']}"
    return name


def _published(row: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(host ip, host port, container port)`` for every published port."""
    out = []
    for port, binds in ((row.get("HostConfig") or {}).get("PortBindings") or {}).items():
        for bind in binds or ():
            out.append((str(bind.get("HostIp") or ""), str(bind.get("HostPort") or ""), str(port)))
    return out


def _loopback_ports(row: dict[str, Any]) -> list[int]:
    """The host ports Kazma reaches the container on (loopback or every address)."""
    ports = []
    for ip, host_port, _port in _published(row):
        if ip in _REACHING and host_port.isdigit() and int(host_port) not in ports:
            ports.append(int(host_port))
    return sorted(ports)


def _carry_problems(row: dict[str, Any], *, fixed: Callable[[str], bool] | None = None) -> list[str]:
    """What of a container's settings Kazma's engine cannot be given as they
    are (said before anything changes)."""
    name = _name(row)
    host = row.get("HostConfig") or {}
    problems = []
    mode = str(host.get("NetworkMode") or "default")
    if mode == "host" or mode.startswith("container:"):
        problems.append(f"{name} uses the network mode {mode!r}, which Kazma's engine cannot give it the same way")
    for key, what in (("PidMode", "process namespace"), ("IpcMode", "IPC namespace")):
        value = str(host.get(key) or "")
        if value == "host" or value.startswith("container:"):
            problems.append(f"{name} shares the {what} {value!r}")
    for key, what in (("Devices", "devices"), ("DeviceRequests", "GPUs or devices"),
                      ("VolumesFrom", "another container's volumes"), ("Links", "legacy links")):
        if host.get(key):
            problems.append(f"{name} uses {what}")
    if str(host.get("Runtime") or "runc") not in ("runc", ""):
        problems.append(f"{name} uses the runtime {host.get('Runtime')!r}")
    for mount in row.get("Mounts") or ():
        kind = mount.get("Type")
        if kind == "bind" and _engine_bind_source(str(mount.get("Source") or ""), fixed=fixed) is None:
            problems.append(f"{name} mounts {mount.get('Source')!r}, which Kazma's engine cannot mount "
                            "(only folders on this PC's fixed drives)")
        elif kind == "volume" and str(mount.get("Driver") or "local") != "local":
            problems.append(f"{name}'s volume {mount.get('Name')!r} uses the driver {mount.get('Driver')!r}")
        elif kind not in ("bind", "volume", "tmpfs"):
            problems.append(f"{name} has a {kind} mount at {mount.get('Destination')!r}")
    state = row.get("State") or {}
    if state.get("Paused") or state.get("Restarting"):
        problems.append(f"{name} is {'paused' if state.get('Paused') else 'restarting'} on Docker Desktop: "
                        "let it settle (start or stop it), then run this again")
    return problems


def _ms(nanoseconds: Any) -> str:
    return f"{int(nanoseconds) // 1_000_000}ms"


def _config_args(row: dict[str, Any]) -> list[str]:
    """The container's own settings: labels, environment, user, health check."""
    import shlex

    cfg = row.get("Config") or {}
    args: list[str] = []
    for key, value in (cfg.get("Labels") or {}).items():
        args += ["--label", f"{key}={value}"]
    for item in cfg.get("Env") or ():
        args += ["--env", item]
    for key, flag in (("WorkingDir", "--workdir"), ("User", "--user"), ("Domainname", "--domainname"),
                      ("StopSignal", "--stop-signal")):
        if cfg.get(key):
            args += [flag, str(cfg[key])]
    hostname = str(cfg.get("Hostname") or "")
    if hostname and not str(row.get("Id") or "").startswith(hostname):
        args += ["--hostname", hostname]
    args += [flag for key, flag in (("Tty", "--tty"), ("OpenStdin", "--interactive")) if cfg.get(key)]
    if cfg.get("StopTimeout") is not None:
        args += ["--stop-timeout", str(cfg["StopTimeout"])]
    check = cfg.get("Healthcheck") or {}
    test = check.get("Test") or []
    if test and test[0] == "NONE":
        args.append("--no-healthcheck")
    elif test:
        args += ["--health-cmd", test[1] if test[0] == "CMD-SHELL" else shlex.join(test[1:])]
        for key, flag in (("Interval", "--health-interval"), ("Timeout", "--health-timeout"),
                          ("StartPeriod", "--health-start-period"), ("StartInterval", "--health-start-interval")):
            if check.get(key):
                args += [flag, _ms(check[key])]
        if check.get("Retries"):
            args += ["--health-retries", str(check["Retries"])]
    return args


def _mount_args(row: dict[str, Any], fixed: Callable[[str], bool] | None) -> list[str]:
    """Ports and mounts, a Windows folder as Kazma's engine sees it."""
    args: list[str] = []
    for ip, host_port, port in _published(row):
        args += ["--publish", f"{ip}:{host_port}:{port}" if ip else f"{host_port}:{port}"]
    for mount in row.get("Mounts") or ():
        kind = mount.get("Type")
        readonly = "" if mount.get("RW", True) else ",readonly"
        if kind == "volume":
            args += ["--mount", f"type=volume,src={mount['Name']},dst={mount['Destination']}{readonly}"]
        elif kind == "bind":
            source = _engine_bind_source(str(mount.get("Source") or ""), fixed=fixed)
            args += ["--mount", f"type=bind,src={source},dst={mount['Destination']}{readonly}"]
        elif kind == "tmpfs":
            args += ["--mount", f"type=tmpfs,dst={mount['Destination']}"]
    for target, options in ((row.get("HostConfig") or {}).get("Tmpfs") or {}).items():
        args += ["--tmpfs", f"{target}:{options}" if options else target]
    return args


def _host_args(host: dict[str, Any]) -> list[str]:
    """Restart policy, capabilities, limits."""
    args = ["--restart", _restart_arg(host.get("RestartPolicy"))]
    for flag, key in (("--cap-add", "CapAdd"), ("--cap-drop", "CapDrop"), ("--security-opt", "SecurityOpt"),
                      ("--add-host", "ExtraHosts"), ("--dns", "Dns"), ("--dns-search", "DnsSearch"),
                      ("--dns-option", "DnsOptions"), ("--group-add", "GroupAdd")):
        for value in host.get(key) or ():
            args += [flag, str(value)]
    for limit in host.get("Ulimits") or ():
        args += ["--ulimit", f"{limit['Name']}={limit['Soft']}:{limit['Hard']}"]
    for key, value in (host.get("Sysctls") or {}).items():
        args += ["--sysctl", f"{key}={value}"]
    args += [flag for key, flag in (("Privileged", "--privileged"), ("Init", "--init"),
                                    ("ReadonlyRootfs", "--read-only")) if host.get(key)]
    if host.get("Memory"):
        args += ["--memory", str(host["Memory"])]
    if host.get("NanoCpus"):
        args += ["--cpus", f"{host['NanoCpus'] / 1e9:g}"]
    if host.get("ShmSize") and host["ShmSize"] != 64 * 1024 * 1024:
        args += ["--shm-size", str(host["ShmSize"])]
    return args


def _network_args(row: dict[str, Any]) -> list[str]:
    """The first network, with the container's aliases on it (the others
    are joined after it is made: ``_extra_networks``)."""
    mode = str((row.get("HostConfig") or {}).get("NetworkMode") or "default")
    primary = mode if mode not in ("default", "") else "bridge"
    args = ["--network", primary]
    if primary not in ("bridge", "none"):
        nets = ((row.get("NetworkSettings") or {}).get("Networks")) or {}
        for alias in (nets.get(primary) or {}).get("Aliases") or ():
            if alias not in (_name(row), str(row.get("Id") or "")[:12]):
                args += ["--network-alias", alias]
    return args


def _create_args(row: dict[str, Any], image: str, *, fixed: Callable[[str], bool] | None = None) -> list[str]:
    """``docker create`` arguments that make *row*'s container again on
    Kazma's engine, from *image* (the engine's reference). Never printed:
    the environment holds the container's passwords."""
    cfg = row.get("Config") or {}
    args = ["create", "--name", _name(row), *_config_args(row), *_host_args(row.get("HostConfig") or {}),
            *_mount_args(row, fixed), *_network_args(row)]
    entrypoint = cfg.get("Entrypoint") or []
    if entrypoint:
        args += ["--entrypoint", entrypoint[0]]
    args.append(image)
    args += list(entrypoint[1:]) + list(cfg.get("Cmd") or [])
    return args


def _extra_networks(row: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """The networks a container joins besides its first, with its aliases."""
    nets = ((row.get("NetworkSettings") or {}).get("Networks")) or {}
    mode = str((row.get("HostConfig") or {}).get("NetworkMode") or "default")
    primary = mode if mode not in ("default", "") else "bridge"
    out = []
    for net, info in nets.items():
        if net == primary:
            continue
        aliases = [a for a in (info or {}).get("Aliases") or () if a not in (_name(row), str(row.get("Id") or "")[:12])]
        out.append((net, aliases))
    return out


def _user_networks(row: dict[str, Any]) -> list[str]:
    nets = list((((row.get("NetworkSettings") or {}).get("Networks")) or {}).keys())
    return [n for n in nets if n not in ("bridge", "host", "none")]


# ── the plan ─────────────────────────────────────────────────────────────


@dataclass
class _Moving:
    """One container to move."""

    name: str
    purposes: list[str]
    row: dict[str, Any]
    image: str  # Docker Desktop's reference for its image
    volumes: list[str]
    running: bool
    ports: list[int]


@dataclass
class MovePlan:
    containers: list[_Moving] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    volumes: dict[str, dict[str, Any]] = field(default_factory=dict)  # Docker Desktop's volume inspect


def _rows(docker: Docker, context: str) -> list[dict[str, Any]] | None:
    ids = docker.call(context, "ps", "-aq", "--no-trunc")
    if not ids.ok:
        return None
    names = ids.out.split()
    if not names:
        return []
    rows = docker.json(context, "inspect", *names)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None


def _image_layers(docker: Docker, context: str, ref: str) -> list[str] | None:
    data = docker.json(context, "image", "inspect", ref)
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    layers = (data[0].get("RootFS") or {}).get("Layers")
    return list(layers) if isinstance(layers, list) else []


def _image_ref(docker: Docker, row: dict[str, Any]) -> str:
    """Docker Desktop's reference for the container's image: its tag when
    the tag still names the image it was made from, else the image ID."""
    tag = str((row.get("Config") or {}).get("Image") or "")
    image_id = str(row.get("Image") or "")
    if tag and "@" not in tag and image_id:
        here = _image_layers(docker, DESKTOP, tag)
        made = _image_layers(docker, DESKTOP, image_id)
        if here is not None and here == made:
            return tag
    return image_id or tag


def plan_move(docker: Docker, *, previous: list[dict[str, Any]] | None = None,
              answering: Callable[[int], bool] | None = None,
              endpoints: tuple[list[Any], set[str]] | None = None,
              fixed: Callable[[str], bool] | None = None) -> MovePlan:
    """What would move, and what refuses the move -- read only.

    The containers are the ones Kazma's record names
    (:func:`kazma_core.guard_dependencies._match_containers`, the rule the
    server tells the guard with) among Docker Desktop's; *previous* is the
    guard's last record. Docker Desktop must answer: it is what is read."""
    from kazma_core import guard_dependencies as gd

    plan = MovePlan()
    if not docker.answers(DESKTOP):
        plan.refusals.append("Docker Desktop does not answer: start it (it holds the containers to move), "
                             "then run this again")
        return plan
    rows = _rows(docker, DESKTOP)
    if rows is None:
        plan.refusals.append("Docker Desktop's containers could not be listed")
        return plan
    found, unread = endpoints if endpoints is not None else gd._endpoints()
    if unread:
        plan.refusals.append("Kazma's settings for " + ", ".join(sorted(unread)) + " could not be read, so "
                             "which containers are Kazma's cannot be told")
        return plan
    chosen, undecided = gd._match_containers(
        found, rows, previous or [], named_db=gd._env(("KAZMA_DB_CONTAINER",)),
        answering=answering or gd._port_answers)
    for line in undecided:
        plan.refusals.append(f"more than one container on Docker Desktop could serve {line}: say which by "
                             "stopping or removing the others")
    by_name = {_name(r): r for r in rows}
    for entry in chosen:
        name = str(entry["name"])
        purposes = list(entry.get("for") or [])
        if _DATABASE_PURPOSES & set(purposes):
            plan.refusals.append(
                f"Kazma's database runs in the container {name} on Docker Desktop: move it to the Postgres "
                f"service first (kazma setup postgres --move-from-container {name}), then run this again")
            continue
        row = by_name.get(name)
        if row is None:
            continue
        plan.refusals += _carry_problems(row, fixed=fixed)
        volumes = [str(m["Name"]) for m in row.get("Mounts") or () if m.get("Type") == "volume"]
        running = bool((row.get("State") or {}).get("Running"))
        plan.containers.append(_Moving(name, purposes, row, _image_ref(docker, row), volumes, running,
                                      _loopback_ports(row)))
        for ip, host_port, _port in _published(row):
            if ip in ("", "0.0.0.0", "::"):
                plan.notes.append(f"{name} publishes port {host_port} on every address: on Kazma's engine it is "
                                  "reached from this PC only, no longer from the network")
        if "host.docker.internal" in json.dumps(row.get("Config") or {}):
            plan.notes.append(f"{name} names host.docker.internal (Docker Desktop's name for Windows): Kazma's "
                              "engine does not define it the same way")
    wanted = sorted({v for m in plan.containers for v in m.volumes})
    moving_names = {m.name for m in plan.containers}
    for row in rows:
        if _name(row) in moving_names:
            continue
        shared = sorted({str(m.get("Name")) for m in row.get("Mounts") or ()
                         if m.get("Type") == "volume" and str(m.get("Name")) in wanted})
        if shared:
            # Copied while another container may write to it, or split between
            # two engines afterwards: neither is a move.
            plan.refusals.append(f"the volume {', '.join(shared)} is also used by {_name(row)}, which is not "
                                 "Kazma's: it cannot be moved without it")
    if wanted:
        info = docker.json(DESKTOP, "volume", "inspect", *wanted)
        for vol in info if isinstance(info, list) else []:
            if not isinstance(vol, dict):
                continue
            plan.volumes[str(vol.get("Name"))] = vol
            if vol.get("Options"):
                plan.refusals.append(f"the volume {vol.get('Name')} is bound to a folder or a device "
                                     "(driver options), which Kazma's engine cannot be given the same way")
        missing = [v for v in wanted if v not in plan.volumes]
        if missing:
            plan.refusals.append("Docker Desktop's volumes " + ", ".join(missing) + " could not be read")
    return plan


def engine_conflicts(docker: Docker, plan: MovePlan) -> list[str]:
    """What on Kazma's engine already stands where a moved container would
    go: a container of the same name, a volume of the same name, a container
    publishing the same port. ``[]`` when the engine does not answer yet
    (asked again once it is set up)."""
    if not plan.containers or not docker.answers(ENGINE):
        return []
    rows = _rows(docker, ENGINE) or []
    names = {_name(r) for r in rows}
    held_ports: dict[int, str] = {}
    for row in rows:
        for port in _loopback_ports(row):
            held_ports[port] = _name(row)
    listed = docker.call(ENGINE, "volume", "ls", "-q")
    volumes = set(listed.out.split()) if listed.ok else set()
    out = []
    for moving in plan.containers:
        if moving.name in names:
            out.append(f"Kazma's engine already has a container named {moving.name}: remove it there "
                       f"(docker --context {ENGINE} rm {moving.name}) if it is an earlier copy, then run this again")
        for port in moving.ports:
            if port in held_ports and held_ports[port] != moving.name:
                out.append(f"{held_ports[port]} on Kazma's engine already publishes port {port}, which "
                           f"{moving.name} needs")
        for vol in moving.volumes:
            if vol in volumes:
                out.append(f"Kazma's engine already has a volume named {vol}: it is never overwritten. Remove it "
                           f"there (docker --context {ENGINE} volume rm {vol}) if it is an earlier copy")
    return list(dict.fromkeys(out))


# ── the journal ──────────────────────────────────────────────────────────


class Journal:
    """The move's record of every change, written before the change is made
    (``setup_record.json``, ``engine_move``): what a rollback undoes."""

    def __init__(self, path: Path | None = None) -> None:
        from kazma_core.postgres_service import _record_path, read_record

        self.path = path or _record_path()
        entry = read_record(self.path).get(JOURNAL_KEY)
        self.data: dict[str, Any] = entry if isinstance(entry, dict) else {}

    @property
    def state(self) -> str:
        return str(self.data.get("state") or "")

    def unfinished(self) -> bool:
        return self.state in ("moving", "switched")

    def begin(self, stored_engine: str) -> None:
        self.data = {"state": "moving", "started": time.time(), "stored_engine": stored_engine,
                     "desktop": {}, "created": {"containers": [], "volumes": [], "networks": []}}
        self.save()

    def note_desktop(self, name: str, row: dict[str, Any]) -> None:
        """Before a Docker Desktop container is changed: how to put it back."""
        self.data.setdefault("desktop", {})[name] = {
            "restart": dict((row.get("HostConfig") or {}).get("RestartPolicy") or {"Name": "no"}),
            "running": bool((row.get("State") or {}).get("Running")),
        }
        self.save()

    def note_created(self, kind: str, name: str) -> None:
        """Before something is made on Kazma's engine: a rollback removes it."""
        made = self.data.setdefault("created", {}).setdefault(kind, [])
        if name not in made:
            made.append(name)
            self.save()

    def created(self, kind: str) -> list[str]:
        return list((self.data.get("created") or {}).get(kind) or [])

    def set_state(self, state: str) -> None:
        self.data["state"] = state
        self.data[f"{state}_at"] = time.time()
        self.save()

    def save(self) -> None:
        from kazma_core.atomic_files import atomic_write_text
        from kazma_core.postgres_service import read_record

        whole = read_record(self.path)
        whole.setdefault("version", 1)
        whole[JOURNAL_KEY] = self.data
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(whole, indent=2))


# ── carrying one container ───────────────────────────────────────────────


def _engine_image(docker: Docker, desktop_ref: str, say: Say) -> str | None:
    """Docker Desktop's image on Kazma's engine, with the same layers: the
    engine's reference to it, or None."""
    want = _image_layers(docker, DESKTOP, desktop_ref)
    if want is None:
        say(f"   Docker Desktop's image {desktop_ref} could not be read")
        return None

    def same(ref: str) -> bool:
        return bool(ref) and _image_layers(docker, ENGINE, ref) == want

    if same(desktop_ref):
        return desktop_ref
    ok, why, loaded = docker.pipe(DESKTOP, ["save", desktop_ref], ENGINE, ["load"])
    if ok:
        candidates = [line.split(":", 1)[1].strip() for line in loaded.splitlines()
                      if line.startswith(("Loaded image:", "Loaded image ID:"))]
        for ref in [desktop_ref, *candidates]:
            if same(ref):
                return ref
        why = "the loaded image is not the same"
    data = docker.json(DESKTOP, "image", "inspect", desktop_ref)
    digests = (data[0].get("RepoDigests") or []) if isinstance(data, list) and data else []
    for digest in digests:
        if docker.call(ENGINE, "pull", digest, timeout=3600).ok and same(digest):
            # Tagged as Docker Desktop names it, when that is a tag (an image
            # ID is not, and is used as the digest it was pulled by).
            if "@" not in desktop_ref and not desktop_ref.startswith("sha256:") and \
                    docker.call(ENGINE, "tag", digest, desktop_ref).ok and same(desktop_ref):
                return desktop_ref

            return digest
    say(f"   the image {desktop_ref} could not be put on Kazma's engine ({why})")
    return None


def _has_tar(docker: Docker, context: str, image: str) -> bool:
    """The image runs ``tar`` (GNU or BusyBox: no ``--version`` in BusyBox,
    so it archives one entry instead)."""
    return docker.call(context, "run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "tar",
                       image, "-C", "/dev", "-cf", "-", "null", timeout=120).ok


# Run as root, tar keeps every entry's owner and mode when it extracts (GNU
# and BusyBox alike); BusyBox has no -p, so none is passed.
def _tar_out(volume: str, image: str) -> list[str]:
    return ["run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "tar",
            "-v", f"{volume}:/v:ro", image, "-C", "/v", "-cf", "-", "."]


def _tar_in(volume: str, image: str) -> list[str]:
    return ["run", "--rm", "-i", "--network", "none", "--user", "0:0", "--entrypoint", "tar",
            "-v", f"{volume}:/v", image, "-C", "/v", "-xf", "-"]


def _copy_volume(docker: Docker, volume: str, info: dict[str, Any], *, desktop_image: str, engine_image: str,
                journal: Journal, say: Say) -> bool:
    """One volume copied from Docker Desktop to Kazma's engine while its
    container is stopped, then checked entry by entry on both engines."""
    args = ["volume", "create"]
    for key, value in (info.get("Labels") or {}).items():
        args += ["--label", f"{key}={value}"]
    journal.note_created("volumes", volume)
    made = docker.call(ENGINE, *args, volume)
    if not made.ok:
        say(f"   the volume {volume} could not be made on Kazma's engine: {made.why()}")
        return False
    ok, why, _ = docker.pipe(DESKTOP, _tar_out(volume, desktop_image), ENGINE, _tar_in(volume, engine_image))
    if not ok:
        say(f"   copying the volume {volume} failed: {why}")
        return False
    source = docker.manifest(DESKTOP, _tar_out(volume, desktop_image))
    copy = docker.manifest(ENGINE, _tar_out(volume, engine_image))
    if isinstance(source, str) or isinstance(copy, str):
        say(f"   the volume {volume} could not be read back to check it: {source if isinstance(source, str) else copy}")
        return False
    difference = _manifest_difference(source, copy)
    if difference:
        say(f"   the copy of the volume {volume} is not the same: {difference}")
        return False
    from kazma_core.english_count import count_noun

    files = sum(1 for e in source if e[1] == "file")
    size = sum(e[5] for e in source if e[1] == "file")
    say(f"   volume {_shown_volume(volume)}: {count_noun(files, 'file')}, {count_noun(len(source), 'entry', 'entries')}, "
        f"{size // 1024} KiB: every one the same on both engines")
    return True


def _make_network(docker: Docker, name: str, journal: Journal, say: Say) -> bool:
    if docker.call(ENGINE, "network", "inspect", name).ok:
        return True
    info = docker.json(DESKTOP, "network", "inspect", name)
    row = info[0] if isinstance(info, list) and info and isinstance(info[0], dict) else {}
    args = ["network", "create", "--driver", str(row.get("Driver") or "bridge")]
    for key, value in (row.get("Labels") or {}).items():
        args += ["--label", f"{key}={value}"]
    if row.get("Internal"):
        args.append("--internal")
    if row.get("Attachable"):
        args.append("--attachable")
    journal.note_created("networks", name)
    made = docker.call(ENGINE, *args, name)
    if not made.ok:
        say(f"   the network {name} could not be made on Kazma's engine: {made.why()}")
        return False
    return True


def _added_paths(docker: Docker, row: dict[str, Any]) -> list[str]:
    """Files a container holds outside its image and its mounts (compose
    writes ``configs`` there; someone may have copied a script in): the
    topmost added path of each, and each changed file."""
    name = _name(row)
    answer = docker.call(DESKTOP, "diff", name)
    if not answer.ok:
        return []
    mounts = [str(m.get("Destination") or "") for m in row.get("Mounts") or ()]
    changes = [(line[0], line[2:].strip()) for line in answer.out.splitlines() if len(line) > 2 and line[1] == " "]
    paths = {path for _kind, path in changes}

    def skipped(path: str) -> bool:
        return (path.startswith(_SKIP_PREFIXES) or "__pycache__" in path or path.endswith((".pyc", ".pid"))
                or any(path == m or path.startswith(m.rstrip("/") + "/") for m in mounts if m))

    top: list[str] = []
    for kind, path in sorted(changes, key=lambda c: c[1]):
        if skipped(path) or any(path == t or path.startswith(t.rstrip("/") + "/") for t in top):
            continue
        if kind == "A":
            top.append(path)
        elif kind == "C" and not any(p != path and p.startswith(path.rstrip("/") + "/") for p in paths):
            top.append(path)  # a changed file (a changed folder lists what changed in it)
    return top


#: How many carried paths the move names; the rest are counted. Neo4j's
#: start changes the owner of its whole folder, so docker diff lists every
#: file of it (248 lines on the drill VM, 2026-11-13).
_CARRIED_NAMED = 5


def _carry_added_files(docker: Docker, row: dict[str, Any], say: Say) -> bool:
    name = _name(row)
    paths = _added_paths(docker, row)
    for path in paths:
        parent = path.rsplit("/", 1)[0] or "/"
        ok, why, _ = docker.pipe(DESKTOP, ["cp", f"{name}:{path}", "-"], ENGINE, ["cp", "-a", "-", f"{name}:{parent}"],
                                 timeout=900)
        if not ok:
            say(f"   the file {path} added inside {name} could not be carried: {why}")
            return False
    if paths:
        shown = ", ".join(paths[:_CARRIED_NAMED])
        more = f" and {len(paths) - _CARRIED_NAMED} more" if len(paths) > _CARRIED_NAMED else ""
        say(f"   carried what was added or changed inside {name}: {shown}{more}")
    return True


def _seen_stopped(docker: Docker, name: str) -> bool:
    """Docker Desktop says the container is stopped (not running, not
    restarting); a container it cannot describe counts as running."""
    data = docker.json(DESKTOP, "inspect", name)
    state = (data[0].get("State") or {}) if isinstance(data, list) and data else {"Running": True}
    return not (state.get("Running") or state.get("Restarting"))


def _move_container(docker: Docker, moving: _Moving, plan: MovePlan, engine_image: str, *, journal: Journal,
                   copied: set[str], say: Say, fixed: Callable[[str], bool] | None = None) -> bool:
    """One container: stopped on Docker Desktop (kept), its volumes copied
    and checked, made on Kazma's engine, started as it was."""
    name = moving.name
    say(f"-> {name} ({', '.join(moving.purposes)})")
    journal.note_desktop(name, moving.row)
    for step in (["update", "--restart", "no", name], ["stop", "--time", str(_STOP_GRACE_S), name]):
        done = docker.call(DESKTOP, *step, timeout=_STOP_GRACE_S + 60)
        if not done.ok:
            say(f"   {name} could not be stopped on Docker Desktop: {done.why()}")
            return False
    # Never a volume copied while its container runs: seen stopped first.
    if not _seen_stopped(docker, name):
        say(f"   {name} still runs on Docker Desktop after its stop: nothing of it is copied")
        return False
    say("   stopped on Docker Desktop (it will not start there by itself; kept as the way back)")
    for volume in moving.volumes:
        if volume in copied:
            continue
        if not _copy_volume(docker, volume, plan.volumes.get(volume) or {}, desktop_image=moving.image,
                           engine_image=engine_image, journal=journal, say=say):
            return False
        copied.add(volume)
    for net in _user_networks(moving.row):
        if not _make_network(docker, net, journal, say):
            return False
    journal.note_created("containers", name)
    made = docker.call(ENGINE, *_create_args(moving.row, engine_image, fixed=fixed), timeout=300)
    if not made.ok:
        say(f"   {name} could not be made on Kazma's engine: {made.why()}")
        return False
    if not _carry_added_files(docker, moving.row, say):
        return False
    for net, aliases in _extra_networks(moving.row):
        extra = [arg for alias in aliases for arg in ("--alias", alias)]
        joined = docker.call(ENGINE, "network", "connect", *extra, net, name)
        if not joined.ok:
            say(f"   {name} could not join the network {net} on Kazma's engine: {joined.why()}")
            return False
    if moving.running:
        started = docker.call(ENGINE, "start", name, timeout=300)
        if not started.ok:
            say(f"   {name} could not be started on Kazma's engine: {started.why()}")
            return False
    return True


def _seen_working(docker: Docker, moving: _Moving, *, answering: Callable[[int], bool],
                 wait_s: float = START_WAIT_S, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> tuple[bool, str]:
    """A moved container that ran on Docker Desktop runs on Kazma's engine,
    passes its health check, and answers on its ports from Windows (through
    WSL's localhost forwarding: the addresses Kazma uses do not change)."""
    if not moving.running:
        return True, "stopped, as it was"
    deadline = clock() + wait_s
    why = "not running"
    while True:
        data = docker.json(ENGINE, "inspect", moving.name)
        state = (data[0].get("State") or {}) if isinstance(data, list) and data else {}
        health = str((state.get("Health") or {}).get("Status") or "")
        if not state.get("Running"):
            why = f"not running ({state.get('Status') or 'unknown'})"
        elif health and health != "healthy":
            why = f"health check: {health}"
        else:
            closed = [p for p in moving.ports if not answering(p)]
            if not closed:
                return True, "running" + (", healthy" if health else "") + (
                    f", answering on {', '.join(str(p) for p in moving.ports)}" if moving.ports else "")
            why = "nothing answers on port " + ", ".join(str(p) for p in closed) + " from Windows"
        if clock() >= deadline:
            return False, why
        sleep(3.0)


# ── rolling back ─────────────────────────────────────────────────────────


def _roll_back(docker: Docker, journal: Journal, say: Say, *, keep_engine_copies: bool) -> bool:
    """Undo what the journal says this move changed: Kazma's engine's new
    containers stopped (removed, with their volumes and networks, unless
    *keep_engine_copies*), Docker Desktop's originals given their restart
    policy back and started as they were. True when all of it was undone."""
    whole = True
    for name in reversed(journal.created("containers")):
        if not docker.call(ENGINE, "inspect", name).ok:
            continue
        docker.call(ENGINE, "update", "--restart", "no", name)
        docker.call(ENGINE, "stop", "--time", "30", name, timeout=180)
        if not keep_engine_copies and not docker.call(ENGINE, "rm", "-f", name).ok:
            say(f"   {name} could not be removed from Kazma's engine")
            whole = False
    if not keep_engine_copies:
        for volume in reversed(journal.created("volumes")):
            if docker.call(ENGINE, "volume", "inspect", volume).ok and \
                    not docker.call(ENGINE, "volume", "rm", volume).ok:
                say(f"   the volume {volume} could not be removed from Kazma's engine")
                whole = False
        for net in reversed(journal.created("networks")):
            if docker.call(ENGINE, "network", "inspect", net).ok:
                docker.call(ENGINE, "network", "rm", net)
    for name, original in (journal.data.get("desktop") or {}).items():
        if not docker.call(DESKTOP, "update", "--restart", _restart_arg(original.get("restart")), name).ok:
            say(f"   {name}'s restart policy could not be put back on Docker Desktop")
            whole = False
        if original.get("running") and not docker.call(DESKTOP, "start", name, timeout=300).ok:
            say(f"   {name} could not be started again on Docker Desktop: docker --context {DESKTOP} start {name}")
            whole = False
    journal.set_state("rolled_back" if whole else "rollback_incomplete")
    return whole


# ── the images Kazma's own containers run ───────────────────────────────


def _sandbox_image() -> str:
    from kazma_core.tools.code_exec import DEFAULT_DOCKER_IMAGE

    return (os.environ.get("KAZMA_CODE_EXEC_IMAGE") or DEFAULT_DOCKER_IMAGE).strip()


def _prepare_sandbox(docker: Docker, say: Say) -> None:
    """The code sandbox's image on the engine: pulled, else copied from
    Docker Desktop. Missing, it is pulled at the first python_exec."""
    image = _sandbox_image()
    if docker.call(ENGINE, "image", "inspect", image).ok:
        say(f"   the code sandbox's image {image} is on Kazma's engine")
        return
    if docker.call(ENGINE, "pull", image, timeout=1800).ok:
        say(f"   the code sandbox's image {image} pulled")
        return
    if docker.call(DESKTOP, "image", "inspect", image).ok and _engine_image(docker, image, lambda _m: None):
        say(f"   the code sandbox's image {image} copied from Docker Desktop")
        return
    say(f"   the code sandbox's image {image} could not be pulled now: the first python_exec pulls it")


def _worker_needed() -> bool:
    from kazma_core.documents.worker_container import isolation_selection

    return isolation_selection().get("mode") == "container"


def _prepare_worker_image(docker: Docker, root: Path | None, python: str, say: Say,
                          run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> bool:
    """The document worker image for this code on the engine, when documents
    run in the container profile: copied when Docker Desktop holds this
    code's build (the same build inputs, so the same image), else built."""
    try:
        needed = _worker_needed()
    except _setting_failures() as exc:  # a policy read: said, and treated as needed
        logger.warning("[engine-move] the document profile could not be read: %s", type(exc).__name__)
        needed = True
    if not needed:
        say("   documents run in the process profile: no worker image is needed")
        return True
    from kazma_core.documents import worker_image

    try:
        if root is None:
            raise FileNotFoundError("no checkout")
        tag = worker_image.tag_for(worker_image.build_fingerprint(root))
    except (FileNotFoundError, OSError) as exc:
        say(f"   the document worker image cannot be made here ({exc}): documents need it in the container profile")
        return False
    if docker.call(ENGINE, "image", "inspect", tag).ok:
        say(f"   the document worker image {tag} is on Kazma's engine")
        return True
    if docker.call(DESKTOP, "image", "inspect", tag).ok and _engine_image(docker, tag, say) == tag:
        say(f"   the document worker image {tag} copied from Docker Desktop")
        return True
    say(f"   building the document worker image {tag} on Kazma's engine (minutes the first time)")
    env = {**_child_env(), "DOCKER_CONTEXT": ENGINE}
    try:
        done = run([python, str(root / "scripts" / "document_worker_image.py"), "build"], cwd=str(root), env=env,
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=3600, check=False,
                   creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"   the build could not run: {exc}")
        return False
    if done.returncode != 0 or not docker.call(ENGINE, "image", "inspect", tag).ok:
        say(f"   the build failed: {_last_line(_text(done.stderr)) or _last_line(_text(done.stdout))}")
        return False
    return True


# ── the switch ───────────────────────────────────────────────────────────


def _setting_failures() -> tuple[type[Exception], ...]:
    """What reading or saving a setting can raise: the store away, a value
    refused."""
    from kazma_core.safety.deployment_policy import policy_read_failures
    from kazma_core.settings_validation import SettingRejected

    return (*policy_read_failures(), SettingRejected)


def stored_engine() -> str:
    """``containers.engine`` as stored (``kazma``, ``desktop`` or ``""``)."""
    from kazma_core.config_store import get_config_store
    from kazma_core.docker_cli import normalize_engine

    return normalize_engine(get_config_store().get(ENGINE_KEY))


def _write_engine(value: str) -> None:
    """``containers.engine`` set (validated, as Settings sets it). Raises
    when it cannot be saved."""
    from kazma_core.config_store import get_config_store
    from kazma_core.settings_validation import validate_setting

    checked, category = validate_setting(ENGINE_KEY, value)
    get_config_store().set(ENGINE_KEY, checked, category=category or "containers")


def keep_desktop() -> bool:
    """Write Docker Desktop down as Kazma's engine when nothing is chosen
    and Docker Desktop is here. True when it wrote it.

    Setting Kazma's engine up records it, and a recorded engine is Kazma's
    choice while nothing is chosen: on an install whose containers live on
    Docker Desktop, the next start would look for them on an empty engine.
    So an existing Docker Desktop install says so first; the move switches."""
    from kazma_core.docker_cli import engine_installed

    if stored_engine() or not engine_installed("desktop"):
        return False
    _write_engine("desktop")
    return True


@dataclass
class MoveSteps:
    """What the move is given: the guard's hold, the setting's writer, the
    guard's record, where the checkout is. Faked in the tests; a default
    left None is this module's own, read when it is used."""

    hold: Any
    say: Say
    answering: Callable[[int], bool]
    set_engine: Callable[[str], None] | None = None
    guard_record: Path | None = None
    root: Path | None = None
    python: str = ""
    sleep: Callable[[float], None] | None = None
    start_wait_s: float | None = None
    fixed: Callable[[str], bool] | None = None
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run

    def write_engine(self, value: str) -> None:
        (self.set_engine or _write_engine)(value)


def _tell_guard(steps: MoveSteps, engine: str) -> None:
    if steps.guard_record is None:
        return
    from kazma_core.guard_dependencies import set_record_engine

    try:
        set_record_engine(steps.guard_record, engine)
    except OSError as exc:
        steps.say(f"   the guard's record could not be updated ({exc}); Kazma tells it at its start")


def _prepare(docker: Docker, plan: MovePlan, steps: MoveSteps) -> dict[str, str] | None:
    """Everything that can be done while Kazma runs: images on the engine,
    each able to copy a volume. ``{container: engine image}``, or None."""
    say = steps.say
    say("-> Preparing Kazma's engine while Kazma runs (images; nothing Kazma uses is touched)")
    images: dict[str, str] = {}
    for moving in plan.containers:
        ref = _engine_image(docker, moving.image, say)
        if ref is None:
            return None
        if moving.volumes and not (_has_tar(docker, DESKTOP, moving.image) and _has_tar(docker, ENGINE, ref)):
            say(f"   {moving.name}'s image has no tar program, so its volumes cannot be copied and checked")
            return None
        images[moving.name] = ref
        say(f"   {moving.name}'s image is on Kazma's engine ({ref})")
    _prepare_sandbox(docker, say)
    if not _prepare_worker_image(docker, steps.root, steps.python, say, run=steps.run):
        return None
    return images


def _undo(docker: Docker, journal: Journal, steps: MoveSteps, *, switched: bool) -> None:
    say = steps.say
    say("-> Putting everything back: Kazma stays on Docker Desktop")
    if switched:
        try:
            steps.write_engine("desktop")
        except _setting_failures() as exc:  # the setting store: said, with the fix
            say(f"   containers.engine could not be set back ({type(exc).__name__}): choose Docker Desktop in "
                "Settings -> System -> Container engine")
        _tell_guard(steps, "desktop")
    whole = _roll_back(docker, journal, say, keep_engine_copies=switched)
    if switched:
        say(f"   the copies on Kazma's engine are stopped and kept: Kazma may have written to them. Remove them "
            f"(docker --context {ENGINE} rm NAME, and their volumes) before running this again.")
    if not whole:
        say("   Not everything could be put back (above). Run kazma setup engine --move-from-desktop again: "
            "it finishes the rollback first.")


def move(docker: Docker, plan: MovePlan, journal: Journal, steps: MoveSteps) -> bool:
    """Carry out *plan* (see the module docstring). True when Kazma runs on
    its own engine with every container moved and checked."""
    say = steps.say
    images = _prepare(docker, plan, steps)
    if images is None:
        say("Nothing was moved: Kazma keeps running on Docker Desktop.")
        return False
    conflicts = engine_conflicts(docker, plan)
    if conflicts:
        for line in conflicts:
            say(f"   {line}")
        say("Nothing was moved: Kazma keeps running on Docker Desktop.")
        return False
    hold = steps.hold
    if not hold.stop():
        say("Nothing was moved: Kazma keeps running on Docker Desktop.")
        return False
    journal.begin(_stored_or_desktop())
    try:
        if not _move_all(docker, plan, journal, steps, images):
            _undo(docker, journal, steps, switched=False)
            hold.resume()
            say("The move stopped: Kazma runs on Docker Desktop, as before.")
            return False
    except KeyboardInterrupt:
        # Stopped by hand half way: put back before giving the console up
        # (a second Ctrl+C leaves the journal, and the next run finishes it).
        say("Interrupted: putting everything back.")
        _undo(docker, journal, steps, switched=False)
        hold.resume()
        raise
    journal.set_state("switched")
    _tell_guard(steps, "kazma")
    say("-> containers.engine = kazma")
    if hold.held:
        say("-> Starting Kazma on its own engine")
        result = hold.resume()
        if result != "up":
            say(f"   Kazma was not seen ready ({result})")
            _undo(docker, journal, steps, switched=True)
            say("The move stopped: Kazma uses Docker Desktop again (it follows the setting without a restart).")
            return False
        say("-> Kazma is up on its own engine.")
    journal.set_state("done")
    names = [m.name for m in plan.containers]
    if names:
        say("-> On Docker Desktop, " + ", ".join(names) + " stay stopped and kept: they are the way back "
            f"(docker --context {DESKTOP} start NAME, and Docker Desktop chosen in Settings -> System -> "
            "Container engine). Remove them once you are sure.")
    say("Done." if hold.running else "Done. Start Kazma: it uses its own engine.")
    return True


def _move_all(docker: Docker, plan: MovePlan, journal: Journal, steps: MoveSteps, images: dict[str, str]) -> bool:
    """Every container moved and seen working, then the setting switched.
    False (said) at the first that fails: nothing is switched then."""
    say = steps.say
    copied: set[str] = set()
    wait_s = START_WAIT_S if steps.start_wait_s is None else steps.start_wait_s
    for moving in plan.containers:
        if not _move_container(docker, moving, plan, images[moving.name], journal=journal, copied=copied,
                               say=say, fixed=steps.fixed):
            return False
        ok, why = _seen_working(docker, moving, answering=steps.answering, wait_s=wait_s,
                                sleep=steps.sleep or time.sleep)
        say(f"   on Kazma's engine: {why}")
        if not ok:
            return False
    try:
        steps.write_engine("kazma")
    except _setting_failures() as exc:  # the setting store refused: nothing switched, all undone
        say(f"   containers.engine could not be set ({type(exc).__name__})")
        return False
    return True


def _stored_or_desktop() -> str:
    try:
        return stored_engine() or "desktop"
    except _setting_failures():  # recorded for the rollback only
        return "desktop"


def recover(docker: Docker, journal: Journal, steps: MoveSteps) -> str:
    """An earlier move that stopped half way (its process died): rolled back
    exactly from its journal, or found finished. Returns ``"none"``,
    ``"rolled_back"``, ``"finished"`` or ``"incomplete"``."""
    if not journal.unfinished():
        return "none"
    say = steps.say
    if journal.state == "switched" and steps.hold.ready():
        journal.set_state("done")
        say("An earlier move had switched Kazma to its own engine, and Kazma is ready there: it is finished.")
        return "finished"
    say("An earlier move stopped half way: putting back what it changed first.")
    _undo(docker, journal, steps, switched=journal.state == "switched")
    return "rolled_back" if journal.state == "rolled_back" else "incomplete"
