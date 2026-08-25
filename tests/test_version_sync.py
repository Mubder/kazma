"""Assert version bases stay in sync across the monorepo.

Canonical source of truth is the **public base** in root ``pyproject.toml``
(e.g. ``0.10.0`` — no ``+gSHA`` in committed files).

Display version is always ``{base}+g{shortsha}`` at runtime via
``kazma_core.version.get_version()`` — that is not stored in these files.

Tracked sites for the public base:
  * ``agent.version`` in ``kazma.yaml``
  * ``version`` in ``kazma-gateway/pyproject.toml``

TUI / CLI resolve display version dynamically (may include ``+g…``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_BASE_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _public_base(version: str) -> str:
    return version.split("+", 1)[0].strip()


def _read_version_from_pyproject(path: Path) -> str:
    """Extract the top-level ``version = "x.y.z"`` from a pyproject.toml."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise AssertionError(f"No top-level version found in {path}")
    return m.group(1)


def _read_version_from_yaml(path: Path) -> str:
    """Extract ``agent.version`` from kazma.yaml."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^\s*version:\s*([^\s#]+)", text, re.MULTILINE)
    if not m:
        raise AssertionError(f"No version found in {path}")
    return m.group(1).strip().strip('"').strip("'")


def test_root_pyproject_is_public_base() -> None:
    """Committed version is public SemVer only (no +local in files)."""
    v = _read_version_from_pyproject(REPO_ROOT / "pyproject.toml")
    assert _BASE_RE.fullmatch(v), (
        f"Canonical version must be public SemVer without +gSHA, got: {v!r}"
    )


def test_kazma_yaml_matches_root() -> None:
    canonical = _read_version_from_pyproject(REPO_ROOT / "pyproject.toml")
    kazma_yaml = _read_version_from_yaml(REPO_ROOT / "kazma.yaml")
    assert _public_base(kazma_yaml) == canonical, (
        f"kazma.yaml version ({kazma_yaml}) != root pyproject ({canonical}). "
        "Update kazma.yaml `agent.version` to match the public base."
    )


def test_gateway_pyproject_matches_root() -> None:
    canonical = _read_version_from_pyproject(REPO_ROOT / "pyproject.toml")
    gw = _read_version_from_pyproject(REPO_ROOT / "kazma-gateway" / "pyproject.toml")
    assert _public_base(gw) == canonical, (
        f"kazma-gateway version ({gw}) != root pyproject ({canonical}). "
        "Update kazma-gateway/pyproject.toml to match."
    )


def test_display_version_embeds_git_sha() -> None:
    """Runtime display is base+gSHORTSHA when git is available."""
    from kazma_core.version import clear_version_cache, get_base_version, get_version

    clear_version_cache()
    base = get_base_version()
    full = get_version()
    assert base == _read_version_from_pyproject(REPO_ROOT / "pyproject.toml")
    assert full == base or full.startswith(f"{base}+g")
    if "+g" in full:
        local = full.split("+g", 1)[1]
        assert re.fullmatch(r"[0-9a-f]+", local), f"bad local segment: {full!r}"
        assert 4 <= len(local) <= 40


def test_tui_version_public_base_matches_root() -> None:
    canonical = _read_version_from_pyproject(REPO_ROOT / "pyproject.toml")
    # Import may resolve dynamic display version
    import kazma_tui

    tui_v = getattr(kazma_tui, "__version__", "")
    assert _public_base(tui_v) == canonical, (
        f"kazma-tui __version__ public base ({tui_v}) != root ({canonical})."
    )
