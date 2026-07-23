"""Progressive-disclosure catalog for Agent Skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kazma_core.agent_skills.discovery import AgentSkill, discover_skills

__all__ = [
    "build_catalog_prompt",
    "format_skill_activation",
    "list_skill_summaries",
]


def list_skill_summaries(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return summary dicts for all enabled skills (for UI / tools)."""
    skills = discover_skills(
        project_root=project_root,
        workspace_root=workspace_root,
    )
    return [s.to_summary() for s in sorted(skills.values(), key=lambda s: s.name)]


def build_catalog_prompt(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
    max_skills: int = 80,
) -> str:
    """Build the system-prompt catalog block (tier-1 progressive disclosure).

    Returns empty string when no skills are installed — omit entirely so
    the model is not confused by an empty catalog.
    """
    skills = discover_skills(
        project_root=project_root,
        workspace_root=workspace_root,
    )
    if not skills:
        return ""

    items = sorted(skills.values(), key=lambda s: s.name)[:max_skills]
    lines = [
        "## Available Agent Skills",
        "",
        "The following skills provide specialized instructions for specific tasks.",
        "When a task matches a skill's description, call `activate_skill` with the",
        "skill's name **before** proceeding. Do NOT re-implement a skill from memory",
        "if it is listed here.",
        "",
        "To install a skill from GitHub or agentskills.io, call `install_agent_skill`",
        "with the repo URL or `owner/repo` (e.g. `shadcn/improve`). Do **not** use",
        "`npx`, `npm`, or `shell_exec` for skill installs — node/npm are not allowed",
        "in the shell allowlist; `install_agent_skill` is the supported path and needs",
        "only one approval.",
        "",
        "<available_skills>",
    ]
    for skill in items:
        lines.append("  <skill>")
        lines.append(f"    <name>{_xml_escape(skill.name)}</name>")
        lines.append(
            f"    <description>{_xml_escape(skill.description)}</description>"
        )
        lines.append(f"    <location>{_xml_escape(str(skill.location))}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")

    if len(skills) > max_skills:
        lines.append(
            f"\n({len(skills) - max_skills} more skills omitted; "
            "use `list_agent_skills` to see all.)"
        )

    return "\n".join(lines)


def format_skill_activation(skill: AgentSkill, *, max_resources: int = 40) -> str:
    """Format full skill body + resource listing for activation (tier 2)."""
    resources = _list_resources(skill.base_dir, max_resources=max_resources)

    parts = [
        f'<skill_content name="{_xml_escape(skill.name)}">',
        skill.parsed.body or "(no body content)",
        "",
        f"Skill directory: {skill.base_dir}",
        "Relative paths in this skill are relative to the skill directory.",
    ]
    if skill.parsed.compatibility:
        parts.append(f"Compatibility: {skill.parsed.compatibility}")
    if resources:
        parts.append("")
        parts.append("<skill_resources>")
        for rel in resources:
            parts.append(f"  <file>{_xml_escape(rel)}</file>")
        parts.append("</skill_resources>")
        parts.append(
            "Load resources with file_read using absolute paths under the skill directory."
        )
    parts.append("</skill_content>")
    return "\n".join(parts)


def _list_resources(base: Path, *, max_resources: int = 40) -> list[str]:
    """List bundled resource files (scripts/, references/, assets/)."""
    out: list[str] = []
    for sub in ("scripts", "references", "assets"):
        folder = base / sub
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.name != "SKILL.md":
                try:
                    rel = path.relative_to(base).as_posix()
                except ValueError:
                    continue
                out.append(rel)
                if len(out) >= max_resources:
                    out.append("… (truncated)")
                    return out
    return out


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
