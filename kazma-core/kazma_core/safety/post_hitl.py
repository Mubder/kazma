"""Post-HITL host-power posture helpers.

After a human approves a danger tool, the agent still runs with significant
host capability by design (trusted-operator model).  These helpers tighten
the residual surface without removing operator power entirely.

Env:
  ``KAZMA_PRODUCTION=1``          — enables strict defaults
  ``KAZMA_SHELL_STRICT=1|0``      — force on/off restricted PATH + binary resolve
  ``KAZMA_SHELL_ALLOW_ARCHIVE=1`` — keep tar/zip/unzip in production allowlist
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path

__all__ = [
    "host_shell_allowed",
    "is_production",
    "resolve_shell_binary",
    "restricted_child_env",
    "shell_strict_mode",
    "system_path_dirs",
]

logger = logging.getLogger(__name__)


def host_shell_allowed() -> bool:
    """Host shell requires an explicit grant in production or multi-user mode.

    ``KAZMA_CODE_EXEC_DOCKER=force|required`` means python_exec is containerized.
    Host ``shell_exec`` is then an escape hatch: opt in with ``KAZMA_HOST_SHELL=1``.
    """
    try:
        from kazma_core.safety.deployment_policy import container_required

        if container_required():
            return False
    except Exception:
        logger.warning("[post_hitl] execution profile unavailable; refusing host shell", exc_info=True)
        return False
    raw = (os.environ.get("KAZMA_HOST_SHELL") or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    docker = (os.environ.get("KAZMA_CODE_EXEC_DOCKER") or "").strip().lower()
    if docker in ("force", "required", "1", "true", "on", "yes", "docker"):
        return False
    from kazma_core.tools.code_exec import _production_or_multi_user

    return not _production_or_multi_user()


def is_production() -> bool:
    return (os.environ.get("KAZMA_PRODUCTION") or "").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )


def shell_strict_mode() -> bool:
    """True when shell_exec should use restricted PATH + which-only binaries."""
    raw = (os.environ.get("KAZMA_SHELL_STRICT") or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    return is_production()


def interpreter_script_dirs() -> list[str]:
    """Directories holding console scripts for the running interpreter.

    Without these the shell allowlist promises tools the PATH cannot deliver.
    ``pytest``, ``ruff``, ``mypy`` and ``uv`` are all allowlisted build tools,
    and in any venv-based install they live in ``<venv>/Scripts`` (Windows) or
    ``<venv>/bin`` — which is NOT on the process PATH, because Kazma is
    normally launched as ``.venv/Scripts/python.exe -m ...`` rather than
    through an activated venv. So an approved ``pytest`` came back "Command
    not found" *after* the human had already said yes: the worst shape of
    refusal, because the operator has been told the thing was allowed.
    Measured on the reference install — ``pytest.exe``, ``ruff.exe`` and
    ``mypy.exe`` all present in ``.venv/Scripts``, all three unreachable.

    This does not widen the trust boundary. Anyone who can plant a binary in
    the interpreter's own script directory already controls the interpreter
    running Kazma. Nor does it un-block interpreters: ``python`` is rejected
    by NAME in the ``shell_exec`` allowlist, before PATH resolution is ever
    consulted. PATH decides where an already-permitted name resolves — never
    which names are permitted.
    """
    out: list[str] = []
    try:
        import sys

        exe_dir = Path(sys.executable).resolve().parent
        for cand in (exe_dir, exe_dir / "Scripts", exe_dir / "bin"):
            d = str(cand)
            if cand.is_dir() and d not in out:
                out.append(d)
    except Exception:  # pragma: no cover - defensive
        pass
    return out


def system_path_dirs() -> list[str]:
    """Minimal PATH entries for the post-HITL shell.

    System directories, plus the directories that actually contain the
    allowlisted build tools.

    This docstring used to read "no user home, no project node_modules, no
    secrets dir", and the first clause was not true: the ``shutil.which(tool)``
    loop at the bottom has always added whatever directory ``uv`` resolves
    from, which on this reference box is ``C:\\Users\\<user>\\.local\\bin`` —
    a user-writable directory under the home the sentence said was excluded.
    The ``interpreter_script_dirs()`` entries are the same category.

    That is not a meaningful escalation on a single-operator install: anyone
    who can write to the operator's home or the venv can already replace the
    interpreter that runs Kazma. It is recorded accurately because a comment
    that overstates a boundary is worse than no comment — the next reader
    reasons from it, and this list is the boundary.

    What this list is NOT is the thing that decides which commands may run.
    That is the ``_SAFE_BINARIES`` allowlist in ``shell_exec``, which matches
    by NAME before PATH is consulted. PATH only resolves a name that has
    already been permitted.
    """
    if os.name == "nt":
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
        candidates = [
            str(Path(windir) / "System32"),
            str(Path(windir) / "System32" / "WindowsPowerShell" / "v1.0"),
            r"C:\Program Files\Git\cmd",
            r"C:\Program Files\Git\bin",
        ]
    else:
        candidates = [
            "/usr/bin",
            "/bin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
        ]
    # Preserve only existing dirs
    out: list[str] = []
    for d in candidates:
        if d and os.path.isdir(d) and d not in out:
            out.append(d)

    for d in interpreter_script_dirs():
        if d not in out:
            out.append(d)

    # Also include directories of known allowlisted tools if present on PATH
    for tool in ("git", "uv", "pytest", "ruff", "mypy"):
        found = shutil.which(tool)
        if found:
            parent = str(Path(found).resolve().parent)
            if parent not in out:
                out.append(parent)
    return out or (os.environ.get("PATH") or "").split(os.pathsep)


def _tool_home_for(cwd: str) -> Path:
    """The private home a shell child gets for workspace *cwd*.

    Outside the workspace and not the operator's own home: one folder per
    workspace under the system temp folder (``kazma-tool-home/<hash>``),
    kept between commands so tool caches still work. Until 2026-10-02 HOME,
    USERPROFILE and TEMP were the workspace itself, so every tool a command
    ran kept its user-level files in the repository the agent was working
    in -- uv's whole cache (143 MB, ``AppData/Local/uv``) sat in the live
    install folder, which is its workspace, where ``git add -A`` takes it.
    """
    resolved = os.path.normcase(os.path.abspath(cwd or "."))
    key = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    home = Path(tempfile.gettempdir()) / "kazma-tool-home" / key
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    return home


def restricted_child_env(*, cwd: str) -> dict[str, str]:
    """Build scrubbed child env; in strict mode PATH is system-only.

    Both modes get :func:`interpreter_script_dirs` appended. Strict mode picks
    them up through ``system_path_dirs``; non-strict mode inherits the process
    PATH, which does not contain the venv's script directory either — so an
    allowlisted ``pytest`` was unreachable in BOTH modes, for the same reason,
    and fixing only the strict path would have left the default broken.
    """
    if shell_strict_mode():
        path = os.pathsep.join(system_path_dirs())
    else:
        parts = [p for p in (os.environ.get("PATH") or "").split(os.pathsep) if p]
        for d in interpreter_script_dirs():
            if d not in parts:
                parts.append(d)
        path = os.pathsep.join(parts)
    try:
        home = _tool_home_for(cwd)
    except OSError:
        # The system temp folder cannot be written: the command still runs,
        # with its files in the workspace as before.
        logger.warning("[post_hitl] no private tool home for %s; using the workspace", cwd, exc_info=True)
        home = Path(cwd)
    private = home != Path(cwd)
    tmp = str(home / "tmp") if private else cwd
    env: dict[str, str] = {
        "PATH": path,
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        "LC_ALL": os.environ.get("LC_ALL") or "C.UTF-8",
        "HOME": str(home),
        "USERPROFILE": str(home),
        # Windows tools find their caches and settings here, not from HOME.
        "APPDATA": str(home / "AppData" / "Roaming") if private and os.name == "nt" else "",
        "LOCALAPPDATA": str(home / "AppData" / "Local") if private and os.name == "nt" else "",
        "TMPDIR": tmp,
        "TEMP": tmp,
        "TMP": tmp,
        "SYSTEMROOT": os.environ.get("SYSTEMROOT") or "",
        "COMSPEC": os.environ.get("COMSPEC") or "",
        "PATHEXT": os.environ.get("PATHEXT") or "",
        "WINDIR": os.environ.get("WINDIR") or "",
    }
    return {k: v for k, v in env.items() if v}


def resolve_shell_binary(argv0: str, *, restricted_path: str) -> str | None:
    """Resolve *argv0* to an executable under *restricted_path*.

    Absolute paths are only accepted if their basename is allowlisted *and*
    the file resolves under a restricted PATH directory (no /tmp/evil/git).
    """
    name = Path(argv0).name
    if os.name == "nt" and name.lower().endswith(".exe"):
        name = name[:-4]

    # Enumerate explicit absolute directories. shutil.which may implicitly
    # search the current directory on Windows, even with a supplied PATH.
    # Resolve symlinks before accepting a candidate from a trusted directory.
    suffixes = (".exe", ".com") if os.name == "nt" else ("",)
    for directory in restricted_path.split(os.pathsep):
        if not directory or not Path(directory).is_absolute():
            continue
        try:
            root = Path(directory).resolve()
            for suffix in suffixes:
                candidate = (root / (name + suffix)).resolve()
                if candidate.is_relative_to(root) and candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
        except (OSError, RuntimeError):
            continue
    return None


def production_archive_allowed() -> bool:
    raw = (os.environ.get("KAZMA_SHELL_ALLOW_ARCHIVE") or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if is_production() and shell_strict_mode():
        return False
    return True


def shell_mutate_allowed() -> bool:
    """Whether shell_exec may run mkdir/cp/mv/touch after HITL.

    Multi-user/production defaults to read-only shell (use file_write tool).
    Opt in with ``KAZMA_SHELL_ALLOW_MUTATE=1``.
    """
    raw = (os.environ.get("KAZMA_SHELL_ALLOW_MUTATE") or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    try:
        from kazma_core.tenant_isolation import multi_user_or_production

        if multi_user_or_production():
            return False
    except Exception:
        if is_production():
            return False
    return True
