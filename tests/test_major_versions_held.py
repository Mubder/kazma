"""A dependency's next major version is held back until it is tested (2026-11-09).

`kazma update` installs the newest version its requirements allow and does not
read uv.lock, and CI's pip installs do not either. So a floor with no ceiling
installs a major version nobody tested the day it is published:

* pypdf 7 reached the drill VM's update while the lock said 6.21.4
  (2026-11-07; held below 7 since).
* PyMuPDF 2 reached CI's first full run in four weeks (run 42612068022,
  2026-11-09): its ``find_tables`` runs an ONNX layout model, the parser
  worker crashed in it (SIGSEGV unsandboxed, killed or timed out in the
  sandbox), and every PDF with a table failed to parse -- Kazma's own Arabic
  PDFs read back reversed, the fidelity corpus fell to nothing, the hostile
  corpus certification failed. The lock held 1.28.2, so no developer machine
  saw it.

Every requirement on a held package in pyproject.toml, and every package a
workflow installs by name, must exclude the held major version. Negative
control: the old requirement and the old workflow line admit it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

REPO = Path(__file__).resolve().parents[1]

#: package -> (first major version not yet tested, why it is held)
HELD: dict[str, tuple[str, str]] = {
    "pypdf": ("7", "untested; `kazma update` installed it on the drill VM (2026-11-07)"),
    "pymupdf": ("2", "its find_tables runs an ONNX layout model that crashed the parser worker (2026-11-09)"),
}


def _admits_held(spec: str, held: str) -> bool:
    req = Requirement(spec)
    return any(req.specifier.contains(Version(v), prereleases=False) for v in (f"{held}.0", f"{held}.1", f"{held}.99"))


def _pyproject_requirements() -> list[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    found = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        found.extend(extra)
    return found


_PIP_LINE = re.compile(r"pip install[^\n]*")
_TOKEN = re.compile(r'"[^"]+"|\'[^\']+\'|\S+')


def _workflow_installs() -> list[tuple[str, str]]:
    """(workflow, requirement) for every package a `pip install` line names."""
    out = []
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        for line in _PIP_LINE.findall(path.read_text(encoding="utf-8")):
            for token in _TOKEN.findall(line)[2:]:
                token = token.strip("\"'")
                if token.startswith("-") or token.startswith("."):
                    continue
                out.append((path.name, token))
    return out


def _violations(requirements: list[tuple[str, str]]) -> list[str]:
    bad = []
    for where, spec in requirements:
        try:
            name = Requirement(spec).name.lower()
        except Exception:  # noqa: BLE001 - not a requirement (a path, a flag)
            continue
        if name in HELD and _admits_held(spec, HELD[name][0]):
            bad.append(f"{where}: {spec!r} admits {name} {HELD[name][0]} ({HELD[name][1]})")
    return bad


def test_pyproject_holds_each_major_version_back():
    bad = _violations([("pyproject.toml", spec) for spec in _pyproject_requirements()])
    assert not bad, "\n".join(bad)


def test_every_workflow_install_holds_it_back_too():
    installs = _workflow_installs()
    assert any(spec.startswith("pymupdf") for _, spec in installs), "the workflows install PyMuPDF by name"
    bad = _violations(installs)
    assert not bad, "\n".join(bad)


def test_each_held_package_is_required_somewhere():
    names = {Requirement(spec).name.lower() for spec in _pyproject_requirements()}
    assert set(HELD) <= names, f"a held package is no longer a requirement: {set(HELD) - names}"


def test_the_old_requirement_and_the_old_workflow_line_admit_it():
    # Negative control (AGENTS §28): what pyproject and ci.yml said before.
    assert _violations([("pyproject.toml", "pymupdf>=1.24.0")])
    old_line = 'run: pip install -e ".[test]" pillow pymupdf sqlite-vec pypdfium2 numpy prometheus-client'
    tokens = [t.strip("\"'") for t in _TOKEN.findall(_PIP_LINE.findall(old_line)[0])[2:]]
    assert _violations([("ci.yml", t) for t in tokens if not t.startswith((".", "-"))])
