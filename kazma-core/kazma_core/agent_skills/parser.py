"""Parse Agent Skills SKILL.md files (YAML frontmatter + markdown body)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = ["ParsedSkill", "parse_skill_md", "validate_skill_name"]

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z",
    re.DOTALL,
)


@dataclass(slots=True)
class ParsedSkill:
    """Parsed SKILL.md content."""

    name: str
    description: str
    body: str
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: str = ""
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    path: Path | None = None

    @property
    def author(self) -> str:
        return self.metadata.get("author", "")

    @property
    def version(self) -> str:
        return self.metadata.get("version", "")


def validate_skill_name(name: str) -> list[str]:
    """Return soft-validation warnings for a skill name (lenient load)."""
    warnings: list[str] = []
    if not name:
        warnings.append("name is empty")
        return warnings
    if len(name) > 64:
        warnings.append(f"name exceeds 64 characters ({len(name)})")
    if not _NAME_RE.match(name):
        warnings.append(
            "name should be lowercase alphanumeric with single hyphens "
            f"(got {name!r})"
        )
    return warnings


def _lenient_yaml_load(text: str) -> dict[str, Any]:
    """Parse frontmatter YAML; retry with quoted values if colons break parse."""
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass

    # Common cross-client issue: unquoted values containing colons.
    fixed_lines: list[str] = []
    for line in text.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if (
                key
                and val
                and not val.startswith(("'", '"', "|", ">", "[", "{"))
                and ":" in val
            ):
                fixed_lines.append(f'{key}: "{val.replace(chr(34), chr(39))}"')
                continue
        fixed_lines.append(line)
    try:
        data = yaml.safe_load("\n".join(fixed_lines))
        if isinstance(data, dict):
            return data
    except yaml.YAMLError as exc:
        logger.debug("SKILL.md frontmatter parse failed: %s", exc)
    return {}


def parse_skill_md(
    content: str,
    *,
    path: Path | None = None,
    directory_name: str | None = None,
) -> ParsedSkill | None:
    """Parse a SKILL.md string into a :class:`ParsedSkill`.

    Returns ``None`` when the file is unusable (no description / unparseable).
    Soft issues become warnings; the skill still loads when possible.
    """
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    match = _FRONTMATTER_RE.match(content)
    if not match:
        logger.warning("SKILL.md missing YAML frontmatter: %s", path or "<string>")
        return None

    fm = _lenient_yaml_load(match.group(1))
    if not fm:
        logger.warning("SKILL.md frontmatter empty/unparseable: %s", path or "<string>")
        return None

    body = (match.group(2) or "").strip()
    name = str(fm.get("name") or directory_name or "").strip()
    description = str(fm.get("description") or "").strip()

    if not description:
        logger.warning("SKILL.md missing description — skipping: %s", path or name)
        return None

    if not name:
        name = directory_name or "unnamed-skill"

    warnings = validate_skill_name(name)
    if directory_name and name != directory_name:
        warnings.append(
            f"name {name!r} does not match directory {directory_name!r}"
        )

    meta_raw = fm.get("metadata") or {}
    metadata: dict[str, str] = {}
    if isinstance(meta_raw, dict):
        metadata = {str(k): str(v) for k, v in meta_raw.items() if v is not None}

    # Clamp description for catalog use (spec max 1024).
    if len(description) > 1024:
        warnings.append("description exceeds 1024 characters; truncating for catalog")
        description = description[:1021] + "..."

    return ParsedSkill(
        name=name,
        description=description,
        body=body,
        license=str(fm.get("license") or ""),
        compatibility=str(fm.get("compatibility") or ""),
        metadata=metadata,
        allowed_tools=str(fm.get("allowed-tools") or fm.get("allowed_tools") or ""),
        raw_frontmatter=fm,
        warnings=warnings,
        path=path,
    )
