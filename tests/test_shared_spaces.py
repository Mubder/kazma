"""Spaces S2-12, piece 1: the two kinds of shared space (plan 4.11, N29).

* The admin makes a shared space, plain (sandboxed, the default) or with a
  project folder (the host kind); it is recorded in the People record, with
  the admin as its owner, and the People page lists it with its kind.
* A plain shared space never has a host tool, for anyone, the admin too.
* A space with a project folder works in that folder.
* The folder's rules (``spaces.project_folder``): an existing absolute local
  folder; never one of Kazma's own, the owner's machine workspace or another
  space's root, nor inside or holding any of them; compared by real path and
  by identity. Checked again every time the space's tools resolve it: a
  folder that became a link into Kazma's data after the space was made is
  refused then.

Controls: the use-time check taken out reads Kazma's data through the link;
the identity comparison taken out lets a second name of Kazma's data pass;
an install that does not enforce Spaces makes no shared space.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, project_folder, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope

ADMIN = {"source": "secret", "role": "admin", "user_id": "", "actor": "secret", "username": "admin"}


@pytest.fixture
def enforced(monkeypatch):
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    try:
        yield
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _data_dir() -> Path:
    from kazma_core.paths import data_dir

    return data_dir(create=False)


def _link(link: Path, target: Path) -> None:
    """*link* leads to the folder *target*: a junction on Windows (no
    privilege needed), a symbolic link elsewhere."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _root_in(space_id: str) -> Path:
    from kazma_core.workspace.binding import resolve_active_root

    with bind_scope(space_id, OWNER_ACCOUNT_ID):
        return resolve_active_root()


# ── the two kinds ───────────────────────────────────────────────────────


def test_a_plain_shared_space_is_the_admins_recorded_and_has_no_host_tool(enforced) -> None:
    from kazma_core.spaces.tools import host_allowance

    made = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)
    assert made["kind"] == "sandboxed" and made["project_folder"] == ""
    assert made["members"] == [{"id": OWNER_ACCOUNT_ID, "name": "", "role": "owner"}]
    reg = identity.read_registry()
    assert reg["events"][-1]["kind"] == "space_created" and reg["events"][-1]["space"] == made["id"]
    assert people.broken_at() is None
    assert [s["id"] for s in people.list_shared_spaces()] == [made["id"]]
    with bind_scope(made["id"], OWNER_ACCOUNT_ID):
        allowed = host_allowance()
    assert not allowed.development and not allowed.admin  # the admin too


def test_a_space_with_a_project_folder_works_in_it(enforced, tmp_path) -> None:
    folder = tmp_path / "project"
    folder.mkdir()
    made = people.create_shared_space("Kazma dev", kind="host", project_folder=str(folder), by=OWNER_ACCOUNT_ID)
    assert made["kind"] == "host" and Path(made["project_folder"]) == folder.resolve()
    assert _root_in(made["id"]) == folder.resolve()


def test_only_the_admin_makes_a_shared_space_and_only_once_enforced(enforced, monkeypatch) -> None:
    monkeypatch.setattr(identity, "is_admin_account", lambda account_id: False)
    with pytest.raises(people.PeopleRefused) as refused:
        people.create_shared_space("Family", by="p-someone")
    assert refused.value.code == "admin_only"
    monkeypatch.setattr(scope, "_enforced", False)
    with pytest.raises(people.PeopleRefused) as off:
        people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)
    assert off.value.code == "spaces_off"


def test_a_space_s_kind_is_one_of_two(enforced) -> None:
    with pytest.raises(people.PeopleRefused) as refused:
        people.create_shared_space("Family", kind="personal", by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "bad_kind"


# ── the folder's rules ──────────────────────────────────────────────────


def _refusal(path: str) -> str:
    with pytest.raises(people.PeopleRefused) as refused:
        people.create_shared_space("Dev", kind="host", project_folder=path, by=OWNER_ACCOUNT_ID)
    return refused.value.code


def test_each_folder_that_is_no_project_folder_is_refused(enforced, tmp_path) -> None:
    from kazma_core.paths import installed_project_root

    afile = tmp_path / "a.txt"
    afile.write_text("x", encoding="utf-8")
    data = _data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "inner").mkdir(exist_ok=True)
    cases = {
        "": "folder_empty",
        "relative/folder": "folder_relative",
        str(tmp_path / "missing"): "folder_missing",
        str(afile): "folder_not_a_folder",
        r"\\server\share\project": "folder_network",
        str(data): "folder_kazma_folder",
        str(data / "inner"): "folder_kazma_folder",
        str(data.parent): "folder_kazma_folder",  # holds Kazma's data
    }
    install = installed_project_root()
    if install is not None:
        cases[str(install)] = "folder_kazma_folder"
    for path, code in cases.items():
        assert _refusal(path) == code, path
    assert people.list_shared_spaces() == []  # nothing was made


def test_the_owner_s_machine_workspace_and_another_space_s_folder_are_refused(enforced, tmp_path,
                                                                               monkeypatch) -> None:
    machine = tmp_path / "machine"
    (machine / "sub").mkdir(parents=True)
    monkeypatch.setenv("KAZMA_WORKSPACE", str(machine))
    assert _refusal(str(machine / "sub")) == "folder_machine_workspace"
    first = tmp_path / "first"
    (first / "nested").mkdir(parents=True)
    people.create_shared_space("First", kind="host", project_folder=str(first), by=OWNER_ACCOUNT_ID)
    assert _refusal(str(first)) == "folder_another_space"
    assert _refusal(str(first / "nested")) == "folder_another_space"
    # A folder holding them all: the suite keeps this test's memory stores in
    # it too, which Kazma's own folders name since the review (finding 9).
    assert _refusal(str(tmp_path)) in ("folder_another_space", "folder_machine_workspace",
                                       "folder_kazma_folder")


def test_a_failed_record_leaves_no_folder_behind(enforced, tmp_path, monkeypatch) -> None:
    from kazma_core.stores import get_workspace_store

    folder = tmp_path / "project"
    folder.mkdir()
    before = get_workspace_store().roots_by_space()

    def broken(change: Any) -> dict[str, Any]:
        raise identity.IdentityUnavailable("the settings store is away")

    monkeypatch.setattr(identity, "update_registry", broken)
    with pytest.raises(identity.IdentityUnavailable):
        people.create_shared_space("Dev", kind="host", project_folder=str(folder), by=OWNER_ACCOUNT_ID)
    assert get_workspace_store().roots_by_space() == before


# ── checked again at use ────────────────────────────────────────────────


def _space_whose_folder_now_leads_into_kazmas_data(tmp_path: Path) -> str:
    folder = tmp_path / "project"
    folder.mkdir()
    made = people.create_shared_space("Dev", kind="host", project_folder=str(folder), by=OWNER_ACCOUNT_ID)
    folder.rmdir()
    data = _data_dir()
    data.mkdir(parents=True, exist_ok=True)
    _link(folder, data)
    return made["id"]


def test_a_folder_that_became_a_link_into_kazmas_data_is_refused_at_use(enforced, tmp_path) -> None:
    space_id = _space_whose_folder_now_leads_into_kazmas_data(tmp_path)
    with pytest.raises(RefusedBeforeEffect):
        _root_in(space_id)


def test_control_without_the_check_the_space_works_in_kazmas_data(enforced, tmp_path, monkeypatch) -> None:
    space_id = _space_whose_folder_now_leads_into_kazmas_data(tmp_path)
    monkeypatch.setattr(project_folder, "check_at_use", lambda root, *, space_id: None)
    assert _root_in(space_id) == _data_dir().resolve()


def _loopback_alias(path: Path) -> Path | None:
    """*path* through this machine's administrative share, when Windows lets
    this account reach it; else None."""
    if os.name != "nt" or len(path.drive.rstrip(":")) != 1:
        return None
    alias = Path(rf"\\localhost\{path.drive.rstrip(':')}$") / path.relative_to(path.anchor)
    try:
        return alias if os.path.samestat(os.stat(alias), os.stat(path)) else None
    except OSError:
        return None


def test_a_second_name_of_kazmas_data_is_refused_by_its_identity(enforced, monkeypatch) -> None:
    data = _data_dir()
    data.mkdir(parents=True, exist_ok=True)
    alias = _loopback_alias(data)
    if alias is None:
        pytest.skip("this machine's administrative share cannot be reached from this account")
    with pytest.raises(RefusedBeforeEffect):
        project_folder.check_at_use(alias, space_id="s-elsewhere")
    # The control: by real path alone the second name is another folder.
    from kazma_core.workspace import folder_identity

    monkeypatch.setattr(folder_identity, "identity", lambda path: None)
    project_folder.check_at_use(alias, space_id="s-elsewhere")


# ── the routes ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    with TestClient(create_app(), client=("127.0.0.1", 40000), base_url="http://127.0.0.1:9090") as made:
        yield made


def _as(monkeypatch, principal: dict[str, Any]) -> None:
    from kazma_ui import auth

    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    monkeypatch.setattr(auth, "admin_decision",
                        lambda request: "ok" if principal.get("role") == "admin" else "forbidden")


def test_the_people_routes_make_and_list_shared_spaces(client, enforced, monkeypatch, tmp_path) -> None:
    _as(monkeypatch, ADMIN)
    answer = client.post("/api/people/spaces", json={"name": "Family"})
    assert answer.status_code == 201, answer.text
    assert answer.json()["space"]["kind"] == "sandboxed"
    refused = client.post("/api/people/spaces",
                          json={"name": "Dev", "kind": "host", "project_folder": str(_data_dir())})
    assert refused.status_code == 400 and refused.json()["error"] == "folder_kazma_folder"
    listed = client.get("/api/people").json()["shared_spaces"]
    assert [s["name"] for s in listed] == ["Family"]
    _as(monkeypatch, {"source": "session", "role": "operator", "user_id": "p-member", "actor": "login",
                      "username": "member"})
    assert client.post("/api/people/spaces", json={"name": "Mine"}).status_code == 403
