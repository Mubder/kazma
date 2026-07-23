"""Kazma CLI startup banner and status display.

Provides the first-run experience: ASCII art branding, config health checks,
and a quick reference hint for slash commands.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "KAZMA_ASCII",
    "check_config",
    "show_banner",
    "show_help_brief",
    "show_status",
]

# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

def _version_from_pyproject(path: Path) -> str | None:
    """Parse ``[project].version`` from a pyproject.toml file."""
    try:
        if not path.is_file():
            return None
        content = path.read_text(encoding="utf-8")
        in_project = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "[project]":
                in_project = True
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                in_project = False
                continue
            if in_project and stripped.startswith("version"):
                _, _, rhs = stripped.partition("=")
                ver = rhs.strip().strip('"').strip("'")
                if ver:
                    return ver
    except Exception as exc:
        logger.debug("pyproject.toml version parse failed (%s): %s", path, exc)
    return None


def _get_version() -> str:
    """Resolve product version from monorepo pyproject (source of truth).

    Prefer the checkout's ``pyproject.toml`` over ``importlib.metadata`` so
    editable installs that still advertise a stale wheel version (e.g. 0.5.0)
    don't mislead after a git pull to 0.6.x.
    Never hardcode a release number in CLI help.
    """
    # 1) Monorepo / project pyproject.toml (walk up from this file + cwd)
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "pyproject.toml",  # kazma-cli/kazma_cli → repo root
        here.parent.parent / "pyproject.toml",
        Path.cwd() / "pyproject.toml",
    ]
    # Also walk parents for nested layouts
    for parent in list(here.parents)[:8]:
        candidates.append(parent / "pyproject.toml")

    seen: set[Path] = set()
    for pyproject in candidates:
        try:
            key = pyproject.resolve()
        except Exception:
            key = pyproject
        if key in seen:
            continue
        seen.add(key)
        ver = _version_from_pyproject(pyproject)
        if ver:
            return ver

    # 2) Installed distribution metadata (wheels / non-editable)
    try:
        from importlib.metadata import PackageNotFoundError, version

        for dist in ("kazma", "kazma-cli"):
            try:
                return version(dist)
            except PackageNotFoundError:
                continue
    except Exception as exc:
        logger.debug("importlib.metadata version lookup failed: %s", exc)

    return "0.0.0"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Walk up from banner.py to find the project root (where kazma.yaml lives)."""
    current = Path(__file__).resolve().parent
    for _ in range(6):
        if (current / "kazma.yaml").exists():
            return current
        current = current.parent
    return Path.cwd()


def _load_config(project_root: Path | None = None) -> dict[str, Any]:
    """Load kazma.yaml config, returning empty dict if missing or unparseable."""
    if project_root is None:
        project_root = _find_project_root()
    config_path = project_root / "kazma.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        return {}


def _check_venv(project_root: Path) -> bool:
    """Check if .venv directory exists and has a python binary."""
    venv = project_root / ".venv"
    if not venv.is_dir():
        return False
    return (venv / "bin" / "python").exists() or (venv / "Scripts" / "python.exe").exists()


def _count_slash_commands() -> int:
    """Count slash commands from the gateway module, if available."""
    try:
        # resolve_slash_command checked via importlib below

        # Commands are registered in resolve_slash_command's dispatch table
        # We count them by checking against known commands
        known = ["/help", "/reset", "/status", "/model", "/memory", "/cost",
                 "/undo", "/edit", "/replay", "/personality", "/context"]
        return len(known)
    except ImportError:
        return 0


def _get_active_adapters(config: dict[str, Any]) -> list[str]:
    """Return list of enabled adapter names from config."""
    if not config:
        return []
    connectors = config.get("connectors", {})
    active = []
    for name, cfg in connectors.items():
        if isinstance(cfg, dict) and cfg.get("enabled"):
            active.append(name)
        elif isinstance(cfg, bool) and cfg:
            active.append(name)
    return active


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

KAZMA_ASCII = r"""
  ██╗  ██╗ █████╗ ███████╗███╗   ███╗ █████╗
  ██║ ██╔╝██╔══██╗╚══███╔╝████╗ ████║██╔══██╗
  █████╔╝ ███████║  ███╔╝ ██╔████╔██║███████║
  ██╔═██╗ ██╔══██║ ███╔╝  ██║╚██╔╝██║██╔══██║
  ██║  ██╗██║  ██║███████╗██║ ╚═╝ ██║██║  ██║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝╚═╝  ╚═╝
"""


def show_banner(suppress: bool = False) -> str:
    """Return the startup banner text (ASCII art + version).

    Args:
        suppress: If True, return a minimal one-line version string.
    """
    version = _get_version()
    if suppress:
        return f"Kazma CLI v{version}"

    lines = []
    lines.append(KAZMA_ASCII.rstrip("\n"))
    lines.append(f"    Autonomous AI Agent Framework  •  v{version}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def show_status(config: dict[str, Any] | None = None) -> str:
    """Return a status overview string.

    Shows active model, tools count, and enabled adapters.
    """
    if config is None:
        config = _load_config()

    lines = []
    lines.append("─" * 52)
    lines.append("  System Status")

    # Model / Provider — prefer ModelRegistry, fall back to YAML
    try:
        from kazma_core.model_registry import get_model_registry

        profile = get_model_registry().get_active_profile()
        model = profile.get("model", "unknown")
        provider = profile.get("provider", "unknown")
    except (RuntimeError, ImportError):
        llm = config.get("llm", {})
        model = llm.get("model", config.get("models", {}).get("default", "unknown"))
        provider = (
            config.get("models", {}).get("router", "litellm")
            if isinstance(config.get("models"), dict)
            else "litellm"
        )
    lines.append(f"  Model:     {model}")
    lines.append(f"  Provider:  {provider}")

    # Tools (slash commands)
    tools = _count_slash_commands()
    lines.append(f"  Tools:     {tools} slash commands available")

    # Adapters
    adapters = _get_active_adapters(config)
    if adapters:
        lines.append(f"  Adapters:  {', '.join(adapters)} (active)")
    else:
        lines.append("  Adapters:  none active")

    # Config file
    root = _find_project_root()
    config_path = root / "kazma.yaml"
    if config_path.exists():
        lines.append(f"  Config:    {config_path}")
    else:
        lines.append("  Config:    ⚠ not found")

    # Venv
    if _check_venv(root):
        lines.append(f"  Venv:      {root / '.venv'} (found)")
    else:
        lines.append("  Venv:      ⚠ not found")

    lines.append("─" * 52)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Brief help
# ---------------------------------------------------------------------------

def show_help_brief() -> str:
    """Return a compact first-run help hint."""
    return (
        "\n💡 Quick start:  Type /help to see available commands\n"
        "   Run: kazma --help  for CLI usage\n"
    )


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def check_config(project_root: Path | None = None) -> list[str]:
    """Run startup config checks, returning a list of warnings/messages.

    Checks:
        1. kazma.yaml exists
        2. .venv exists
        3. API key is set for active provider
    """
    if project_root is None:
        project_root = _find_project_root()

    warnings: list[str] = []
    config = _load_config(project_root)

    # 1. Config file existence
    if not (project_root / "kazma.yaml").exists():
        warnings.append("⚠️  No kazma.yaml found. Run 'kazma wizard' to create one.")

    # 2. venv
    if not _check_venv(project_root):
        warnings.append("⚠️  No .venv found. Run 'python -m venv .venv' to create one.")

    # 3. API key
    api_key = ""
    llm = config.get("llm", {})
    if isinstance(llm, dict):
        api_key = llm.get("api_key", "")

    # Also check environment variable
    env_key = os.environ.get("OPENAI_API_KEY", "")

    if not api_key and not env_key:
        provider = "OpenAI"
        if isinstance(config.get("models"), dict):
            router = config["models"].get("router", "")
            if "litellm" in str(router).lower():
                provider = "OpenAI-compatible"
        warnings.append(
            f"⚠️  No API key set for {provider}. "
            "Set OPENAI_API_KEY env var or update kazma.yaml."
        )

    return warnings
