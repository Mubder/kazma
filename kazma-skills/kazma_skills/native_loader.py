"""Kazma Native Skill Loader — dynamically auto-discovers and registers native skills."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any
import yaml

logger = logging.getLogger(__name__)


class NativeSkillLoader:
    """Discovers and dynamically registers built-in native skills."""

    def __init__(self, registry: Any) -> None:
        self.registry = registry
        self.native_dir = Path(__file__).parent / "native"

    def register_all(self) -> None:
        """Scan and register all native skills from the native/ folder."""
        if not self.native_dir.is_dir():
            logger.warning("[NativeSkillLoader] Native skills directory not found: %s", self.native_dir)
            return

        for skill_dir in sorted(self.native_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
                continue
            manifest_path = skill_dir / "skill_manifest.yaml"
            if not manifest_path.exists():
                continue

            try:
                self.register_skill(skill_dir)
                logger.info("[NativeSkillLoader] Successfully loaded native skill: %s", skill_dir.name)
            except Exception as e:
                logger.error("[NativeSkillLoader] Failed to load native skill %s: %s", skill_dir.name, e, exc_info=True)

    def register_skill(self, skill_dir: Path) -> None:
        """Loads a single native skill manifest and registers its tools."""
        manifest_path = skill_dir / "skill_manifest.yaml"
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = yaml.safe_load(f) or {}

        skill_name = manifest_data.get("name", skill_dir.name)
        tools_dict = manifest_data.get("tools", {})
        if not tools_dict:
            return

        module_path = f"kazma_skills.native.{skill_dir.name}.tools"
        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            logger.error("[NativeSkillLoader] Failed to import module %s for skill %s: %s", module_path, skill_name, e)
            return

        for tool_name, tool_info in tools_dict.items():
            func = getattr(mod, tool_name, None)
            if func is None:
                logger.warning("[NativeSkillLoader] Function '%s' not found in %s", tool_name, module_path)
                continue

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
                # Attach custom fields for UI rendering & localized system prompt building
                local_tool.arabic_name = tool_info.get("arabic_name", tool_name)
                local_tool.prompt_chain = tool_info.get("prompt_chain", [])
                local_tool.cultural_context = tool_info.get("cultural_context", {})
