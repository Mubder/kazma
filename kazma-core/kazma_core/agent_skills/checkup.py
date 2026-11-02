"""Check one skill the way Kazma loads it: the Skills page's Validate.

The 2026-11 functional audit (FA-249): the Validate tab ran the retired hub
validator (``skill_manifest.yaml``) on a path resolved against the server's
working directory, so every skill the install had failed or was refused, and
a folder that did not exist scored 70/100. A skill is now found where
discovery finds it (``discovery.skill_base_dirs``, by name or by folder), and
judged by what activation applies: the ``SKILL.md`` parse and manifest rules
(``parser``) and the integrity check (``catalog.skill_integrity``). A native
skill is judged by its loader (``NativeSkillLoader.resolve_skill``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["SkillNotFound", "check_skill"]


class SkillNotFound(LookupError):
    """No skill by that name, and no skill folder at that path inside Kazma's
    skill folders."""


def _inside(folder: Path, bases: list[Path]) -> bool:
    for base in bases:
        try:
            folder.relative_to(base)
            return True
        except ValueError:
            continue
    return False


def _native(ref: str) -> dict[str, Any] | None:
    """A native skill named by ``native:<folder>``, its folder name or path."""
    try:
        from kazma_skills.native_loader import NativeSkillLoader
    except ImportError:  # kazma-skills not installed: no native skills
        return None
    loader = NativeSkillLoader(registry=None)
    wanted = ref.removeprefix("native:").strip()
    for folder in loader.skill_dirs():
        if wanted in (folder.name, str(folder)) or Path(wanted).expanduser().resolve() == folder.resolve():
            errors: list[str] = []
            try:
                name, _found, missing = loader.resolve_skill(folder)
            except Exception as exc:  # the tools module can raise anything on import; so does the loader
                name = folder.name
                errors.append(f"{type(exc).__name__}: {exc}")
            else:
                errors.extend(f"tool {t!r} has no function in its tools module" for t in missing)
            return {"name": name, "kind": "native", "scope": "built_in", "location": str(folder),
                    "passed": not errors, "state": "built_in", "errors": errors, "warnings": []}
    return None


def check_skill(ref: str, *, project_root: Path | None = None) -> dict[str, Any]:
    """Check the skill *ref* names: a skill's name, ``native:<folder>``, or a
    folder inside one of Kazma's skill folders (a relative one is read against
    each of them, never the working directory). Raises :class:`SkillNotFound`
    when there is none, ValueError for a folder outside the skill folders."""
    from kazma_core.agent_skills.catalog import skill_integrity
    from kazma_core.agent_skills.discovery import discover_skills, skill_base_dirs
    from kazma_core.agent_skills.parser import parse_skill_md, validate_manifest

    ref = (ref or "").strip()
    if not ref:
        raise SkillNotFound("no skill named")
    native = _native(ref)
    if native is not None:
        return native

    skills = discover_skills(project_root=project_root, include_disabled=True)
    skill = skills.get(ref)
    bases = [b.resolve() for _scope, b in skill_base_dirs(project_root=project_root)]
    if skill is None:
        raw = Path(ref).expanduser()
        candidates = [raw.resolve()] if raw.is_absolute() else [(b / raw).resolve() for b in bases]
        folder = next((c for c in candidates if c.is_dir()), None)
        if folder is None:
            if raw.is_absolute() and not _inside(raw.resolve(), bases):
                raise ValueError("outside_skill_folders")
            raise SkillNotFound(ref)
        if not _inside(folder, bases):
            raise ValueError("outside_skill_folders")
        skill = next((s for s in skills.values() if s.location.parent.resolve() == folder), None)
        if skill is None:
            md = folder / "SKILL.md"
            errors = ["no SKILL.md in this folder"] if not md.is_file() else []
            if not errors:
                parsed = parse_skill_md(md.read_text(encoding="utf-8"), path=md, directory_name=folder.name)
                if parsed is None:
                    errors.append("SKILL.md could not be read: it needs YAML front matter with a "
                                  "description, and a name that is not a path")
            return {"name": folder.name, "kind": "agent_skill", "scope": "", "location": str(folder),
                    "passed": False, "state": "unusable", "errors": errors, "warnings": []}
    state, reason = skill_integrity(skill)
    errors = validate_manifest({**skill.parsed.raw_frontmatter, "name": skill.name},
                               source=str(skill.location))
    if state == "refused":
        errors.append(reason)
    warnings = list(skill.warnings) + ([reason] if state == "unsigned" and reason else [])
    return {"name": skill.name, "kind": "agent_skill", "scope": skill.scope,
            "location": str(skill.location.parent), "passed": not errors, "state": state,
            "errors": errors, "warnings": warnings}
