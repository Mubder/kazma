"""Kazma Hub — dynamic skill loader for installed skills."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

# ─── Custom Exceptions ────────────────────────────────────────────────────


class SkillError(Exception):
    """Base exception for skill operations."""


class SkillNotFoundError(SkillError):
    """Skill directory or manifest not found."""


class SkillLoadError(SkillError):
    """Failed to load or instantiate skill."""


# ─── Skill Loader ─────────────────────────────────────────────────────────


class SkillLoader:
    """Dynamically load and manage installed Kazma skills."""

    def __init__(self, skills_dir: str = "~/.kazma/skills") -> None:
        self.skills_dir = Path(skills_dir).expanduser()

    # ------------------------------------------------------------------
    # Core loading
    # ------------------------------------------------------------------

    async def load_skill(self, skill_name: str) -> Any:
        """Load a skill by name.

        1. Check skill directory exists
        2. Read and validate manifest
        3. Parse entry_point (dotted module path with optional :ClassName)
        4. Import the module, instantiate class if specified
        5. Return the instance or module
        """
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.is_dir():
            raise SkillNotFoundError(f"Skill directory not found: {skill_dir}")

        manifest_path = skill_dir / "skill_manifest.yaml"
        if not manifest_path.exists():
            raise SkillNotFoundError(f"Manifest not found for skill: {skill_name}")

        # Load and validate manifest
        try:
            with open(manifest_path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise SkillLoadError(f"Invalid YAML in manifest for {skill_name}: {exc}") from exc

        if not isinstance(data, dict):
            raise SkillLoadError(f"Invalid manifest format for skill: {skill_name}")

        # Validate required fields
        required = ("name", "version", "description", "author", "license")
        for field_name in required:
            if field_name not in data:
                raise SkillLoadError(f"Manifest missing required field '{field_name}' for skill: {skill_name}")

        entry_point = data.get("entry_point")
        if not entry_point:
            # No entry point — import the directory as a package-like module
            return await self._import_skill_module(skill_dir, skill_name)

        return await self._import_entry_point(skill_dir, entry_point, skill_name)

    async def load_all(self) -> dict[str, Any]:
        """Scan skills directory and load every valid skill.

        Returns ``{name: instance_or_module}`` for each skill that has a
        valid ``skill_manifest.yaml``.
        """
        if not self.skills_dir.is_dir():
            return {}

        loaded: dict[str, Any] = {}
        for child in sorted(self.skills_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = child / "skill_manifest.yaml"
            if not manifest_path.exists():
                continue
            try:
                instance = await self.load_skill(child.name)
                loaded[child.name] = instance
            except (SkillLoadError, SkillNotFoundError):
                # Skip skills that fail to load
                continue
        return loaded

    async def reload(self, skill_name: str) -> Any:
        """Hot-reload a skill: evict cached modules, then re-import."""
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.is_dir():
            raise SkillNotFoundError(f"Skill directory not found: {skill_dir}")

        # Evict all cached modules for this skill
        prefix = f"_kazma_skill_{skill_name}"
        to_remove = [key for key in sys.modules if key == prefix or key.startswith(f"{prefix}_")]
        for key in to_remove:
            del sys.modules[key]

        return await self.load_skill(skill_name)

    async def list_available(self) -> list[str]:
        """Return names of skill directories that contain a valid manifest."""
        if not self.skills_dir.is_dir():
            return []

        available: list[str] = []
        for child in sorted(self.skills_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = child / "skill_manifest.yaml"
            if not manifest_path.exists():
                continue
            # Quick validation: check YAML is parseable and has required fields
            try:
                with open(manifest_path) as f:
                    data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    continue
                required = ("name", "version", "description", "author", "license")
                if all(k in data for k in required):
                    available.append(child.name)
            except (yaml.YAMLError, OSError):
                continue
        return available

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _import_entry_point(self, skill_dir: Path, entry_point: str, skill_name: str) -> Any:
        """Import a skill via its entry_point spec (e.g. 'my_skill.main:MySkill').

        The entry point format is ``module_path:ClassName``.  If no colon
        is present the module is imported but not instantiated.
        """
        if ":" in entry_point:
            module_path, class_name = entry_point.rsplit(":", 1)
        else:
            module_path = entry_point
            class_name = None

        # Convert dotted path to file path: 'my_skill.main' -> skill_dir / 'my_skill' / 'main.py'
        file_stem = module_path.replace(".", "/")
        module_file = skill_dir / f"{file_stem}.py"
        if not module_file.exists():
            raise SkillLoadError(f"entry_point file not found: {module_file} for skill {skill_name}")

        # Unique module name to avoid cache collisions across skills
        unique_name = f"_kazma_skill_{skill_name}_{module_path.replace('.', '_')}"
        return await self._load_module_from_file(module_file, unique_name, skill_name, class_name)

    async def _import_skill_module(self, skill_dir: Path, skill_name: str) -> Any:
        """Import the skill directory as a module when no entry_point is set."""
        main_py = skill_dir / "main.py"
        if not main_py.exists():
            raise SkillLoadError(f"No entry_point and no main.py found for skill: {skill_name}")

        unique_name = f"_kazma_skill_{skill_name}_main"
        return await self._load_module_from_file(main_py, unique_name, skill_name, class_name=None)

    async def _load_module_from_file(
        self,
        file_path: Path,
        unique_name: str,
        skill_name: str,
        class_name: str | None,
    ) -> Any:
        """Load a Python module from a file path using importlib.util.

        This avoids collisions with common module names like 'main' and
        allows proper hot-reloading.
        """
        # Remove any cached version
        if unique_name in sys.modules:
            del sys.modules[unique_name]

        spec = importlib.util.spec_from_file_location(unique_name, str(file_path))
        if spec is None or spec.loader is None:
            raise SkillLoadError(f"Cannot create module spec for {file_path} (skill: {skill_name})")

        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module

        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            # Clean up on failure
            sys.modules.pop(unique_name, None)
            raise SkillLoadError(f"Failed to execute module {file_path.name} for skill {skill_name}: {exc}") from exc

        if class_name is not None:
            cls = getattr(module, class_name, None)
            if cls is None:
                raise SkillLoadError(
                    f"Class {class_name!r} not found in module {file_path.name!r} for skill {skill_name}"
                )
            try:
                return cls()
            except Exception as exc:
                raise SkillLoadError(f"Failed to instantiate {class_name!r} for skill {skill_name}: {exc}") from exc

        return module
