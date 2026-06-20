"""Skill Manifest — YAML manifests wrapping MCP tools for Arabic context.

Each manifest entry defines how a specific MCP tool should be presented
to Arabic-speaking users, including RTL-aware prompt chains and cultural
formatting rules.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Canonical path for the certified-servers manifest
_CERTIFIED_PATH = Path(__file__).resolve().parent / "certified_servers.yaml"


class SkillManifest:
    """YAML manifest defining how MCP tools are used in Arabic context.

    The manifest maps tool names to Arabic-aware prompt chains, cultural
    formatting rules, and metadata for the Kazma UI.

    Manifest format::

        tools:
          filesystem_read:
            arabic_name: "قراءة الملفات"
            prompt_chain:
              - "اسحب الملف المطلوب"
              - "اعرض المحتوى بالعربية"
            cultural_context:
              direction: rtl
              number_format: arabic-indic
              date_format: hijri
            description: "Read files from the filesystem"
            certified: true

    Args:
        manifest_path: Path to a YAML manifest file.  If ``None``, the
            built-in certified servers manifest is loaded.
    """

    def __init__(self, manifest_path: str | Path | None = None) -> None:
        self._path = Path(manifest_path) if manifest_path else _CERTIFIED_PATH
        self.manifest: dict[str, Any] = {}
        self._load()

    # -- public API --------------------------------------------------------

    def get_arabic_prompt(self, tool_name: str) -> str:
        """Get the Arabic prompt chain for a tool, joined by newlines.

        Args:
            tool_name: MCP tool name.

        Returns:
            The Arabic prompt chain as a single string.  Returns an empty
            string if the tool is not in the manifest.
        """
        tool = self._get_tool(tool_name)
        chain: list[str] = tool.get("prompt_chain", [])
        return "\n".join(chain)

    def get_cultural_context(self, tool_name: str) -> dict[str, Any]:
        """Get cultural formatting rules for a tool's output.

        Args:
            tool_name: MCP tool name.

        Returns:
            A dict with keys like ``direction``, ``number_format``,
            ``date_format``.  Returns an empty dict if not found.
        """
        tool = self._get_tool(tool_name)
        return tool.get("cultural_context", {})

    def get_arabic_name(self, tool_name: str) -> str:
        """Get the Arabic display name for a tool."""
        tool = self._get_tool(tool_name)
        return tool.get("arabic_name", tool_name)

    def is_certified(self, tool_name: str) -> bool:
        """Check whether a tool is Kazma-certified."""
        tool = self._get_tool(tool_name)
        return tool.get("certified", False)

    def list_tools(self) -> list[str]:
        """Return all tool names in the manifest."""
        return list(self.manifest.get("tools", {}).keys())

    def list_certified(self) -> list[str]:
        """Return names of all certified tools."""
        return [
            name
            for name, info in self.manifest.get("tools", {}).items()
            if info.get("certified", False)
        ]

    def get_description(self, tool_name: str) -> str:
        """Get the English description for a tool."""
        tool = self._get_tool(tool_name)
        return tool.get("description", "")

    def add_tool(
        self,
        tool_name: str,
        arabic_name: str = "",
        prompt_chain: list[str] | None = None,
        cultural_context: dict[str, Any] | None = None,
        description: str = "",
        certified: bool = False,
    ) -> None:
        """Add or update a tool in the manifest."""
        if "tools" not in self.manifest:
            self.manifest["tools"] = {}

        self.manifest["tools"][tool_name] = {
            "arabic_name": arabic_name or tool_name,
            "prompt_chain": prompt_chain or [],
            "cultural_context": cultural_context or {},
            "description": description,
            "certified": certified,
        }

    def save(self, path: str | Path | None = None) -> None:
        """Persist the manifest to disk."""
        target = Path(path) if path else self._path
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            yaml.dump(self.manifest, fh, default_flow_style=False, allow_unicode=True)
        logger.info("Saved manifest to %s (%d tools)", target, len(self.list_tools()))

    # -- internal ----------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as fh:
                self.manifest = yaml.safe_load(fh) or {}
            logger.debug("Loaded manifest from %s (%d tools)", self._path, len(self.list_tools()))
        else:
            self.manifest = {"tools": {}}
            logger.warning("Manifest not found at %s, starting empty", self._path)

    def _get_tool(self, tool_name: str) -> dict[str, Any]:
        return self.manifest.get("tools", {}).get(tool_name, {})
