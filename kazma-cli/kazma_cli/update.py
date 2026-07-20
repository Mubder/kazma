"""Update CLI command — check for and install Kazma CLI updates.

Detects whether the package was installed via pip or as an editable
git install, then checks for updates accordingly:

* **pip install**: queries PyPI (``https://pypi.org/pypi/kazma/json``) for
  the latest version and runs ``pip install --upgrade kazma``.
* **git / editable install**: runs ``git fetch`` then checks
  ``git log HEAD..origin/main`` for commits behind, and updates with
  ``git pull origin main`` followed by ``pip install -e .``.

Flags:
    --check / -c   Only check for updates, don't install (dry run)
    --force / -f   Force update even if already latest
    --yes   / -y   Skip confirmation prompt
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from kazma_cli.banner import _get_version

logger = logging.getLogger(__name__)

console = Console()

__all__ = [
    "PACKAGE_NAME",
    "PYPI_URL",
    "check_git_behind",
    "detect_active_extras",
    "detect_install_type",
    "do_git_update",
    "do_pip_update",
    "get_current_version",
    "get_git_commit",
    "get_latest_pypi_version",
    "is_newer",
    "load_persisted_extras",
    "parse_update_flags",
    "parse_version",
    "persist_extras",
    "print_help",
    "run",
]

PYPI_URL = "https://pypi.org/pypi/kazma/json"
PACKAGE_NAME = "kazma"

# Timeouts (seconds) for various subprocess operations.
_CHECK_TIMEOUT = 15.0
_INSTALL_TIMEOUT = 120.0
# RAG pulls sentence-transformers + torch — allow a long first install.
_INSTALL_TIMEOUT_HEAVY = 900.0
_GIT_TIMEOUT = 60.0

# pyproject optional-dependencies → importable marker modules.
# Used so ``kazma update`` reinstalls the same extras the user already had
# (bare ``uv sync`` would prune them and wipe VectorMemory deps).
_EXTRA_MARKERS: dict[str, tuple[str, ...]] = {
    "rag": ("chromadb", "sentence_transformers"),
    "tui": ("textual", "bidi"),
    "observability": ("prometheus_client",),
    "web": ("playwright",),
    "dev": ("pytest", "ruff", "mypy"),
    "test": ("fakeredis",),
}
_KNOWN_EXTRAS: tuple[str, ...] = (
    "rag", "tui", "observability", "web", "dev", "test",
)

# Flags that are boolean toggles (no value follows them).
_UPDATE_BOOL_FLAGS = {"--check", "-c", "--force", "-f", "--yes", "-y", "--help", "-h"}


# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------

def parse_update_flags(args: list[str]) -> tuple[set[str], list[str]]:
    """Split *args* into (bool_flags, positionals).

    All update flags are boolean toggles, so any token starting with ``-``
    or matching a known flag is collected into the flags set.
    """
    flags: set[str] = set()
    positionals: list[str] = []
    for token in args:
        if token.startswith("-"):
            flags.add(token)
        else:
            positionals.append(token)
    return flags, positionals


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run_pip(
    pip_args: list[str], timeout: float = _CHECK_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m pip <pip_args>`` and capture output."""
    return subprocess.run(
        [sys.executable, "-m", "pip", *pip_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_cmd(
    cmd: list[str], cwd: str | None = None, timeout: float = _GIT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """Run an arbitrary command and capture output."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


# ---------------------------------------------------------------------------
# Install-type detection
# ---------------------------------------------------------------------------

def detect_install_type() -> str:
    """Detect whether kazma is pip-installed or git/editable-installed.

    Returns ``"git"`` for editable installs or when a monorepo ``.git``
    root is found (typical developer install), ``"pip"`` for wheel installs
    without a local git tree.  Uses ``pip show`` for the editable marker.
    """
    # Prefer git when the monorepo is present — Kazma is not always on PyPI.
    if _find_git_root() is not None:
        try:
            result = _run_pip(["show", PACKAGE_NAME])
            if result.returncode == 0 and "Editable project location:" in result.stdout:
                return "git"
            # Non-editable but repo present (uv/pip path into monorepo)
            if result.returncode == 0 and "Location:" in result.stdout:
                git_root = _find_git_root()
                if git_root and str(git_root) in result.stdout.replace("\\", "/"):
                    return "git"
        except Exception as exc:
            logger.debug("pip show failed during install-type detection: %s", exc)
        # Developer checkout: always use git update path
        return "git"
    try:
        result = _run_pip(["show", PACKAGE_NAME])
        if result.returncode == 0 and "Editable project location:" in result.stdout:
            return "git"
    except Exception as exc:
        logger.debug("pip show failed during install-type detection: %s", exc)
    return "pip"


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def get_current_version() -> str:
    """Return the currently installed kazma version.

    For git/monorepo installs prefer ``pyproject.toml`` (source of truth
    after ``git pull``). Otherwise use importlib / pip metadata.
    """
    # Git checkout: always trust repo pyproject so version matches HEAD
    if _find_git_root() is not None:
        ver = _get_version()
        if ver and ver != "0.0.0":
            return ver
    try:
        from importlib.metadata import version as pkg_version

        return pkg_version(PACKAGE_NAME)
    except Exception as exc:
        logger.debug("importlib.metadata version failed: %s", exc)
    try:
        result = _run_pip(["show", PACKAGE_NAME])
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.strip().startswith("Version:"):
                    return line.split(":", 1)[1].strip()
    except Exception as exc:
        logger.debug("pip show version fallback failed: %s", exc)

    return _get_version()


def get_latest_pypi_version() -> str | None:
    """Fetch the latest kazma version from PyPI, then GitHub releases.

    Returns the version string, or ``None`` on network/parse errors.
    """
    try:
        import httpx

        with httpx.Client(timeout=_CHECK_TIMEOUT, follow_redirects=True) as client:
            try:
                response = client.get(PYPI_URL)
                if response.status_code == 200:
                    data = response.json()
                    version = data.get("info", {}).get("version")
                    if version:
                        return str(version)
            except Exception as exc:
                logger.debug("PyPI version fetch failed: %s", exc)

            # Fallback: GitHub Releases (primary distribution for monorepo)
            for url in (
                "https://api.github.com/repos/Mubder/kazma/releases/latest",
                "https://api.github.com/repos/Mubder/kazma/tags?per_page=1",
            ):
                try:
                    response = client.get(
                        url,
                        headers={"Accept": "application/vnd.github+json"},
                    )
                    if response.status_code != 200:
                        continue
                    data = response.json()
                    if isinstance(data, dict):
                        tag = data.get("tag_name") or data.get("name") or ""
                    elif isinstance(data, list) and data:
                        tag = data[0].get("name") or ""
                    else:
                        tag = ""
                    tag = str(tag).lstrip("v").strip()
                    if tag:
                        return tag
                except Exception as exc:
                    logger.debug("GitHub version fetch failed: %s", exc)
    except Exception as exc:
        logger.warning("Failed to fetch remote version: %s", exc)
    return None


def parse_version(version: str) -> tuple[int, ...]:
    """Parse a semantic version string into a comparable tuple of ints.

    Handles versions like ``"0.1.0"``, ``"1.2.3"``, ``"2.0.0a1"``.
    Pre-release suffixes are stripped — only the numeric components are
    compared.
    """
    parts: list[int] = []
    for part in version.split("."):
        numeric = ""
        for ch in part:
            if ch.isdigit():
                numeric += ch
            else:
                break
        parts.append(int(numeric) if numeric else 0)
    return tuple(parts)


def is_newer(current: str, latest: str) -> bool:
    """Return ``True`` if *latest* is strictly newer than *current*."""
    return parse_version(latest) > parse_version(current)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _find_git_root() -> Path | None:
    """Walk up from this file to find a directory containing ``.git``."""
    current = Path(__file__).resolve().parent
    for _ in range(8):
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def check_git_behind() -> tuple[int, list[str]]:
    """Check if the git repo is behind ``origin/main``.

    Runs ``git fetch`` first, then ``git log HEAD..origin/main --oneline``.
    Returns ``(commits_behind, log_lines)``.
    """
    git_root = _find_git_root()
    if git_root is None:
        return 0, []

    cwd = str(git_root)

    # Fetch first (best-effort)
    try:
        _run_cmd(["git", "fetch", "origin"], cwd=cwd)
    except Exception as exc:
        logger.warning("git fetch failed: %s", exc)
        return 0, []

    # Count commits behind
    try:
        result = _run_cmd(
            ["git", "log", "HEAD..origin/main", "--oneline"],
            cwd=cwd,
        )
        if result.returncode != 0:
            return 0, []
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        return len(lines), lines
    except Exception as exc:
        logger.warning("git log failed: %s", exc)
        return 0, []


def get_git_commit(ref: str = "HEAD") -> str:
    """Return the short commit hash for *ref*, or ``"unknown"``."""
    git_root = _find_git_root()
    if git_root is None:
        return "unknown"
    try:
        result = _run_cmd(
            ["git", "rev-parse", "--short", ref],
            cwd=str(git_root),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as exc:
        logger.debug("git remote detection failed: %s", exc)
    return "unknown"


# ---------------------------------------------------------------------------
# Update operations
# ---------------------------------------------------------------------------

def do_pip_update() -> bool:
    """Upgrade kazma via pip. Returns ``True`` on success."""
    console.print("[cyan]Running pip install --upgrade kazma...[/cyan]")
    try:
        result = _run_pip(
            ["install", "--upgrade", PACKAGE_NAME],
            timeout=_INSTALL_TIMEOUT,
        )
        if result.returncode == 0:
            console.print("[green]pip upgrade completed.[/green]")
            return True
        console.print(f"[red]pip upgrade failed:[/red]\n{result.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        console.print("[red]pip upgrade timed out.[/red]")
        return False
    except FileNotFoundError:
        console.print("[red]pip not found. Is Python installed correctly?[/red]")
        return False
    except PermissionError:
        console.print("[red]Permission denied. Try running with --user or as admin.[/red]")
        return False
    except Exception as exc:
        console.print(f"[red]pip upgrade error:[/red] {exc}")
        return False


def _extras_file() -> Path:
    """User-level file that survives git pull / stash (not in the repo)."""
    return Path.home() / ".kazma" / "installed_extras.json"


def load_persisted_extras() -> list[str]:
    """Load previously recorded optional extras (ConfigStore + ~/.kazma)."""
    found: list[str] = []
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get("system.installed_extras")
        if isinstance(raw, list):
            found.extend(str(x) for x in raw)
        elif isinstance(raw, str) and raw.strip():
            found.extend(p.strip() for p in raw.split(",") if p.strip())
    except Exception:
        pass
    try:
        path = _extras_file()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("extras") or data.get("installed") or []
            if isinstance(data, list):
                found.extend(str(x) for x in data)
    except Exception:
        pass
    return _normalize_extras(found)


def persist_extras(extras: list[str]) -> None:
    """Remember which optional extras the user has installed."""
    clean = _normalize_extras(extras)
    try:
        path = _extras_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"extras": clean}, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("Could not write installed_extras.json: %s", exc)
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(
            "system.installed_extras", clean, category="system"
        )
    except Exception as exc:
        logger.debug("Could not persist extras to ConfigStore: %s", exc)


def _normalize_extras(extras: list[str] | set[str] | tuple[str, ...]) -> list[str]:
    order = {name: i for i, name in enumerate(_KNOWN_EXTRAS)}
    out: list[str] = []
    for e in extras:
        name = str(e).strip().lower()
        if name == "all":
            return list(_KNOWN_EXTRAS)
        if name in order and name not in out:
            out.append(name)
    out.sort(key=lambda n: order.get(n, 99))
    return out


def _module_available(mod: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def _vector_memory_data_present(cwd: str) -> bool:
    """True if on-disk vector memory exists (implies user had ``rag``)."""
    candidates: list[Path] = []
    try:
        from kazma_core.paths import vector_memory_path

        candidates.append(Path(vector_memory_path()))
    except Exception:
        pass
    home = Path.home()
    candidates.extend(
        [
            home / ".kazma" / "vector_memory",
            home / ".kazma" / "chroma",
            Path(cwd) / "kazma-data" / "vector_memory",
            Path(cwd) / "kazma-data" / "chroma",
        ]
    )
    for p in candidates:
        try:
            if p.is_dir() and any(p.iterdir()):
                return True
        except Exception:
            continue
    # ConfigStore memory status historically ACTIVE / INSTALLING
    try:
        from kazma_core.config_store import get_config_store

        st = str(get_config_store().get("system.memory.status") or "").upper()
        if st in ("ACTIVE", "INSTALLING", "READY"):
            return True
    except Exception:
        pass
    return False


def detect_active_extras(cwd: str | None = None) -> list[str]:
    """Detect optional extras currently installed (or previously recorded).

    Combines:
    1. Persisted list (``~/.kazma/installed_extras.json`` + ConfigStore)
    2. Live import markers in the active venv
    3. Heuristic: vector memory data on disk → include ``rag``
    """
    extras: set[str] = set(load_persisted_extras())
    for extra, markers in _EXTRA_MARKERS.items():
        # Any marker present counts (partial install still means user wanted it)
        if any(_module_available(m) for m in markers):
            extras.add(extra)
    root = cwd or str(_find_git_root() or Path.cwd())
    if _vector_memory_data_present(root):
        extras.add("rag")
    return _normalize_extras(extras)


def _editable_spec(extras: list[str]) -> str:
    if extras:
        return f".[{','.join(extras)}]"
    return "."


def _reinstall_local(cwd: str) -> bool:
    """Reinstall editable package without wiping optional extras.

    Bare ``uv sync`` is exact-by-default and **removes** packages not in the
    default lock set (e.g. chromadb / sentence-transformers from ``[rag]``).
    That made ``kazma update`` destroy VectorMemory. We now:

    1. Detect + persist active extras
    2. Prefer **additive** ``uv pip install -e ".[extras]"``
    3. Fall back to ``uv sync --inexact --extra …`` (never bare ``uv sync``)
    4. Fall back to ``python -m pip install -e ".[extras]"``
    """
    extras = detect_active_extras(cwd)
    if extras:
        persist_extras(extras)
        console.print(
            f"[cyan]Preserving optional extras:[/cyan] {', '.join(extras)}"
        )
    else:
        console.print(
            "[dim]No optional extras detected "
            "(install later via Settings → Packages or "
            "uv pip install -e \".[rag]\").[/dim]"
        )

    spec = _editable_spec(extras)
    timeout = (
        _INSTALL_TIMEOUT_HEAVY
        if any(e in extras for e in ("rag", "all", "dev", "web"))
        else _INSTALL_TIMEOUT
    )

    # 1) Additive uv pip install (does NOT prune other packages)
    uv_pip_cmd = [
        "uv", "pip", "install", "--python", sys.executable, "-e", spec,
    ]
    attempts: list[tuple[list[str], str]] = [
        (uv_pip_cmd, f"uv pip install -e {spec}"),
    ]

    # 2) uv sync --inexact + extras (retains extraneous packages)
    sync_cmd = ["uv", "sync", "--inexact"]
    for e in extras:
        sync_cmd.extend(["--extra", e])
    sync_label = "uv sync --inexact" + (
        " " + " ".join(f"--extra {e}" for e in extras) if extras else ""
    )
    attempts.append((sync_cmd, sync_label))

    for cmd, label in attempts:
        console.print(f"[cyan]Reinstalling via {label}...[/cyan]")
        try:
            result = _run_cmd(cmd, cwd=cwd, timeout=timeout)
            if result.returncode == 0:
                console.print(f"[green]{label} completed.[/green]")
                if extras:
                    persist_extras(extras)
                return True
            err = (result.stderr or result.stdout or "").strip()
            console.print(f"[yellow]{label} failed:[/yellow] {err[:400]}")
        except FileNotFoundError:
            console.print(f"[dim]{cmd[0]} not on PATH — trying next method.[/dim]")
        except subprocess.TimeoutExpired:
            console.print(f"[yellow]{label} timed out.[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]{label} error:[/yellow] {exc}")

    # 3) python -m pip (may be missing in pure uv venvs)
    console.print(f"[cyan]Reinstalling via python -m pip install -e {spec}...[/cyan]")
    try:
        result = _run_cmd(
            [sys.executable, "-m", "pip", "install", "-e", spec],
            cwd=cwd,
            timeout=timeout,
        )
        if result.returncode == 0:
            console.print("[green]pip install -e completed.[/green]")
            if extras:
                persist_extras(extras)
            return True
        err = (result.stderr or result.stdout or "").strip()
        console.print(f"[yellow]pip install -e failed:[/yellow] {err[:400]}")
    except Exception as exc:
        console.print(f"[yellow]pip reinstall skipped:[/yellow] {exc}")

    console.print(
        "[yellow]Code was pulled successfully, but package reinstall did not run.[/yellow]\n"
        "  Restart the server: [cyan]kazma serve[/cyan]\n"
        "  To restore memory/RAG deps: [cyan]uv pip install -e \".[rag]\"[/cyan]\n"
        "  To restore everything optional: [cyan]uv pip install -e \".[all]\"[/cyan]\n"
        "  Avoid bare [red]uv sync[/red] — it removes extras."
    )
    # git pull already succeeded — do not fail the whole update
    return True


def _git_status_porcelain(cwd: str) -> str:
    """Return ``git status --porcelain`` output (empty if clean)."""
    try:
        result = _run_cmd(["git", "status", "--porcelain"], cwd=cwd)
        if result.returncode == 0:
            return (result.stdout or "").strip()
    except Exception:
        pass
    return ""


def _commits_ahead_of_origin(cwd: str) -> int:
    """How many local commits are not on origin/main (0 = safe to reset)."""
    try:
        result = _run_cmd(
            ["git", "rev-list", "--count", "origin/main..HEAD"],
            cwd=cwd,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            return int(result.stdout.strip())
    except Exception:
        pass
    return 0


def _stash_local_changes(cwd: str) -> bool:
    """Stash tracked + untracked local edits. Returns True if a stash was created."""
    status = _git_status_porcelain(cwd)
    if not status:
        return False

    console.print("[yellow]Local changes detected (would block a normal pull):[/yellow]")
    for line in status.splitlines()[:20]:
        console.print(f"  [dim]{line}[/dim]")
    if status.count("\n") >= 20:
        console.print("  [dim]…[/dim]")

    msg = "kazma-update-auto"
    console.print(
        "[cyan]Stashing local changes so update can run without merge conflicts…[/cyan]"
    )
    # Include untracked so new local files don't block either
    result = _run_cmd(
        ["git", "stash", "push", "-u", "-m", msg],
        cwd=cwd,
    )
    if result.returncode != 0:
        console.print(
            f"[red]Could not stash local changes:[/red]\n"
            f"{(result.stderr or result.stdout or '').strip()}"
        )
        console.print(
            "Fix manually:\n"
            "  [cyan]git stash push -u -m 'my-local'[/cyan]\n"
            "  [cyan]git pull origin main[/cyan]\n"
            "  [cyan]git stash pop[/cyan]"
        )
        return False

    # Confirm stash was created (stash push is no-op if nothing to stash)
    if "No local changes to save" in (result.stdout or ""):
        return False
    console.print("[green]Stashed local changes.[/green]")
    return True


def _restore_stash(cwd: str) -> None:
    """Pop the latest stash; keep it if conflicts so nothing is lost."""
    console.print("[cyan]Restoring your local changes (git stash pop)…[/cyan]")
    result = _run_cmd(["git", "stash", "pop"], cwd=cwd)
    out = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode == 0:
        console.print("[green]Local changes restored on top of the update.[/green]")
        if out:
            console.print(f"[dim]{out[:500]}[/dim]")
        return

    # Conflicts or other failure — stash entry remains if pop failed mid-way
    console.print(
        "[yellow]Could not auto-apply all local changes (likely a conflict).[/yellow]\n"
        "Your edits are still safe in the git stash.\n"
        "  List:   [cyan]git stash list[/cyan]\n"
        "  Apply:  [cyan]git stash pop[/cyan]\n"
        "  Drop:   [cyan]git stash drop[/cyan]  (only after you copied what you need)\n"
        f"[dim]{out[:600]}[/dim]"
    )


def do_git_update() -> bool:
    """Update kazma from git: stash → sync to origin/main → restore → reinstall.

    Designed so operators never hit interactive merge/edit-file prompts:

    1. Stash local dirty files (e.g. ``kazma.yaml`` runtime tweaks)
    2. ``git fetch`` + fast-forward (or hard reset if no unique local commits)
    3. ``git stash pop`` to put local config back
    4. Best-effort package reinstall (uv/pip)
    """
    git_root = _find_git_root()
    if git_root is None:
        console.print("[red]Could not locate git repository root.[/red]")
        return False

    cwd = str(git_root)
    stashed = False

    try:
        # ── 1. Stash local edits so pull never aborts ─────────────
        if _git_status_porcelain(cwd):
            stashed = _stash_local_changes(cwd)
            # If still dirty after failed stash, abort
            if _git_status_porcelain(cwd) and not stashed:
                console.print("[red]Working tree still dirty — cannot update safely.[/red]")
                return False

        # ── 2. Fetch + land on origin/main (no merge commits) ─────
        console.print("[cyan]Fetching origin…[/cyan]")
        fetch = _run_cmd(["git", "fetch", "origin"], cwd=cwd)
        if fetch.returncode != 0:
            console.print(
                f"[red]git fetch failed:[/red]\n{(fetch.stderr or fetch.stdout or '').strip()}"
            )
            if stashed:
                _restore_stash(cwd)
            return False

        ahead = _commits_ahead_of_origin(cwd)
        if ahead > 0:
            console.print(
                f"[yellow]You have {ahead} local commit(s) not on origin/main.[/yellow]\n"
                "Trying rebase onto origin/main (no merge commit)…"
            )
            result = _run_cmd(
                ["git", "pull", "--rebase", "origin", "main"],
                cwd=cwd,
            )
            if result.returncode != 0:
                console.print(
                    f"[red]git pull --rebase failed:[/red]\n"
                    f"{(result.stderr or result.stdout or '').strip()}\n"
                    "Resolve manually, then re-run [cyan]kazma update[/cyan]."
                )
                if stashed:
                    _restore_stash(cwd)
                return False
            console.print("[green]Rebased onto origin/main.[/green]")
        else:
            # Clean tracking branch: hard reset is the no-merge path
            console.print("[cyan]Updating to origin/main (fast-forward / reset)…[/cyan]")
            result = _run_cmd(["git", "reset", "--hard", "origin/main"], cwd=cwd)
            if result.returncode != 0:
                console.print(
                    f"[red]git reset --hard origin/main failed:[/red]\n"
                    f"{(result.stderr or result.stdout or '').strip()}"
                )
                if stashed:
                    _restore_stash(cwd)
                return False
            console.print("[green]Now at origin/main.[/green]")

        # ── 3. Restore local config edits ─────────────────────────
        if stashed:
            _restore_stash(cwd)

    except FileNotFoundError:
        console.print("[red]git not found. Is Git installed and on PATH?[/red]")
        return False
    except subprocess.TimeoutExpired:
        console.print("[red]git update timed out.[/red]")
        return False
    except Exception as exc:
        console.print(f"[red]git update error:[/red] {exc}")
        return False

    return _reinstall_local(cwd)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_help() -> None:
    """Print the update subcommand help text."""
    console.print("Kazma Update — check for and install CLI updates")
    console.print()
    console.print("Usage: kazma update [options]")
    console.print()
    console.print("Options:")
    console.print("  --check, -c    Only check for updates, don't install (dry run)")
    console.print("  --force, -f    Force update even if already latest")
    console.print("  --yes, -y      Skip confirmation prompt")
    console.print("  --help, -h     Show this help message")
    console.print()
    console.print("Git installs (monorepo):")
    console.print("  • Local edits (e.g. kazma.yaml) are [bold]auto-stashed[/bold], then restored")
    console.print("  • Uses reset/ff to origin/main — [bold]no merge commit prompts[/bold]")
    console.print("  • Reinstall prefers [cyan]uv[/cyan] when pip is missing")


def _confirm(prompt: str) -> bool:
    """Ask for yes/no confirmation. Returns ``True`` for yes."""
    try:
        response = input(prompt).strip().lower()
        return response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _print_version_table(
    install_type: str,
    current_version: str,
    latest_version: str | None = None,
    current_commit: str | None = None,
    remote_commit: str | None = None,
) -> None:
    """Render a rich table summarising version information."""
    table = Table(title="Kazma Version Info", show_header=True)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("Install type", install_type)
    table.add_row("Current version", f"v{current_version}")
    if latest_version is not None:
        table.add_row("Latest version", f"v{latest_version}")
    if current_commit is not None:
        table.add_row("Current HEAD", current_commit)
    if remote_commit is not None:
        table.add_row("Remote main", remote_commit)

    console.print(table)


# ---------------------------------------------------------------------------
# Check & update flows
# ---------------------------------------------------------------------------

def _run_pip_check_and_update(
    current_version: str, check_only: bool, force: bool, skip_confirm: bool
) -> None:
    """Check PyPI/GitHub for updates and optionally upgrade via pip."""
    # If a monorepo is present, prefer git update even when pip-installed
    if _find_git_root() is not None:
        console.print("[dim]Local git repo detected — using git update path.[/dim]")
        _run_git_check_and_update(current_version, check_only, force, skip_confirm)
        return

    latest = get_latest_pypi_version()

    if latest is None:
        console.print()
        console.print("[red]Could not fetch version info from PyPI or GitHub.[/red]")
        console.print("If you cloned the repo, run from the monorepo: [cyan]git pull[/cyan]")
        console.print("Or check network / GitHub releases: https://github.com/Mubder/kazma")
        sys.exit(1)

    console.print(f"  Latest:       [cyan]v{latest}[/cyan] (PyPI)")
    console.print()
    _print_version_table("pip", current_version, latest_version=latest)

    update_available = is_newer(current_version, latest)

    if update_available:
        console.print()
        console.print(f"[green]Update available![/green] v{current_version} -> v{latest}")
    elif force:
        console.print()
        console.print("[yellow]Forcing update (--force)[/yellow]")
    else:
        console.print()
        console.print("[green]Already up to date.[/green]")
        return

    if check_only:
        console.print()
        console.print("[yellow]--check mode: not installing.[/yellow]")
        return

    if not skip_confirm:
        if not _confirm(f"Update kazma from v{current_version} to v{latest}? [y/N] "):
            console.print("Update cancelled.")
            return

    if do_pip_update():
        new_version = get_current_version()
        console.print()
        console.print(f"[green]Update complete![/green] Now at v{new_version}")
    else:
        sys.exit(1)


def _run_git_check_and_update(
    current_version: str, check_only: bool, force: bool, skip_confirm: bool
) -> None:
    """Check git for updates and optionally pull + reinstall."""
    commits_behind, log_lines = check_git_behind()
    current_commit = get_git_commit("HEAD")
    remote_commit = get_git_commit("origin/main")

    console.print(f"  Current HEAD: [cyan]{current_commit}[/cyan]")
    console.print(f"  Remote main:  [cyan]{remote_commit}[/cyan]")
    console.print()
    _print_version_table(
        "git",
        current_version,
        current_commit=current_commit,
        remote_commit=remote_commit,
    )

    update_available = commits_behind > 0

    if update_available:
        console.print()
        console.print(
            f"[green]Update available![/green] "
            f"{commits_behind} commit(s) behind origin/main"
        )
        # Show changelog summary (last few git log entries)
        if log_lines:
            console.print()
            console.print("[bold]Recent changes:[/bold]")
            for line in log_lines[:10]:
                console.print(f"  {line}")
    elif force:
        console.print()
        console.print("[yellow]Forcing update (--force)[/yellow]")
    else:
        console.print()
        console.print("[green]Already up to date.[/green]")
        return

    if check_only:
        console.print()
        console.print("[yellow]--check mode: not installing.[/yellow]")
        return

    if not skip_confirm:
        if not _confirm(f"Pull {commits_behind} commit(s) and reinstall? [y/N] "):
            console.print("Update cancelled.")
            return

    if do_git_update():
        new_version = get_current_version()
        new_commit = get_git_commit("HEAD")
        console.print()
        console.print("[green]Update complete![/green]")
        console.print(f"  Version: v{new_version}")
        console.print(f"  HEAD:    {new_commit}")
    else:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Sync entry point
# ---------------------------------------------------------------------------

def run(args: list[str]) -> None:
    """Dispatch the ``kazma update`` command."""
    flags, _positionals = parse_update_flags(args)

    if "--help" in flags or "-h" in flags:
        print_help()
        return

    check_only = "--check" in flags or "-c" in flags
    force = "--force" in flags or "-f" in flags
    skip_confirm = "--yes" in flags or "-y" in flags

    install_type = detect_install_type()
    current_version = get_current_version()

    console.print()
    console.print("[bold]Kazma Update[/bold]")
    console.print(f"  Install type: [cyan]{install_type}[/cyan]")
    console.print(f"  Current:      [cyan]v{current_version}[/cyan]")

    if install_type == "git":
        _run_git_check_and_update(current_version, check_only, force, skip_confirm)
    else:
        _run_pip_check_and_update(current_version, check_only, force, skip_confirm)
