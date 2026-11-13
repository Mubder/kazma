"""The document worker image's locked packages: what Kazma tests, never below a floor.

``deploy/document-worker/requirements.txt`` (the image's engines) and
``ocr-models.txt`` (its OCR model build stage) are hash locks compiled from
their ``.in`` files. On 2026-11-11 GitHub listed 72 advisories against them:
Pillow 12.3.0 (13.0.0 fixes them), pypdf 6.19.0, brotli 1.2.0, and in the build
stage filelock 3.32.3 and setuptools 83.0.0 -- every one a version Kazma's own
environment had already left behind through pyproject.toml's security floors
(FA-258), while the image kept it. Held here:

* the engines' direct pins are versions ``uv.lock`` holds (the header's rule:
  the image runs what the test suite runs);
* no package either lock holds is below a floor pyproject.toml declares for
  it, or below the fix of an advisory in the recorded OSV answer
  (``tests/fixtures/osv_advisories.json``, reviewed ones aside);
* the engines hold no httpx, Docling or torch (AGENTS.md §19 E).

The negative control is the locks as they were.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from packaging.version import Version

from kazma_core.security.dependency_scanner import REVIEWED_ADVISORIES
from tests.test_dependency_floors import _floors

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "deploy" / "document-worker"
LOCKS = ("requirements.txt", "ocr-models.txt")

_LOCKED = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", re.M)


def _name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked(text: str) -> dict[str, Version]:
    """``name==version`` lines of a lock (a local label such as ``+cpu`` dropped)."""
    return {_name(n): Version(v.split("+", 1)[0]) for n, v in _LOCKED.findall(text)}


def _uv_lock() -> dict[str, set[str]]:
    text = (REPO / "uv.lock").read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for name, version in re.findall(r'\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', text):
        out.setdefault(_name(name), set()).add(version)
    return out


def _required_floors() -> dict[str, Version]:
    """The highest version each package must reach: pyproject.toml's floors,
    and the fixes of the recorded advisories."""
    out: dict[str, Version] = {}
    for name, lows in _floors((REPO / "pyproject.toml").read_text(encoding="utf-8")).items():
        named = [low for low in lows if low is not None]
        if named:
            out[name] = max(named)
    record = json.loads((REPO / "tests" / "fixtures" / "osv_advisories.json").read_text(encoding="utf-8"))
    for advisory in record["advisories"]:
        if advisory["fixed_version"] and advisory["vuln_id"] not in REVIEWED_ADVISORIES:
            name, fix = _name(advisory["package"]), Version(advisory["fixed_version"])
            out[name] = max(out.get(name, fix), fix)
    return out


def _below(locked: dict[str, Version]) -> list[str]:
    floors = _required_floors()
    return sorted(f"{name} {version} < {floors[name]}" for name, version in locked.items()
                  if name in floors and version < floors[name])


def test_the_engines_direct_pins_are_what_uv_lock_holds():
    pins = _LOCKED.findall((WORKER / "requirements.in").read_text(encoding="utf-8"))
    assert pins, "the instrument found no pins"
    lock = _uv_lock()
    off = sorted(f"{name}=={version} (uv.lock: {sorted(lock.get(_name(name), set()))})"
                 for name, version in pins if version not in lock.get(_name(name), set()))
    assert not off, "requirements.in pins a version Kazma's own environment does not run: " + "; ".join(off)


def test_no_locked_package_is_below_a_floor():
    for lock in LOCKS:
        locked = _locked((WORKER / lock).read_text(encoding="utf-8"))
        assert locked, f"{lock}: the instrument found no packages"
        problems = _below(locked)
        assert not problems, f"{lock} holds a version below a security floor: {problems}"


def test_the_engines_hold_no_egress_and_no_salvage_tier():
    locked = _locked((WORKER / "requirements.txt").read_text(encoding="utf-8"))
    assert not [name for name in locked if name in ("httpx", "torch") or name.startswith("docling")]


def test_negative_control_the_locks_as_they_were():
    old_engines = "pillow==12.3.0 \\\npypdf==6.19.0 \\\nbrotli==1.2.0 \\\nweasyprint==70.0 \\\n"
    old_stage = "filelock==3.32.3 \\\nsetuptools==83.0.0 \\\ntorch==2.14.1+cpu \\\n"
    assert _below(_locked(old_engines)) == ["brotli 1.2.0 < 1.2.1", "pillow 12.3.0 < 13.0.0",
                                            "pypdf 6.19.0 < 6.21.4"]
    assert _below(_locked(old_stage)) == ["filelock 3.32.3 < 4.2.1", "setuptools 83.0.0 < 84.2.1"]
    assert "6.1.1" not in _uv_lock()["lxml"], "the old lxml pin is one uv.lock does not hold"
