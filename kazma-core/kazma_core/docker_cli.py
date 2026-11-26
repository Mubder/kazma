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
chosen. One that is refused is said once at WARNING, and the next CLI is
used (PATH, then Docker's own install folders). A refusal is tried again
after :data:`REFUSED_RECHECK_S`, so a CLI that is allowed again comes back
by itself; a CLI that started is remembered for as long as the file does
not change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess  # nosec B404 - runs only the docker CLI's own --version, never shell
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

logger = logging.getLogger(__name__)

__all__ = [
    "DESKTOP_CONTEXT",
    "ENGINE_CHOICES",
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
#: Windows' answers that refuse a program by policy: access denied, blocked
#: by group policy (Software Restriction), and an Application Control policy
#: (App Control for Business, Smart App Control), the one live met.
_REFUSALS = frozenset({5, 1260, 4551})


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


def _say_once(key: str, message: str, *args: object) -> None:
    with _reported_lock:
        if key in _reported:
            return
        _reported.add(key)
    logger.warning(message, *args)


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


def _refused_by_os(path: str | Path) -> str | None:
    """Why Windows refuses to start the CLI at ``path``, or None.

    Only a refusal by the system's policy counts (:data:`_REFUSALS`): a file
    that cannot be read, or is no program, is left to fail as it did before.
    Elsewhere than Windows the answer is None without a start.

    The answer is kept per file version: a CLI that started is not tried
    again until the file changes; a refusal is tried again after
    :data:`REFUSED_RECHECK_S`."""
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
    if held is not None and (held[0] is None or now - held[1] < REFUSED_RECHECK_S):
        return held[0]
    why: str | None = None
    try:
        _start(candidate)
    except subprocess.TimeoutExpired:
        why = None  # it started
    except OSError as exc:
        code = getattr(exc, "winerror", None)
        why = f"WinError {code}: {exc.strerror}" if code in _REFUSALS else None
    with _reported_lock:
        _started[key] = (why, now)
    return why


def _usable(path: str | Path) -> bool:
    """True when ``path`` starts; a refusal is said once per CLI and reason."""
    why = _refused_by_os(path)
    if why is None:
        return True
    _say_once(
        f"refused:{path}:{why}",
        "[docker] the operating system refuses to start %s (%s) -- passing it over "
        "for the next docker CLI. Allow it again (on Windows: Smart App Control or "
        "the Application Control policy) to use it; Kazma tries it again every %d s.",
        path, why, int(REFUSED_RECHECK_S),
    )
    return False


def find_docker_cli() -> str | None:
    """Absolute path of a usable docker CLI, or ``None``. In a Kazma process
    the first call also pins the chosen container engine (see the module
    docstring), so the CLI it returns talks to that engine."""
    _apply_stored_choice_once()
    explicit = (os.environ.get(_ENV) or "").strip().strip('"')
    if explicit:
        if Path(explicit).is_file() and _usable(explicit):
            return explicit
        _say_once(
            f"env:{explicit}",
            "[docker] %s=%s does not exist -- falling back to PATH and the "
            "standard install locations", _ENV, explicit,
        )
    if os.name == "nt":
        own = kazma_engine_dir() / "bin" / "docker.exe"
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
    if context == KAZMA_CONTEXT:
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
    _say_once(f"no-context:{context}",
              "[docker] the container engine chosen (%s) has no Docker context %r on this "
              "machine; the docker CLI's current context is used. %s", choice, context,
              "Set Kazma's engine up again: kazma setup engine" if choice == "kazma"
              else "Start Docker Desktop once to create it")
    return None


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
    global _applied, _choice_applied
    with _apply_lock:
        _choice_applied = True
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
    if not windows or os.environ.get("DOCKER_HOST") or _context_in_force() != KAZMA_CONTEXT:
        return path
    win = PureWindowsPath(path)
    if len(win.drive) != 2 or not win.drive.endswith(":") or not _drive_is_fixed(win.drive):
        return None
    rest = "/".join(win.parts[1:])
    return f"/{win.drive[0].lower()}" + (f"/{rest}" if rest else "")
