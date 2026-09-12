"""`kazma mcp` must not resolve its data directory from the client's CWD.

An MCP client spawns the server as a child process with the working directory
of whatever folder the editor has open. `get_project_root()` walks up from the
CWD looking for `pyproject.toml`, which is right for a CLI the operator runs
inside their own project and wrong for a server somebody else launches.

The consequence, found on a live install (2026-09-12): Zed opened an unrelated
project, `kazma mcp` looked for `kazma-data` beside *that* project, created an
empty one, found no watcher heartbeat in it, and withheld all 55 danger tools —
while the banner reported that no Kazma instance was running. Kazma was running
the whole time. The two processes were looking at different databases.

Worse than a plain miss: if the editor's folder happens to be any other Python
project, the CWD walk *succeeds* and silently anchors Kazma's entire data
directory — memory, checkpoints, vault, gate registry — beside a stranger's
code. The failure is silent in both directions.

Measured before and after on the same unrelated CWD: 100 tools with everything
dangerous withheld, versus 155 tools and 55 `destructiveHint` annotations.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from kazma_core import paths


@pytest.fixture
def clean_root(monkeypatch):
    """Reset the cached project root around each test."""
    monkeypatch.setattr(paths, "_project_root", None)
    monkeypatch.delenv("KAZMA_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("KAZMA_DATA_DIR", raising=False)
    yield
    paths._project_root = None


# ── the resolver ────────────────────────────────────────────────────────────


def test_the_installed_root_is_the_one_holding_this_package(clean_root):
    root = paths.installed_project_root()
    assert root is not None, "an editable checkout must resolve its own root"
    assert (root / "pyproject.toml").exists()
    assert Path(paths.__file__).resolve().is_relative_to(root)


def test_the_installed_root_ignores_the_working_directory(clean_root, tmp_path, monkeypatch):
    """The whole point. A CWD inside another project must not move it."""
    decoy = tmp_path / "someone-elses-project"
    decoy.mkdir()
    (decoy / "pyproject.toml").write_text("[project]\nname='decoy'\n", encoding="utf-8")
    monkeypatch.chdir(decoy)

    assert paths.get_project_root() == decoy, "cwd walk finds the decoy, as designed"
    paths._project_root = None
    assert paths.installed_project_root() != decoy
    assert (paths.installed_project_root() / "kazma-core").exists()


def test_a_site_packages_install_has_no_project_root(clean_root, tmp_path, monkeypatch):
    """A wheel in site-packages has no project above it. Returning the
    site-packages directory itself would put `kazma-data/` inside the Python
    installation, so that case must decline and leave the CWD answer alone."""
    fake = tmp_path / "site-packages" / "kazma_core"
    fake.mkdir(parents=True)
    (fake.parent / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(paths, "__file__", str(fake / "paths.py"))

    assert paths.installed_project_root() is None


def test_pinning_overrides_the_cwd_walk(clean_root, tmp_path, monkeypatch):
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "pyproject.toml").write_text("[project]\nname='decoy'\n", encoding="utf-8")
    monkeypatch.chdir(decoy)

    pinned = paths.pin_project_root(tmp_path / "chosen")
    assert paths.get_project_root() == pinned
    assert paths.get_project_root() != decoy


# ── the server's use of it ──────────────────────────────────────────────────


def test_the_mcp_server_anchors_to_the_install(clean_root, tmp_path, monkeypatch):
    from kazma_core.mcp.server import _anchor_project_root

    decoy = tmp_path / "editor-workspace"
    decoy.mkdir()
    (decoy / "pyproject.toml").write_text("[project]\nname='decoy'\n", encoding="utf-8")
    monkeypatch.chdir(decoy)

    _anchor_project_root()
    root = paths.get_project_root()
    assert root != decoy, "the editor's folder must not become Kazma's data dir"
    assert (root / "kazma-core").exists()


def test_an_explicit_project_root_still_wins(clean_root, tmp_path, monkeypatch):
    """An operator who deliberately relocated the install keeps what they set —
    the fix must not override explicit configuration."""
    from kazma_core.mcp.server import _anchor_project_root

    chosen = tmp_path / "relocated"
    chosen.mkdir()
    monkeypatch.setenv("KAZMA_PROJECT_ROOT", str(chosen))

    _anchor_project_root()
    assert paths.get_project_root() == chosen.resolve()


def test_anchoring_runs_before_anything_resolves_a_path():
    """The root is cached on first use, so anchoring after any path lookup is
    a no-op that looks like a fix. Pin the call order."""
    import inspect

    from kazma_core.mcp import server

    src = inspect.getsource(server.serve_stdio)
    body = src.split("\n")
    called_at = next(i for i, l in enumerate(body) if "_anchor_project_root()" in l)
    first_server = next(
        (i for i, l in enumerate(body) if "MCPServer(" in l or "build_tool_list(" in l),
        len(body),
    )
    assert called_at < first_server, (
        "_anchor_project_root() must run before the server resolves any path"
    )


def test_the_banner_names_the_gate_database():
    """The line that would have made this a five-second diagnosis instead of an
    afternoon: the banner said no instance was watching, without ever saying
    which database it had looked in."""
    import inspect

    from kazma_core.mcp import server

    src = inspect.getsource(server.serve_stdio)
    assert "gate_db_path()" in src
    assert "gate registry" in src
