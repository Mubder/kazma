"""Hands 0.11 WP4 — workspace file checkpoints."""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.ide.file_checkpoints import FileCheckpointStore
from kazma_core.workspace.binding import configure_workspace


def _pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    configure_workspace(workspace=str(tmp_path))


def test_checkpoint_restore_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_FILE_CHECKPOINTS_DB", str(tmp_path / "ck.db"))
    _pin(tmp_path, monkeypatch)
    target = tmp_path / "a.py"
    target.write_text("n = 1\n", encoding="utf-8")
    store = FileCheckpointStore(tmp_path / "ck.db")
    cid = store.create([str(target)], reason="test")
    target.write_text("n = 2\n", encoding="utf-8")
    store.restore(cid)
    assert target.read_text(encoding="utf-8").strip() == "n = 1"


def test_checkpoint_skips_outside_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_FILE_CHECKPOINTS_DB", str(tmp_path / "ck.db"))
    ws = tmp_path / "ws"
    ws.mkdir()
    _pin(ws, monkeypatch)
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    store = FileCheckpointStore(tmp_path / "ck.db")
    with pytest.raises(ValueError):
        store.create([str(outside)], reason="escape")


def test_store_uses_sqlite_pragmas() -> None:
    import inspect

    from kazma_core.ide import file_checkpoints as mod

    src = inspect.getsource(mod.FileCheckpointStore._connect)
    assert "apply_sqlite_pragmas" in src
