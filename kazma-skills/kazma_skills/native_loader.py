"""Kazma Native Skill Loader — dynamically auto-discovers and registers native skills."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any
import yaml

logger = logging.getLogger(__name__)

#: The manifest file every native skill folder carries.
MANIFEST_NAME = "skill_manifest.yaml"


class NativeSkillLoader:
    """Discovers and dynamically registers built-in native skills."""

    def __init__(self, registry: Any) -> None:
        self.registry = registry
        self.native_dir = Path(__file__).parent / "native"

    def skill_dirs(self) -> list[Path]:
        """Every native skill folder the loader reads, in load order."""
        if not self.native_dir.is_dir():
            return []
        return [
            d for d in sorted(self.native_dir.iterdir())
            if d.is_dir() and not d.name.startswith("_") and (d / MANIFEST_NAME).exists()
        ]

    def register_all(self) -> None:
        """Scan and register all native skills from the native/ folder."""
        if not self.native_dir.is_dir():
            logger.warning("[NativeSkillLoader] Native skills directory not found: %s", self.native_dir)
            return

        for skill_dir in self.skill_dirs():
            try:
                self.register_skill(skill_dir)
                logger.info("[NativeSkillLoader] Successfully loaded native skill: %s", skill_dir.name)
            except Exception as e:
                logger.error("[NativeSkillLoader] Failed to load native skill %s: %s", skill_dir.name, e, exc_info=True)

    def resolve_skill(self, skill_dir: Path) -> tuple[str, dict[str, tuple[Any, dict[str, Any]]], list[str]]:
        """Read *skill_dir*'s manifest and find its tool functions.

        Returns the skill's name, ``{tool: (function, manifest entry)}`` and
        the tools the manifest names that its module does not define. A
        manifest that is not a mapping, or a tools module that does not
        import, raises. A skill with no tools (an instruction skill) has
        nothing to find.
        """
        manifest_path = skill_dir / MANIFEST_NAME
        with open(manifest_path, encoding="utf-8") as f:
            manifest_data = yaml.safe_load(f) or {}
        if not isinstance(manifest_data, dict):
            raise ValueError(f"{MANIFEST_NAME} is not a mapping")

        skill_name = str(manifest_data.get("name", skill_dir.name))
        tools_dict = manifest_data.get("tools") or {}
        if not isinstance(tools_dict, dict):
            raise ValueError(f"{MANIFEST_NAME}: 'tools' is not a mapping")
        if not tools_dict:
            return skill_name, {}, []

        mod = importlib.import_module(f"kazma_skills.native.{skill_dir.name}.tools")
        found: dict[str, tuple[Any, dict[str, Any]]] = {}
        missing: list[str] = []
        for tool_name, tool_info in tools_dict.items():
            func = getattr(mod, tool_name, None)
            if func is None:
                missing.append(str(tool_name))
            else:
                found[str(tool_name)] = (func, tool_info if isinstance(tool_info, dict) else {})
        return skill_name, found, missing

    def problems(self) -> dict[str, list[str]]:
        """What keeps each native skill from loading as :meth:`register_all` loads it.

        Only skills with a problem are listed. Registers nothing.
        """
        out: dict[str, list[str]] = {}
        for skill_dir in self.skill_dirs():
            try:
                _name, _found, missing = self.resolve_skill(skill_dir)
            except Exception as exc:  # a tools module can raise anything on import; register_all logs the same
                out[skill_dir.name] = [f"{type(exc).__name__}: {exc}"]
                continue
            if missing:
                out[skill_dir.name] = [f"tool {name!r} has no function in its tools module" for name in missing]
        return out

    def register_skill(self, skill_dir: Path) -> None:
        """Loads a single native skill manifest and registers its tools."""
        try:
            skill_name, found, missing = self.resolve_skill(skill_dir)
        except ImportError as e:
            logger.error("[NativeSkillLoader] Failed to import the tools of skill %s: %s", skill_dir.name, e)
            return
        module_path = f"kazma_skills.native.{skill_dir.name}.tools"
        for tool_name in missing:
            logger.warning("[NativeSkillLoader] Function '%s' not found in %s", tool_name, module_path)

        for tool_name, (func, tool_info) in found.items():
            desc = tool_info.get("description", func.__doc__ or f"Native Tool: {tool_name}")
            category = tool_info.get("category", "general")

            # Dynamic registration onto LocalToolRegistry
            self.registry.register_function(
                name=tool_name,
                func=func,
                description=desc,
                category=category,
            )

            # Bind Arabic and cultural metadata onto the registered tool object
            if hasattr(self.registry, "_tools") and tool_name in self.registry._tools:
                local_tool = self.registry._tools[tool_name]
                # The Skills page's id for this skill: its switch applies to
                # every tool the skill registers (kazma_core.skills.switches).
                local_tool.skill_id = f"native:{skill_dir.name}"
                # Attach custom fields for UI rendering & localized system prompt building
                local_tool.arabic_name = tool_info.get("arabic_name", tool_name)
                local_tool.prompt_chain = tool_info.get("prompt_chain", [])
                local_tool.cultural_context = tool_info.get("cultural_context", {})
        logger.debug("[NativeSkillLoader] %s: %d tool(s) registered", skill_name, len(found))
