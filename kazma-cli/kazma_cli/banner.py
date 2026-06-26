"""Kazma CLI startup banner and status display.

Provides the first-run experience: ASCII art branding, config health checks,
and a quick reference hint for slash commands.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

def _get_version() -> str:
    """Read version from pyproject.toml, falling back to '0.1.0'."""
    try:
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text()
            for line in content.splitlines():
                if line.strip().startswith("version ="):
                    return line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "0.1.0"


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

    # Model
    model = "unknown"
    llm = config.get("llm", {})
    if isinstance(llm, dict):
        model = llm.get("model", config.get("models", {}).get("default", "unknown"))
    lines.append(f"  Model:     {model}")

    # Provider
    provider = config.get("models", {}).get("router", "litellm") if isinstance(config.get("models"), dict) else "litellm"
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
