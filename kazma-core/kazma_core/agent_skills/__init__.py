"""Agent Skills (agentskills.io / SKILL.md) support for Kazma.

Implements the open Agent Skills format:
https://agentskills.io/specification

Progressive disclosure:
  1. Catalog  — name + description at session start
  2. Body     — full SKILL.md when activated
  3. Resources — scripts/references/assets on demand via file tools
"""

from __future__ import annotations

from kazma_core.agent_skills.catalog import (
    build_catalog_prompt,
    format_skill_activation,
    list_skill_summaries,
)
from kazma_core.agent_skills.discovery import (
    AgentSkill,
    discover_skills,
    get_skill,
    skill_base_dirs,
)
from kazma_core.agent_skills.installer import (
    InstallResult,
    install_from_any,
    install_from_github,
    install_from_source,
    uninstall_skill,
)
from kazma_core.agent_skills.parser import ParsedSkill, parse_skill_md

__all__ = [
    "AgentSkill",
    "InstallResult",
    "ParsedSkill",
    "build_catalog_prompt",
    "discover_skills",
    "format_skill_activation",
    "get_skill",
    "install_from_any",
    "install_from_github",
    "install_from_source",
    "list_skill_summaries",
    "parse_skill_md",
    "skill_base_dirs",
    "uninstall_skill",
]
