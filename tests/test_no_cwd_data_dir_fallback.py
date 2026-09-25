"""When the data dir cannot be resolved, nothing quietly picks another one.

Sixteen ``except`` branches answered a failed ``data_dir()`` with
``Path.cwd() / "kazma-data" / ...`` — the same path the helper computes,
re-derived against wherever the process happened to start. That is not a
fallback; it is a different directory: a second ``settings.db``, a document
store no backup copies, a research-session DB, a workspace sandbox the IDE
used while the chat tools used another, and — in the database client — an
extra root in a path ALLOWLIST, added on the error path.

``tests/test_store_registry.py::test_no_except_branch_rederives_the_data_dir``
is the source gate. These are the behaviour: a data-dir failure now raises,
denies, or lands somewhere that is not a store.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _unwritable(*_a, **_k):
    raise PermissionError(13, "Permission denied", "kazma-data")


def test_the_default_sandbox_does_not_move_to_the_cwd(monkeypatch):
    import kazma_core.paths as paths
    from kazma_core.workspace.binding import default_sandbox_root

    monkeypatch.setattr(paths, "data_dir", _unwritable)
    with pytest.raises(PermissionError):
        default_sandbox_root()


def test_the_default_sandbox_follows_the_data_dir(tmp_path, monkeypatch):
    from kazma_core.workspace.binding import default_sandbox_root

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "dd"))
    assert default_sandbox_root() == (tmp_path / "dd" / "workspace").resolve()


def test_the_document_store_root_does_not_move_to_the_cwd(monkeypatch):
    import kazma_core.paths as paths
    from kazma_core.documents.config import _default_storage_root

    monkeypatch.setattr(paths, "data_dir", _unwritable)
    with pytest.raises(PermissionError):
        _default_storage_root()


def test_research_sessions_do_not_move_to_the_cwd(monkeypatch):
    import kazma_core.paths as paths
    from kazma_core.tools.research_session import _db_path

    monkeypatch.setattr(paths, "data_dir", _unwritable)
    with pytest.raises(PermissionError):
        _db_path()


def test_the_database_allowlist_denies_instead_of_widening(tmp_path, monkeypatch):
    """The old fallback ADDED a CWD-relative root to the allowlist on error."""
    import kazma_core.paths as paths
    from kazma_skills.native.database_client import tools as dbc

    monkeypatch.chdir(tmp_path)
    candidate = tmp_path / "kazma-data" / "other.db"
    candidate.parent.mkdir()
    candidate.write_bytes(b"")
    monkeypatch.setattr(dbc, "_get_workspace", lambda: tmp_path / "ws")
    monkeypatch.setattr(paths, "data_dir", _unwritable)
    assert dbc._is_path_allowed(str(candidate)) is False


def test_an_unusable_active_root_shows_the_canonical_sandbox(tmp_path, monkeypatch):
    import kazma_core.workspace.binding as binding
    from kazma_ui import workspace_api

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "dd"))

    class _Unmountable(type(Path())):
        def mkdir(self, *a, **k):
            raise OSError(19, "No such device")

    monkeypatch.setattr(binding, "resolve_active_root", lambda: _Unmountable(tmp_path / "gone"))
    assert workspace_api._resolve_workspace_root() == (tmp_path / "dd" / "workspace").resolve()
