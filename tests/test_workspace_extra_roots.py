"""Folders outside the workspace: listed, added and removed on the Workspace
page, and refused when a grant there would be the wrong one (2026-09-28).

``GET/PUT /api/workspace/extra-roots`` existed and the docs said "Settings /
API", but no page called them: the owner could not see which folders the
agent may use without asking, nor take one away. The routes were ``async``
over settings-store I/O, a failed read answered "no folders", and any path
was stored as given -- a relative one resolved against the server's working
directory, a whole drive granted the whole drive.

A new root must be a full path to an existing folder that is neither a whole
drive nor one holding Kazma's own install or data (its keys in ``.env``,
its stores). A root already kept passes as it is: removing one must not fail
because another sits on an unplugged drive.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_what_a_new_root_must_be(tmp_path: Path) -> None:
    from kazma_core.paths import data_dir, installed_project_root
    from kazma_core.workspace.path_grants import _extra_root_problem

    good = tmp_path / "notes"
    good.mkdir()
    assert _extra_root_problem(str(good)) is None
    assert "full path" in _extra_root_problem("notes")
    # The path as the owner typed it (a repr showed notes\\sub on the page).
    assert r"“notes\sub”" in _extra_root_problem(r"notes\sub")
    assert "not a folder" in _extra_root_problem(str(tmp_path / "missing"))
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    assert "not a folder" in _extra_root_problem(str(tmp_path / "file.txt"))
    assert "whole drive" in _extra_root_problem(Path(tmp_path).anchor)
    assert "Kazma's own files" in _extra_root_problem(str(data_dir().parent))
    install = installed_project_root()
    assert install is not None
    assert "Kazma's own files" in _extra_root_problem(str(install))
    # A folder INSIDE the install holds none of it as a whole: allowed.
    assert _extra_root_problem(str(install / "docs")) is None


def test_a_refused_root_writes_nothing_and_a_kept_one_stays(tmp_path: Path) -> None:
    from kazma_core.workspace.path_grants import list_durable_roots, set_durable_roots

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    set_durable_roots([{"path": str(a), "mode": "read"}, {"path": str(b), "mode": "write"}])
    assert {g.path for g in list_durable_roots()} == {str(a.resolve()), str(b.resolve())}

    with pytest.raises(ValueError, match="whole drive"):
        set_durable_roots([{"path": str(a)}, {"path": Path(tmp_path).anchor}])
    assert len(list_durable_roots()) == 2, "nothing written"

    # b's drive is gone today; removing a still works.
    b.rmdir()
    kept = set_durable_roots([{"path": str(b.resolve()), "mode": "write"}])
    assert [g.path for g in kept] == [str(b.resolve())]


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui.workspace_api import create_workspace_router

    app = FastAPI()
    app.include_router(create_workspace_router())
    return TestClient(app)


def test_the_routes(tmp_path: Path) -> None:
    folder = tmp_path / "shared"
    folder.mkdir()
    c = _client()
    assert c.get("/api/workspace/extra-roots").json() == {"ok": True, "extra_roots": []}
    r = c.put("/api/workspace/extra-roots", json={"extra_roots": [{"path": "relative/dir"}]})
    assert r.status_code == 400 and "full path" in r.json()["error"]
    r = c.put("/api/workspace/extra-roots",
              json={"extra_roots": [{"path": str(folder), "mode": "read", "label": "Shared"}]})
    assert r.status_code == 200
    roots = r.json()["extra_roots"]
    assert [(x["path"], x["mode"], x["label"]) for x in roots] == [(str(folder.resolve()), "read", "Shared")]
    assert c.get("/api/workspace/extra-roots").json()["extra_roots"] == roots


def test_a_failed_read_is_not_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.workspace import path_grants

    def broken():
        raise RuntimeError("settings store down")

    monkeypatch.setattr(path_grants, "list_durable_roots", broken)
    r = _client().get("/api/workspace/extra-roots")
    assert r.status_code == 500 and r.json()["ok"] is False


def test_the_routes_run_off_the_loop() -> None:
    from kazma_ui import workspace_api

    src = inspect.getsource(workspace_api.create_workspace_router)
    for name in ("get_extra_roots", "put_extra_roots"):
        assert f"    def {name}(" in src and f"async def {name}(" not in src, name


def test_the_page_offers_the_list_and_both_changes() -> None:
    html = (REPO / "kazma-ui/kazma_ui/templates/workspace.html").read_text(encoding="utf-8")
    card = html[html.index("{{ t('workspace.extra_roots_title') }}"):]
    card = card[: card.index("<!-- Recent Activity")]
    assert '<template x-for="r in extraRoots" :key="r.path">' in card
    assert '@click="removeExtraRoot(r)"' in card and '@click="addExtraRoot()"' in card
    assert "extraRootsLoaded && extraRoots.length === 0" in card, "empty state only once loaded"
    script = html[html.index("async _saveExtraRoots("):]
    script = script[: script.index("\n    },")]
    assert "'/api/workspace/extra-roots'" in script and "method: 'PUT'" in script
