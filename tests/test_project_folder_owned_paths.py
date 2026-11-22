"""The S2-12 review, findings 9 and 10: a shared space's project folder.

Finding 9: Kazma's folders, which a project folder is never inside and never
holds, come from ``store_registry.kazma_owned_paths``: a store a variable
put elsewhere, the vector store, the document store, what the setup record
names (the data folder, Postgres's data, archive and base backups, before
and after an upgrade), the engine's disk, the backup folder copy, the
admin's extra folders. A source that cannot be read makes the folder
"unreachable", never taken for one that overlaps nothing.

Finding 10: the at-use check runs on the per-task rung too (a swarm task's
workspace row, an approval resume's root): in a host space other than the
owner's, another space's root is refused there. A create is atomic: one at
a time from the folder check to the record, so two creates never take one
folder; a row a cut-off create left is dropped, never an orphan root.

Controls: the list of before (the data folder, the install and Kazma's home)
lets each folder pass; the per-task rung of before hands out the owner's
root; creates without the lock both take the folder; without the orphan
drop the folder reads as another space's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, project_folder, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, bind_scope


@pytest.fixture
def enforced(monkeypatch):
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    get_config_store().delete(identity.REGISTRY_KEY)
    try:
        yield
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _folders_of_before() -> list[Path]:
    """``project_folder._kazma_folders`` as it was before the review."""
    import os

    from kazma_core.paths import data_dir, installed_project_root

    folders = [data_dir(create=False), installed_project_root() or Path(project_folder.__file__).parents[2]]
    home = (os.environ.get("KAZMA_USER_HOME") or "").strip()
    if home:
        folders.append(Path(home).expanduser())
    return folders


def _plant(kind: str, where: Path, monkeypatch) -> Path:
    """Make *where* one of Kazma's own by *kind*; the folder a project would
    take that overlaps it."""
    where.mkdir(parents=True, exist_ok=True)
    if kind == "store_override":
        monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(where / "memory_state.db"))
        return where  # the folder holding the store
    if kind == "vector_path":
        monkeypatch.setenv("KAZMA_VECTOR_PATH", str(where))
    elif kind == "documents":
        import kazma_core.documents.config as doc_config

        monkeypatch.setattr(doc_config, "get_document_storage_root", lambda: where)
    elif kind in ("data_folder", "postgres"):
        from kazma_core.paths import data_dir

        record = ({"data_folder": {"path": str(where)}} if kind == "data_folder"
                  else {"postgres": {"data_dir": str(where / "data"), "wal_archive_dir": str(where / "wal")}})
        (data_dir() / "setup_record.json").write_text(json.dumps(record), encoding="utf-8")
        monkeypatch.setattr("kazma_core.data_folder.chosen", lambda path=None: Path(str(where))
                            if kind == "data_folder" else None)
    elif kind == "engine_disk":
        monkeypatch.setattr("kazma_core.docker_cli.kazma_engine_disk_dir", lambda: where)
    elif kind == "backup_folder":
        monkeypatch.setattr(get_config_store(), "get", _getter_with("backups.folder.path", str(where)))
    elif kind == "extra_root":
        monkeypatch.setattr(get_config_store(), "get", _getter_with("workspace.extra_roots", str(where)))
    # Inside Postgres's archive, for that kind; inside the folder for the rest.
    project = (where / "wal" if kind == "postgres" else where) / "project"
    project.mkdir(parents=True, exist_ok=True)
    return project


def _getter_with(key: str, value: str) -> Any:
    real = get_config_store().get

    def get(asked: str, *args: Any, **kwargs: Any) -> Any:
        return value if asked == key else real(asked, *args, **kwargs)

    return get


KINDS = ("store_override", "vector_path", "documents", "data_folder", "postgres", "engine_disk",
         "backup_folder", "extra_root")


@pytest.mark.parametrize("kind", KINDS)
def test_every_folder_of_kazmas_is_refused(enforced, tmp_path, monkeypatch, kind) -> None:
    folder = _plant(kind, tmp_path / "kazmas" / kind, monkeypatch)
    assert project_folder.problem(str(folder)) == "kazma_folder", kind


@pytest.mark.parametrize("kind", KINDS)
def test_control_the_list_of_before_lets_it_pass(enforced, tmp_path, monkeypatch, kind) -> None:
    folder = _plant(kind, tmp_path / "kazmas" / kind, monkeypatch)
    monkeypatch.setattr(project_folder, "_kazma_folders", _folders_of_before)
    assert project_folder.problem(str(folder)) is None, kind


def test_a_source_that_cannot_be_read_is_unreachable(enforced, tmp_path, monkeypatch) -> None:
    def broken() -> list[Path]:
        raise RuntimeError("the settings store is away")

    monkeypatch.setattr("kazma_core.store_registry.kazma_owned_paths", broken)
    (tmp_path / "p").mkdir()
    assert project_folder.problem(str(tmp_path / "p")) == "unreachable"


# ── finding 10 ──────────────────────────────────────────────────────────


@pytest.fixture
def dev(enforced, tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return people.create_shared_space("Dev", kind="host", project_folder=str(folder), by=OWNER_ACCOUNT_ID)


def _owners_root(tmp_path: Path) -> Path:
    from kazma_core.stores import get_workspace_store

    root = tmp_path / "owners-repo"
    root.mkdir()
    store = get_workspace_store()
    with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        made = store.create_workspace("owner", str(root))
        store.set_active_workspace(made["id"])
    return root


async def _root_through_the_task_scope(root: Path) -> Path:
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.workspace.binding import resolve_active_root

    async with workspace_path_scope(root):
        return resolve_active_root()


def test_the_task_rung_refuses_another_spaces_root(dev, tmp_path) -> None:
    owners = _owners_root(tmp_path)
    with bind_scope(dev["id"], OWNER_ACCOUNT_ID), pytest.raises(RefusedBeforeEffect):
        asyncio.run(_root_through_the_task_scope(owners))
    # Its own project folder passes.
    with bind_scope(dev["id"], OWNER_ACCOUNT_ID):
        own = Path(dev["project_folder"]) if dev.get("project_folder") else tmp_path / "project"
        assert asyncio.run(_root_through_the_task_scope(own)) == own


def test_control_the_task_rung_of_before_hands_out_the_owners_root(dev, tmp_path, monkeypatch) -> None:
    owners = _owners_root(tmp_path)
    monkeypatch.setattr(project_folder, "check_at_use", lambda *_a, **_k: None)
    with bind_scope(dev["id"], OWNER_ACCOUNT_ID):
        assert asyncio.run(_root_through_the_task_scope(owners)) == owners


def _two_creates(folder: Path) -> list[Any]:
    both_checked = threading.Barrier(2, timeout=2)
    real = project_folder.problem
    answers: list[Any] = []

    def slow(path: str, **kwargs: Any) -> str | None:
        found = real(path, **kwargs)
        with contextlib.suppress(threading.BrokenBarrierError):
            both_checked.wait()
        return found

    def one(name: str) -> None:
        try:
            answers.append(people.create_shared_space(name, kind="host", project_folder=str(folder),
                                                      by=OWNER_ACCOUNT_ID)["id"])
        except people.PeopleRefused as exc:
            answers.append(exc.code)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(project_folder, "problem", slow)
        threads = [threading.Thread(target=one, args=(n,)) for n in ("A", "B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
    return answers


def test_two_creates_never_take_one_folder(enforced, tmp_path) -> None:
    folder = tmp_path / "shared-project"
    folder.mkdir()
    answers = _two_creates(folder)
    assert sorted(a == "folder_another_space" for a in answers) == [False, True], answers


def test_control_without_the_lock_both_take_it(enforced, tmp_path, monkeypatch) -> None:
    folder = tmp_path / "shared-project"
    folder.mkdir()
    monkeypatch.setattr(people, "_CREATE_LOCK", contextlib.nullcontext())
    answers = _two_creates(folder)
    assert not any(a == "folder_another_space" for a in answers), answers


def _orphan_row(folder: Path) -> None:
    """The row a create left when a stop cut it off before its record."""
    from kazma_core.stores import get_workspace_store

    with bind_scope("s0rphan0000000", OWNER_ACCOUNT_ID):
        get_workspace_store().create_workspace("cut off", str(folder))


def test_a_cut_off_create_leaves_no_orphan_root(enforced, tmp_path) -> None:
    from kazma_core.stores import get_workspace_store

    folder = tmp_path / "shared-project"
    folder.mkdir()
    _orphan_row(folder)
    made = people.create_shared_space("Dev", kind="host", project_folder=str(folder), by=OWNER_ACCOUNT_ID)
    assert made["id"]
    assert not any(space == "s0rphan0000000" for space, _root in get_workspace_store().roots_by_space())


def _roots_of_before(space_id: str) -> tuple[list[Path], list[Path]]:
    """``project_folder._roots_of_spaces`` as it was: every row a space's."""
    import os

    from kazma_core.stores import get_workspace_store

    owners: list[Path] = []
    others: list[Path] = []
    for space, root in get_workspace_store().roots_by_space():
        if not root or space == space_id:
            continue
        (owners if space == OWNER_SPACE_ID else others).append(Path(root))
    machine = (os.environ.get("KAZMA_WORKSPACE") or "").strip()
    if machine and space_id != OWNER_SPACE_ID:
        owners.append(Path(machine).expanduser())
    return owners, others


def test_control_an_orphan_row_of_before_blocks_the_folder(enforced, tmp_path, monkeypatch) -> None:
    folder = tmp_path / "shared-project"
    folder.mkdir()
    _orphan_row(folder)
    monkeypatch.setattr(people, "_drop_orphan_rows", lambda: None)
    monkeypatch.setattr(project_folder, "_roots_of_spaces", _roots_of_before)
    with pytest.raises(people.PeopleRefused) as refused:
        people.create_shared_space("Dev", kind="host", project_folder=str(folder), by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "folder_another_space"
