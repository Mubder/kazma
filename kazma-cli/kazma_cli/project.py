"""Project-level .kazma/ directory manager.

Creates, loads, validates, and displays project-level configuration
stored in the .kazma/ directory at the project root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Default templates
# ---------------------------------------------------------------------------

DEFAULT_RULES = """# Project-specific agent rules
# These rules are applied to every agent session in this project.
language: python
test_command: .venv/bin/pytest tests/ -q
git_branch: main
"""

DEFAULT_CONTEXT = """# Project Context

<!-- Add project-specific context that agents should know.
     This is like .cursorrules — describe conventions, architecture,
     key files, and patterns unique to this project. -->

## Overview

## Conventions

## Architecture

## Key Files
"""

DEFAULT_PERSONALITY = """# Project-level personality override
# Override the default agent personality for this project.
# Leave empty to use the global default.
#
# Example:
#   persona: senior-backend
#   verbosity: concise
#   tone: professional
#
# Supported keys: persona, verbosity, tone, language, role
"""

DEFAULT_TOOLS = """# Enabled/disabled tools per project
# List tool names to enable or disable for this project.
# An empty enabled list means "use global defaults".
#
# Example:
#   enabled:
#     - file
#     - terminal
#     - web
#   disabled:
#     - browser
enabled: []
disabled: []
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def project_exists(path: str | Path = ".") -> bool:
    """Check if a .kazma/ directory exists at *path*."""
    root = Path(path).resolve()
    return (root / ".kazma").is_dir()


def init_project(path: str | Path = ".") -> Path:
    """Create .kazma/ directory structure at *path*.

    Returns the resolved path to the .kazma/ directory.
    Idempotent — running twice only creates missing files.
    """
    root = Path(path).resolve()
    kazma_dir = root / ".kazma"

    # Top-level directory
    kazma_dir.mkdir(parents=True, exist_ok=True)

    # Sub-directories
    (kazma_dir / "history").mkdir(exist_ok=True)

    # Template files (only create if missing)
    _write_if_missing(kazma_dir / "rules.yaml", DEFAULT_RULES)
    _write_if_missing(kazma_dir / "context.md", DEFAULT_CONTEXT)
    _write_if_missing(kazma_dir / "personality.yaml", DEFAULT_PERSONALITY)
    _write_if_missing(kazma_dir / "tools.yaml", DEFAULT_TOOLS)

    return kazma_dir


def load_project(path: str | Path = ".") -> dict[str, Any] | None:
    """Load .kazma/ config if it exists, otherwise return None."""
    root = Path(path).resolve()
    kazma_dir = root / ".kazma"

    if not kazma_dir.is_dir():
        return None

    config: dict[str, Any] = {"_path": str(kazma_dir)}

    for name in ("rules.yaml", "personality.yaml", "tools.yaml"):
        filepath = kazma_dir / name
        if filepath.is_file():
            try:
                data = yaml.safe_load(filepath.read_text())
                # yaml.safe_load returns None for empty or all-comments files.
                # Treat as empty dict so show_project displays "(empty)" instead
                # of "(parse error)".
                config[name.replace(".yaml", "")] = data if data is not None else {}
            except yaml.YAMLError:
                config[name.replace(".yaml", "")] = None

    context_md = kazma_dir / "context.md"
    if context_md.is_file():
        config["context"] = context_md.read_text()

    return config


def show_project(path: str | Path = ".") -> str:
    """Return a human-readable summary of the project config."""
    config = load_project(path)
    if config is None:
        return "No .kazma/ project directory found. Run 'kazma project init' to create one."

    lines: list[str] = []
    kazma_path = config.pop("_path", "unknown")
    lines.append(f"Project config: {kazma_path}")
    lines.append("-" * 40)

    for key, value in config.items():
        lines.append(f"[{key}]")
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, list):
                    lines.append(f"  {k}: {', '.join(str(x) for x in v) if v else '(none)'}")
                elif v is None:
                    lines.append(f"  {k}: (empty)")
                else:
                    lines.append(f"  {k}: {v}")
        elif isinstance(value, str):
            # Truncate long context strings for display
            preview = value[:200] + "..." if len(value) > 200 else value
            lines.append(f"  {preview}")
        elif value is None:
            lines.append("  (parse error)")

    return "\n".join(lines)


def validate_project(path: str | Path = ".") -> tuple[bool, list[str]]:
    """Validate .kazma/ config.

    Returns (is_valid, list_of_issues).
    """
    root = Path(path).resolve()
    kazma_dir = root / ".kazma"
    issues: list[str] = []

    if not kazma_dir.is_dir():
        issues.append("Missing .kazma/ directory. Run 'kazma project init'.")
        return False, issues

    # Check required files
    required = ["rules.yaml", "context.md", "personality.yaml", "tools.yaml"]
    for name in required:
        if not (kazma_dir / name).is_file():
            issues.append(f"Missing file: {name}")

    # Validate YAML files
    for name in ("rules.yaml", "personality.yaml", "tools.yaml"):
        filepath = kazma_dir / name
        if filepath.is_file():
            try:
                data = yaml.safe_load(filepath.read_text())
                if data is None and name != "personality.yaml":
                    issues.append(f"Empty YAML file: {name}")
            except yaml.YAMLError as exc:
                issues.append(f"Invalid YAML in {name}: {exc}")

    # Validate rules.yaml content if present
    rules_path = kazma_dir / "rules.yaml"
    if rules_path.is_file():
        try:
            rules = yaml.safe_load(rules_path.read_text())
            if isinstance(rules, dict):
                if "language" not in rules:
                    issues.append("rules.yaml: missing 'language' key")
                if "git_branch" not in rules:
                    issues.append("rules.yaml: missing 'git_branch' key")
        except yaml.YAMLError:
            pass  # already reported above

    # History directory
    if not (kazma_dir / "history").is_dir():
        issues.append("Missing history/ directory")

    return len(issues) == 0, issues


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_if_missing(filepath: Path, content: str) -> bool:
    """Write *content* to *filepath* only if the file does not already exist.

    Returns True if the file was created, False if it already existed.
    """
    if not filepath.exists():
        filepath.write_text(content)
        return True
    return False
