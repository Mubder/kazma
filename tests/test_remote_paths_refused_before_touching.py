"""A network or device path is refused on its text, before anything touches it.

On Windows, ``Path.resolve()``, ``os.path.realpath``, ``exists`` and ``stat``
of ``\\\\attacker\\share\\x`` (or ``//attacker/share/x``) open an SMB
connection to that host as the service account: its NetNTLM hash leaks, and
the path carries data out -- BEFORE the path policy says "outside the
workspace" (2026-11-10 audit). ``file_read`` is read tier (no approval card);
``GET /api/workspace/suggest`` and ``GET /api/research/download`` can be
reached by a cross-site navigation.

``workspace.remote_paths.remote_path_reason`` is the one check, on the text.
Every entry point asks it first: ``resolve_tool_path`` (all file tools),
``check_path_access``, shell_exec's argument check, the workspace select,
suggest and create routes and the research download. The recorder below
answers every filesystem call on an "attacker" path itself, so no test here
ever touches the network; the negative controls run the old bodies.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.workspace.remote_paths import remote_path_reason

REMOTE = [
    r"\\attacker\share\x.txt",
    "//attacker/share/x.txt",
    r"\\?\UNC\attacker\share\x.txt",
    r"\\.\pipe\attacker",
    r"\\?\C:\attacker\x.txt",
    r"\??\UNC\attacker\share\x",
    r"\/attacker/share/x",
    '  "\\\\attacker\\share\\x"  ',
]
LOCAL = ["C:\\work\\a.txt", "/tmp/a.txt", "rel/a.txt", ".\\a.txt", "~/a.txt", "a.txt", "", "C:a.txt"]


@pytest.mark.parametrize("text", REMOTE)
def test_network_and_device_forms_are_refused(text):
    assert remote_path_reason(text)


@pytest.mark.parametrize("text", LOCAL)
def test_local_forms_pass(text):
    assert remote_path_reason(text) is None


@pytest.fixture
def touches(monkeypatch) -> list[str]:
    """Every filesystem call made on an "attacker" path, answered here."""
    seen: list[str] = []

    def names_attacker(path) -> bool:
        if not isinstance(path, (str, os.PathLike)):
            return False  # a file descriptor
        return "attacker" in str(os.fspath(path))

    def guard(real, *, returns_path=False):
        def call(path, *args, **kwargs):
            if names_attacker(path):
                seen.append(os.fspath(path))
                if returns_path:
                    return os.fspath(path)
                raise FileNotFoundError(os.fspath(path))
            return real(path, *args, **kwargs)
        return call

    monkeypatch.setattr(os.path, "realpath", guard(os.path.realpath, returns_path=True))
    for name in ("stat", "lstat", "scandir", "listdir", "makedirs"):
        monkeypatch.setattr(os, name, guard(getattr(os, name)))
    real_resolve = Path.resolve

    def resolve(self, *args, **kwargs):
        if "attacker" in str(self):
            seen.append(str(self))
            return self
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    return seen


@pytest.mark.parametrize("text", REMOTE[:3])
def test_resolve_tool_path_refuses_before_resolving(touches, text, tmp_path):
    from kazma_core.workspace.binding import resolve_tool_path

    with pytest.raises(RefusedBeforeEffect) as refused:
        resolve_tool_path(text, root=tmp_path)
    assert "network" in str(refused.value)
    assert touches == []


def test_check_path_access_denies_before_resolving(touches):
    from kazma_core.workspace.path_policy import check_path_access

    result = check_path_access(r"\\attacker\share\x.txt", "read")
    assert not result.allowed and "network" in result.reason
    assert touches == []


async def test_file_read_refuses_before_resolving(touches):
    from kazma_core.tools.file_read import file_read

    try:
        answer = await file_read(r"\\attacker\share\x.txt")
    except RefusedBeforeEffect as refused:
        answer = "Error: " + str(refused)
    assert "network" in answer
    assert touches == []


def _client(*routers) -> TestClient:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return TestClient(app)


def test_workspace_routes_refuse_before_resolving(touches):
    from kazma_gateway.routers.workspace import create_workspace_select_router
    from kazma_gateway.routers.workspaces import create_workspaces_router

    client = _client(create_workspace_select_router(), create_workspaces_router())
    for path in (r"\\attacker\share\repo", "//attacker/share/repo"):
        selected = client.post("/api/workspace/select", json={"path": path})
        assert selected.status_code == 400, selected.text
        suggested = client.get("/api/workspace/suggest", params={"path": path})
        assert suggested.status_code == 400 and suggested.json()["suggestions"] == []
        created = client.post("/api/workspaces/create", json={"name": "x", "path": path})
        assert created.status_code == 400, created.text
    assert touches == []


def test_research_download_refuses_absolute_and_network_paths(touches, tmp_path, monkeypatch):
    from kazma_ui.research_panel.routes import create_research_router

    client = _client(create_research_router())
    for path in (r"\\attacker\share\x.pdf", "//attacker/share/x.pdf", str(tmp_path / "x.pdf")):
        got = client.get("/api/research/download", params={"path": path})
        assert got.status_code == 400, (path, got.status_code)
    assert touches == []


def test_research_download_still_serves_a_report_by_name(monkeypatch, tmp_path):
    import kazma_core.paths as paths
    from kazma_ui.research_panel.routes import create_research_router

    monkeypatch.setattr(paths, "data_dir", lambda *a, **k: tmp_path)
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    client = _client(create_research_router())
    got = client.get("/api/research/download", params={"path": "report.pdf"})
    assert got.status_code == 200 and got.content == b"%PDF-1.4 fake"
    assert client.get("/api/research/download", params={"path": "../x"}).status_code == 403


# ── Negative controls: the old bodies touch the path first ─────────────────

def test_control_the_old_bodies_touch_the_network_path(touches, tmp_path):
    text = r"\\attacker\share\x.txt"

    # resolve_tool_path before 2026-11-10.
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = tmp_path / p
    p.resolve()
    # /api/workspace/suggest: Path(raw).resolve() first.
    Path("//attacker/share/repo").resolve()
    # /api/research/download: realpath of an absolute path first.
    absolute = "//attacker/share/x.pdf"
    if os.path.isabs(absolute):
        os.path.realpath(absolute)
    assert len(touches) == 3, touches
