"""Agent-facing tools for Agent Skills (list / activate / install / uninstall)."""

from __future__ import annotations

import json
import logging
from typing import Any

__all__ = [
    "activate_skill",
    "install_agent_skill",
    "list_agent_skills",
    "uninstall_agent_skill",
]

logger = logging.getLogger(__name__)


def _workspace_root() -> Any:
    try:
        from kazma_core.tools.file_write import _get_workspace

        return _get_workspace()
    except Exception:
        return None


async def list_agent_skills() -> str:
    """List installed Agent Skills (agentskills.io / SKILL.md format)."""
    from kazma_core.agent_skills.catalog import list_skill_summaries

    summaries = list_skill_summaries(workspace_root=_workspace_root())
    if not summaries:
        return (
            "No Agent Skills installed.\n\n"
            "Install one with `install_agent_skill` — e.g.\n"
            "  install_agent_skill(source='shadcn/improve')\n"
            "  install_agent_skill(source='https://github.com/shadcn/improve')\n\n"
            "Browse the public hub: https://agentskills.io/"
        )
    lines = [f"**{len(summaries)} Agent Skill(s) installed:**\n"]
    for s in summaries:
        ver = f" v{s['version']}" if s.get("version") else ""
        auth = f" by {s['author']}" if s.get("author") else ""
        lines.append(f"- **{s['name']}**{ver}{auth} [{s['scope']}]")
        lines.append(f"  {s['description'][:200]}")
        lines.append(f"  `{s['location']}`")
    lines.append("\nActivate with `activate_skill(name=\"…\")`.")
    return "\n".join(lines)


async def activate_skill(name: str) -> str:
    """Load full instructions for an Agent Skill into context.

    Call this when a user task matches a skill's description.
    """
    from kazma_core.agent_skills.catalog import format_skill_activation
    from kazma_core.agent_skills.discovery import get_skill

    skill_name = (name or "").strip()
    if not skill_name:
        return "Error: skill name is required. Use list_agent_skills to see names."

    skill = get_skill(skill_name, workspace_root=_workspace_root())
    if skill is None:
        # Fuzzy help
        from kazma_core.agent_skills.discovery import discover_skills

        available = sorted(discover_skills(workspace_root=_workspace_root()).keys())
        hint = (
            f" Available: {', '.join(available)}"
            if available
            else " None installed — use install_agent_skill first."
        )
        return f"Error: skill {skill_name!r} not found.{hint}"

    if not skill.enabled:
        return (
            f"Error: skill {skill_name!r} is disabled. "
            "Re-enable it in Settings → Skills."
        )

    logger.info("[agent_skills] activated skill=%s path=%s", skill.name, skill.location)
    return format_skill_activation(skill)


async def install_agent_skill(source: str, scope: str = "user") -> str:
    """Install an Agent Skill from GitHub or a local path.

    Preferred over npx/npm — works without Node.js and needs a single
    HITL approval. Accepts:
      - owner/repo  (e.g. shadcn/improve)
      - https://github.com/owner/repo
      - local path containing SKILL.md
      - `npx skills add owner/repo` (parsed automatically)
    """
    from kazma_core.agent_skills.installer import install_from_any

    src = (source or "").strip()
    if not src:
        return (
            "Error: source is required. Examples:\n"
            "  install_agent_skill(source='shadcn/improve')\n"
            "  install_agent_skill(source='https://github.com/shadcn/improve')\n"
            "Browse: https://agentskills.io/"
        )

    scope_norm = (scope or "user").strip().lower()
    if scope_norm not in ("user", "project"):
        scope_norm = "user"

    logger.info("[agent_skills] install source=%s scope=%s", src, scope_norm)
    result = await install_from_any(src, scope=scope_norm)
    return result.to_user_message()


async def uninstall_agent_skill(name: str) -> str:
    """Uninstall a user-level Agent Skill by name."""
    from kazma_core.agent_skills.installer import uninstall_skill

    skill_name = (name or "").strip()
    if not skill_name:
        return "Error: skill name is required."
    result = uninstall_skill(skill_name)
    return result.to_user_message()
