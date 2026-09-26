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

# No importorskip here. `_unified_turn_harness` needs nothing beyond the
# standard library at import time, and the two helpers used below touch
# neither httpx nor uvicorn — so a skip could only ever hide a real
# failure, which plan §13 forbids in as many words: "Do not treat
# importorskip, xfail, or retries that conceal deterministic failure as
# acceptance."


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Apply exactly what ``unified_turn_server`` applies, and no more."""
    from tests.e2e._unified_turn_harness import _isolate_workspace_store

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_WORKSPACE", str(tmp_path))
    try:
        with _isolate_workspace_store(str(tmp_path)):
            yield tmp_path
    finally:
        import kazma_core.stores.workspaces as ws
        from kazma_core.workspace.binding import configure_workspace

        ws.reset_workspace_store()
        # The store and the PIN are two different things. Every test
        # below calls `resolve_active_root()`, which memoises the active
        # row into `binding._WORKSPACE_ROOT` — so without this, the next
        # test in the session resolves its workspace to a temp directory
        # pytest has already deleted.
        configure_workspace(None)


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
    before_store = ws._workspace_store
    with _isolate_workspace_store(str(tmp_path)):
        swapped = ws._workspace_store
        assert swapped is not None and swapped is not before_store

    assert ws._workspace_store is before_store, (
        "the harness's store outlived the harness"
    )
    assert os.environ.get("KAZMA_WORKSPACE") == before, (
        "_isolate_workspace_store is writing environment variables; the "
        "server context manager owns those and restores them"
    )


def test_the_harness_teardown_clears_the_workspace_pin(tmp_path) -> None:
    """Dropping the store is not enough — the PIN is a separate thing.

    ``resolve_active_root()`` does not only read the ladder, it memoises
    it: rung 2 assigns the active row into ``binding._WORKSPACE_ROOT``.
    So merely ASKING where the workspace is installs a process pin at
    rung 3, and dropping the isolated store afterwards leaves rung 2
    empty with rung 3 still answering — with a temp directory that has
    since been deleted.

    Measured 2026-09-20:
    ``test_ui004_ui008_gateway_misc.py::…::test_file_write_workspace_not_drive_root``
    failed in the full suite and passed alone, which is what that always
    looks like.
    """
    import kazma_core.workspace.binding as binding
    from tests.e2e._unified_turn_harness import (
        _isolate_workspace_store,
        _reset_process_singletons,
    )

    with _isolate_workspace_store(str(tmp_path)):
        pinned = binding.resolve_active_root()
        assert Path(pinned).resolve() == tmp_path.resolve(), (
            "the isolation helper did not take effect, so this test is not "
            "exercising the leak it names"
        )
        assert binding._WORKSPACE_ROOT is not None, (
            "resolve_active_root no longer memoises; if that is deliberate, "
            "this test and the teardown it guards can both go"
        )

        # The harness resets inside the isolation, as unified_turn_server does.
        _reset_process_singletons()

    assert binding._WORKSPACE_ROOT is None, (
        "the harness teardown leaves a workspace pin behind; every test "
        "after it in the session resolves to a deleted temp directory"
    )
