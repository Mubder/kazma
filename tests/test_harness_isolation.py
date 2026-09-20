"""The unified-turn harness must not borrow the operator's repository.

``docs/plans/UNIFIED_TURN_BLOCK.md`` §14.1:

    Build and test with an isolated data directory. Never use the
    operator's active approvals as test fixtures.

An isolated *database* is not an isolated *workspace*, and the harness
had the second one wrong for as long as it existed. Measured 2026-09-20
in a process whose ``KAZMA_DATA_DIR`` and ``KAZMA_WORKSPACE`` both
pointed at a fresh temp directory::

    resolve_active_root() -> G:\\GitHubRepos\\kazma
    check_path_access(<tmp>/README.md, "write")
        -> allowed=False, reason='outside workspace; no grant',
           workspace='G:\\GitHubRepos\\kazma'

Two causes compounding. ``stores/workspaces.py`` computes its default
database path at IMPORT time, so the singleton is aimed at the real
``kazma-data/workspaces.db`` before any fixture runs; and a store with
no rows does not stay quiet — it registers and activates the current
working directory. That active row is rung 2 of the ladder and outranks
the ``KAZMA_WORKSPACE`` at rung 4.

Every approved ``file_write`` in the harness was therefore refused, and
silently: ``kazma_core/tools/file_write.py`` reports a refusal by
RETURNING ``"Error: …"`` rather than raising, so the worker logs
``error=False`` and the turn continues having written nothing.

Nothing reached the repository, but only by geometry — the fixture's
paths are absolute and point into the temp directory, so they were
refused for being *outside* the workspace. A fixture written the obvious
way, with a relative ``"README.md"``, would have had an approved
``file_write`` overwrite the repo's own README.

``tests/e2e/test_unified_turn_app_graph.py::test_approved_tools_actually_execute``
is the end-to-end proof and needs a real graph and about a minute. This
is the fast lock on the safety property itself.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("httpx")
pytest.importorskip("uvicorn")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Apply exactly what ``unified_turn_server`` applies, and no more."""
    from tests.e2e._unified_turn_harness import _isolate_workspace_store

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_WORKSPACE", str(tmp_path))
    _isolate_workspace_store(str(tmp_path))
    try:
        yield tmp_path
    finally:
        import kazma_core.stores.workspaces as ws

        ws.reset_workspace_store()


def test_the_active_workspace_is_the_isolated_directory(isolated) -> None:
    """Rung 2 of the ladder must name the harness's tree, not the repo."""
    from kazma_core.workspace.binding import resolve_active_root

    root = Path(resolve_active_root()).resolve()
    assert root == Path(isolated).resolve(), (
        f"the harness's agent would run against {root}, not its own "
        f"isolated directory {isolated}"
    )


def test_the_repository_is_not_the_agents_workspace(isolated) -> None:
    """Stated the other way round, because this is the one that matters.

    A relative path in a fixture resolves against the workspace. If the
    workspace is the checkout, an approved ``file_write`` edits the
    checkout.
    """
    from kazma_core.workspace.binding import resolve_active_root

    repo = Path(__file__).resolve().parent.parent
    root = Path(resolve_active_root()).resolve()
    assert root != repo, (
        "the harness's agent has the repository itself as its workspace; "
        "an approved file_write on a relative path would edit the checkout"
    )
    assert repo not in root.parents and root != repo


def test_a_scripted_path_is_actually_writable(isolated) -> None:
    """The refusal was silent, so assert the permission, not the log.

    ``file_write`` returns "Error: …" on denial instead of raising, which
    is why a whole suite of approved danger tools wrote nothing while
    reporting ``error=False``. ``check_path_access`` is where the truth
    was all along.
    """
    from kazma_core.workspace.path_policy import check_path_access

    target = Path(isolated) / "README.md"
    access = check_path_access(target, "write")
    assert getattr(access, "allowed", False), (
        f"the harness cannot write its own fixture path: {access}"
    )


def test_the_store_singleton_is_restored_afterwards(tmp_path) -> None:
    """Isolation that leaks is a different bug wearing the same clothes.

    The harness swaps a process singleton that everything else in the
    test session shares. If the swap outlived the fixture, the next
    suite would be reading a temp directory that no longer exists.
    """
    import kazma_core.stores.workspaces as ws
    from tests.e2e._unified_turn_harness import _isolate_workspace_store

    before = os.environ.get("KAZMA_WORKSPACE")
    _isolate_workspace_store(str(tmp_path))
    swapped = ws._workspace_store
    assert swapped is not None

    ws.reset_workspace_store()
    assert ws._workspace_store is None, (
        "reset_workspace_store left the harness's store installed"
    )
    assert os.environ.get("KAZMA_WORKSPACE") == before, (
        "_isolate_workspace_store is writing environment variables; the "
        "server context manager owns those and restores them"
    )
