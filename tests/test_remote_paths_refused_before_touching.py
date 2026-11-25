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


# ── 2026-11-14 (CodeQL py/path-injection): the places the rule missed ──────

def test_the_ide_resolver_refuses_before_resolving(touches, monkeypatch, tmp_path):
    from kazma_core.ide import service

    monkeypatch.setattr(service, "_resolve_workspace_root", lambda: tmp_path)
    ide = service.IdeService()
    for text in REMOTE[:3]:
        with pytest.raises(ValueError, match="network"):
            ide.resolve(text)
    assert ide.resolve("a.txt") == (tmp_path / "a.txt").resolve()
    assert touches == []


def test_the_workspace_file_list_refuses_before_resolving(touches, monkeypatch, tmp_path):
    from kazma_ui import workspace_api

    monkeypatch.setattr(workspace_api, "_resolve_workspace_root", lambda: tmp_path)
    (tmp_path / "sub").mkdir()
    client = _client(workspace_api.create_workspace_router())
    for text in (r"\\attacker\share\x", "//attacker/share/x", r"\attacker", "C:attacker"):
        got = client.get("/api/workspace/files", params={"path": text})
        assert got.status_code == 200 and got.json()["files"] == [], (text, got.json())
    assert client.get("/api/workspace/files", params={"path": "sub"}).json()["path"] == "sub"
    assert touches == []


def test_extra_roots_refuse_and_skip_network_paths(touches, monkeypatch):
    from kazma_core.workspace import path_grants

    stored: dict = {}

    class _Store:
        def get(self, key, default=None):
            return stored.get(key, default)

        def set(self, key, value, **_kw):
            stored[key] = value

    monkeypatch.setattr(path_grants, "_cs", lambda: _Store())
    for text in REMOTE[:3]:
        assert "network" in (path_grants._extra_root_problem(text) or "")
        with pytest.raises(ValueError, match="network"):
            path_grants.set_durable_roots([{"path": text}])
    stored["workspace.extra_roots"] = [{"path": r"\\attacker\share\kept", "mode": "read"}]
    assert path_grants.list_durable_roots() == []
    assert touches == []


def test_the_store_check_before_a_card_never_resolves_a_network_path(touches, tmp_path):
    from kazma_core.workspace.path_policy import control_plane_store_targeted

    for text in (r"\\attacker\share\x.db", "//attacker/share/vault.db"):
        assert control_plane_store_targeted(text, cwd=tmp_path) is None
    assert touches == []


async def test_skill_install_refuses_a_network_folder(touches):
    from kazma_core.agent_skills.installer import install_from_any

    result = await install_from_any(r"\\attacker\share\skill")
    assert not result.success and "network" in result.message
    assert touches == []


def test_workspace_roots_policy_refuses_a_network_folder(touches):
    from kazma_core.safety.deployment_policy import normalize_workspace_roots

    with pytest.raises(ValueError, match="network"):
        normalize_workspace_roots([r"\\attacker\share\work"])
    assert touches == []


def test_control_the_old_ide_and_list_bodies_touch_the_network_path(touches, tmp_path):
    # IdeService.resolve before 2026-11-14: an absolute input resolved first
    # ("//host/share" is absolute on every platform; a backslash one only on
    # Windows, where CI does not run).
    text = "//attacker/share/x.txt"
    if os.path.isabs(text):
        Path(text).resolve()
    # /api/workspace/files: only "/" stripped, then root / rel resolved.
    (tmp_path / r"\\attacker\share\x.txt".strip("/")).resolve()
    assert len(touches) == 2, touches


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


# ── 2026-11-25: a drive named after the first part (Windows) ───────────────
# ``Path.resolve()`` of ``<workspace>\file:\C:\x`` answers the drive-relative
# ``C:x``: the access check joined it to the workspace again and allowed it,
# and the tool opened ``x`` in the server's current folder.

INNER_DRIVE = ["file:///C:/x", "a?b/C:/x", "a|b/C:x", "a/C:/x", "a\\C:\\x", "./C:/x", "nul/d:/x", "a/../C:/x",
               "C:/ok/D:/x", '  "a/C:/x"  ']
NO_INNER_DRIVE = ["C:/work/a.txt", "C:\\work\\ab:c", "C:a.txt", "a.txt", "rel/ab:c", "https://host/x", "",
                  "~/a.txt", "x:y"]


@pytest.mark.parametrize("text", INNER_DRIVE)
def test_a_drive_after_the_first_part_is_named(text):
    from kazma_core.workspace.remote_paths import _inner_drive_reason

    assert _inner_drive_reason(text)
    assert bool(remote_path_reason(text)) is (os.name == "nt"), "every door asks it, on Windows"


@pytest.mark.parametrize("text", NO_INNER_DRIVE)
def test_a_path_with_one_drive_or_none_passes(text):
    from kazma_core.workspace.remote_paths import _inner_drive_reason

    assert _inner_drive_reason(text) is None


@pytest.fixture
def server_folder(tmp_path, monkeypatch) -> Path:
    """The server's current folder, holding a file no tool may open."""
    here = tmp_path / "server"
    here.mkdir()
    (here / "secret.txt").write_text("the server's own words\n", encoding="utf-8")
    monkeypatch.chdir(here)
    return here


def _from_the_server_folder(here: Path) -> list[str]:
    drive = here.drive or "C:"
    return [f"file:///{drive}/secret.txt", f"a?b/{drive}/secret.txt", f"a/{drive}secret.txt", f"{drive}secret.txt"]


async def test_no_file_tool_opens_the_servers_folder(server_folder, tmp_path):
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.tools.file_apply_patch import file_apply_patch
    from kazma_core.tools.file_read import clear_read_cache, file_read
    from kazma_core.tools.file_write import file_write

    clear_read_cache()
    workspace = tmp_path / "work"
    workspace.mkdir()
    async with workspace_path_scope(workspace):
        for text in _from_the_server_folder(server_folder):
            for tool, args in ((file_read, (text,)), (file_write, (text, "overwritten")),
                               (file_apply_patch, (text, "server's", "agent's"))):
                try:
                    shown = str(await tool(*args))
                except RefusedBeforeEffect as refused:
                    shown = "Error: " + str(refused)
                assert "own words" not in shown, (text, shown)
    assert (server_folder / "secret.txt").read_text(encoding="utf-8") == "the server's own words\n"
    assert [p.name for p in server_folder.iterdir()] == ["secret.txt"]


@pytest.mark.skipif(os.name != "nt", reason="a drive means something on Windows only")
def test_a_drive_relative_path_is_refused_by_the_resolver_the_policy_and_the_ide(server_folder, tmp_path, monkeypatch):
    from kazma_core.ide import service
    from kazma_core.workspace import path_policy, remote_paths
    from kazma_core.workspace.binding import resolve_tool_path
    from kazma_core.workspace.real_paths import real_path

    workspace = tmp_path / "work"
    workspace.mkdir()
    monkeypatch.setattr(service, "_resolve_workspace_root", lambda: workspace)
    monkeypatch.setattr(path_policy, "resolve_active_root", lambda: workspace)
    spelled = _from_the_server_folder(server_folder)
    for text in spelled:
        with pytest.raises(RefusedBeforeEffect):
            resolve_tool_path(text, root=workspace)
        assert not path_policy.check_path_access(text, "read").allowed, text
        assert not path_policy.check_path_access(Path(text), "write").allowed, text
        assert real_path(workspace / text).is_absolute()
    for text in spelled[:3]:
        with pytest.raises(ValueError, match="drive"):
            service.IdeService().resolve(text)
    # A resolve that still answers a drive-relative path is not believed.
    monkeypatch.setattr(remote_paths, "_inner_drive_reason", lambda _path: None)
    with pytest.raises(RefusedBeforeEffect, match="can hold"):
        resolve_tool_path(spelled[0], root=workspace)
    answer = path_policy.check_path_access(spelled[0], "read")
    assert not answer.allowed and "can hold" in answer.reason


@pytest.mark.skipif(os.name != "nt", reason="a drive means something on Windows only")
def test_control_windows_resolves_such_a_path_to_the_servers_folder(server_folder, tmp_path):
    for text in _from_the_server_folder(server_folder)[:3]:
        resolved = (tmp_path / "work" / text).resolve()
        assert not resolved.is_absolute(), "no longer drive-relative: the rule may go"
        assert resolved.read_text(encoding="utf-8") == "the server's own words\n"
