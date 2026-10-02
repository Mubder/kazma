"""Does this environment hold what this build of Kazma declares? -- the one answer.

A checkout is deployed by ``git pull`` and a reload, and nothing in that
installs the packages a newer ``pyproject.toml`` asks for. When a commit
raises a minimum -- a security floor, a release a new feature needs -- the
install keeps running the old version until someone runs ``kazma update``,
and nothing said so. On 2026-10-02 the floors of 14 packages with known
advisories were raised; the live install still ran every affected version.

:func:`unmet_requirements` reads the requirements this build declares: the
install's own ``pyproject.toml`` for a checkout (the installed ``kazma``
metadata is written at install time and goes stale with every pull -- it
still named a dependency removed two days earlier), else the installed
``kazma`` distribution's metadata. Each requirement is checked against the
installed distribution: a base requirement must be installed at a version
in its range; an extra's requirement binds only a package that is installed
(an extra the install never chose is not missing).

The server checks at boot (:func:`report_unmet_requirements`): a WARNING
naming each package and ``kazma update``, and the ops alert
``install.requirements_unmet``. The security report shows the same list.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

__all__ = ["UnmetRequirement", "report_unmet_requirements", "unmet_requirements"]

logger = logging.getLogger(__name__)

_SELF = "kazma"


@dataclass(frozen=True)
class UnmetRequirement:
    """One requirement this build declares that the environment does not meet."""

    name: str
    required: str
    installed: str | None
    #: "base" and/or the extras that declare it.
    groups: tuple[str, ...]

    def describe(self) -> str:
        have = f"{self.installed} installed" if self.installed else "not installed"
        extras = [g for g in self.groups if g != "base"]
        where = "" if "base" in self.groups or not extras else f" (extra {', '.join(extras)})"
        return f"{self.name} {self.required}{where}: {have}"


def _declared_from_pyproject(path: Path) -> list[tuple[str, str]]:
    project = tomllib.loads(path.read_text(encoding="utf-8")).get("project") or {}
    declared = [(spec, "base") for spec in project.get("dependencies") or ()]
    for extra, specs in (project.get("optional-dependencies") or {}).items():
        declared.extend((spec, str(extra)) for spec in specs or ())
    return declared


def _declared_from_metadata() -> list[tuple[str, str]]:
    declared: list[tuple[str, str]] = []
    for spec in importlib.metadata.requires(_SELF) or ():
        try:
            req = Requirement(spec)
        except InvalidRequirement:
            continue
        extra = ""
        if req.marker is not None and "extra" in str(req.marker):
            for candidate in importlib.metadata.metadata(_SELF).get_all("Provides-Extra") or ():
                if req.marker.evaluate({"extra": candidate}):
                    extra = candidate
                    break
        declared.append((spec, extra or "base"))
    return declared


def _declared_requirements(project_root: Path | None = None) -> list[tuple[str, str]]:
    """``(requirement, group)`` this build declares; group is "base" or an extra."""
    if project_root is None:
        from kazma_core.paths import installed_project_root

        project_root = installed_project_root()
    if project_root is not None and (project_root / "pyproject.toml").is_file():
        return _declared_from_pyproject(project_root / "pyproject.toml")
    return _declared_from_metadata()


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def unmet_requirements(project_root: Path | None = None) -> list[UnmetRequirement]:
    """Every declared requirement the installed packages do not meet, one per package."""
    unmet: dict[str, UnmetRequirement] = {}
    for spec, group in _declared_requirements(project_root):
        try:
            req = Requirement(spec)
        except InvalidRequirement:
            logger.warning("[install] cannot read the declared requirement %r", spec)
            continue
        name = canonicalize_name(req.name)
        if name == _SELF:
            continue  # an extra that names other extras (kazma[document,ocr])
        environment = {"extra": "" if group == "base" else group}
        if req.marker is not None and not req.marker.evaluate(environment):
            continue  # another platform or Python
        installed = _installed_version(name)
        if installed is None:
            if group == "base":
                _add(unmet, UnmetRequirement(name, str(req.specifier), None, (group,)))
            continue
        try:
            ok = req.specifier.contains(Version(installed), prereleases=True)
        except InvalidVersion:
            continue
        if not ok:
            _add(unmet, UnmetRequirement(name, str(req.specifier), installed, (group,)))
    return sorted(unmet.values(), key=lambda u: ("base" not in u.groups, u.name))


def _add(unmet: dict[str, UnmetRequirement], found: UnmetRequirement) -> None:
    """Record *found*, merging the groups of a package already recorded."""
    known = unmet.get(found.name)
    if known is None:
        unmet[found.name] = found
        return
    groups = tuple(dict.fromkeys((*known.groups, *found.groups)))
    required = known.required if known.required == found.required else f"{known.required}, {found.required}"
    unmet[found.name] = UnmetRequirement(found.name, required, known.installed, groups)


def _update_instructions() -> str:
    """How to bring this install's packages up to date, in the owner's words.

    The packages cannot be replaced while the server has them loaded (on
    Windows the reinstall fails half way), and a guarded server killed by
    hand is back within seconds: the guard stops it. The guard hands the
    server its state file's path, so the server knows it is guarded.
    """
    if os.environ.get("KAZMA_GUARD_STATE_FILE"):
        return (
            "To fix, in the install folder: python scripts/service/kazma_guard.py "
            '--pause --stop --when-idle --reason "package update", then '
            "kazma update --reinstall -y, then python scripts/service/kazma_guard.py --resume."
        )
    return (
        "To fix: stop the server, run kazma update --reinstall -y in the install "
        "folder, and start the server again."
    )


def report_unmet_requirements(project_root: Path | None = None) -> list[UnmetRequirement]:
    """Say at boot when the environment is behind this build; returns the list.

    A WARNING with every package, and the ops alert
    ``install.requirements_unmet`` (warn): the owner learns that a deploy
    needs ``kazma update`` from the page, not from a feature that breaks.
    """
    unmet = unmet_requirements(project_root)
    if not unmet:
        return unmet
    detail = "; ".join(u.describe() for u in unmet)
    fix = _update_instructions()
    logger.warning(
        "[install] %d installed package(s) are older than this build requires: %s. %s",
        len(unmet), detail, fix,
    )
    from kazma_core.observability.ops_alerts import alert

    alert(
        "install.requirements_unmet",
        "Installed packages are older than this build of Kazma requires",
        f"{len(unmet)} package(s): {detail}. {fix}",
        severity="warn",
        cooldown_s=12 * 3600,
    )
    return unmet
