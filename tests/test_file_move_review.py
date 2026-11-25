"""Spaces S2-12, piece 7: the independent review of the file move (25 November
2026), each finding as the sequence that was run, through the real tool, the
real route or the real file system.

H1: a space's own file tools read, wrote and forged what a move held inside
that space's folder; what lies in the holding folder is believed no further
than its space. H2: one record nobody wrote stopped everybody's moves,
exports, erases and half of the start. M1 and M2: a stop inside the placing,
and a disk with no hard links. M3: a read-only file. M4: a person erased while
their file was held. M5: a copy that something else removed. Then the smaller
ones.
"""

from __future__ import annotations

import ast
import asyncio
import errno
import json
import logging
import os
import stat
import sys
import threading
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import chat_moves, file_moves, identity, moves, people, scope, shared_files, space_data
from kazma_core.spaces.files import FILES, moving_dir, space_dir
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_ui import auth, space_binding
from kazma_ui.space_files_api import create_space_files_router

from tests import _files_space_cases as path_cases
from tests._file_move_world import PLAN, Files, Killed, digest, files_world

REPO = Path(__file__).resolve().parents[1]
REL = "notes/trip.txt"
ATTEMPT = "a" * 32
POSIX = pytest.mark.skipif(os.name == "nt", reason="Windows places by a rename that never replaces")


@pytest.fixture
def world(monkeypatch, tmp_path):
    with files_world(monkeypatch, tmp_path) as w:
        yield w


def _tool(space: str, person: str, name: str, **kwargs: Any) -> str:
    """The registered tool, as the agent calls it, in *person*'s turn."""
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect

    from tests._chat_space_cases import _registry

    tool = _registry().get_tool(name)
    assert tool is not None, name

    async def call() -> str:
        with scope.bind_scope(space, person):
            try:
                return str(await tool.func(**kwargs))
            except RefusedBeforeEffect as refused:
                return str(refused)

    return asyncio.run(call())


def _from_files(w: Files, space: str, place: Path) -> str:
    """*place* as a tool of *space* would type it."""
    w.root(space).mkdir(parents=True, exist_ok=True)
    return os.path.relpath(place, w.root(space)).replace("\\", "/")


def _record(w: Files, **given: Any) -> dict[str, Any]:
    """The record of a move of Reem's, each field as this code writes it."""
    record: dict[str, Any] = {
        "attempt": ATTEMPT, "source": w.mine, "target": w.family, "path": "trip.txt", "target_path": "trip.txt",
        "by": w.reem, "sha256": digest(PLAN), "size": len(PLAN), "taken_out": False, "shared_by_attempt": "",
        "shared_at": 0.0, "made": [], "copy_id": [], "source_id": [], **given,
    }
    known = {f.name for f in fields(file_moves._Move)}
    return {key: value for key, value in record.items() if key in known}


def _plant(space: str, record: Any, *, attempt: str = ATTEMPT, claimed: bytes | None = None) -> Path:
    folder = moving_dir(space) / attempt
    folder.mkdir(parents=True)
    (folder / "move.json").write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
    if claimed is not None:
        (folder / "claimed").write_bytes(claimed)
    return folder


def _ids(path: Path) -> list[int]:
    info = os.lstat(path)
    return [int(info.st_ino), int(info.st_mtime_ns)]


def _cut(w: Files, monkeypatch, step: str = "_put_in_place", when: str = "before") -> None:
    """A share of Reem's the process stopped in, at *step*."""
    real = getattr(file_moves, step)

    def killed(*args: Any, **kwargs: Any) -> Any:
        if when == "after":
            real(*args, **kwargs)
        raise Killed

    w.put(w.mine, REL)
    with monkeypatch.context() as patch:
        patch.setattr(file_moves, step, killed)
        with pytest.raises(Killed):
            w.move(REL, w.mine, w.family)


def _junction(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


# ── H1: no tool of a space reaches what a move holds ──────────────────────


_MARK = "heldwordsmark"


@pytest.mark.parametrize("tool_name", sorted(path_cases._PATH_TOOLS))
@pytest.mark.parametrize("whose", ["the copy a move holds", "an import's mark"])
def test_no_path_tool_of_a_space_opens_kazmas_own_beside_its_files(world: Files, monkeypatch, tool_name, whose):
    """Every tool that takes a path, pointed at what Kazma keeps beside the
    bound space's files: shows nothing of it and changes nothing."""
    from kazma_core.documents.service import DocumentService

    async def no_worker(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("stand-in: no document worker here")

    for name in ("generate", "parse_transient", "convert", "mutate", "ocr_transient_within", "read_with_ocr_budget"):
        if hasattr(DocumentService, name):
            monkeypatch.setattr(DocumentService, name, no_worker)
    w = world
    planted, kwargs = path_cases._PATH_TOOLS[tool_name]
    if whose == "the copy a move holds":
        folder = moving_dir(w.family) / ATTEMPT
    else:
        folder = space_dir(w.family) / ".kazma-arriving"
    folder.mkdir(parents=True)
    path_cases._plant(folder, _MARK)
    before = path_cases._tree(folder)
    place = folder / planted if planted else folder
    for typed in (_from_files(w, w.family, place), str(place), _from_files(w, w.family, place).replace("/", "\\")):
        try:
            shown = _tool(w.family, w.omar, tool_name, **kwargs(typed))
        except Exception as exc:  # noqa: BLE001 -- a stand-in's own error shows nothing
            shown = f"{type(exc).__name__}: {exc}"
        assert _MARK not in shown and (planted or "data.db" not in shown), (typed, shown[:400])
        assert path_cases._tree(folder) == before, (typed, "it was changed")


def test_every_folder_beside_a_spaces_own_is_kazmas(world: Files) -> None:
    """The predicate names what is the space's, never what is Kazma's: a
    folder Kazma adds later is refused the day it is added."""
    from kazma_core import store_registry
    from kazma_core.spaces import files

    w = world
    home = space_dir(w.family)
    with scope.bind_scope(w.family, w.reem):
        for name in (FILES, *files.FOLDER_KINDS):
            assert not store_registry.is_kazma_store(home / name / "x.txt", write=True), name
        assert not store_registry.is_kazma_store(home)
        for name in (".kazma-arriving", "moving/x", "added-later/x", "Moving/x", "files.old/x"):
            for write in (True, False):
                assert store_registry.is_kazma_store(home / name, write=write), name
        for place in (moving_dir(w.family) / ATTEMPT / "copy", moving_dir(w.mine) / "x", home.parent / "x.moving-1"):
            assert store_registry.is_kazma_store(place), place
        assert "Kazma's own" in store_registry.store_refusal(home / "added-later" / "x", door="file tools")


def _written_under_a_space(text: str) -> set[str]:
    """What a module joins to a space's folder: a name written there, or the
    name that holds it."""
    found = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.left, ast.Call):
            called = node.left.func
            if getattr(called, "id", getattr(called, "attr", "")) == "space_dir":
                found.add(str(node.right.value) if isinstance(node.right, ast.Constant) else f"<{ast.unparse(node.right)}>")
    return found


def test_what_kazma_writes_under_a_spaces_folder_is_the_spaces() -> None:
    """Every name Kazma joins to a space's folder, from the source: the
    working folder, or one of ``FOLDER_KINDS`` (``space_folder`` checks it).
    Anything else is refused to the space's tools by the predicate above."""
    found: set[str] = set()
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-skills/kazma_skills"):
        for path in (REPO / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "space_dir" in text:
                found |= _written_under_a_space(text)
    assert found == {"<FILES>", "<files.FILES>", "<kind>"}, found
    assert _written_under_a_space("space_dir(s) / 'moving' / x\nfiles.space_dir(s) / A\n") == {"moving", "<A>"}


def _forge(w: Files, record: dict[str, Any]) -> None:
    """Reem's agent writes a record of a move into her own space's holding
    folder, with the file tool."""
    folder = _from_files(w, w.mine, moving_dir(w.mine) / ATTEMPT)
    _tool(w.mine, w.reem, "file_write", path=f"{folder}/move.json", content=json.dumps(record))


def test_a_forged_record_makes_nobody_the_maker_of_anothers_file(world: Files) -> None:
    w = world
    w.put(w.family, "omar.txt", PLAN)
    _forge(w, _record(w, target_path="omar.txt", copy_id=_ids(w.root(w.family) / "omar.txt")))
    file_moves.settle_file_moves()
    assert w.maker(w.family, "omar.txt") is None, "she is the maker of Omar's file"
    assert w.refused("omar.txt", w.family, w.mine) == "not_the_sharer"
    assert w.where() == ["family:omar.txt"]


def test_a_forged_record_says_nothing_of_anothers_own_space(world: Files) -> None:
    """Planted on the disk itself: what lies in the holding folder is believed
    no further than a move there can be."""
    w = world
    private = w.put(w.omars, "private.txt", PLAN)
    (w.root(w.omars) / "Plans").mkdir()
    omars = _ids(private)
    for n, record in enumerate((
        _record(w, target=w.omars, target_path="private.txt", copy_id=omars, made=["Plans"]),
        _record(w, target=w.omars, target_path="private.txt", copy_id=omars, taken_out=True),
        _record(w, source=w.mine, target=w.family, by=w.omar, target_path="private.txt", made=["Plans"]),
        _record(w, target="default", target_path="private.txt"),
    )):
        attempt = f"{n}" * 32
        _plant(w.mine, {**record, "attempt": attempt}, attempt=attempt, claimed=PLAN)
    file_moves.settle_file_moves()
    assert w.maker(w.omars, "private.txt") is None and w.lines() == []
    assert (w.root(w.omars) / "Plans").is_dir(), "a folder of Omar's own space was removed"
    # A guess is never answered: the claimed bytes are back in her own space each time.
    assert len([p for p in w.where() if p.startswith("own:moved file ")]) == 4 and w.held() == []


def test_a_line_of_a_file_taken_out_is_not_named_by_its_move(world: Files) -> None:
    """The page gets an id for each line: no move's, so no record can name the
    line to be taken back."""
    w = world
    w.share("trip.txt")
    w.move("trip.txt", w.family, w.mine)
    line = shared_files.list_files(w.family, by=w.omar)["taken_out"][0]
    _plant(w.family, _record(w, attempt=line["id"], source=w.family, target=w.mine, path="x.txt", target_path="x.txt",
                             taken_out=True), attempt=line["id"])
    file_moves.settle_file_moves()
    assert w.lines() == [("trip.txt", w.reem)], "Family's line is gone"


def test_a_file_at_the_holding_folders_place_stops_no_share(world: Files) -> None:
    w = world
    _tool(w.family, w.omar, "file_write", path=_from_files(w, w.family, moving_dir(w.family)), content="x")
    w.put(w.mine, REL)
    assert w.refused(REL, w.mine, w.family) == "moved"


@pytest.mark.parametrize("level", ["all spaces'", "the space's", "the move's"])
@pytest.mark.parametrize("what", ["a link", "a file"])
def test_the_holding_folder_is_never_looked_for_where_a_link_leads(world: Files, tmp_path, level, what) -> None:
    w = world
    outside = tmp_path / "outside"
    inner = {"all spaces'": f"{w.mine}/{ATTEMPT}", "the space's": ATTEMPT, "the move's": ""}[level]
    (outside / inner).mkdir(parents=True)
    (outside / inner / "move.json").write_text(json.dumps(_record(w)), encoding="utf-8")
    (outside / inner / "claimed").write_bytes(b"a file of this computer")
    place = {"all spaces'": moving_dir(w.mine).parent, "the space's": moving_dir(w.mine),
             "the move's": moving_dir(w.mine) / ATTEMPT}[level]
    place.parent.mkdir(parents=True, exist_ok=True)
    if what == "a link":
        _junction(place, outside)
    else:
        place.write_bytes(b"not a folder")
    file_moves.settle_file_moves()
    assert (outside / inner / "claimed").exists(), "what a link leads to was taken for a held file"
    assert not [p for p in w.where(b"a file of this computer") if p != "held"]  # seen through the link
    if level != "the move's":
        w.put(w.mine, REL)
        assert w.refused(REL, w.mine, w.family) == "move_failed"
        assert w.where() == [f"own:{REL}"]


# ── H2: one record nobody wrote stops nobody ──────────────────────────────


POISON: dict[str, Any] = {
    "deep": "[" * 200_000,
    "long": " " * (3 * 1024 * 1024) + "{}",
    "not text": "\ud800",
    "a list": "[1]",
    "a size": {"size": "x"},
    "a digest": {"sha256": ["x"]},
    "a path": {"path": ["x"]},
    "a place": {"target_path": 7},
    "folders": {"made": [["x"]]},
    "a person": {"by": {"a": 1}},
    "a time": {"shared_at": "soon", "taken_out": True},
    "out": {"taken_out": "yes"},
    "a number": {"size": 10**400},
}


@pytest.mark.parametrize("name", sorted(POISON))
def test_one_record_nobody_wrote_stops_no_one(world: Files, tmp_path, caplog, name) -> None:
    w = world
    poison = POISON[name]
    record = poison if isinstance(poison, str) else {**_record(w, target_path=REL), **poison}
    folder = moving_dir(w.mine) / ATTEMPT
    folder.mkdir(parents=True)
    text = record if isinstance(record, str) else json.dumps(record)
    (folder / "move.json").write_bytes(text.encode("utf-8", "surrogatepass"))
    w.put(w.family, REL, PLAN)
    with caplog.at_level(logging.WARNING, logger="kazma_core.spaces.file_moves"):
        for _ in range(5):
            file_moves.settle_file_moves()
        file_moves.sweep()
        w.put(w.omars, "omar.txt", b"Omar's")
        assert w.refused("omar.txt", w.omars, w.family, by=w.omar) == "moved"
        space_data.export_space(w.family, tmp_path / "family.zip")
        other = people.create_shared_space("Other", by=OWNER_ACCOUNT_ID)["id"]
        space_data.erase_space(other, by=OWNER_ACCOUNT_ID)
    said = [r for r in caplog.records if "file move" in r.getMessage()]
    assert len(said) <= 1, "it is said at every try"
    assert w.maker(w.family, REL) is None


def test_a_move_nothing_expected_is_worded_and_ended(world: Files, monkeypatch) -> None:
    """A step that fails in a way nobody thought of: an answer with words, and
    the file where it was."""
    w = world
    w.put(w.mine, REL)

    def fails(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("nobody thought of this")

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_put_in_place", fails)
        assert w.refused(REL, w.mine, w.family) == "move_failed"
    assert w.where() == [f"own:{REL}"] and w.held() == []


def test_one_move_that_fails_unexpectedly_stops_no_erase(world: Files, monkeypatch) -> None:
    w = world
    _cut(w, monkeypatch)

    def fails(_move: Any) -> str:
        raise ValueError("nobody thought of this")

    other = people.create_shared_space("Other", by=OWNER_ACCOUNT_ID)["id"]
    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_end", fails)
        space_data.erase_space(other, by=OWNER_ACCOUNT_ID)
    assert not space_dir(other).exists() and w.where() == ["held"]
    file_moves.settle_file_moves()
    assert w.where() == [f"own:{REL}"]


def test_the_start_goes_on_whatever_the_settling_does() -> None:
    """The call at start is in a try of its own that says it and goes on: it
    was inside the one whose handler skips turn resume, the watchdog, the run
    supervisor and the gateway's handler."""
    source = (REPO / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")
    tries = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Try)
             and "to_thread(settle_file_moves)" in ast.unparse(node) and len(node.body) <= 2]
    assert len(tries) == 1
    handlers = tries[0].handlers
    assert [ast.unparse(h.type) for h in handlers if h.type is not None] == ["Exception"]
    said = ast.unparse(handlers[0])
    assert "logger.warning" in said and "raise" not in said


# ── M1, M2: inside the placing ────────────────────────────────────────────


def _no_atomic_rename(patch: pytest.MonkeyPatch) -> None:
    """A POSIX system or disk with no rename that never replaces."""
    patch.setattr(file_moves, "_rename_call", lambda: None, raising=False)


def _kill_in_place(patch: pytest.MonkeyPatch, nth: int, when: str) -> None:
    """The process stops in the *nth* placing: before its file-system call,
    after it and (a hard link) before the old name goes, or at its end."""
    if os.name != "nt":
        _no_atomic_rename(patch)
    real_place = file_moves._place
    calls: list[Path] = []

    def place(staged: Path, where: Path) -> bool:
        calls.append(where)
        if len(calls) != nth:
            return real_place(staged, where)
        with pytest.MonkeyPatch.context() as inner:
            call = "rename" if os.name == "nt" else "link"
            real_call, real_unlink = getattr(os, call), Path.unlink

            def killed(*args: Any, **kwargs: Any) -> Any:
                if when == "before":
                    raise Killed
                done = real_call(*args, **kwargs)
                if when == "after" and os.name == "nt":
                    raise Killed
                return done

            def unlinked(path: Path, *args: Any, **kwargs: Any) -> None:
                if path == staged and when == "after":
                    raise Killed
                real_unlink(path, *args, **kwargs)
                if path == staged and when == "at its end":
                    raise Killed

            inner.setattr(os, call, killed)
            inner.setattr(Path, "unlink", unlinked)
            return real_place(staged, where)

    patch.setattr(file_moves, "_place", place)


def _one_file(w: Files, places: tuple[str, ...]) -> None:
    assert w.held() == [], "something is still held"
    assert w.where() in [[place] for place in places], w.where()
    space, rel = w.where()[0].split(":", 1)
    path = w.root({"own": w.mine, "family": w.family}[space]) / rel
    assert os.lstat(path).st_nlink == 1, "it has a second name: it can never move again"


WHEN = ["before", "after"] if os.name == "nt" else ["before", "after", "at its end"]


@pytest.mark.parametrize("when", WHEN)
@pytest.mark.parametrize("nth", [1, 2])
def test_a_stop_inside_the_placing_leaves_one_file(world: Files, monkeypatch, nth, when) -> None:
    """The copy gets its name, then its place in the target."""
    w = world
    w.put(w.mine, REL)
    with monkeypatch.context() as patch:
        _kill_in_place(patch, nth, when)
        with pytest.raises(Killed):
            w.move(REL, w.mine, w.family)
    file_moves.settle_file_moves()
    _one_file(w, (f"own:{REL}", f"family:{REL}"))
    back = w.where()[0].startswith("own:")
    w.move(REL, *((w.mine, w.family) if back else (w.family, w.mine)))
    _one_file(w, (f"family:{REL}",) if back else (f"own:{REL}",))


@pytest.mark.parametrize("when", WHEN)
@pytest.mark.parametrize("taken", [False, True])
def test_a_stop_inside_the_giving_back_leaves_one_file(world: Files, monkeypatch, taken, when) -> None:
    """Also where the name is taken and it goes beside it."""
    w = world
    _cut(w, monkeypatch)
    if taken:
        w.put(w.mine, REL, b"a new file at its name")
    with monkeypatch.context() as patch:
        _kill_in_place(patch, 2 if taken else 1, when)
        with pytest.raises(Killed):
            file_moves.settle_file_moves()
    for _ in range(2):
        file_moves.settle_file_moves()
    _one_file(w, ("own:notes/trip (2).txt",) if taken else (f"own:{REL}",))


@POSIX
def test_a_held_name_that_cannot_go_yet_goes_later(world: Files, monkeypatch) -> None:
    """Given back as a second name, and the first cannot be removed now."""
    w = world
    _cut(w, monkeypatch)
    real = Path.unlink

    def refused(path: Path, *args: Any, **kwargs: Any) -> None:
        if path.name == "claimed":
            raise OSError(errno.EIO, "not now")
        real(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        _no_atomic_rename(patch)
        patch.setattr(Path, "unlink", refused)
        file_moves.settle_file_moves()
    file_moves.settle_file_moves()
    _one_file(w, (f"own:{REL}",))


@POSIX
def test_a_disk_that_cannot_place_without_replacing_is_refused(world: Files, monkeypatch) -> None:
    """No hard links and no rename that never replaces: nothing is claimed,
    nothing is made under a person's name, and it is worded."""
    from kazma_ui.i18n.catalog.documents import TRANSLATIONS

    def no_links(*_a: Any, **_k: Any) -> None:
        raise PermissionError(errno.EPERM, "no hard links here")

    w = world
    w.put(w.mine, REL)
    with monkeypatch.context() as patch:
        _no_atomic_rename(patch)
        patch.setattr(file_moves.os, "link", no_links)
        assert w.refused(REL, w.mine, w.family) == "move_failed"
    assert w.where() == [f"own:{REL}"] and w.held() == []
    assert not (w.root(w.family) / "notes").exists()
    assert "where it was" in TRANSLATIONS["documents.files.err.move_failed"]["en"]


def test_no_move_places_with_the_imports_helper() -> None:
    source = (REPO / "kazma-core" / "kazma_core" / "spaces" / "file_moves.py").read_text(encoding="utf-8")
    assert "_claim_and_replace" not in source, "an empty file that claims a name is replaced later"


@POSIX
def test_the_systems_rename_never_replaces(tmp_path) -> None:
    if file_moves._rename_call() is None:
        pytest.skip("this system has none")
    (tmp_path / "a").write_bytes(b"a")
    (tmp_path / "b").write_bytes(b"b")
    done = file_moves._renamed_new(tmp_path / "a", tmp_path / "b")
    if done is None:
        pytest.skip("not on this disk")
    assert done is False and (tmp_path / "b").read_bytes() == b"b"
    assert file_moves._renamed_new(tmp_path / "a", tmp_path / "c") is True
    with pytest.raises(FileNotFoundError):
        file_moves._renamed_new(tmp_path / "none", tmp_path / "d")


# ── M3: a read-only file ──────────────────────────────────────────────────


def test_a_read_only_file_moves_to_its_end_both_ways(world: Files) -> None:
    w = world
    path = w.put(w.mine, REL)
    os.chmod(path, stat.S_IREAD)
    assert w.refused(REL, w.mine, w.family) == "moved"
    assert w.where() == [f"family:{REL}"] and w.held() == []
    os.chmod(w.root(w.family) / REL, stat.S_IREAD)
    assert w.refused(REL, w.family, w.mine) == "moved"
    assert w.where() == [f"own:{REL}"] and w.held() == []


# ── M4: a person erased while their file is held ──────────────────────────


def _everything_of(space: str) -> bytes:
    roots = (space_dir(space), moving_dir(space))
    return b"".join(p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("closed_first", [False, True])
def test_a_person_erased_leaves_no_words_of_a_share_cut_off(world: Files, monkeypatch, closed_first) -> None:
    w = world
    _cut(w, monkeypatch)
    if closed_first:  # an erase that stopped, run again; the file is read-only
        os.chmod(next(moving_dir(w.mine).glob("*/claimed")), stat.S_IREAD)
        people.delete_person(w.reem, by=OWNER_ACCOUNT_ID)
        people.close_space(w.mine, by=OWNER_ACCOUNT_ID)
        space_data.erase_space(w.mine, by=OWNER_ACCOUNT_ID)
        assert PLAN not in _everything_of(w.family) and w.where() == [] and w.held() == []
        return
    people.delete_person(w.reem, by=OWNER_ACCOUNT_ID)
    space_data.erase_space(w.mine, by=OWNER_ACCOUNT_ID)
    assert PLAN not in _everything_of(w.family), "her words are still in the shared space"
    assert not (w.root(w.family) / "notes").exists(), "a folder of hers is in the shared space"
    assert w.where() == [] and w.held() == []
    assert not space_dir(w.mine).exists() and not moving_dir(w.mine).exists()


def test_a_share_cut_off_as_its_space_closes_leaves_nothing_there(world: Files, monkeypatch, caplog) -> None:
    """Closed before it could be ended: the target keeps nothing, the file
    goes with its space, it is said, and a second settling does the same."""
    w = world
    _cut(w, monkeypatch)
    people.delete_person(w.reem, by=OWNER_ACCOUNT_ID)
    people.close_space(w.mine, by=OWNER_ACCOUNT_ID)
    with caplog.at_level(logging.WARNING, logger="kazma_core.spaces.file_moves"):
        for _ in range(3):
            file_moves.settle_file_moves()
    assert PLAN not in _everything_of(w.family) and not (w.root(w.family) / "notes").exists()
    assert w.where() == ["held"], "the erase has it"
    assert len([r for r in caplog.records if "being erased" in r.getMessage()]) == 1, "it is silent, or said each time"
    space_data.erase_space(w.mine, by=OWNER_ACCOUNT_ID)
    assert w.where() == [] and w.held() == []


# ── M5: a copy something else removed ─────────────────────────────────────


def test_a_copy_that_is_gone_is_no_proof_that_it_was_placed(world: Files, monkeypatch) -> None:
    """A virus scanner took the copy away; Omar's own file of the same bytes
    is at its place."""
    w = world
    _cut(w, monkeypatch)
    for copy in moving_dir(w.family).glob("*/copy"):
        copy.unlink()
    w.put(w.family, REL, PLAN)
    file_moves.settle_file_moves()
    assert w.where() == [f"family:{REL}", f"own:{REL}"], "her file went for another's"
    assert w.maker(w.family, REL) is None and w.held() == []


# ── L1 to L4: folders and names that change as it moves ───────────────────


def test_a_target_folder_that_becomes_a_link_takes_no_file_out(world: Files, monkeypatch, tmp_path) -> None:
    w = world
    outside = tmp_path / "outside"
    outside.mkdir()
    w.put(w.mine, REL)
    w.put(w.family, "notes/other.txt", b"other")
    real = file_moves._place

    def swapped(staged: Path, where: Path) -> bool:
        if where == w.root(w.family) / REL and not file_moves.is_linked(where.parent):
            os.rename(where.parent, where.parent.with_name("kept"))
            _junction(where.parent, outside)
        return real(staged, where)

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_place", swapped)
        assert w.refused(REL, w.mine, w.family) != "moved"
    assert list(outside.iterdir()) == [], "the file is outside every space"
    assert w.where() == [f"own:{REL}"] and w.held() == [] and w.maker(w.family, REL) is None


def test_a_source_folder_that_becomes_a_link_brings_no_file_in(world: Files, monkeypatch, tmp_path) -> None:
    w = world
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "trip.txt").write_bytes(b"a file of this computer")
    path = w.put(w.mine, REL)
    real_claim = file_moves._claim

    def claim(*args: Any) -> None:
        # Between the check and the rename.
        os.rename(path.parent, path.parent.with_name("kept"))
        _junction(path.parent, outside)
        real_claim(*args)

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_claim", claim)
        assert w.refused(REL, w.mine, w.family) == "changed_meanwhile"
    assert (outside / "trip.txt").is_file(), "a file of this computer was taken"
    assert (outside / "trip.txt").read_bytes() == b"a file of this computer"
    assert w.where() == ["own:kept/trip.txt"] and w.held() == []


def test_a_folder_another_member_made_meanwhile_stays(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, REL)
    real = file_moves._make_folders

    def made_meanwhile(root: Path, names: Any) -> Any:
        if root == w.root(w.family):
            (root / "notes").mkdir(parents=True, exist_ok=True)  # Omar's
        return real(root, names)

    def fails(*_a: Any) -> None:
        raise PermissionError("held")

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_make_folders", made_meanwhile)
        patch.setattr(file_moves, "_claim", fails)
        assert w.refused(REL, w.mine, w.family) == "in_use"
    assert (w.root(w.family) / "notes").is_dir(), "another member's empty folder was removed"
    assert w.where() == [f"own:{REL}"] and w.held() == []


@pytest.mark.parametrize("letter", ["n", "خ"])
def test_a_long_name_taken_meanwhile_is_given_back_shorter(world: Files, monkeypatch, letter) -> None:
    """As long as the disk takes: "name (2)" would not fit."""
    w = world
    rel = letter * (251 if os.name == "nt" or letter == "n" else 125) + ".txt"
    try:
        w.put(w.mine, rel)
    except OSError:
        pytest.skip("the whole path is too long here")

    def killed(_move: Any) -> None:
        raise Killed

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_put_in_place", killed)
        with pytest.raises(Killed):
            w.move(rel, w.mine, w.family)
    w.put(w.mine, rel, b"a new file at its name")
    file_moves.settle_file_moves()
    assert w.held() == [] and len(w.where()) == 1, (w.held(), w.where())


def test_the_name_beside_a_long_one_fits_the_disk() -> None:
    for name in ("n" * 251 + ".txt", "خ" * 125 + ".txt", "n" * 255, "😀" * 60 + ".txt"):
        for count in (2, 99):
            beside = file_moves._beside(name, count)
            assert len(beside.encode("utf-16-le")) // 2 <= 255 and f"({count})" in beside, name[:4]
            assert os.name == "nt" or len(os.fsencode(beside)) <= 255, name[:4]
    assert file_moves._beside("trip.txt", 2) == "trip (2).txt" and file_moves._beside("trip.txt", 1) == "trip.txt"


# ── L6: what travels with the bytes ───────────────────────────────────────


@POSIX
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o755, 0o4755])
def test_a_files_permission_bits_travel(world: Files, mode) -> None:
    w = world
    os.chmod(w.put(w.mine, "run.sh"), mode)
    w.move("run.sh", w.mine, w.family)
    assert stat.S_IMODE(os.lstat(w.root(w.family) / "run.sh").st_mode) == mode & 0o777


@pytest.mark.skipif(os.name != "nt", reason="a Windows mark")
def test_the_mark_of_a_downloaded_file_travels(world: Files) -> None:
    w = world
    path = w.put(w.mine, "setup.txt")
    try:
        Path(f"{path}:Zone.Identifier").write_bytes(b"[ZoneTransfer]\r\nZoneId=3\r\n")
    except OSError:
        pytest.skip("this disk keeps none")
    w.move("setup.txt", w.mine, w.family)
    with open(f"{w.root(w.family) / 'setup.txt'}:Zone.Identifier", "rb") as mark:
        assert b"ZoneId=3" in mark.read()


# ── L7, L9: who settles, and for how long ─────────────────────────────────


def test_only_the_server_settles() -> None:
    """One process: the server, which holds the data folder alone."""
    callers = set()
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-skills/kazma_skills", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui"):
        for path in (REPO / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "settle_file_moves" in text or "file_moves.quiet" in text or "file_moves import" in text:
                callers.add(path.relative_to(REPO).as_posix())
    assert callers == {
        "kazma-core/kazma_core/spaces/file_moves.py",
        "kazma-core/kazma_core/spaces/space_data.py",  # its erase and export: admin routes
        "kazma-core/kazma_core/spaces/space_move.py",  # the bundle: a route
        "kazma-core/kazma_core/memory/worker_bootstrap.py",  # started by the server
        "kazma-ui/kazma_ui/app.py",
        "kazma-ui/kazma_ui/space_files_api.py",
    }
    app = (REPO / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")
    assert app.index("self._server_lease.acquire()") < app.index("to_thread(settle_file_moves)")


def test_a_settling_waits_for_another_only_so_long(world: Files, monkeypatch) -> None:
    w = world
    _cut(w, monkeypatch)
    monkeypatch.setattr(file_moves, "_SETTLING_WAIT_S", 0.2, raising=False)
    done = threading.Event()
    with file_moves._settling:
        waiting = threading.Thread(target=lambda: (file_moves.settle_file_moves(), done.set()), daemon=True)
        waiting.start()
        assert done.wait(10), "it waits for ever"
    waiting.join(10)
    file_moves.settle_file_moves()
    assert w.where() == [f"own:{REL}"]


# ── the delta check (N3 to N5, N8) ────────────────────────────────────────


def _busy(monkeypatch) -> None:
    monkeypatch.setattr(file_moves, "_SETTLING_WAIT_S", 0.1)


def test_an_erase_that_cannot_wait_for_a_settling_is_refused(world: Files, monkeypatch) -> None:
    """Another settling is at work: it could put a file back into the erased
    space. Nothing is closed, and the erase is tried again."""
    w = world
    _cut(w, monkeypatch)
    _busy(monkeypatch)
    with file_moves._settling, pytest.raises(space_data.SpaceDataRefused) as refused:
        space_data.erase_space(w.family, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "files_moving" and refused.value.status == 503
    assert not (identity.read_registry()["spaces"][w.family].get("erasing_at")), "it is closed, and not erased"
    space_data.erase_space(w.family, by=OWNER_ACCOUNT_ID)
    assert not space_dir(w.family).exists() and w.where() == [f"own:{REL}"]


def test_an_erase_that_meets_a_settling_after_it_closed_stops_there(world: Files, monkeypatch) -> None:
    w = world
    _cut(w, monkeypatch)
    _busy(monkeypatch)
    real = people.close_space

    def close(space: str, *, by: str) -> None:
        real(space, by=by)
        assert file_moves._settling.acquire(timeout=5)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(people, "close_space", close)
            with pytest.raises(space_data.SpaceDataRefused):
                space_data.erase_space(w.family, by=OWNER_ACCOUNT_ID)
    finally:
        file_moves._settling.release()
    assert identity.read_registry()["spaces"][w.family].get("erasing_at"), "its people are out, nothing moves in"
    space_data.erase_space(w.family, by=OWNER_ACCOUNT_ID)
    assert not space_dir(w.family).exists() and w.where() == [f"own:{REL}"]


@pytest.mark.parametrize("why", ["another settling", "its move cannot be ended"])
def test_an_export_that_would_miss_a_held_file_says_so(world: Files, monkeypatch, tmp_path, why) -> None:
    w = world
    _cut(w, monkeypatch)
    _busy(monkeypatch)

    def fails(_move: Any) -> str:
        raise OSError("not now")

    with monkeypatch.context() as patch:
        if why == "another settling":
            assert file_moves._settling.acquire(timeout=5)
        else:
            patch.setattr(file_moves, "_end", fails)
        try:
            with pytest.raises(space_data.SpaceDataRefused) as refused:
                space_data.export_space(w.mine, tmp_path / "mine.zip")
        finally:
            if why == "another settling":
                file_moves._settling.release()
    assert refused.value.code == "files_moving" and not (tmp_path / "mine.zip").exists()
    space_data.export_space(w.mine, tmp_path / "mine.zip")


def test_a_bundle_that_would_miss_a_held_file_says_so(world: Files, monkeypatch, tmp_path) -> None:
    from kazma_core.spaces import space_move

    w = world
    w.share(REL)
    _busy(monkeypatch)
    with file_moves._settling, pytest.raises(space_move.SpaceMoveRefused) as refused:
        space_move.export_bundle(w.family, tmp_path / "x.kazmaspace", "a-long-enough-password", by=w.omar)
    assert refused.value.code == "files_moving"


def test_one_holding_folder_that_cannot_be_read_stops_no_other(world: Files, monkeypatch) -> None:
    w = world
    _cut(w, monkeypatch)
    moving_dir(w.omars).mkdir(parents=True)
    real = os.scandir

    def refuses(path: Any) -> Any:
        if Path(path) == moving_dir(w.omars):
            raise PermissionError("not now")
        return real(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", refuses)
        file_moves.settle_file_moves()
        assert w.where() == [f"own:{REL}"], "one folder stopped every settling"
        assert file_moves.quiet(w.family) is True and file_moves.quiet(w.omars) is False


@pytest.mark.parametrize("what", ["a size", "a folder"])
def test_a_record_nobody_writes_holds_nothing_for_ever(world: Files, what) -> None:
    w = world
    placed = w.put(w.family, "trip.txt", PLAN)
    held = moving_dir(w.mine) / ATTEMPT
    if what == "a size":
        _plant(w.mine, _record(w, size=10**30, copy_id=_ids(placed)), claimed=b"hers")
    else:
        (held / "move.json" / "x").mkdir(parents=True)
        (held / "claimed").write_bytes(b"hers")
    for _ in range(2):
        file_moves.settle_file_moves()
    assert [p.startswith("own:moved file ") for p in w.where(b"hers")] == [True], "it is held for ever"
    assert w.maker(w.family, "trip.txt") is None


def test_a_disk_that_does_not_say_which_file_is_which_is_refused(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, REL)
    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_identity", lambda info: [0, int(info.st_mtime_ns)])
        assert w.refused(REL, w.mine, w.family) == "move_failed"
    assert w.where() == [f"own:{REL}"] and w.held() == []


def test_a_space_on_another_disk_moves_no_file(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, REL)
    real = os.rename

    def another_disk(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if Path(dst).name == "claimed":
            raise OSError(errno.EXDEV, "another disk")
        real(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "rename", another_disk)
        assert w.refused(REL, w.mine, w.family) == "move_failed"
    assert w.where() == [f"own:{REL}"] and w.held() == []


# ── L10: a disabled account moves nothing ─────────────────────────────────


class _Service:
    """A document service that must not be reached."""

    def move_document(self, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("a disabled account moved a document")


def test_a_disabled_account_moves_no_file(world: Files) -> None:
    w = world
    w.put(w.mine, REL)
    people.set_enabled(w.reem, False, by=OWNER_ACCOUNT_ID)
    assert w.refused(REL, w.mine, w.family) == "not_yours"
    people.set_enabled(w.reem, True, by=OWNER_ACCOUNT_ID)
    assert w.refused(REL, w.mine, w.family) == "moved"


def test_a_disabled_account_moves_no_chat(world: Files) -> None:
    w = world
    assert chat_moves.move_refusal(from_space=w.mine, to_space=w.family, by=w.reem)[0] is None
    people.set_enabled(w.reem, False, by=OWNER_ACCOUNT_ID)
    refused, _out = chat_moves.move_refusal(from_space=w.mine, to_space=w.family, by=w.reem)
    assert refused is not None and refused.code == "not_yours", "a disabled account moves a chat"


def test_a_disabled_account_moves_no_document(world: Files) -> None:
    w = world
    people.set_enabled(w.reem, False, by=OWNER_ACCOUNT_ID)
    with pytest.raises(moves.MoveRefused) as refused:
        moves.move_document("d", from_space=w.mine, to_space=w.family, by=w.reem, workspace_id="w", service=_Service())
    assert refused.value.code == "not_yours"


def test_an_account_disabled_as_its_file_moves_moves_nothing(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, REL)
    real = file_moves._claim

    def meanwhile(*args: Any) -> None:
        real(*args)
        people.set_enabled(w.reem, False, by=OWNER_ACCOUNT_ID)

    monkeypatch.setattr(file_moves, "_claim", meanwhile)
    assert w.refused(REL, w.mine, w.family) == "not_yours"
    assert w.where() == [f"own:{REL}"] and w.held() == []


# ── L11: the words of a refusal ───────────────────────────────────────────


def test_a_person_who_left_is_not_told_they_did_not_share_it(world: Files, monkeypatch) -> None:
    from kazma_ui.i18n.catalog.documents import TRANSLATIONS

    w = world
    monkeypatch.setattr(auth, "get_request_principal", lambda request: {
        "source": "session", "role": "operator", "actor": "login", "user_id": w.reem})
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())
    app.include_router(create_space_files_router())
    w.put(w.mine, REL)
    w.put(w.family, "other.txt", b"Omar's")
    with TestClient(app) as client:
        def code(space: str, rel: str, to: str) -> str:
            return client.post("/api/space-files/move", headers={space_binding.SPACE_HEADER: space},
                               json={"path": rel, "to_space": to}).json()["code"]

        assert code(w.family, "other.txt", w.mine) == "not_the_sharer"
        people.remove_member(w.family, w.reem, by=OWNER_ACCOUNT_ID)
        assert code(w.mine, REL, w.family) == "not_yours"
    words = {c: TRANSLATIONS[f"documents.files.err.{c}"] for c in ("not_yours", "not_the_sharer")}
    assert "who shared" in words["not_the_sharer"]["en"] and "who shared" not in words["not_yours"]["en"]
    assert all(each["ar"] for each in words.values())
