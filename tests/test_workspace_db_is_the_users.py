"""A user's own ``*.db`` in the workspace is not a Kazma store (2026-11, FA-217).

``control_plane_store_targeted`` read a relative name in the data dir as well
as where the command runs, and any SQLite file under the data dir is a Kazma
store: ``sqlite3 app.db`` in a workspace holding ``app.db`` was refused as a
control-plane store. A relative name now means the file where the command
runs; the data dir's file counts only when the workspace has no such file.
Kazma's own stores, by name, path or ``cd`` into the data dir, still refuse.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def places(tmp_path, monkeypatch):
    data = tmp_path / "kazma-data"
    workspace = tmp_path / "project"
    data.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    (workspace / "app.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)
    (data / "app.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)
    (data / "hitl_gates.db").write_bytes(b"")
    return data, workspace


def test_the_users_app_db_passes(places):
    from kazma_core.workspace.path_policy import control_plane_store_targeted

    _data, workspace = places
    assert control_plane_store_targeted("app.db", cwd=workspace) is None


def test_negative_control_without_a_file_here_the_data_dirs_counts(places):
    """The rule that refused it: the data dir's ``app.db`` when the
    workspace holds none (a command that ``cd``s into the data dir)."""
    from kazma_core.workspace.path_policy import control_plane_store_targeted

    data, workspace = places
    (workspace / "app.db").unlink()
    hit = control_plane_store_targeted("app.db", cwd=workspace)
    assert hit is not None and hit.endswith("app.db") and str(data.name) in hit


@pytest.mark.parametrize("token", ["hitl_gates.db", "kazma-data/hitl_gates.db"])
def test_kazmas_stores_still_refuse(places, token):
    from kazma_core.workspace.path_policy import control_plane_store_targeted

    data, workspace = places
    cwd = workspace if token == "hitl_gates.db" else data.parent
    assert control_plane_store_targeted(token, cwd=cwd) is not None


def test_an_absolute_store_path_refuses(places):
    from kazma_core.workspace.path_policy import control_plane_store_targeted

    data, workspace = places
    assert control_plane_store_targeted(str(data / "app.db"), cwd=workspace) is not None
