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

import os
import shutil
from pathlib import Path

__all__ = [
    "host_shell_allowed",
    "is_production",
    "resolve_shell_binary",
    "restricted_child_env",
    "shell_strict_mode",
    "system_path_dirs",
]


def host_shell_allowed() -> bool:
    """False when attention-profile Docker jail is forced and HOST_SHELL is off.

    ``KAZMA_CODE_EXEC_DOCKER=force|required`` means python_exec is containerized.
    Host ``shell_exec`` is then an escape hatch: opt in with ``KAZMA_HOST_SHELL=1``.
    """
    raw = (os.environ.get("KAZMA_HOST_SHELL") or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    docker = (os.environ.get("KAZMA_CODE_EXEC_DOCKER") or "").strip().lower()
    if docker in ("force", "required"):
        return False
    return True


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
    """Minimal PATH entries — no user home, no project node_modules, no secrets dir."""
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
    env: dict[str, str] = {
        "PATH": path,
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        "LC_ALL": os.environ.get("LC_ALL") or "C.UTF-8",
        "HOME": cwd,
        "USERPROFILE": cwd,
        "TMPDIR": cwd,
        "TEMP": cwd,
        "TMP": cwd,
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

    # Prefer which() with restricted PATH only
    found = shutil.which(name, path=restricted_path)
    if found:
        return found

    # Absolute path: only if it lands inside a restricted PATH dir
    p = Path(argv0)
    if p.is_absolute() or (len(argv0) > 2 and argv0[1] == ":"):
        try:
            resolved = p.resolve()
        except OSError:
            return None
        if not resolved.is_file():
            return None
        allowed_roots = [Path(d).resolve() for d in restricted_path.split(os.pathsep) if d]
        for root in allowed_roots:
            try:
                resolved.relative_to(root)
                return str(resolved)
            except ValueError:
                continue
        return None

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
