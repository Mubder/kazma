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
    "detect_install_type",
    "do_git_update",
    "do_pip_update",
    "get_current_version",
    "get_git_commit",
    "get_latest_pypi_version",
    "is_newer",
    "parse_update_flags",
    "parse_version",
    "print_help",
    "run",
]

PYPI_URL = "https://pypi.org/pypi/kazma/json"
PACKAGE_NAME = "kazma"

# Timeouts (seconds) for various subprocess operations.
_CHECK_TIMEOUT = 15.0
_INSTALL_TIMEOUT = 120.0
_GIT_TIMEOUT = 60.0

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

    Returns ``"git"`` for editable installs, ``"pip"`` for regular pip
    installs.  Uses ``pip show`` to check for the ``Editable project
    location`` marker.
    """
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

    Tries ``importlib.metadata`` first (fast, reliable), then falls back
    to parsing ``pip show kazma`` output, and finally to reading
    ``pyproject.toml``.
    """
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
    """Fetch the latest kazma version from PyPI.

    Returns the version string, or ``None`` on network/parse errors.
    """
    try:
        import httpx

        with httpx.Client(timeout=_CHECK_TIMEOUT) as client:
            response = client.get(PYPI_URL)
            response.raise_for_status()
            data = response.json()
            version = data.get("info", {}).get("version")
            if version:
                return str(version)
    except Exception as exc:
        logger.warning("Failed to fetch PyPI version: %s", exc)
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


def do_git_update() -> bool:
    """Update kazma from git source (pull + reinstall editable).

    Returns ``True`` on success.
    """
    git_root = _find_git_root()
    if git_root is None:
        console.print("[red]Could not locate git repository root.[/red]")
        return False

    cwd = str(git_root)

    # git pull
    console.print("[cyan]Running git pull origin main...[/cyan]")
    try:
        result = _run_cmd(["git", "pull", "origin", "main"], cwd=cwd)
        if result.returncode != 0:
            console.print(f"[red]git pull failed:[/red]\n{result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        console.print("[red]git pull timed out.[/red]")
        return False
    except FileNotFoundError:
        console.print("[red]git not found. Is Git installed and on PATH?[/red]")
        return False
    except Exception as exc:
        console.print(f"[red]git pull error:[/red] {exc}")
        return False

    # pip install -e .
    console.print("[cyan]Reinstalling package in editable mode (pip install -e .)...[/cyan]")
    try:
        result = _run_cmd(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            cwd=cwd,
            timeout=_INSTALL_TIMEOUT,
        )
        if result.returncode == 0:
            console.print("[green]Reinstall completed.[/green]")
            return True
        console.print(f"[red]pip install -e . failed:[/red]\n{result.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        console.print("[red]Reinstall timed out.[/red]")
        return False
    except PermissionError:
        console.print("[red]Permission denied during reinstall.[/red]")
        return False
    except Exception as exc:
        console.print(f"[red]Reinstall error:[/red] {exc}")
        return False


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
    """Check PyPI for updates and optionally upgrade via pip."""
    latest = get_latest_pypi_version()

    if latest is None:
        console.print()
        console.print("[red]Could not fetch version info from PyPI.[/red]")
        console.print("Check your network connection and try again.")
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
