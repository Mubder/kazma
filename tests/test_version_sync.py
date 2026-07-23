"""Assert version strings stay in sync across the monorepo.

Canonical source of truth is ``version`` in the root ``pyproject.toml``.
The following must track it:
  * ``agent.version`` in ``kazma.yaml``
  * ``version`` in ``kazma-gateway/pyproject.toml``
  * ``__version__`` in ``kazma-tui/kazma_tui/__init__.py``

The CLI (``kazma_cli.banner._get_version``) already resolves dynamically
from the root pyproject, so it is not asserted here.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_version_from_pyproject(path: Path) -> str:
    """Extract the top-level ``version = "x.y.z"`` from a pyproject.toml."""
    text = path.read_text(encoding="utf-8")
    # First `version = "..."` at column 0 (project table), not nested tables.
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise AssertionError(f"No top-level version found in {path}")
    return m.group(1)


def _read_version_from_yaml(path: Path) -> str:
    """Extract ``agent.version`` from kazma.yaml."""
    text = path.read_text(encoding="utf-8")
    # The version lives under the `agent:` table near the top.
    m = re.search(r'^\s*version:\s*([^\s#]+)', text, re.MULTILINE)
    if not m:
        raise AssertionError(f"No version found in {path}")
    return m.group(1).strip().strip('"').strip("'")


def test_root_pyproject_is_canonical() -> None:
    """The root pyproject declares a version (the source of truth)."""
    v = _read_version_from_pyproject(REPO_ROOT / "pyproject.toml")
    assert re.fullmatch(r"\d+\.\d+\.\d+", v), f"Canonical version malformed: {v}"


def test_kazma_yaml_matches_root() -> None:
    canonical = _read_version_from_pyproject(REPO_ROOT / "pyproject.toml")
    kazma_yaml = _read_version_from_yaml(REPO_ROOT / "kazma.yaml")
    assert kazma_yaml == canonical, (
        f"kazma.yaml version ({kazma_yaml}) != root pyproject ({canonical}). "
        "Update kazma.yaml `agent.version` to match."
    )


def test_gateway_pyproject_matches_root() -> None:
    canonical = _read_version_from_pyproject(REPO_ROOT / "pyproject.toml")
    gw = _read_version_from_pyproject(REPO_ROOT / "kazma-gateway" / "pyproject.toml")
    assert gw == canonical, (
        f"kazma-gateway version ({gw}) != root pyproject ({canonical}). "
        "Update kazma-gateway/pyproject.toml to match."
    )


def test_tui_version_matches_root() -> None:
    canonical = _read_version_from_pyproject(REPO_ROOT / "pyproject.toml")
    init_text = (REPO_ROOT / "kazma-tui" / "kazma_tui" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    assert m, "kazma-tui __init__.py has no __version__"
    tui_v = m.group(1)
    assert tui_v == canonical, (
        f"kazma-tui __version__ ({tui_v}) != root pyproject ({canonical}). "
        "Update kazma-tui/kazma_tui/__init__.py to match."
    )
