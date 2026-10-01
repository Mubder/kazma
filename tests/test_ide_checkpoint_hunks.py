"""The IDE's per-hunk reject writes only a file the checkpoint holds (2026-10-01).

``POST /api/ide/checkpoints/{id}/restore-hunk`` took the file path from the
request and wrote it itself: any path the server could write, never checked
against the checkpoint or ``check_path_access``, and in text mode (an LF file
came back CRLF on Windows). The review read files in text mode too, so an
unchanged CRLF file was listed as changed, and a file created after the
checkpoint was never listed. Hunk restore now lives in the checkpoint store
beside ``restore_one``, under the same rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.ide.file_checkpoints import FileCheckpointStore
from kazma_core.workspace.binding import configure_workspace

LF = b"one\ntwo\nthree\n"
CRLF = b"one\r\ntwo\r\nthree\r\n"


@pytest.fixture()
def ide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("KAZMA_WORKSPACE", str(ws))
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    configure_workspace(workspace=str(ws))
    store = FileCheckpointStore(tmp_path / "ck.db")
    monkeypatch.setattr("kazma_core.ide.file_checkpoints.get_file_checkpoint_store", lambda: store)
    from kazma_ui.ide_api import create_ide_router

    app = FastAPI()
    app.include_router(create_ide_router())
    return ws, store, TestClient(app), tmp_path


def _hunk(client: TestClient, cid: str, path: Path, idx: int = 0) -> dict:
    return client.post(
        f"/api/ide/checkpoints/{cid}/restore-hunk", json={"path": str(path), "hunk_index": idx}
    ).json()


@pytest.mark.parametrize("original", [LF, CRLF], ids=["lf", "crlf"])
def test_a_rejected_hunk_keeps_the_files_line_endings(ide, original) -> None:
    ws, store, client, _ = ide
    f = ws / "a.txt"
    f.write_bytes(original)
    cid = store.create([str(f)], reason="t")
    f.write_bytes(original.replace(b"two", b"TWO"))
    assert _hunk(client, cid, f)["ok"] is True
    assert f.read_bytes() == original


def test_a_file_the_checkpoint_does_not_hold_is_refused(ide) -> None:
    ws, store, client, tmp_path = ide
    held = ws / "a.txt"
    held.write_bytes(LF)
    cid = store.create([str(held)], reason="t")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(LF)
    other = ws / "b.txt"
    other.write_bytes(LF)

    for target in (outside, other):
        out = _hunk(client, cid, target)
        assert out["ok"] is False and "not in checkpoint" in out["error"]
        assert target.read_bytes() == LF


def test_the_old_route_wrote_whatever_path_it_was_given(ide) -> None:
    """Negative control: the route's old body, as it was, empties a file the
    checkpoint never held (its 'before' is empty, so every line is a hunk)."""
    from kazma_core.ide.hunks import apply_reverse_hunk, split_hunks
    import difflib

    _ws, _store, _client, tmp_path = ide
    victim = tmp_path / "outside.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    before = ""  # the path was in no checkpoint entry
    after = victim.read_text(encoding="utf-8")
    hunks = split_hunks("\n".join(difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile="a", tofile="b", lineterm="",
    )))
    rewritten = apply_reverse_hunk(after, hunks[0]["header"], hunks[0]["diff"].splitlines()[1:])
    victim.write_text(rewritten, encoding="utf-8")
    assert victim.read_text(encoding="utf-8").strip() == ""


def test_the_review_lists_what_changed_and_only_that(ide) -> None:
    ws, store, client, _ = ide
    same = ws / "same.txt"
    same.write_bytes(CRLF)
    edited = ws / "edited.txt"
    edited.write_bytes(LF)
    created = ws / "created.txt"  # missing when the checkpoint was taken
    cid = store.create([str(same), str(edited), str(created)], reason="t")
    edited.write_bytes(LF.replace(b"two", b"TWO"))
    created.write_bytes(b"new\n")

    files = {Path(f["path"]).name: f for f in client.get(f"/api/ide/checkpoints/{cid}/review").json()["files"]}
    assert files["same.txt"]["changed"] is False  # CRLF, untouched
    assert files["edited.txt"]["changed"] is True and len(files["edited.txt"]["hunks"]) == 1
    assert files["created.txt"]["changed"] is True and files["created.txt"]["after"] == "new\n"


def test_the_old_review_flagged_an_unchanged_crlf_file(tmp_path: Path) -> None:
    """Negative control: a text-mode read of an unchanged CRLF file differs
    from the checkpoint's exact text."""
    f = tmp_path / "same.txt"
    f.write_bytes(CRLF)
    before = CRLF.decode("utf-8")
    assert f.read_text(encoding="utf-8") != before
