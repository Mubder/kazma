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

Lookup order: ``KAZMA_DOCKER_BIN`` (an explicit path), then PATH, then the
places Docker installs itself on each OS. A CLI found off PATH is used, and
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
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
from pathlib import Path, PureWindowsPath

logger = logging.getLogger(__name__)

__all__ = [
    "DESKTOP_CONTEXT",
    "ENGINE_CHOICES",
    "ENGINE_KEY",
    "KAZMA_CONTEXT",
    "apply_engine_choice",
    "engine_host_path",
    "engine_installed",
    "find_docker_cli",
    "normalize_engine",
]

#: The setting that chooses Kazma's container engine.
ENGINE_KEY = "containers.engine"
#: ``kazma``: Kazma's own engine; ``desktop``: Docker Desktop.
ENGINE_CHOICES = ("kazma", "desktop")
KAZMA_CONTEXT = "kazma-engine"
DESKTOP_CONTEXT = "desktop-linux"

_ENV = "KAZMA_DOCKER_BIN"
_reported: set[str] = set()
_reported_lock = threading.Lock()


def _candidates() -> list[Path]:
    """Where Docker installs its CLI, per OS. Order is preference."""
    if os.name == "nt":
        # Kazma's own engine installs Docker's CLI here (container_engine_setup);
        # it is first: it works without Docker Desktop installed at all.
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        kazma = [Path(local) / "Kazma" / "engine" / "bin" / "docker.exe"]
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


def find_docker_cli() -> str | None:
    """Absolute path of a usable docker CLI, or ``None``. In a Kazma process
    the first call also pins the chosen container engine (see the module
    docstring), so the CLI it returns talks to that engine."""
    _apply_stored_choice_once()
    explicit = (os.environ.get(_ENV) or "").strip().strip('"')
    if explicit:
        if Path(explicit).is_file():
            return explicit
        _say_once(
            f"env:{explicit}",
            "[docker] %s=%s does not exist -- falling back to PATH and the "
            "standard install locations", _ENV, explicit,
        )
    on_path = shutil.which("docker")
    if on_path:
        return on_path
    for candidate in _candidates():
        if candidate.is_file():
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
