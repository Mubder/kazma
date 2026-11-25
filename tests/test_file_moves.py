"""Spaces S2-12, piece 7: moving one's own file between one's own space and a
shared one.

Reem shares a file of her personal space with the shared space Family, and
takes it back out. After each move the file is in exactly one space's folder,
its maker recorded, and a file taken out leaves a line. Only the person who
shared a file takes it out, and only while nobody changed it. A name taken in
the target is refused, never overwritten. Only one regular file moves, named
as the disk spells it. Only between a plain personal space and a plain shared
space. Nothing is made where Spaces is not enforced. The routes are the
Documents feature's, and nothing but the page's route moves a file.

A move that is cut off, or that meets another change, is
``tests/test_file_move_crashes.py``. Each rule's test has a control: the same
scenario with that rule's step taken out, which must fail it.
"""

from __future__ import annotations

import ast
import asyncio
import errno
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import file_moves, files, people, scope, shared_files, space_limits
from kazma_core.spaces.files import FILES, moving_dir, space_dir
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_ui import auth, space_binding
from kazma_ui.space_files_api import REFUSALS, create_space_files_router

from tests._file_move_world import BIG, PLAN, Files, digest, files_world

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(monkeypatch, tmp_path):
    with files_world(monkeypatch, tmp_path) as w:
        yield w


# ── in exactly one space after each move ──────────────────────────────────


def test_shared_then_taken_out_is_in_one_space_each_time(world: Files) -> None:
    w = world
    w.put(w.mine, "notes/trip.txt")
    assert w.move("notes/trip.txt", w.mine, w.family) == {
        "path": "notes/trip.txt", "space": w.family, "taken_out": False}
    assert w.where() == ["family:notes/trip.txt"], "it left her own space"
    assert w.maker(w.family, "notes/trip.txt") == (w.reem, digest(PLAN))
    assert w.lines() == [] and w.held() == []

    assert w.move("notes/trip.txt", w.family, w.mine)["taken_out"] is True
    assert w.where() == ["own:notes/trip.txt"]
    assert w.maker(w.family, "notes/trip.txt") is None
    assert w.lines() == [("notes/trip.txt", w.reem)], "the shared space keeps a line"
    assert w.held() == []
    # And again: the line of the first time stays, one is added.
    w.move("notes/trip.txt", w.mine, w.family)
    w.move("notes/trip.txt", w.family, w.mine)
    assert len(w.lines()) == 2


def test_control_without_removing_the_source_it_is_in_two_places(world: Files, monkeypatch) -> None:
    monkeypatch.setattr(file_moves, "_end", lambda move: "moved")
    with pytest.raises(AssertionError):
        test_shared_then_taken_out_is_in_one_space_each_time(world)


def test_the_copy_is_a_fresh_file(world: Files) -> None:
    """A handle or a hard link made before the move reaches nothing of it."""
    w = world
    path = w.put(w.mine, "trip.txt")
    before = os.lstat(path)
    w.move("trip.txt", w.mine, w.family)
    assert not os.path.samestat(before, os.lstat(w.root(w.family) / "trip.txt"))


def test_arabic_and_long_names_move(world: Files) -> None:
    w = world
    rel = "رحلة العائلة/" + "خ" * 100 + ".txt"
    w.put(w.mine, rel)
    w.move(rel, w.mine, w.family)
    assert w.where() == [f"family:{rel}"] and w.maker(w.family, rel) == (w.reem, digest(PLAN))
    w.move(rel, w.family, w.mine)
    assert w.where() == [f"own:{rel}"] and w.lines() == [(rel, w.reem)]


# ── only the sharer takes it out, while unchanged ─────────────────────────


def test_only_the_person_who_shared_a_file_takes_it_out(world: Files) -> None:
    w = world
    w.share("trip.txt")
    # Omar, a member and an owner of the space: not his file.
    assert w.refused("trip.txt", w.family, w.omars, by=w.omar) == "not_the_sharer"
    # A file nobody shared (Kazma wrote it there): nobody's to take out.
    w.put(w.family, "report.txt", b"a report")
    assert w.refused("report.txt", w.family, w.mine) == "not_the_sharer"
    assert w.where() == ["family:trip.txt"] and w.where(b"a report") == ["family:report.txt"]
    assert w.held() == [] and w.lines() == []


def test_control_without_the_maker_check_another_member_takes_it(world: Files, monkeypatch) -> None:
    real = shared_files.maker_of
    monkeypatch.setattr(shared_files, "maker_of",
                        lambda space, rel: (lambda m: {**m, "maker": world.omar} if m else None)(real(space, rel)))
    with pytest.raises(AssertionError):
        test_only_the_person_who_shared_a_file_takes_it_out(world)


def test_a_file_changed_since_it_was_shared_stays(world: Files) -> None:
    w = world
    w.share("trip.txt")
    w.put(w.family, "trip.txt", b"with Omar's change")
    assert w.refused("trip.txt", w.family, w.mine) == "changed_since"
    assert w.where(b"with Omar's change") == ["family:trip.txt"] and w.held() == []
    listed = shared_files.list_files(w.family, by=w.reem)["files"]
    assert [f["mine"] for f in listed] == [False], "the panel does not offer it"


def test_control_without_the_digest_check_a_changed_file_is_taken(world: Files, monkeypatch) -> None:
    real = shared_files.maker_of

    def any_bytes(space: str, rel: str) -> dict[str, Any] | None:
        made, now = real(space, rel), world.read(space, rel)
        return {**made, "sha256": digest(now)} if made and now is not None else made

    monkeypatch.setattr(shared_files, "maker_of", any_bytes)
    with pytest.raises(AssertionError):
        test_a_file_changed_since_it_was_shared_stays(world)


# ── a name taken in the target ────────────────────────────────────────────


def test_a_name_taken_in_the_target_is_refused_never_overwritten(world: Files) -> None:
    w = world
    w.put(w.family, "trip.txt", b"Omar's own trip plan")
    w.put(w.mine, "trip.txt")
    assert w.refused("trip.txt", w.mine, w.family) == "name_taken"
    assert w.read(w.family, "trip.txt") == b"Omar's own trip plan" and w.where() == ["own:trip.txt"]
    # A file where a folder would go.
    w.put(w.family, "notes", b"a file called notes")
    w.put(w.mine, "notes/a.txt", b"a")
    assert w.refused("notes/a.txt", w.mine, w.family) == "name_taken"
    assert w.held() == [] and w.maker(w.family, "trip.txt") is None


def _taken_at_the_last_moment(w: Files, monkeypatch) -> None:
    w.put(w.mine, "trip.txt")
    real = file_moves._put_in_place

    def taken_meanwhile(move: Any) -> None:
        w.put(w.family, "trip.txt", b"written by someone else meanwhile")
        real(move)

    monkeypatch.setattr(file_moves, "_put_in_place", taken_meanwhile)
    assert w.refused("trip.txt", w.mine, w.family) == "name_taken"
    assert w.read(w.family, "trip.txt") == b"written by someone else meanwhile", "another's file was replaced"
    assert w.where() == ["own:trip.txt"] and w.held() == [] and w.maker(w.family, "trip.txt") is None


def test_a_name_taken_at_the_last_moment_is_refused_too(world: Files, monkeypatch) -> None:
    _taken_at_the_last_moment(world, monkeypatch)


def test_control_a_placing_that_replaces_takes_anothers_file(world: Files, monkeypatch) -> None:
    def replaces(staged: Path, where: Path) -> bool:
        os.replace(staged, where)
        return True

    monkeypatch.setattr(file_moves, "_place", replaces)
    with pytest.raises(AssertionError):
        _taken_at_the_last_moment(world, monkeypatch)


def _no_links(monkeypatch) -> None:
    def no_links(*_a: Any, **_k: Any) -> None:
        raise PermissionError(errno.EPERM, "no hard links here")

    monkeypatch.setattr(file_moves.os, "link", no_links)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows places by a rename that never replaces")
def test_a_disk_with_no_hard_links_moves_and_never_replaces(world: Files, monkeypatch) -> None:
    if file_moves._rename_call() is None:
        pytest.skip("this system has no rename that never replaces")
    _no_links(monkeypatch)
    w = world
    w.share("trip.txt")
    w.move("trip.txt", w.family, w.mine)
    assert w.where() == ["own:trip.txt"] and w.held() == []
    _taken_at_the_last_moment(w, monkeypatch)


# ── one regular file of the space, named as the disk spells it ────────────


def _junction(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_bytes(b"a file of this computer")
    return outside


def test_a_link_to_another_place_is_refused(world: Files, tmp_path) -> None:
    w = world
    outside = _outside(tmp_path)
    w.put(w.mine, "a.txt")
    _junction(w.root(w.mine) / "elsewhere", outside)
    assert w.refused("elsewhere/secret.txt", w.mine, w.family) == "not_a_file"
    # And a link in the target, where the file would go.
    w.put(w.family, "b.txt")
    _junction(w.root(w.family) / "notes", outside)
    w.put(w.mine, "notes/n.txt", b"n")
    assert w.refused("notes/n.txt", w.mine, w.family) == "name_taken"
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"], "a file left the spaces"


def test_control_without_the_link_check_a_file_of_this_computer_is_shared(world: Files, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(file_moves, "is_linked", lambda path: False)
    from kazma_core.workspace import folder_identity

    monkeypatch.setattr(folder_identity, "inside", lambda child, parent: True)
    with pytest.raises(AssertionError):
        test_a_link_to_another_place_is_refused(world, tmp_path)


def test_a_folder_a_second_name_and_a_path_out_are_refused(world: Files, tmp_path) -> None:
    w = world
    outside = _outside(tmp_path)
    w.put(w.mine, "a-folder/x.txt", b"x")
    os.link(outside / "secret.txt", w.root(w.mine) / "second-name.txt")
    for rel in ("a-folder", "second-name.txt"):
        assert w.refused(rel, w.mine, w.family) == "not_a_file", rel
    for rel in ("", "../outside/secret.txt", "/etc/passwd", "a-folder//x.txt", "./a-folder/x.txt", "a-folder/",
                ".hidden", "a-folder/.hidden", "nothing.txt", "a-folder/x.txt/y", "CON"):
        assert w.refused(rel, w.mine, w.family) == "not_found", rel
    for rel in ("C:/Windows/win.ini", "\\\\server\\share\\x", "a-folder\\x.txt", "x.txt::$DATA", "a\x00b", "a\nb"):
        assert w.refused(rel, w.mine, w.family) == "bad_name", rel
    assert not w.root(w.family).exists() or list(w.root(w.family).iterdir()) == []


def _another_spelling(w: Files) -> None:
    """A path typed in another case: never a second name of the file."""
    w.put(w.family, "Notes/other.txt", b"other")
    w.put(w.mine, "Notes/trip.txt")
    # As the disk spells it, or no such file; never stored under a name the list does not say.
    assert w.refused("notes/trip.txt", w.mine, w.family) == "not_found"
    assert w.refused("Notes/TRIP.txt", w.mine, w.family) == "not_found"
    w.move("Notes/trip.txt", w.mine, w.family)
    listed = {f["path"]: f["mine"] for f in shared_files.list_files(w.family, by=w.reem)["files"]}
    assert listed["Notes/trip.txt"] is True


def test_a_path_is_the_one_the_list_says(world: Files) -> None:
    _another_spelling(world)


@pytest.mark.skipif(sys.platform != "win32", reason="a disk that reads two cases as one name")
def test_a_folder_of_another_case_in_the_target_is_that_folder(world: Files) -> None:
    w = world
    w.put(w.family, "Notes/other.txt", b"other")
    w.put(w.mine, "notes/trip.txt")
    assert w.move("notes/trip.txt", w.mine, w.family)["path"] == "Notes/trip.txt"
    listed = {f["path"]: f["mine"] for f in shared_files.list_files(w.family, by=w.reem)["files"]}
    assert listed["Notes/trip.txt"] is True, "it could never be taken out"
    w.move("Notes/trip.txt", w.family, w.mine)
    assert w.where() == ["own:notes/trip.txt"]
    for rel in ("notes/trip.txt.", "notes/TRIP~1.TXT"):
        assert w.refused(rel, w.mine, w.family) == "not_found", rel


@pytest.mark.skipif(sys.platform != "win32", reason="a disk that reads two cases as one name")
def test_control_the_lookup_that_takes_what_was_typed(world: Files, monkeypatch) -> None:
    monkeypatch.setattr(file_moves, "_spelled", lambda folder, name: name if (folder / name).exists() else None)
    with pytest.raises(AssertionError):
        _another_spelling(world)


@pytest.mark.skipif(sys.platform == "win32", reason="a name Windows cannot hold")
def test_a_name_another_system_cannot_hold_is_listed_and_stays(world: Files) -> None:
    w = world
    w.put(w.mine, "a:b.txt")
    (w.root(w.mine) / os.fsdecode(b"not-text-\xff.txt")).write_bytes(b"x")
    listed = shared_files.list_files(w.mine, by=w.reem)["files"]
    assert len(listed) == 2 and all(f["movable"] is False for f in listed)
    assert w.refused("a:b.txt", w.mine, w.family) == "bad_name"


# ── limits, and steps that fail ───────────────────────────────────────────


def test_the_upload_limit_applies(world: Files) -> None:
    w = world
    w.put(w.mine, "large.bin", b"x" * 64)
    assert w.refused("large.bin", w.mine, w.family, max_bytes=63) == "too_large"
    assert w.where(b"x" * 64) == ["own:large.bin"] and w.held() == []


def test_the_target_spaces_disk_limit_applies(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, "trip.txt")
    monkeypatch.setattr(space_limits, "disk_refusal",
                        lambda space, incoming=0: "full" if space == w.family and incoming == len(PLAN) else None)
    assert w.refused("trip.txt", w.mine, w.family) == "space_limit"
    assert w.where() == ["own:trip.txt"] and w.held() == []


@pytest.mark.parametrize(("failure", "code"), [
    (OSError(errno.ENOSPC, "no space left"), "disk_full"),
    (OSError(errno.ENAMETOOLONG, "name too long"), "name_too_long"),
    (PermissionError(errno.EACCES, "held"), "in_use"),
    (OSError(errno.EIO, "the disk failed"), "move_failed"),
])
@pytest.mark.parametrize("step", ["_copy_aside", "_make_folders", "_write_record", "_claim", "_put_in_place"])
def test_a_step_that_fails_is_worded_and_the_file_stays(world: Files, monkeypatch, step, failure, code) -> None:
    w = world
    w.put(w.mine, "notes/trip.txt")

    def fails(*_a: Any, **_k: Any) -> Any:
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, step, fails)
        assert w.refused("notes/trip.txt", w.mine, w.family) == code
    assert w.where() == ["own:notes/trip.txt"] and w.held() == []
    assert w.maker(w.family, "notes/trip.txt") is None
    assert not (w.root(w.family) / "notes").exists(), "an empty folder stays"
    w.move("notes/trip.txt", w.mine, w.family)
    assert w.where() == ["family:notes/trip.txt"]


@pytest.mark.skipif(sys.platform != "win32", reason="an open file is not renamed there")
def test_an_open_file_is_in_use(world: Files) -> None:
    w = world
    path = w.put(w.mine, "trip.txt")
    with open(path, "rb"):
        assert w.refused("trip.txt", w.mine, w.family) == "in_use"
    assert w.where() == ["own:trip.txt"] and w.held() == []


# ── one move of a file at a time, two at once ─────────────────────────────


def test_one_move_of_a_file_and_two_moves_at_once(world: Files) -> None:
    w = world
    for name in ("a.txt", "b.txt", "c.txt"):
        w.put(w.mine, name, name.encode())
    with file_moves._under_way("1" * 32, w.mine, w.family, "a.txt"):
        assert w.refused("a.txt", w.mine, w.family) == "move_in_progress"
        assert w.refused("b.txt", w.mine, w.family) == "moved"
        with file_moves._under_way("2" * 32, w.mine, w.family, "c.txt"):
            assert w.refused("b.txt", w.mine, w.family) in ("busy", "not_found")
            w.put(w.mine, "d.txt", b"d")
            assert w.refused("d.txt", w.mine, w.family) == "busy"
    assert w.refused("a.txt", w.mine, w.family) == "moved"


# ── only plain spaces ─────────────────────────────────────────────────────


def test_only_a_plain_personal_space_and_a_plain_shared_space(world: Files, tmp_path) -> None:
    w = world
    project = tmp_path / "project"
    project.mkdir()
    repo = people.create_shared_space("Repo", kind="host", project_folder=str(project), by=OWNER_ACCOUNT_ID)["id"]
    people.add_member(repo, w.reem, by=OWNER_ACCOUNT_ID)
    w.put(w.mine, "trip.txt")
    assert w.refused("trip.txt", w.mine, repo) == "project_folder"
    assert w.refused("trip.txt", OWNER_SPACE_ID, w.family, by=OWNER_ACCOUNT_ID) == "owners_space"
    assert w.refused("trip.txt", w.mine, w.omars) == "not_yours"
    assert w.refused("trip.txt", w.mine, w.mine) == "not_yours"
    second = people.create_shared_space("Second", by=OWNER_ACCOUNT_ID)["id"]
    people.add_member(second, w.reem, by=OWNER_ACCOUNT_ID)
    w.share("shared.txt", b"s")
    assert w.refused("shared.txt", w.family, second) == "not_between_own_and_shared"
    assert w.where() == ["own:trip.txt"] and list(project.iterdir()) == []
    assert shared_files.list_files(repo, by=w.reem)["available"] is False
    assert shared_files.list_files(OWNER_SPACE_ID, by=OWNER_ACCOUNT_ID)["available"] is False


def test_with_spaces_off_nothing_moves_and_nothing_is_made(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, "trip.txt")
    before = sorted(p.as_posix() for p in space_dir(w.mine).parent.parent.rglob("*"))
    monkeypatch.setattr(scope, "_enforced", False)
    with pytest.raises(file_moves.FileMoveRefused) as off:
        file_moves.move_file("trip.txt", from_space=w.mine, to_space=w.family, by=w.reem, max_bytes=BIG)
    assert off.value.code == "spaces_off"
    assert shared_files.list_files(w.mine, by=w.reem)["available"] is False
    assert file_moves.settle_file_moves() == []
    file_moves.sweep()
    assert sorted(p.as_posix() for p in space_dir(w.mine).parent.parent.rglob("*")) == before


def test_no_store_is_made_until_a_file_is_shared(world: Files) -> None:
    from kazma_core.paths import data_dir

    w = world
    w.put(w.mine, "trip.txt")
    shared_files.list_files(w.mine, by=w.reem)
    file_moves.sweep()
    w.refused("trip.txt", w.mine, w.omars)
    assert not (data_dir() / shared_files.STORE).exists()
    w.move("trip.txt", w.mine, w.family)
    assert (data_dir() / shared_files.STORE).exists()


# ── the listing ───────────────────────────────────────────────────────────


def test_the_listing_is_bounded_pages_and_leaves_hidden_names(world: Files, monkeypatch) -> None:
    w = world
    for n in range(120):
        w.put(w.mine, f"note-{n:03d}.txt", b"n")
    w.put(w.mine, ".hidden", b"h")
    w.put(w.mine, ".git/config", b"h")
    first = shared_files.list_files(w.mine, by=w.reem, limit=50)
    assert first["total"] == 120 and len(first["files"]) == 50 and first["available"] and not first["cut"]
    assert shared_files.list_files(w.mine, by=w.reem, offset=100)["files"][-1]["path"] == "note-119.txt"
    assert len(shared_files.list_files(w.mine, by=w.reem, limit=1000)["files"]) == shared_files.PAGE_MAX
    monkeypatch.setattr(shared_files, "SCAN_CAP", 10)
    cut = shared_files.list_files(w.mine, by=w.reem)
    assert cut["cut"] is True and cut["total"] == 10
    monkeypatch.setattr(shared_files, "SCAN_CAP", 5000)
    monkeypatch.setattr(shared_files, "_FOLDER_CAP", 3)
    for n in range(10):
        (w.root(w.mine) / f"empty-{n}").mkdir()
    assert shared_files.list_files(w.mine, by=w.reem)["cut"] is True


def test_a_folder_that_cannot_be_read_is_said(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, "a.txt")
    w.put(w.mine, "closed/b.txt")
    real = os.scandir
    closed = {"closed"}

    def refuses(path: Any) -> Any:
        if Path(path).name in closed:
            raise PermissionError("not yours")
        return real(path)

    monkeypatch.setattr(os, "scandir", refuses)
    part = shared_files.list_files(w.mine, by=w.reem)
    assert part["cut"] is True and [f["path"] for f in part["files"]] == ["a.txt"]
    closed.add(FILES)
    with pytest.raises(shared_files.SharedFilesUnreadable):
        shared_files.list_files(w.mine, by=w.reem)


# ── the maker of a file that is gone ──────────────────────────────────────


def test_the_sweep_forgets_the_maker_of_a_file_that_is_gone(world: Files, monkeypatch) -> None:
    w = world
    w.share("gone.txt")
    w.share("stays.txt", b"stays")
    (w.root(w.family) / "gone.txt").unlink()
    file_moves.sweep()
    assert w.maker(w.family, "gone.txt") is not None, "within the hour it is not gone"
    real = time.time
    monkeypatch.setattr(shared_files.time, "time", lambda: real() + 7200)
    with file_moves._under_way("1" * 32, w.mine, w.family, "x.txt"):
        file_moves.sweep()
        assert w.maker(w.family, "gone.txt") is not None, "a file being moved is not gone"
    file_moves.sweep()
    assert w.maker(w.family, "gone.txt") is None
    assert w.maker(w.family, "stays.txt") == (w.reem, digest(b"stays"))


def test_a_spaces_lines_are_bounded(world: Files, monkeypatch) -> None:
    w = world
    monkeypatch.setattr(shared_files, "_KEPT_PER_SPACE", 2)
    for n in range(3):
        w.share(f"{n}.txt", str(n).encode())
        w.move(f"{n}.txt", w.family, w.mine)
    assert [path for path, _by in w.lines()] == ["2.txt", "1.txt"]


# ── the routes ────────────────────────────────────────────────────────────


@pytest.fixture
def client(world: Files, monkeypatch):
    who = {"reem": world.reem, "omar": world.omar}
    monkeypatch.setattr(auth, "get_request_principal", lambda request: {
        "source": "session", "role": "operator", "actor": "login",
        "user_id": who.get(request.headers.get("x-person", ""), "")})
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())
    app.include_router(create_space_files_router())
    with TestClient(app) as made:
        yield made


def _call(client: TestClient, who: str, method: str, path: str, space: str, **kwargs: Any) -> Any:
    return client.request(method, path, headers={"x-person": who, space_binding.SPACE_HEADER: space}, **kwargs)


def test_the_routes_move_and_list(world: Files, client: TestClient) -> None:
    w = world
    w.put(w.mine, "trip.txt")
    moved = _call(client, "reem", "POST", "/api/space-files/move", w.mine,
                  json={"path": "trip.txt", "to_space": w.family})
    assert moved.status_code == 200 and moved.json()["taken_out"] is False, moved.text
    mine = _call(client, "reem", "GET", "/api/space-files", w.family).json()
    theirs = _call(client, "omar", "GET", "/api/space-files", w.family).json()
    assert mine["files"][0]["mine"] is True and theirs["files"][0]["mine"] is False
    back = _call(client, "reem", "POST", "/api/space-files/move", w.family,
                 json={"path": "trip.txt", "to_space": w.mine})
    assert back.status_code == 200, back.text
    line = _call(client, "omar", "GET", "/api/space-files", w.family).json()["taken_out"][0]
    assert line["path"] == "trip.txt" and line["by_name"] == "Reem" and line["at"]


def test_every_refusal_is_a_code_the_page_words(world: Files, client: TestClient) -> None:
    from kazma_ui.i18n.catalog.documents import TRANSLATIONS

    source = (REPO / "kazma-core" / "kazma_core" / "spaces" / "file_moves.py").read_text(encoding="utf-8")
    moves = (REPO / "kazma-core" / "kazma_core" / "spaces" / "moves.py").read_text(encoding="utf-8")

    def codes(text: str, name: str) -> set[str]:
        return {str(node.args[0].value) for node in ast.walk(ast.parse(text))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
                and node.args and isinstance(node.args[0], ast.Constant)}

    refused = codes(source, "FileMoveRefused") | codes(moves, "MoveRefused")
    assert len(refused) > 15 and refused <= set(REFUSALS), refused - set(REFUSALS)
    for code in (*REFUSALS, "failed"):
        words = TRANSLATIONS[f"documents.files.err.{code}"]
        assert words["en"] and words["ar"], code
    assert codes("FileMoveRefused('planted', 409, 'x')", "FileMoveRefused") - set(REFUSALS) == {"planted"}
    got = _call(client, "reem", "POST", "/api/space-files/move", world.mine,
                json={"path": "none.txt", "to_space": world.family})
    assert got.status_code == 404 and got.json() == {"ok": False, "error": "not_found", "code": "not_found"}


def test_a_list_that_cannot_be_read_is_never_empty(world: Files, client: TestClient, monkeypatch) -> None:
    def unreadable(*_a: Any, **_k: Any) -> Any:
        raise shared_files.SharedFilesUnreadable

    monkeypatch.setattr("kazma_ui.space_files_api.list_files", unreadable)
    got = _call(client, "reem", "GET", "/api/space-files", world.mine)
    assert got.status_code == 503 and got.json()["ok"] is False


def test_the_upload_limit_is_the_routes(world: Files, client: TestClient, monkeypatch) -> None:
    from kazma_ui.chat_attachments import MAX_UPLOAD_BYTES

    assert MAX_UPLOAD_BYTES == BIG
    world.put(world.mine, "large.bin", b"x" * 64)
    monkeypatch.setattr("kazma_ui.space_files_api.MAX_UPLOAD_BYTES", 32)
    got = _call(client, "reem", "POST", "/api/space-files/move", world.mine,
                json={"path": "large.bin", "to_space": world.family})
    assert got.status_code == 413 and got.json()["code"] == "too_large"


@pytest.mark.real_features
def test_with_documents_off_there_is_no_panel_and_no_route(world: Files, client: TestClient) -> None:
    w = world
    w.put(w.mine, "trip.txt")
    people.set_features(w.reem, {"documents": False}, by=OWNER_ACCOUNT_ID)
    listed = _call(client, "reem", "GET", "/api/space-files", w.mine)
    moved = _call(client, "reem", "POST", "/api/space-files/move", w.mine,
                  json={"path": "trip.txt", "to_space": w.family})
    assert listed.status_code == 403 and listed.json().get("code") == "feature_off", listed.text
    assert moved.status_code == 403 and w.where() == ["own:trip.txt"]


def test_the_routes_are_in_the_documents_feature() -> None:
    from kazma_ui import route_scopes

    for path in ("/api/space-files", "/api/space-files/move"):
        assert "documents" in route_scopes.features_of_route(path), path


# ── nothing but the page's route moves a file, and off the loop ───────────


def _names(text: str, name: str) -> bool:
    return any((isinstance(node, ast.Name) and node.id == name)
               or (isinstance(node, ast.Attribute) and node.attr == name) for node in ast.walk(ast.parse(text)))


def _movers(sources: dict[str, str]) -> list[str]:
    """Every product file that calls ``move_file`` or hands it on (an import
    alone is not a call)."""
    return sorted(rel for rel, text in sources.items()
                  if "move_file" in text and _names(text, "move_file") and not rel.endswith("spaces/file_moves.py"))


def test_only_the_pages_route_moves_a_file() -> None:
    sources = {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8", errors="replace")
               for pkg in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                           "kazma-skills/kazma_skills", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui")
               for p in (REPO / pkg).rglob("*.py")}
    assert len(sources) > 1000
    assert _movers(sources) == ["kazma-ui/kazma_ui/space_files_api.py"]


def test_control_a_tool_that_moves_a_file_is_caught() -> None:
    planted = {"kazma-skills/kazma_skills/native/files/tools.py": "move_file(path, from_space=a, to_space=b)\n"}
    assert _movers(planted) == ["kazma-skills/kazma_skills/native/files/tools.py"]


def test_the_move_and_the_settling_run_off_the_loop(world: Files, client: TestClient, monkeypatch) -> None:
    on_loop: list[bool] = []

    def noted(*_a: Any, **_k: Any) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return {"path": "x", "space": "y", "taken_out": False}

    monkeypatch.setattr("kazma_ui.space_files_api.move_file", noted)
    assert _call(client, "reem", "POST", "/api/space-files/move", world.mine,
                 json={"path": "x", "to_space": world.family}).status_code == 200
    assert on_loop == [False]
    app = (REPO / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(settle_file_moves)" in app
    from kazma_core.memory import worker_bootstrap

    assert "file moves" in dict(worker_bootstrap._MAINTENANCE_SWEEPS)


def test_no_folder_of_a_move_is_a_space_folder_anything_reads(world: Files) -> None:
    w = world
    held = moving_dir(w.family) / ("a" * 32)
    held.mkdir(parents=True)
    (held / "copy").write_bytes(b"held")
    assert files.is_space_files_path(held / "copy") is False
    assert "held" not in str(shared_files.list_files(w.family, by=w.reem))
    for space, person in ((w.mine, w.reem), (w.family, w.reem), (OWNER_SPACE_ID, OWNER_ACCOUNT_ID)):
        with scope.bind_scope(space, person):
            assert files.another_spaces_path((held / "copy").resolve()) is True
