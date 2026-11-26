"""Find the docker CLI even when it is not on PATH.

Live 2026-09-25: Docker Desktop updated itself to 4.91.0 (``docker.exe``
rewritten at 01:35, reboot at 01:38) and the new version left
``C:\\Program Files\\Docker\\Docker\\resources\\bin`` off the system PATH.
Everything Kazma starts from the registry environment -- the ``KazmaAgent``
scheduled task, the guard, the server -- then failed ``shutil.which("docker")``,
and the six-hourly Postgres dump, which runs ``pg_dump`` inside the database
container through ``docker exec``, stopped: "native_pg_backup produced no
dump". The CLI was installed and the container was running the whole time;
only the lookup was wrong.

Lookup order: ``KAZMA_DOCKER_BIN`` (an explicit path), then on Windows the
CLI Kazma's own engine setup installed (``%LOCALAPPDATA%\\Kazma\\engine\\bin``:
it stays when Docker Desktop is uninstalled, and Kazma does not depend on a
copy another product may take away, 2026-11-14), then PATH, then the places
Docker installs itself on each OS. A CLI found off PATH is used, and
said once, so the operator can put it back on PATH instead of depending on
the fallback without knowing it. A reload is then enough: the server adopts
PATH entries the OS settings gained since its supervisor started
(:mod:`kazma_core.path_refresh`).

Which engine (2026-11-12, ``docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md``):
the setting ``containers.engine`` is ``kazma`` (Kazma's own container engine:
Docker Engine in the ``kazma-engine`` WSL distro, started at boot by the
``KazmaEngine`` task, reached through the ``kazma-engine`` Docker context;
setup chooses it when it installs that engine), ``desktop`` (Docker Desktop),
or unset (the docker CLI's current context, as before). Kazma pins the choice
in ``DOCKER_CONTEXT`` for every docker call it starts
(:func:`apply_engine_choice`) instead of trusting the CLI's current context:
Docker Desktop, when someone opens it, may set that back to itself. The first
:func:`find_docker_cli` of a Kazma process applies the stored choice, so the
server, the CLI and scripts all use one engine. A context the operator set
in the environment wins. The guard loads this file by path and takes the
choice from the server's record, so it stays standard-library only.

A CLI the operating system refuses to start is passed over (live
2026-11-26, from 10:56 UTC): Windows began refusing Kazma's own engine's
``docker.exe`` ("An Application Control policy has blocked this file").
The file was there, so it was chosen; every docker call then failed, the
guard read the engine as down although it answered, restarted Docker in it
again and again, and the server's code execution waited for an engine that
was up. A candidate is now started once (``docker --version``) before it is
chosen. One that is refused is said once at INFO, calmly -- what Windows
checked, what Kazma used instead -- and the next CLI is used (PATH, then
Docker's own install folders, where Docker Desktop's signed CLI lives). When
no CLI is left, :mod:`kazma_core.windows_trust` says which feature waits
(its one alert). A refusal is tried again
after :data:`REFUSED_RECHECK_S`, so a CLI that is allowed again comes back
by itself; a CLI that started is remembered for as long as the file does
not change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess  # nosec B404 - runs only the docker CLI's own --version, never shell
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

logger = logging.getLogger(__name__)

__all__ = [
    "DESKTOP_CONTEXT",
    "ENGINE_CHOICES",
    "ROUTE_ENV",
    "DockerCommand",
    "carry_env",
    "distro_path",
    "docker_args_start",
    "docker_argv",
    "docker_command",
    "is_docker_exec",
    "ENGINE_KEY",
    "KAZMA_CONTEXT",
    "KAZMA_DISTRO",
    "apply_engine_choice",
    "engine_host_path",
    "engine_in_force",
    "engine_installed",
    "engine_label",
    "find_docker_cli",
    "kazma_engine_dir",
    "kazma_engine_disk_dir",
    "normalize_engine",
    "refusal_codes",
    "refused_by_os",
    "refused_clis",
    "wsl_distro_location",
]

#: The setting that chooses Kazma's container engine.
ENGINE_KEY = "containers.engine"
#: ``kazma``: Kazma's own engine; ``desktop``: Docker Desktop.
ENGINE_CHOICES = ("kazma", "desktop")
KAZMA_CONTEXT = "kazma-engine"
#: Kazma's engine's WSL distro (``container_engine_setup`` imports it there).
KAZMA_DISTRO = "kazma-engine"
DESKTOP_CONTEXT = "desktop-linux"

_ENV = "KAZMA_DOCKER_BIN"
_reported: set[str] = set()
_reported_lock = threading.Lock()

#: How long a CLI the operating system refused stays passed over before it
#: is tried again (an operator who allows it again needs no restart).
REFUSED_RECHECK_S = 300.0
#: A start that takes longer than this still started: slow is not refused.
_START_TIMEOUT_S = 15.0
#: (path, mtime_ns, size) -> (why it was refused or None, when it was tried)
_started: dict[tuple[str, int, int], tuple[str | None, float]] = {}
#: A CLI that started is asked again after this long: Windows' policy can
#: arrive while Kazma runs (live 2026-11-26, 10:56 UTC), and a start
#: remembered for the life of the file kept a refused CLI chosen.
STARTED_RECHECK_S = 900.0


def _candidates() -> list[Path]:
    """Where Docker installs its CLI, per OS. Order is preference."""
    if os.name == "nt":
        # Kazma's own engine installs Docker's CLI here (container_engine_setup);
        # it is first: it works without Docker Desktop installed at all.
        kazma = [kazma_engine_dir() / "bin" / "docker.exe"]
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432")] + [r"C:\Program Files"]
        out = kazma + [Path(r) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe" for r in roots if r]
        data = os.environ.get("ProgramData") or r"C:\ProgramData"
        out.append(Path(data) / "DockerDesktop" / "version-bin" / "docker.exe")
        return list(dict.fromkeys(out))
    out = [Path("/usr/bin/docker"), Path("/usr/local/bin/docker"), Path("/snap/bin/docker")]
    if sys.platform == "darwin":
        out = [
            Path("/usr/local/bin/docker"),
            Path("/opt/homebrew/bin/docker"),
            Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
            Path.home() / ".docker" / "bin" / "docker",
        ]
    return out


def _say_once(key: str, message: str, *args: object, level: int = logging.WARNING) -> None:
    with _reported_lock:
        if key in _reported:
            return
        _reported.add(key)
    logger.log(level, message, *args)


def _on_windows() -> bool:
    return os.name == "nt"


#: Bound here, not looked up at the call: a test that stands in for
#: ``subprocess.run`` to watch the docker calls Kazma makes must not see this
#: start among them.
_RUN = subprocess.run


def _start(candidate: Path) -> None:
    """Start ``candidate --version`` once; an OSError says Windows refused it."""
    _RUN(  # nosec B603 - a fixed argument list, no shell
        [str(candidate), "--version"],
        capture_output=True,
        timeout=_START_TIMEOUT_S,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _refused_by_os(path: str | Path, *, fresh: bool = False) -> str | None:
    """Why Windows refuses to start the CLI at ``path``, or None.

    Only a refusal by the system's policy counts
    (``windows_policy.REFUSAL_CODES``): a file that cannot be read, or is no
    program, is left to fail as it did before. Elsewhere than Windows the
    answer is None without a start.

    The answer is kept per file version: a CLI that started is asked again
    after :data:`STARTED_RECHECK_S`, a refusal after
    :data:`REFUSED_RECHECK_S`; *fresh* asks now (a docker call that could not
    start its CLI)."""
    if not _on_windows():
        return None
    candidate = Path(path)
    try:
        stat = candidate.stat()
    except OSError:
        return None
    key = (str(candidate), stat.st_mtime_ns, stat.st_size)
    now = time.monotonic()
    with _reported_lock:
        held = _started.get(key)
    keep = STARTED_RECHECK_S if held is not None and held[0] is None else REFUSED_RECHECK_S
    if held is not None and not fresh and now - held[1] < keep:
        return held[0]
    why: str | None = None
    try:
        _start(candidate)
    except subprocess.TimeoutExpired:
        why = None  # it started
    except OSError as exc:
        code = getattr(exc, "winerror", None)
        why = f"WinError {code}: {exc.strerror}" if code in refusal_codes() else None
    with _reported_lock:
        _started[key] = (why, now)
    return why


def refused_by_os(path: str | Path, *, fresh: bool = False) -> str | None:
    """Why Windows' file check does not allow the program at ``path`` to start
    (``WinError 4551: ...``), or None when it starts. Kept per file version
    (:func:`_refused_by_os`); *fresh* starts it now. Read by
    :mod:`kazma_core.windows_trust` and by the guard, which asks fresh when a
    docker call could not start its CLI."""
    return _refused_by_os(path, fresh=fresh)


def _usable(path: str | Path) -> bool:
    """True when ``path`` starts; a refusal is said once per CLI and reason.

    Calm (the owner's rule, 2026-11-26): Windows checking a program is
    Windows doing its job. Kazma uses the next CLI, says so at INFO with the
    operator's detail, and never asks anyone to change the protection."""
    why = _refused_by_os(path)
    if why is None:
        return True
    with _reported_lock:
        first = f"refused:{path}:{why}" not in _reported
        _reported.add(f"refused:{path}:{why}")
    if first:
        logger.info(
            "[docker] Windows checked %s and did not allow it to start on this PC; Kazma uses "
            "the next docker CLI (a signed one, when one is installed). Detail: %s; tried again "
            "every %d s.", path, why, int(REFUSED_RECHECK_S),
        )
    return False


#: ``kazma_core.windows_policy``, loaded once (by path outside a Kazma process).
_policy = None


def _windows_policy():
    """``kazma_core.windows_policy``: imported in a Kazma process, loaded by
    path where this file was (the guard, prepush, the reload preflight),
    which never import Kazma. None when it cannot be read."""
    global _policy
    if _policy is None:
        try:
            if __name__ == "kazma_core.docker_cli":
                from kazma_core import windows_policy as module
            else:
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    f"{__name__}_windows_policy", Path(__file__).with_name("windows_policy.py"))
                if spec is None or spec.loader is None:
                    return None
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
        except (ImportError, OSError, SyntaxError):
            return None
        _policy = module
    return _policy


def refusal_codes() -> frozenset[int]:
    """Windows' answers that refuse a program by policy
    (``windows_policy.REFUSAL_CODES``, the one definition), for this file's
    callers that load it by path: the guard, prepush."""
    policy = _windows_policy()
    return frozenset(getattr(policy, "REFUSAL_CODES", ()) or ())


def _checks_files() -> bool:
    """Smart App Control checks every program on this PC
    (``kazma_core.windows_policy``)."""
    policy = _windows_policy()
    try:
        return bool(policy is not None and policy.checks_files())
    except (OSError, AttributeError):
        return False


def _signed_cli(own: Path) -> str | None:
    """While Smart App Control checks every program: Docker Desktop's own CLI
    (signed by Docker Inc; the static-zip CLI Kazma's engine installs carries
    no signature, measured 2026-11-26), which reaches Kazma's engine through
    the same context. Said once at INFO; None when Docker Desktop's is not here."""
    for candidate in _candidates():
        if candidate != own and candidate.is_file() and _usable(candidate):
            with _reported_lock:
                first = f"signed:{candidate}" not in _reported
                _reported.add(f"signed:{candidate}")
            if first:
                logger.info("[docker] Windows checks each program on this PC (Smart App Control), so "
                            "Kazma uses Docker's signed CLI (%s) with its own engine", candidate)
            return str(candidate)
    return None


def refused_clis() -> list[str]:
    """The docker CLIs on this machine that Windows' file check does not allow
    to start (answers kept per file version, :func:`_refused_by_os`). With
    :func:`find_docker_cli` None it tells "Windows did not allow the CLI"
    from "no CLI is installed"; the guard reads it so a refused CLI is never
    taken for a stopped engine."""
    explicit = (os.environ.get(_ENV) or "").strip().strip('"')
    found = [Path(explicit)] if explicit else []
    if _on_windows():
        found.append(kazma_engine_dir() / "bin" / "docker.exe")
    on_path = shutil.which("docker")
    if on_path:
        found.append(Path(on_path))
    found += _candidates()
    return [str(p) for p in dict.fromkeys(found) if p.is_file() and _refused_by_os(p)]


def find_docker_cli() -> str | None:
    """Absolute path of a usable docker CLI, or ``None``. In a Kazma process
    the first call also pins the chosen container engine (see the module
    docstring), so the CLI it returns talks to that engine. While Smart App
    Control checks every program, Docker's signed CLI comes before Kazma's
    engine's unsigned one, which is then never started (:func:`_signed_cli`)."""
    _apply_stored_choice_once()
    explicit = (os.environ.get(_ENV) or "").strip().strip('"')
    if explicit:
        if not Path(explicit).is_file():
            _say_once(
                f"env:{explicit}",
                "[docker] %s=%s does not exist -- falling back to PATH and the "
                "standard install locations", _ENV, explicit,
            )
        elif _usable(explicit):
            return explicit
        else:
            # It exists: Windows did not let it start. Said as that, calmly,
            # never as a missing file (the audit's F1, 2026-11-26).
            _say_once(
                f"env-refused:{explicit}",
                "[docker] %s=%s: Windows checked it and did not allow it to start on this PC; "
                "Kazma uses the next docker CLI (PATH, then Docker's install folders)", _ENV, explicit,
                level=logging.INFO,
            )
    if _on_windows():
        own = kazma_engine_dir() / "bin" / "docker.exe"
        if own.is_file() and _checks_files():
            signed = _signed_cli(own)
            if signed:
                return signed
        if own.is_file() and _usable(own):
            return str(own)
    on_path = shutil.which("docker")
    if on_path and _usable(on_path):
        return on_path
    for candidate in _candidates():
        if candidate.is_file() and _usable(candidate):
            if candidate.parent.parent.name == "engine" and candidate.parent.parent.parent.name == "Kazma":
                return str(candidate)  # Kazma's own engine's CLI lives there by design
            _say_once(
                f"found:{candidate}",
                "[docker] the docker CLI is not on PATH; using %s. Docker Desktop "
                "updates can drop it from PATH -- add its folder back and reload "
                "Kazma (the server re-reads PATH from the OS settings), or set %s.",
                candidate, _ENV,
            )
            return str(candidate)
    return None


# ── which engine ──────────────────────────────────────────────────────────


def _docker_config_dir() -> Path:
    raw = (os.environ.get("DOCKER_CONFIG") or "").strip()
    return Path(raw) if raw else Path(os.path.expanduser("~")) / ".docker"


def _context_exists(name: str) -> bool:
    """Whether the docker CLI knows a context *name*. Read from its files
    (``contexts/meta/<sha256 of the name>/meta.json``): no process started."""
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    meta = _docker_config_dir() / "contexts" / "meta" / digest / "meta.json"
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("Name") == name
    except (OSError, ValueError, AttributeError):
        return False


def _context_in_force() -> str:
    """The Docker context a docker call uses now: ``DOCKER_CONTEXT``, else the
    CLI's current context (its ``config.json``), else ``default``."""
    pinned = (os.environ.get("DOCKER_CONTEXT") or "").strip()
    if pinned:
        return pinned
    try:
        data = json.loads((_docker_config_dir() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "default"
    return str((data or {}).get("currentContext") or "default") if isinstance(data, dict) else "default"


#: How Kazma names each engine where it says which one it waited for.
_ENGINE_LABELS = {
    "kazma": "Kazma's container engine",
    "desktop": "Docker Desktop",
    "system": "the Docker service",
    "other": "the Docker engine",
}


def engine_in_force() -> str:
    """Which engine a docker call reaches now: ``kazma`` (Kazma's own),
    ``desktop`` (Docker Desktop), ``system`` (a Linux machine's Docker
    service) or ``other`` (a context or ``DOCKER_HOST`` the operator chose).

    Read from the context in force, as :func:`engine_host_path` is: no
    process started, nothing asked of the engine, so a down engine is named
    as well as a running one. The ``default`` context is Docker Desktop's
    pipe or socket on Windows and macOS, the system service's on Linux.
    Readiness (the code sandbox, the document isolation report) and the
    guard's disk watch read it, so each says the engine it waited for.
    """
    if os.environ.get("DOCKER_HOST"):
        return "other"
    context = _context_in_force()
    if context == KAZMA_CONTEXT or _kazma_by_choice():
        return "kazma"
    if context == DESKTOP_CONTEXT:
        return "desktop"
    if context == "default":
        return "system" if sys.platform.startswith("linux") else "desktop"
    return "other"


def engine_label(engine: str | None = None) -> str:
    """How Kazma names *engine* (default: the one in force) in what it says:
    "Kazma's container engine", "Docker Desktop", "the Docker service", or
    the operator's own context or ``DOCKER_HOST``."""
    if engine is None:
        engine = engine_in_force()
        if engine == "other":
            if os.environ.get("DOCKER_HOST"):
                return "the Docker engine DOCKER_HOST names"
            return f"the Docker engine of the {_context_in_force()!r} context"
    return _ENGINE_LABELS.get(engine, _ENGINE_LABELS["other"])


def kazma_engine_dir() -> Path:
    """``%LOCALAPPDATA%\\Kazma\\engine``: Kazma's engine's folder on Windows
    (its Docker CLI, its scripts, its downloads and its distro's disk).
    ``container_engine_setup`` installs there; the guard watches its disk."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Kazma" / "engine"


def wsl_distro_location(name: str) -> tuple[Path, str] | None:
    """Where WSL keeps the distro *name*'s disk: (its folder, the disk's file
    name), from WSL's own registration (``HKCU\\...\\Lxss``: what ``wsl
    --import`` and ``wsl --manage --move`` write). None when it is not
    registered, or not on Windows."""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    root = r"Software\Microsoft\Windows\CurrentVersion\Lxss"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root) as lxss:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(lxss, index)
                except OSError:
                    return None
                index += 1
                with winreg.OpenKey(lxss, sub) as key:
                    try:
                        if str(winreg.QueryValueEx(key, "DistributionName")[0]) != name:
                            continue
                        base = str(winreg.QueryValueEx(key, "BasePath")[0])
                    except OSError:
                        continue
                    try:
                        vhd = str(winreg.QueryValueEx(key, "VhdFileName")[0]) or "ext4.vhdx"
                    except OSError:
                        vhd = "ext4.vhdx"
                    if base.startswith("\\\\?\\"):
                        base = base[4:]
                    return Path(base), vhd
    except OSError:
        return None


def kazma_engine_disk_dir() -> Path:
    """The folder holding the ``kazma-engine`` distro's virtual disk: where
    WSL has it registered (a data folder on another drive, ``data_folder``),
    else where setup imports it (``wsl --import`` puts its ``ext4.vhdx``
    there). What fills as the engine's images, containers and volumes grow."""
    found = wsl_distro_location(KAZMA_DISTRO)
    return found[0] if found is not None else kazma_engine_dir() / KAZMA_DISTRO


def engine_installed(engine: str) -> bool:
    """Whether this machine has *engine*'s Docker context (``kazma``,
    ``desktop``)."""
    context = {"kazma": KAZMA_CONTEXT, "desktop": DESKTOP_CONTEXT}.get(engine)
    return bool(context) and _context_exists(context)


def normalize_engine(value: object) -> str:
    """A stored choice as one of :data:`ENGINE_CHOICES`, or ``""``: no
    choice, and Kazma uses the docker CLI's current context as it always
    did."""
    text = str(value or "").strip().lower()
    return text if text in ENGINE_CHOICES else ""


def _chosen_context(engine: object) -> str | None:
    """The Docker context for *engine*, or None for the CLI's current one.

    A choice is pinned only when its context exists: Kazma's engine without
    its context (a removed engine, a copied settings store) is said once and
    leaves the CLI's choice, rather than every docker call failing."""
    choice = normalize_engine(engine)
    if not choice:
        return None
    context = KAZMA_CONTEXT if choice == "kazma" else DESKTOP_CONTEXT
    if _context_exists(context):
        return context
    if choice == "kazma" and _on_windows() and wsl_distro_location(KAZMA_DISTRO) is not None:
        return None  # reached through wsl.exe, no Windows context needed (docker_command)
    _say_once(f"no-context:{context}",
              "[docker] the container engine chosen (%s) has no Docker context %r on this "
              "machine; the docker CLI's current context is used. %s", choice, context,
              "Set Kazma's engine up again: kazma setup engine" if choice == "kazma"
              else "Start Docker Desktop once to create it")
    return None


#: The engine the stored choice names (``kazma``, ``desktop`` or ""), as
#: :func:`apply_engine_choice` last saw it.
_engine_choice = ""


def _kazma_by_choice() -> bool:
    """Kazma's own engine is the stored choice and its distro is registered,
    with no engine of the operator's own in the way: it is reached through
    wsl.exe whether or not the Windows CLI's ``kazma-engine`` context exists
    (only the Windows docker.exe creates that, and Windows may refuse it:
    review M4, 2026-11-26)."""
    if _engine_choice != "kazma" or not _on_windows() or os.environ.get("DOCKER_HOST"):
        return False
    pinned = (os.environ.get("DOCKER_CONTEXT") or "").strip()
    if pinned and pinned != KAZMA_CONTEXT:
        return False
    return wsl_distro_location(KAZMA_DISTRO) is not None


#: The DOCKER_CONTEXT this module set; one the operator set is never changed.
_applied: str | None = None
_apply_lock = threading.Lock()
#: Whether this process has applied the stored choice (see find_docker_cli).
_choice_applied = False


def apply_engine_choice(engine: object) -> str | None:
    """Pin *engine*'s context in ``DOCKER_CONTEXT`` for this process and every
    docker call it starts (children inherit it, the document broker's
    included). Returns the context in force (None: the CLI's current one).
    The operator's own ``DOCKER_CONTEXT`` or ``DOCKER_HOST`` is left alone."""
    global _applied, _choice_applied, _engine_choice
    with _apply_lock:
        _choice_applied = True
        _engine_choice = normalize_engine(engine)
        current = os.environ.get("DOCKER_CONTEXT")
        if os.environ.get("DOCKER_HOST") or (current and current != _applied):
            return current or None
        wanted = _chosen_context(engine)
        if wanted:
            os.environ["DOCKER_CONTEXT"] = wanted
        else:
            os.environ.pop("DOCKER_CONTEXT", None)
        if wanted != _applied:
            logger.info("[docker] Kazma's container engine: %s",
                        {KAZMA_CONTEXT: "Kazma's own (the kazma-engine context)",
                         DESKTOP_CONTEXT: "Docker Desktop"}.get(wanted or "",
                                                                "the docker CLI's current context"))
        _applied = wanted
        return wanted


def _apply_stored_choice_once() -> None:
    """In a Kazma process (not the guard, which loads this file by path and
    takes the choice from the server's record): apply the stored choice
    before the first docker call, so a CLI command, a script or the server
    uses the same engine. The server applies it at boot and follows changes
    (:mod:`kazma_core.container_engine`)."""
    if _choice_applied or __name__ != "kazma_core.docker_cli":
        return
    try:
        from kazma_core.container_engine import apply_engine_setting
        from kazma_core.safety.deployment_policy import policy_read_failures
    except ImportError:
        return
    try:
        apply_engine_setting()
    except policy_read_failures():
        logger.warning("[docker] the container engine choice could not be applied; "
                       "the docker CLI's current context is used", exc_info=True)


# ── paths an engine can mount ─────────────────────────────────────────────

#: Windows' GetDriveTypeW: a fixed disk is the one kind WSL mounts.
_DRIVE_FIXED = 3


def _drive_is_fixed(drive: str) -> bool:
    try:
        import ctypes

        return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == _DRIVE_FIXED
    except (AttributeError, OSError):
        return True


def engine_host_path(path: str, *, windows: bool | None = None) -> str | None:
    """*path* (a folder on this machine) as the engine in force must be told
    it in a bind mount, or None when that engine cannot mount it.

    Not Windows: the path itself. Windows: Docker Desktop and the CLI's own
    engine take the Windows path (Docker Desktop translates it); Kazma's
    engine mounts fixed drive ``G:`` at ``/g`` (its distro mounts drives at
    the root), and cannot mount a network share or a mapped, ``subst`` or
    removable drive. Decided from the context in force, never from a query
    of the engine (2026-11-12: a cached answer from an engine that was down
    sent ``G:\\...`` to it for a minute). *windows* defaults to this OS."""
    if windows is None:
        windows = os.name == "nt"
    if not windows or os.environ.get("DOCKER_HOST") or not (
            _context_in_force() == KAZMA_CONTEXT or _kazma_by_choice()):
        return path
    win = PureWindowsPath(path)
    if len(win.drive) != 2 or not win.drive.endswith(":") or not _drive_is_fixed(win.drive):
        return None
    rest = "/".join(win.parts[1:])
    return f"/{win.drive[0].lower()}" + (f"/{rest}" if rest else "")


# ── how a docker call is made: one decision (2026-11-26) ─────────────────
#
# Kazma's own engine is Docker Engine inside the ``kazma-engine`` WSL
# distro. Its docker CLI is reached through Microsoft's signed ``wsl.exe``
# (``wsl.exe -d kazma-engine -u root --exec docker ...``): a Linux program
# inside WSL is outside Windows' file check, so Kazma needs no Windows
# ``docker.exe`` for its own engine. Measured on the owner's PC (26 Nov,
# medians, the Windows CLI first): ``docker version`` 145 ms either way,
# ``docker exec`` 281 -> 152 ms, ``docker run --rm`` 699 -> 425 ms, 50 MB out
# of ``docker exec`` 551 -> 316 ms, 20 MB into ``docker exec -i`` 334 ->
# 198 ms, bytes intact both ways. The Windows CLI stays the route for Docker
# Desktop and the operator's own engine, and the fallback where Windows allows
# it (an argument the distro cannot reach, ``KAZMA_DOCKER_ROUTE=cli``).

#: ``auto`` (the default): Kazma's engine through wsl.exe, any other engine
#: through the Windows CLI; ``cli``: always the Windows CLI; ``wsl``: the
#: distro's CLI even when the context in force is another (diagnostics).
ROUTE_ENV = "KAZMA_DOCKER_ROUTE"
#: The distro's own docker CLI, as root (the engine's distro is Kazma's own),
#: started with ``--exec``: no Linux shell reads the arguments (AGENTS §26).
_WSL_ARGS = ("-d", KAZMA_DISTRO, "-u", "root", "--exec", "docker")
#: A network share (two backslashes): never reachable from the distro.
_UNC = "\\\\"
#: An absolute Windows path (``C:\\x``, ``C:/x``).
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
def _wsl_exe() -> str | None:
    """Microsoft's ``wsl.exe`` in System32 (never one found on PATH)."""
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    path = Path(root) / "System32" / "wsl.exe"
    return str(path) if path.is_file() else None


def _distro_path(path: str) -> str | None:
    """*path* on this machine as the engine's distro reads it (``G:\\x`` ->
    ``/g/x``: its drives are mounted at the root), or None (a network share,
    a removable, mapped or ``subst`` drive)."""
    win = PureWindowsPath(path)
    if len(win.drive) != 2 or not win.drive.endswith(":") or not _drive_is_fixed(win.drive):
        return None
    rest = "/".join(win.parts[1:])
    return f"/{win.drive[0].lower()}" + (f"/{rest}" if rest else "")


def _cross(arg: str, before: str | None) -> str | None:
    """*arg* as the distro's docker CLI must read it, or None when it names a
    place the distro cannot reach. Paths on this machine are translated
    wherever Kazma passes them: a whole argument, ``--opt=<path>``, a
    ``-v <path>:<dst>`` source, a ``--mount`` ``src=``/``source=`` field."""
    if before in ("-v", "--volume") and _WINDOWS_PATH.match(arg):
        cut = arg.find(":", 2)
        host, rest = (arg, "") if cut < 0 else (arg[:cut], arg[cut:])
        crossed = _distro_path(host)
        return None if crossed is None else crossed + rest
    if arg.startswith("--mount="):
        spec = _cross(arg[len("--mount="):], "--mount")
        return None if spec is None else f"--mount={spec}"
    if before == "--mount" or (arg.startswith(("type=", "src=", "source=")) and "," in arg):
        fields = []
        for field in arg.split(","):
            key, eq, value = field.partition("=")
            if eq and key in ("src", "source") and _WINDOWS_PATH.match(value):
                crossed = _distro_path(value)
                if crossed is None:
                    return None
                field = f"{key}={crossed}"
            fields.append(field)
        return ",".join(fields)
    if _WINDOWS_PATH.match(arg):
        return _distro_path(arg)
    if arg.startswith(_UNC) or f"={_UNC}" in arg or (before in ("-v", "--volume") and arg.startswith(_UNC)):
        return None  # a network share: the distro cannot reach it
    if arg.startswith("--") and "=" in arg:
        name, _, value = arg.partition("=")
        if _WINDOWS_PATH.match(value):
            crossed = _distro_path(value)
            return None if crossed is None else f"{name}={crossed}"
    return arg


def _cross_args(args: Sequence[str]) -> list[str] | None:
    """*args* with docker's own paths as the distro reads them, or None when
    one names a place the distro cannot reach (the Windows CLI then).

    Only the values of docker's own path options, a build's context and a
    cp's host side are translated; the container program's own arguments
    are left as the caller wrote them (review L6, 2026-11-26)."""
    path_options = frozenset({"-v", "--volume", "--mount", "--env-file", "-f", "--file", "--iidfile",
                              "--cidfile", "--label-file", "--project-directory", "--config"})
    words = [a for a in args if not a.startswith("-")]
    command = words[0] if words else ""
    positional_paths = command in ("build", "cp") or (command in ("buildx", "image") and "build" in words[:2])
    out: list[str] = []
    before: str | None = None
    for arg in args:
        name = arg.split("=", 1)[0] if arg.startswith("--") else ""
        if (before in path_options or name in path_options
                or (positional_paths and not arg.startswith("-") and before not in ("-t", "--tag"))):
            value = _cross(arg, before)
            if value is None:
                return None
            out.append(value)
        else:
            out.append(arg)
        before = arg
    return out


def _passed_by_name(args: Sequence[str]) -> list[str]:
    """Variables a docker call takes from its environment (``-e NAME``,
    ``--env NAME``, ``--env=NAME``: a value never on the command line, §26)."""
    names = []
    for i, arg in enumerate(args):
        value = None
        if arg in ("-e", "--env") and i + 1 < len(args):
            value = args[i + 1]
        elif arg.startswith("--env="):
            value = arg[len("--env="):]
        if value and "=" not in value and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            names.append(value)
    return names


def docker_args_start(argv: Sequence[str]) -> int | None:
    """Where docker's own arguments begin in an argv :func:`docker_command`
    built (after the Windows CLI, or after ``wsl.exe ... --exec docker``);
    None when *argv* is no docker call."""
    if not argv:
        return None
    name = PureWindowsPath(str(argv[0])).name.lower()
    if name == "wsl.exe":
        tail = [str(a) for a in argv[1:]]
        for i in range(len(tail) - 1):
            if tail[i] == "--exec" and tail[i + 1] == "docker":
                return i + 3
        return None
    return 1 if "docker" in name else None


def is_docker_exec(argv: Sequence[str]) -> bool:
    """*argv* is a ``docker exec`` by either route (the Windows CLI, or
    ``wsl.exe ... --exec docker``). Never "``docker`` in argv[0]": on the
    wsl.exe route argv[0] is ``wsl.exe`` (review H1, 2026-11-26)."""
    start = docker_args_start(argv)
    return start is not None and len(argv) > start and str(argv[start]) == "exec"


def carry_env(argv: Sequence[str], env: Mapping[str, str] | None,
              names: Sequence[str] = ()) -> dict[str, str] | None:
    """*env* for running *argv*: through wsl.exe, the variables a docker call
    takes by name (``-e NAME``) and *names* (what compose interpolates from
    its environment) are carried into the distro (``WSLENV``), so a value
    never goes on a command line (AGENTS §26). Elsewhere *env* as it is
    (None: this process's)."""
    start = docker_args_start(argv)
    if start is None or PureWindowsPath(str(argv[0])).name.lower() != "wsl.exe":
        return dict(env) if env is not None else None
    names = [*_passed_by_name([str(a) for a in argv[start:]]), *names]
    if not names:
        return dict(env) if env is not None else None
    run_env = dict(env if env is not None else os.environ)
    have = [w for w in (run_env.get("WSLENV") or "").split(":") if w]
    run_env["WSLENV"] = ":".join(have + [f"{n}/u" for n in names if f"{n}/u" not in have])
    return run_env


@dataclass(frozen=True)
class DockerCommand:
    """A docker call ready to run: its argv, its environment (None: this
    process's), and the route (``wsl`` or ``cli``)."""

    argv: list[str]
    env: dict[str, str] | None
    route: str


def docker_command(args: Sequence[str], *, env: Mapping[str, str] | None = None,
                   context: str | None = None) -> DockerCommand | None:
    """How to run ``docker <args>`` now: the one decision every docker call in
    Kazma goes through (``tests/test_docker_route.py`` finds a caller that
    builds its own). *context* names the engine when the caller chooses one
    (``kazma-engine``, ``desktop-linux``); else the context in force.

    Kazma's own engine is reached through ``wsl.exe`` (no Windows docker.exe
    needed); an argument the distro cannot reach, ``KAZMA_DOCKER_ROUTE=cli``,
    an explicit ``KAZMA_DOCKER_BIN`` or ``DOCKER_HOST``, and every other
    engine take the Windows CLI (:func:`find_docker_cli`). None when neither
    route exists. Standard library only; nothing is started here."""
    args = [str(a) for a in args]
    # The stored engine choice first: the first docker call of a process
    # decides as every later one does (review M1, 2026-11-26).
    _apply_stored_choice_once()
    mode = (os.environ.get(ROUTE_ENV) or "auto").strip().lower()
    engine_context = context or _context_in_force()
    kazmas = engine_context == KAZMA_CONTEXT or (context is None and _kazma_by_choice())
    if (_on_windows() and mode != "cli" and not os.environ.get("DOCKER_HOST")
            and not (os.environ.get(_ENV) or "").strip()
            # An explicit other engine (Docker Desktop's context) is never
            # sent to Kazma's distro, whatever KAZMA_DOCKER_ROUTE says (L5).
            and (kazmas or (mode == "wsl" and context in (None, KAZMA_CONTEXT)))):
        wsl = _wsl_exe()
        if wsl and wsl_distro_location(KAZMA_DISTRO) is not None:
            crossed = _cross_args(args)
            if crossed is not None:
                argv = [wsl, *_WSL_ARGS, *crossed]
                run_env = carry_env(argv, env)
                run_env = dict(run_env if run_env is not None else os.environ)
                run_env["WSL_UTF8"] = "1"  # wsl.exe's own messages in UTF-8, not UTF-16 (L2)
                return DockerCommand(argv, run_env, "wsl")
    cli = find_docker_cli()
    if not cli:
        return None
    pinned = ["--context", context] if context else []
    return DockerCommand([cli, *pinned, *args], dict(env) if env is not None else None, "cli")


def docker_argv(args: Sequence[str], *, context: str | None = None) -> list[str] | None:
    """The argv of :func:`docker_command` (for a caller that runs it with its
    own environment and takes no variable by name), or None."""
    cmd = docker_command(args, context=context)
    return cmd.argv if cmd is not None else None


def distro_path(path: str) -> str | None:
    """*path* on this machine as Kazma's engine's distro reads it (``G:/x``
    -> ``/g/x``), or None when the distro cannot reach it. A compose label
    written through the wsl.exe route holds this form (``container_services``)."""
    return _distro_path(path)
