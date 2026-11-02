"""Every advisory OSV knows for an installed package has a floor at its fix.

The 2026-11 functional audit (FA-258): live ran 81 published advisories
across 10 packages; five of those packages had no floor in pyproject.toml,
and pillow's and pypdf's floors sat below their fix. A deploy installs
additively (``kazma update``), so only a floor moves an existing install.

The advisories are a recorded OSV answer for the versions live ran
(``tests/fixtures/osv_advisories.json``, refreshed with
``scripts/record_osv_advisories.py --python <live python> --write``). Each one
with a fixed release must have a floor at or above it wherever pyproject
names the package (and be named at least once), unless it is reviewed
(``dependency_scanner.REVIEWED_ADVISORIES``). The negative control is the
pyproject.toml before the floors.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

from kazma_core.security.dependency_scanner import REVIEWED_ADVISORIES

REPO = Path(__file__).resolve().parents[1]


def _advisories() -> list[dict]:
    record = json.loads((REPO / "tests/fixtures/osv_advisories.json").read_text(encoding="utf-8"))
    return [a for a in record["advisories"] if a["fixed_version"] and a["vuln_id"] not in REVIEWED_ADVISORIES]


def _floors(pyproject: str) -> dict[str, list[Version | None]]:
    """Each package's floors, every place pyproject names it (None: no floor)."""
    out: dict[str, list[Version | None]] = {}
    for spec in re.findall(r'^\s*"([A-Za-z0-9][^"]*)",?', pyproject, flags=re.M):
        try:
            req = Requirement(spec)
        except Exception:  # noqa: BLE001 - a string that is not a requirement
            continue
        name = re.sub(r"[-_.]+", "-", req.name).lower()
        lows = [Version(s.version) for s in req.specifier if s.operator in (">=", "==", "~=")]
        out.setdefault(name, []).append(max(lows) if lows else None)
    return out


def _short_of(pyproject: str) -> list[str]:
    floors = _floors(pyproject)
    problems = []
    for a in _advisories():
        name = re.sub(r"[-_.]+", "-", a["package"]).lower()
        fix = Version(a["fixed_version"])
        named = floors.get(name)
        if not named:
            problems.append(f"{name}: no floor ({a['vuln_id']} fixed in {fix})")
        elif any(low is None or low < fix for low in named):
            problems.append(f"{name}: a floor below {fix} ({a['vuln_id']})")
    return sorted(set(problems))


def test_every_advisory_has_a_floor_at_its_fix():
    problems = _short_of((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert not problems, (
        "pyproject.toml lets an install keep a version OSV has an advisory for. Raise the "
        "floor (in the group that brings the package in) or review it in "
        "dependency_scanner.REVIEWED_ADVISORIES:\n  " + "\n  ".join(problems))


def test_the_recording_names_what_live_ran():
    advisories = json.loads((REPO / "tests/fixtures/osv_advisories.json").read_text(encoding="utf-8"))
    assert advisories["advisories"] and advisories["recorded"]


def test_the_floors_before_the_fix_let_them_through():
    """Negative control: pyproject.toml as it was before these floors."""
    old = subprocess.run(["git", "show", "938b325c:pyproject.toml"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    problems = _short_of(old)
    assert any(p.startswith("pillow: a floor below 13.0.0") for p in problems)
    assert any(p.startswith("msgpack: no floor") for p in problems)
