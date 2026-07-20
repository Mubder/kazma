"""Discover Agent Skills (SKILL.md) on the filesystem.

Scan order (later scopes override earlier on name collision):
  user-level  → project-level

Within a scope, client-specific dirs are scanned before the shared
``.agents/skills/`` convention. Project skills win over user skills.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from kazma_core.agent_skills.parser import ParsedSkill, parse_skill_md

__all__ = [
    "AgentSkill",
    "discover_skills",
    "get_skill",
    "skill_base_dirs",
    "user_agent_skills_dir",
]

logger = logging.getLogger(__name__)

_SKIP_DIR_NAMES = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    "site-packages",
})

_MAX_SCAN_DEPTH = 5
_MAX_DIRS = 2000


@dataclass(slots=True)
class AgentSkill:
    """A discovered Agent Skill with parsed metadata and location."""

    name: str
    description: str
    location: Path  # absolute path to SKILL.md
    scope: str  # "user" | "project" | "bundled"
    parsed: ParsedSkill
    enabled: bool = True
    source: str = ""  # install source URL / owner/repo if known
    warnings: list[str] = field(default_factory=list)

    @property
    def base_dir(self) -> Path:
        return self.location.parent

    @property
    def author(self) -> str:
        return self.parsed.author

    @property
    def version(self) -> str:
        return self.parsed.version

    def to_summary(self) -> dict[str, str | bool | list[str]]:
        return {
            "name": self.name,
            "description": self.description,
            "location": str(self.location),
            "scope": self.scope,
            "author": self.author,
            "version": self.version,
            "enabled": self.enabled,
            "source": self.source,
            "warnings": list(self.warnings),
        }


def user_agent_skills_dir() -> Path:
    """Primary install target for Agent Skills (cross-client convention)."""
    # Prefer ~/.agents/skills for agentskills.io interoperability.
    agents = Path.home() / ".agents" / "skills"
    kazma = Path.home() / ".kazma" / "agent-skills"
    # Create primary target on first use.
    agents.mkdir(parents=True, exist_ok=True)
    kazma.mkdir(parents=True, exist_ok=True)
    return agents


def skill_base_dirs(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> list[tuple[str, Path]]:
    """Return ``(scope, path)`` pairs to scan, lowest → highest precedence.

    Project / workspace scopes come last so they override user skills.
    """
    dirs: list[tuple[str, Path]] = []

    # User-level (lowest precedence)
    dirs.append(("user", Path.home() / ".agents" / "skills"))
    dirs.append(("user", Path.home() / ".kazma" / "agent-skills"))
    # Claude/Cursor pragmatic compatibility
    dirs.append(("user", Path.home() / ".claude" / "skills"))
    dirs.append(("user", Path.home() / ".cursor" / "skills"))

    roots: list[Path] = []
    if project_root is not None:
        roots.append(project_root)
    if workspace_root is not None and workspace_root not in roots:
        roots.append(workspace_root)
    if not roots:
        try:
            from kazma_core.paths import get_project_root

            roots.append(get_project_root())
        except Exception:
            roots.append(Path.cwd())

    for root in roots:
        # Shared convention first within project, then client-specific.
        dirs.append(("project", root / ".agents" / "skills"))
        dirs.append(("project", root / ".kazma" / "skills"))
        dirs.append(("project", root / "skills"))
        dirs.append(("project", root / ".claude" / "skills"))
        dirs.append(("project", root / ".cursor" / "skills"))

    return dirs


def _iter_skill_md_files(base: Path) -> Iterable[Path]:
    """Yield SKILL.md paths under *base* with depth/skip bounds."""
    if not base.is_dir():
        return

    dirs_seen = 0
    base_resolved = base.resolve()

    for dirpath, dirnames, filenames in os.walk(base_resolved):
        dirs_seen += 1
        if dirs_seen > _MAX_DIRS:
            logger.warning("Skill scan hit max dir limit under %s", base)
            break

        # Prune skipped directories in-place
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIR_NAMES and not d.startswith(".")
        ]

        rel = Path(dirpath).relative_to(base_resolved)
        depth = len(rel.parts)
        if depth > _MAX_SCAN_DEPTH:
            dirnames.clear()
            continue

        if "SKILL.md" in filenames:
            yield Path(dirpath) / "SKILL.md"
            # Don't recurse into a skill's own resource dirs for nested skills
            dirnames[:] = [
                d for d in dirnames
                if d not in ("scripts", "references", "assets", "examples")
            ]


def _is_enabled(skill_name: str) -> bool:
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        val = store.get(f"agent_skills.enabled.{skill_name}")
        if val is None:
            return True
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("1", "true", "yes", "on")
    except Exception:
        return True


def _read_install_meta(skill_dir: Path) -> str:
    meta = skill_dir / ".kazma-install.json"
    if not meta.is_file():
        return ""
    try:
        import json

        data = json.loads(meta.read_text(encoding="utf-8"))
        return str(data.get("source") or "")
    except Exception:
        return ""


def discover_skills(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
    include_disabled: bool = False,
) -> dict[str, AgentSkill]:
    """Discover all Agent Skills. Project skills override user skills by name."""
    found: dict[str, AgentSkill] = {}

    for scope, base in skill_base_dirs(
        project_root=project_root,
        workspace_root=workspace_root,
    ):
        if not base.is_dir():
            continue
        for skill_md in _iter_skill_md_files(base):
            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError as exc:
                logger.debug("Cannot read %s: %s", skill_md, exc)
                continue

            parsed = parse_skill_md(
                text,
                path=skill_md.resolve(),
                directory_name=skill_md.parent.name,
            )
            if parsed is None:
                continue

            enabled = _is_enabled(parsed.name)
            if not enabled and not include_disabled:
                continue

            skill = AgentSkill(
                name=parsed.name,
                description=parsed.description,
                location=skill_md.resolve(),
                scope=scope,
                parsed=parsed,
                enabled=enabled,
                source=_read_install_meta(skill_md.parent),
                warnings=list(parsed.warnings),
            )

            if skill.name in found:
                prev = found[skill.name]
                logger.info(
                    "Skill name collision: %s (%s) overrides %s (%s)",
                    skill.name,
                    skill.location,
                    prev.name,
                    prev.location,
                )
            found[skill.name] = skill

    return found


def get_skill(
    name: str,
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> AgentSkill | None:
    """Look up a single skill by name."""
    skills = discover_skills(
        project_root=project_root,
        workspace_root=workspace_root,
        include_disabled=True,
    )
    return skills.get(name)
