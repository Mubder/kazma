"""Spaces S1-34: each host space has its own root.

The admin's own space and another host space each keep their own workspaces
and their own active one. A switch (and a clone, which registers a
workspace and activates it the same way) in one never moves the other's
root, IDE root or code index, and only the admin's own space moves the
machine's process pin and MCP binding. A host space with no workspace of its
own works in its own folder, never the machine's. Its file tools stay in its
workspace, and a filesystem MCP call there -- a server bound to the
machine's workspace, which does not serve it -- is refused.

Control: the store of before -- one list, one active row for the install --
lets a switch in the other space move the admin's root.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID


@pytest.fixture
def two_hosts(monkeypatch, tmp_path):
    from kazma_core.stores import get_workspace_store
    from kazma_core.workspace import binding

    monkeypatch.setattr(scope, "_enforced", True)
    other = f"host-{uuid.uuid4().hex[:6]}"
    identity.create_space("The project", created_by=OWNER_ACCOUNT_ID, kind="host", space_id=other)
    assert identity.is_member(OWNER_ACCOUNT_ID, other)  # its maker is its owner
    folders = {name: tmp_path / name for name in ("admin-repo", "admin-repo-2", "project-repo")}
    for folder in folders.values():
        folder.mkdir()
    moved: list[Path] = []

    def _moved(root, reason):
        moved.append(Path(root).resolve())

    binding.subscribe_root_changed(_moved)
    store = get_workspace_store()
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        admin_ws = store.create_workspace("admin", str(folders["admin-repo"]))
        assert store.set_active_workspace(admin_ws["id"])
    moved.clear()
    try:
        yield {"other": other, "folders": folders, "admin_ws": admin_ws, "store": store, "moved": moved}
    finally:
        binding.unsubscribe_root_changed(_moved)
        get_config_store().delete(identity.REGISTRY_KEY)


def _root_in(space: str) -> Path:
    from kazma_core.workspace.binding import resolve_active_root

    with scope.bind_scope(space, OWNER_ACCOUNT_ID):
        return resolve_active_root().resolve()


def _ide_and_index(space: str) -> tuple[Path, Path]:
    from kazma_core.code_index.store import db_path_for
    from kazma_core.ide.service import IdeService

    with scope.bind_scope(space, OWNER_ACCOUNT_ID):
        root = Path(IdeService().root).resolve()
    return root, db_path_for(root)


def test_a_host_space_with_no_workspace_works_in_its_own_folder(two_hosts) -> None:
    from kazma_core.spaces.files import space_dir

    other = two_hosts["other"]
    assert _root_in(other) == (space_dir(other) / "files").resolve()
    assert _root_in(OWNER_SPACE_ID) == two_hosts["folders"]["admin-repo"].resolve()


def test_a_switch_in_one_space_never_moves_the_other(two_hosts) -> None:
    from kazma_core.workspace.binding import get_bound_mcp_root, get_process_pin

    store, folders, other = two_hosts["store"], two_hosts["folders"], two_hosts["other"]
    admin_ide, admin_index = _ide_and_index(OWNER_SPACE_ID)
    pin, mcp = get_process_pin(), get_bound_mcp_root()
    # The other host space registers and activates a workspace of its own.
    with scope.bind_scope(other, OWNER_ACCOUNT_ID):
        project = store.create_workspace("project", str(folders["project-repo"]))
        assert store.set_active_workspace(project["id"])
        assert [w["id"] for w in store.list_workspaces()] == [project["id"]]
    assert _root_in(other) == folders["project-repo"].resolve()
    assert _root_in(OWNER_SPACE_ID) == folders["admin-repo"].resolve()
    assert _ide_and_index(OWNER_SPACE_ID) == (admin_ide, admin_index)
    assert _ide_and_index(other)[1] != admin_index  # its own code index
    assert get_process_pin() == pin and get_bound_mcp_root() == mcp
    assert two_hosts["moved"] == []  # no rebind of the machine's MCP servers
    # And the other way round: the admin's switch leaves the project's root.
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        again = store.create_workspace("admin-2", str(folders["admin-repo-2"]))
        assert store.set_active_workspace(again["id"])
        assert project["id"] not in [w["id"] for w in store.list_workspaces()]
        assert store.get_workspace(project["id"]) is None
        assert store.set_active_workspace(project["id"]) is False  # not this space's
    assert _root_in(other) == folders["project-repo"].resolve()
    assert _root_in(OWNER_SPACE_ID) == folders["admin-repo-2"].resolve()
    assert two_hosts["moved"] == [folders["admin-repo-2"].resolve()]


def test_the_other_spaces_file_tools_stay_in_its_workspace(two_hosts) -> None:
    from kazma_core.workspace.path_policy import check_path_access

    secret = two_hosts["folders"]["admin-repo"] / "notes.txt"
    secret.write_text("the admin's", encoding="utf-8")
    with scope.bind_scope(two_hosts["other"], OWNER_ACCOUNT_ID):
        assert not check_path_access(secret, "read").allowed
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert check_path_access(secret, "read").allowed


def test_a_filesystem_mcp_call_in_the_space_it_does_not_serve_is_refused(two_hosts) -> None:
    from kazma_core.spaces.tools import _host_refusal

    with scope.bind_scope(two_hosts["other"], OWNER_ACCOUNT_ID):
        assert _host_refusal("mcp__filesystem__read_file")


def test_the_environment_note_of_the_other_space_names_nothing_of_the_machine(two_hosts) -> None:
    from kazma_core.ide.env_context import _build_env_context_sync

    with scope.bind_scope(two_hosts["other"], OWNER_ACCOUNT_ID):
        note = _build_env_context_sync()
    assert str(two_hosts["folders"]["admin-repo"].resolve()) not in note
    assert "MCP global root" not in note


def test_control_one_active_row_for_the_install_moves_both(two_hosts, monkeypatch) -> None:
    from kazma_core.spaces import files as space_files
    from kazma_core.stores.workspaces import WorkspaceStore

    monkeypatch.setattr(WorkspaceStore, "_space", staticmethod(lambda: None))
    monkeypatch.setattr(space_files, "host_space_root", lambda: None)
    store, folders, other = two_hosts["store"], two_hosts["folders"], two_hosts["other"]
    with scope.bind_scope(other, OWNER_ACCOUNT_ID):
        project = store.create_workspace("project", str(folders["project-repo"]))
        assert store.set_active_workspace(project["id"])
    assert _root_in(OWNER_SPACE_ID) == folders["project-repo"].resolve()


def test_without_spaces_there_is_one_active_workspace_as_before(two_hosts, monkeypatch) -> None:
    store, folders = two_hosts["store"], two_hosts["folders"]
    monkeypatch.setattr(scope, "_enforced", False)
    project = store.create_workspace("project", str(folders["project-repo"]))
    assert store.set_active_workspace(project["id"])
    from kazma_core.workspace.binding import resolve_active_root

    assert resolve_active_root().resolve() == folders["project-repo"].resolve()
    assert {w["id"] for w in store.list_workspaces()} >= {project["id"], two_hosts["admin_ws"]["id"]}


def test_erasing_the_other_space_takes_its_workspaces_and_only_them(two_hosts) -> None:
    from kazma_core.spaces import space_data

    store, folders, other = two_hosts["store"], two_hosts["folders"], two_hosts["other"]
    with scope.bind_scope(other, OWNER_ACCOUNT_ID):
        project = store.create_workspace("project", str(folders["project-repo"]))
    assert [row["id"] for row in space_data._workspace_rows(other)] == [project["id"]]
    assert space_data._erase_workspaces(other) == 1
    assert space_data._workspace_rows(other) == []
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert store.get_workspace(two_hosts["admin_ws"]["id"]) is not None
