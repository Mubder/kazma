"""Path grants + path_policy — external folder access with permission."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("hello-out", encoding="utf-8")
    (ws / "in.txt").write_text("hello-in", encoding="utf-8")

    from kazma_core.workspace import binding

    root = ws.resolve()
    monkeypatch.setattr(binding, "_WORKSPACE_ROOT", root)
    monkeypatch.setattr(binding, "_ALLOW_ABSOLUTE", False)
    # Force the whole resolution ladder onto this temp root.
    monkeypatch.setattr(binding, "resolve_active_root", lambda: root)
    monkeypatch.setattr(
        "kazma_core.workspace.path_policy.resolve_active_root",
        lambda: root,
    )
    return ws, outside


def test_denied_outside_workspace(tmp_workspace):
    from kazma_core.workspace.path_policy import check_path_access, denied_message

    _ws, outside = tmp_workspace
    r = check_path_access(outside / "secret.txt", "read")
    assert r.allowed is False
    assert r.via == "denied"
    msg = denied_message(str(outside / "secret.txt"), "read", result=r)
    assert "request_path_access" in msg


def test_allowed_inside_workspace(tmp_workspace):
    from kazma_core.workspace.path_policy import check_path_access

    ws, _ = tmp_workspace
    r = check_path_access(ws / "in.txt", "read")
    assert r.allowed is True
    assert r.via == "workspace"


def test_session_grant_enables_read(tmp_workspace, monkeypatch):
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_core.workspace.path_grants import grant_session_path
    from kazma_core.workspace.path_policy import check_path_access

    mem = ConfigStore.__new__(ConfigStore)
    # Use a real in-memory store if possible
    import tempfile

    db = Path(tempfile.mkdtemp()) / "cfg.db"
    store = ConfigStore(db)
    set_config_store(store)

    _ws, outside = tmp_workspace
    tid = "thread-test-1"
    grant = grant_session_path(tid, str(outside / "secret.txt"), mode="read", actor="test")
    assert grant.path == str(outside.resolve())

    monkeypatch.setattr(
        "kazma_core.safety.hitl.get_current_thread_id",
        lambda: tid,
    )
    r = check_path_access(outside / "secret.txt", "read")
    assert r.allowed is True
    assert r.via == "session"

    # write still denied with read-only grant
    r_w = check_path_access(outside / "secret.txt", "write")
    assert r_w.allowed is False


def test_durable_root_enables_access(tmp_workspace):
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_core.workspace.path_grants import set_durable_roots
    from kazma_core.workspace.path_policy import check_path_access

    import tempfile

    db = Path(tempfile.mkdtemp()) / "cfg2.db"
    set_config_store(ConfigStore(db))

    _ws, outside = tmp_workspace
    set_durable_roots([{"path": str(outside), "mode": "write", "label": "Out"}])
    r = check_path_access(outside / "secret.txt", "write")
    assert r.allowed is True
    assert r.via == "durable"


@pytest.mark.asyncio
async def test_file_read_respects_grant(tmp_workspace, monkeypatch):
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_core.tools.file_read import file_read
    from kazma_core.workspace.path_grants import grant_session_path

    import tempfile

    db = Path(tempfile.mkdtemp()) / "cfg3.db"
    set_config_store(ConfigStore(db))
    _ws, outside = tmp_workspace
    tid = "thread-read"
    grant_session_path(tid, str(outside), mode="read")
    monkeypatch.setattr(
        "kazma_core.safety.hitl.get_current_thread_id",
        lambda: tid,
    )
    out = await file_read(str(outside / "secret.txt"))
    assert "hello-out" in out
    assert "Safety:" not in out


@pytest.mark.asyncio
async def test_file_read_denied_without_grant(tmp_workspace):
    from kazma_core.tools.file_read import file_read

    _ws, outside = tmp_workspace
    out = await file_read(str(outside / "secret.txt"))
    assert "Safety:" in out
    assert "request_path_access" in out
