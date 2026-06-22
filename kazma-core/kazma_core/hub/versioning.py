"""Skill versioning utilities — semver parsing, compatibility, and conflict resolution."""

from __future__ import annotations

from dataclasses import dataclass, field

from kazma_core.hub.manifest_schema import SkillManifest


@dataclass
class ConflictResolution:
    """Result of conflict detection between installed and new skills."""

    has_conflicts: bool = False
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    can_proceed: bool = True


class SkillVersioning:
    """Version parsing and compatibility checking for skill manifests."""

    @staticmethod
    def parse_version(version: str) -> tuple[int, int, int]:
        """Parse 'X.Y.Z' into (X, Y, Z). Raises ValueError on bad input."""
        parts = version.split(".")
        if len(parts) != 3:
            raise ValueError(f"Version must have 3 parts (X.Y.Z), got: {version!r}")
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            raise ValueError(f"Version parts must be integers, got: {version!r}")

    @staticmethod
    def is_compatible(skill_version: str, min_core_version: str) -> bool:
        """Return True if skill_version >= min_core_version."""
        return SkillVersioning.parse_version(skill_version) >= SkillVersioning.parse_version(min_core_version)

    @staticmethod
    def get_latest(versions: list[str]) -> str:
        """Return the highest semver string from a list. Raises ValueError if empty."""
        if not versions:
            raise ValueError("Cannot get latest from empty version list")
        return max(versions, key=lambda v: SkillVersioning.parse_version(v))

    @staticmethod
    def resolve_conflicts(
        installed: list[SkillManifest],
        new: SkillManifest,
    ) -> ConflictResolution:
        """Check if *new* skill conflicts with any *installed* skills.

        Same name = replacement (allowed, with warning).
        Same capabilities = warning.
        """
        conflicts: list[str] = []
        warnings: list[str] = []
        new_name = new.data.get("name", "")
        new_caps = set(new.data.get("capabilities", []))

        for inst in installed:
            inst_name = inst.data.get("name", "")
            inst_caps = set(inst.data.get("capabilities", []))

            if inst_name == new_name:
                warnings.append(f"Replacing installed skill '{inst_name}' with new version")

            overlap = new_caps & inst_caps
            if overlap:
                warnings.append(f"Skill '{inst_name}' shares capabilities: {', '.join(sorted(overlap))}")

        return ConflictResolution(
            has_conflicts=len(conflicts) > 0,
            conflicts=conflicts,
            warnings=warnings,
            can_proceed=not conflicts,
        )
