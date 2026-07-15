"""Swarm-native coding skill manifests — prompt/instruction helpers.

These are *prompt/instruction* skills only. They contain no tool
implementations; instead each manifest under ``native/<name>`` declares a
``swarm_instruction_template`` that, given a workspace path, produces an
instruction string suitable for ``IdeService.send_to_swarm(instruction=...)``.

The swarm workers already expose file_write / file_read / shell_exec /
python_exec, and danger-tier operations are routed through the HITL approval
gate by the safety layer. These helpers only read manifests and fill in the
``{path}`` placeholder — they never perform file or exec operations.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_NATIVE_DIR = Path(__file__).resolve().parent / "native"
_CODING_SKILL_NAMES = ("refactor-file", "write-tests", "fix-lint", "code-review")


def list_coding_skills() -> list[dict[str, Any]]:
    """Return metadata for the swarm-native coding skills.

    Returns:
        A list of dicts with ``name``, ``description``, ``category``,
        ``capabilities``, ``tags``, ``workspace_scoped``, and
        ``hitl_gated`` for each known coding skill.
    """
    skills: list[dict[str, Any]] = []
    for name in _CODING_SKILL_NAMES:
        manifest = _load_manifest(name)
        if manifest is None:
            continue
        skills.append(
            {
                "name": manifest.get("name", name),
                "description": manifest.get("description", ""),
                "category": manifest.get("category", "code"),
                "capabilities": manifest.get("capabilities", []),
                "tags": manifest.get("tags", []),
                "workspace_scoped": manifest.get("workspace_scoped", True),
                "hitl_gated": manifest.get("hitl_gated", True),
            }
        )
    return skills


def render_instruction(skill_name: str, path: str) -> str:
    """Render the swarm instruction for a coding skill with a concrete path.

    Args:
        skill_name: One of the coding skill names (e.g. ``"refactor-file"``).
        path: A file or directory path inside the active workspace.

    Returns:
        The fully-rendered instruction string for ``send_to_swarm``.

    Raises:
        ValueError: If the skill is unknown or has no instruction template.
    """
    if skill_name not in _CODING_SKILL_NAMES:
        raise ValueError(f"Unknown coding skill: {skill_name}")
    manifest = _load_manifest(skill_name)
    if manifest is None:
        raise ValueError(f"Manifest not found for skill: {skill_name}")
    template = manifest.get("swarm_instruction_template")
    if not template:
        raise ValueError(f"Skill {skill_name} has no swarm_instruction_template")
    return template.replace("{path}", path)


def _load_manifest(skill_name: str) -> dict[str, Any] | None:
    manifest_path = _NATIVE_DIR / skill_name / "skill_manifest.yaml"
    if not manifest_path.exists():
        logger.warning("Coding skill manifest missing: %s", manifest_path)
        return None
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to load coding skill %s: %s", skill_name, exc)
        return None
