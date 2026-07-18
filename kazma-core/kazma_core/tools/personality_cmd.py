"""/personality slash command — runtime personality switching.

Intercepts ``/personality`` commands before the LLM is invoked, so
switching tone is instant (<50ms) and costs zero tokens.

Supported invocations:
    /personality              → show current personality
    /personality list         → show all available templates
    /personality <name>       → switch to <name> immediately
    /personality current      → alias for /personality (show current)

Usage in the gateway dispatcher:
    from kazma_core.tools.personality_cmd import is_personality_command, handle_personality_command

    if is_personality_command(text):
        response = handle_personality_command(text)
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.personalities import (
    PERSONALITIES,
    get_current_personality,
    list_personalities,
    set_runtime_personality,
)

__all__ = ["handle_personality_command", "is_personality_command"]

logger = logging.getLogger(__name__)


def is_personality_command(text: str) -> bool:
    """Check if *text* is a /personality command."""
    stripped = text.strip().lower()
    return stripped.startswith("/personality") or stripped.startswith("/persona")


def handle_personality_command(
    text: str,
    config: dict[str, Any] | None = None,
) -> str:
    """Handle a /personality slash command.

    Args:
        text:    Raw command text (e.g. "/personality list").
        config:  Optional config dict for resolving "current" personality.

    Returns:
        Response string for the user.
    """
    parts = text.strip().split()

    # /personality or /personality current → show current
    if len(parts) == 1 or (len(parts) == 2 and parts[1].lower() == "current"):
        return _format_current(config)

    subcommand = parts[1].lower()

    # /personality list → show all
    if subcommand == "list":
        return _format_list()

    # /personality <name> → switch
    if subcommand in PERSONALITIES:
        return _switch(subcommand)

    # Unknown personality name
    available = ", ".join(sorted(PERSONALITIES.keys()))
    return (
        f"❌ Unknown personality: `{subcommand}`\n\n"
        f"Available: {available}\n\n"
        f"Use `/personality list` to see descriptions."
    )


def _format_current(config: dict[str, Any] | None) -> str:
    """Format the current personality info."""
    p = get_current_personality(config=config)
    return (
        f"🎭 Current personality: **{p.name}** {p.emoji}\n"
        f"{p.description}"
    )


def _format_list() -> str:
    """Format the list of all available personalities."""
    lines = ["🎭 *Available personalities:*", ""]
    for p in list_personalities():
        lines.append(f"• `{p.name}` {p.emoji} — {p.description}")
    lines.append("")
    lines.append("_Switch with `/personality <name>`_")
    return "\n".join(lines)


def _switch(name: str) -> str:
    """Switch to a personality by name."""
    try:
        set_runtime_personality(name)
        p = PERSONALITIES[name]
        logger.info("Personality switched to %s via /personality command", name)
        return f"✅ Switched to **{name}**: {p.description}"
    except ValueError as exc:
        return f"❌ {exc}"
