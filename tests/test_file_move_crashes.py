"""Spaces S2-12, piece 7: a file move that is cut off, or that meets another
change, loses nothing and deletes nothing of another's.

A move is stopped at every step (the process dies there: no handler runs), in
both directions, and settled as a start does: the file is whole, in exactly
one space, with one maker, and nothing is left held. Then the same with the
other side changed meanwhile -- a write as it moves, an identical file of
another member, a delete, a name taken, an erase, a member taken out -- and
with a step that cannot be done now. The sequence of a backup whose files and
store are of different moments is here too.

Every control takes one step out and must lose or leave what that step is for:
no claim, settling by bytes alone, a finish that does not look at the target,
a rule asked once.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest
from kazma_core.paths import data_dir
from kazma_core.spaces import file_moves, identity, people, shared_files, space_data, space_move
from kazma_core.spaces.files import FILES, moving_dir, space_dir
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._file_move_world import PLAN, Files, Killed, digest, files_world

REL = "notes/trip.txt"


@pytest.fixture
def world(monkeypatch, tmp_path):
    with files_world(monkeypatch, tmp_path) as w:
        yield w


# ── where a move can stop ─────────────────────────────────────────────────

#: Every step of a move, and whether the file was in place in the target when
#: it was cut before it and after it.
STEPS: tuple[tuple[str, bool, bool], ...] = (
    ("_copy_aside", False, False),
    ("_write_record", False, False),
    ("_make_folders", False, False),
    ("_claim", False, False),
    ("_put_in_place", False, True),
    ("_target_has_it", True, True),
    ("record_move", True, True),
    ("_drop", True, True),
)
CUTS = [(step, when, placed) for step, before, after in STEPS
        for when, placed in (("before", before), ("after", after))]


def _kill_at(patch: pytest.MonkeyPatch, step: str, when: str) -> None:
    home = shared_files if step == "record_move" else file_moves
    real = getattr(home, step)

    def killed(*args: Any, **kwargs: Any) -> Any:
        if when == "after":
            real(*args, **kwargs)
        raise Killed

    patch.setattr(home, step, killed)


def _ready(w: Files, direction: str) -> tuple[str, str, str]:
    """The file where the move starts; ``(source, target, the name a place
    has in Files.where)`` ."""
    if direction == "share":
        w.put(w.mine, REL)
        return w.mine, w.family, "own"
    w.share(REL)
    return w.family, w.mine, "family"


def _cut(w: Files, monkeypatch, direction: str, step: str, when: str) -> tuple[str, str, str]:
    source, target, name = _ready(w, direction)
    with monkeypatch.context() as patch:
        _kill_at(patch, step, when)
        with pytest.raises(Killed):
            w.move(REL, source, target)
    return source, target, name


def _whole(w: Files, direction: str, moved: bool) -> None:
    """The file is in one space, its maker and its line as they should be."""
    there = {"share": ("own", "family"), "take out": ("family", "own")}[direction][1 if moved else 0]
    assert w.where() == [f"{there}:{REL}"], "the file is not in exactly one space"
    assert w.held() == [], "something is still held"
    shared = there == "family"
    assert w.maker(w.family, REL) == ((w.reem, digest(PLAN)) if shared else None), "its maker is wrong"
    assert len(w.lines()) == (1 if direction == "take out" and moved else 0), "its line is wrong"


@pytest.mark.parametrize("direction", ["share", "take out"])
@pytest.mark.parametrize(("step", "when", "placed"), CUTS)
def test_a_move_cut_off_at_any_step_is_whole_after_a_start(world: Files, monkeypatch, direction, step, when, placed):
    w = world
    _cut(w, monkeypatch, direction, step, when)
    assert w.where(), "the bytes are nowhere"
    file_moves.settle_file_moves()
    _whole(w, direction, placed)
    assert file_moves.settle_file_moves() == [], "a settled move is settled once"
    if not placed:  # it can be made again, and leaves no folder behind meanwhile
        target = w.family if direction == "share" else w.mine
        assert direction == "take out" or not (w.root(target) / "notes").exists()
        w.move(REL, w.mine if direction == "share" else w.family, target)
        _whole(w, direction, True)


@pytest.mark.parametrize("cut_at", [1, 2, 3])
def test_a_settling_that_is_cut_off_ends_the_same(world: Files, monkeypatch, cut_at) -> None:
    """Killed while its holding folders go, then settled."""
    w = world
    _cut(w, monkeypatch, "share", "record_move", "after")
    real, calls = file_moves._take_away, []

    def killed(folder: Path) -> None:
        calls.append(folder)
        if len(calls) >= cut_at:
            raise Killed
        real(folder)

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_take_away", killed)
        if cut_at < 3:
            with pytest.raises(Killed):
                file_moves.settle_file_moves()
    file_moves.settle_file_moves()
    _whole(w, "share", True)


def test_a_copy_that_lost_its_record_is_never_taken_for_the_file(world: Files, monkeypatch) -> None:
    """The ending removes the record before the copy: killed between, an
    identical file of Omar's is not made Reem's, and hers stays."""
    w = world
    _cut(w, monkeypatch, "share", "_claim", "before")
    w.put(w.family, REL)  # Omar's own, the same bytes
    real, calls = file_moves._take_away, []

    def killed(folder: Path) -> None:
        calls.append(folder)
        if len(calls) == 2:
            raise Killed
        real(folder)

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_take_away", killed)
        with pytest.raises(Killed):
            file_moves.settle_file_moves()
    file_moves.settle_file_moves()
    assert w.where() == [f"family:{REL}", f"own:{REL}"] and w.held() == []
    assert w.maker(w.family, REL) is None


def test_the_start_the_sweep_and_the_next_move_settle(world: Files, monkeypatch) -> None:
    w = world
    for settle in (file_moves.settle_file_moves, file_moves.sweep,
                   lambda: w.refused("none.txt", w.mine, w.family)):
        _cut(w, monkeypatch, "share", "_put_in_place", "before")
        assert w.where() == ["held"]
        settle()
        assert w.where() == [f"own:{REL}"] and w.held() == []


def test_a_settling_leaves_a_live_move_alone(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, REL)
    real, settled = file_moves._put_in_place, []

    def settles_meanwhile(move: Any) -> None:
        settled.append(file_moves.settle_file_moves())
        real(move)

    monkeypatch.setattr(file_moves, "_put_in_place", settles_meanwhile)
    w.move(REL, w.mine, w.family)
    assert settled == [[]]
    _whole(w, "share", True)


# ── a write as it moves ───────────────────────────────────────────────────

NEW = b"the trip plan, written as it moved"


def _late_write(w: Files, monkeypatch, step: str, when: str) -> None:
    """The source is written at its name, beside the step: those words are
    somewhere afterwards, and nothing is in two spaces but as two versions."""
    path = w.put(w.mine, REL)
    home = shared_files if step == "record_move" else file_moves
    real = getattr(home, step)

    def writes(*args: Any, **kwargs: Any) -> Any:
        if when == "before":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(NEW)
            return real(*args, **kwargs)
        done = real(*args, **kwargs)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(NEW)
        return done

    with monkeypatch.context() as patch:
        patch.setattr(home, step, writes)
        w.refused(REL, w.mine, w.family)
    assert w.where(NEW), "the words written as it moved are gone"
    assert all(place.startswith("own:") for place in w.where(NEW)), "they left her own space unasked"
    assert w.held() == []
    assert len(w.where()) <= 1


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("step", ["_make_folders", "_write_record", "_claim", "_put_in_place", "record_move"])
def test_a_write_as_it_moves_is_never_lost(world: Files, monkeypatch, step, when) -> None:
    _late_write(world, monkeypatch, step, when)


def test_control_without_the_claim_the_late_write_is_lost(world: Files, monkeypatch) -> None:
    """The source left under its name, compared, and removed at the end."""
    w = world
    real_end = file_moves._end

    def unclaimed(source: Path, info: Any, move: Any) -> None:
        assert file_moves._digest(source) == move.sha256

    def removes(move: Any) -> str:
        outcome = real_end(move)
        (w.root(w.mine) / move.path).unlink(missing_ok=True)
        return outcome

    monkeypatch.setattr(file_moves, "_claim", unclaimed)
    monkeypatch.setattr(file_moves, "_end", removes)
    with pytest.raises(AssertionError, match="are gone"):
        _late_write(w, monkeypatch, "_put_in_place", "before")


@pytest.mark.skipif(os.name == "nt", reason="Windows does not rename an open file")
def test_a_write_through_a_handle_opened_before_is_never_lost(world: Files, monkeypatch) -> None:
    """Written through an open handle after the bytes were compared: the
    target has the version that moved, and hers stays hers."""
    w = world
    path = w.put(w.mine, REL)
    real = file_moves._put_in_place
    with open(path, "r+b") as handle:
        def writes(move: Any) -> None:
            real(move)
            handle.write(NEW)
            handle.truncate()
            handle.flush()

        with monkeypatch.context() as patch:
            patch.setattr(file_moves, "_put_in_place", writes)
            assert w.refused(REL, w.mine, w.family) == "changed_meanwhile"
    assert w.where(NEW) == [f"own:{REL}"] and w.where() == [f"family:{REL}"]
    assert w.maker(w.family, REL) == (w.reem, digest(PLAN)) and w.held() == []


def test_a_name_taken_meanwhile_is_given_back_beside_it(world: Files, monkeypatch) -> None:
    w = world
    _cut(w, monkeypatch, "share", "_put_in_place", "before")
    w.put(w.mine, REL, b"a new file at its name")
    file_moves.settle_file_moves()
    assert w.where() == ["own:notes/trip (2).txt"]
    assert w.read(w.mine, REL) == b"a new file at its name" and w.held() == []


def test_a_folder_gone_or_replaced_meanwhile_still_gives_it_back(world: Files, monkeypatch) -> None:
    w = world
    _cut(w, monkeypatch, "share", "_put_in_place", "before")
    shutil.rmtree(w.root(w.mine) / "notes")
    file_moves.settle_file_moves()
    assert w.where() == [f"own:{REL}"]
    _cut(w, monkeypatch, "share", "_put_in_place", "before")
    shutil.rmtree(w.root(w.mine) / "notes")
    w.put(w.mine, "notes", b"a file called notes")
    file_moves.settle_file_moves()
    assert w.where() == ["own:trip.txt"] and w.held() == []


# ── another person's file, and a delete, in the target ────────────────────


def _identical_file_of_another(w: Files, monkeypatch) -> None:
    _cut(w, monkeypatch, "share", "_put_in_place", "before")
    w.put(w.family, REL)  # Omar's own file, the same name and the same bytes
    file_moves.settle_file_moves()
    assert f"family:{REL}" in w.where(), "another person's file was deleted"
    assert f"own:{REL}" in w.where(), "her file went though it never moved"
    assert w.maker(w.family, REL) is None, "another person's file was made hers"
    assert w.held() == []


def test_an_identical_file_of_another_member_is_never_hers_nor_deleted(world: Files, monkeypatch) -> None:
    _identical_file_of_another(world, monkeypatch)


def test_control_settling_by_bytes_alone_takes_anothers_file_for_hers(world: Files, monkeypatch) -> None:
    monkeypatch.setattr(file_moves, "_was_placed", lambda move: move.placed_at().is_file())
    with pytest.raises(AssertionError):
        _identical_file_of_another(world, monkeypatch)


def _deleted_in_the_target(w: Files, monkeypatch) -> None:
    _cut(w, monkeypatch, "share", "_put_in_place", "after")
    (w.root(w.family) / REL).unlink()
    file_moves.settle_file_moves()
    assert w.where() == [f"own:{REL}"], "the file is in neither space"
    assert w.maker(w.family, REL) is None and w.held() == []


def test_a_delete_in_the_target_before_the_end_gives_it_back(world: Files, monkeypatch) -> None:
    _deleted_in_the_target(world, monkeypatch)


def test_control_a_finish_that_does_not_look_at_the_target_loses_it(world: Files, monkeypatch) -> None:
    real = file_moves._target_has_it
    with pytest.raises(AssertionError, match="in neither space"):
        with monkeypatch.context() as patch:
            def settle_blind(**_k: Any) -> Any:
                patch.setattr(file_moves, "_target_has_it", lambda move: True)
                return real_settle()

            real_settle = file_moves.settle_file_moves
            patch.setattr(file_moves, "settle_file_moves", settle_blind)
            _deleted_in_the_target(world, patch)
    assert file_moves._target_has_it is real


@pytest.mark.parametrize("direction", ["share", "take out"])
def test_a_file_changed_in_the_target_before_the_end_leaves_a_version_in_each(world: Files, monkeypatch, direction):
    w = world
    source, target, _name = _cut(w, monkeypatch, direction, "record_move", "after")
    w.put(target, REL, b"changed there")
    file_moves.settle_file_moves()
    assert w.read(target, REL) == b"changed there" and w.read(source, REL) == PLAN
    assert w.maker(w.family, REL) == ((w.reem, digest(PLAN)) if direction == "take out" else None)
    assert w.lines() == [] and w.held() == []


def test_two_people_at_one_name_both_keep_their_file(world: Files) -> None:
    """Reem and Omar share a file of one name at the same moment."""
    w = world
    w.put(w.mine, REL, b"Reem's")
    w.put(w.omars, REL, b"Omar's")
    start, said = threading.Barrier(2), {}

    def go(who: str, space: str) -> None:
        start.wait()
        said[who] = w.refused(REL, space, w.family, by=who)

    threads = [threading.Thread(target=go, args=pair) for pair in ((w.reem, w.mine), (w.omar, w.omars))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert sorted(said.values()) == ["moved", "name_taken"], said
    assert len(w.where(b"Reem's")) == len(w.where(b"Omar's")) == 1 and w.held() == []


# ── a step that cannot be done now ────────────────────────────────────────


def test_an_unfinished_move_is_said_and_ended_later(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, REL)
    with monkeypatch.context() as patch:
        real = file_moves._unlink

        def held_by_a_scanner(path: Path) -> None:
            if path.name == "claimed":
                raise PermissionError("held")
            real(path)

        patch.setattr(file_moves, "_unlink", held_by_a_scanner)
        assert w.refused(REL, w.mine, w.family) == "unfinished"
        file_moves.settle_file_moves()  # still held: said, and tried again
    assert w.where() == [f"family:{REL}", "held"]
    file_moves.sweep()
    _whole(w, "share", True)


def test_accounts_that_cannot_be_read_end_it_later(world: Files, monkeypatch) -> None:
    w = world
    w.put(w.mine, REL)

    def away(space: str) -> bool:
        raise identity.IdentityUnavailable("the settings store is away")

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_is_open", away)
        assert w.refused(REL, w.mine, w.family) == "unfinished"
    file_moves.settle_file_moves()
    _whole(w, "share", True)


def test_one_move_that_cannot_be_ended_does_not_stop_the_others(world: Files, monkeypatch) -> None:
    w = world
    _cut(w, monkeypatch, "share", "_put_in_place", "before")
    first = w.held()
    w.put(w.mine, "other.txt", b"other")
    with monkeypatch.context() as patch:
        _kill_at(patch, "_put_in_place", "before")
        patch.setattr(file_moves, "settle_file_moves", lambda **_k: [])
        with pytest.raises(Killed):
            w.move("other.txt", w.mine, w.family)
    stuck = next(p for p in first if p.startswith(w.mine)).split("/")[-1]
    real = file_moves._end

    def fails(move: Any) -> str:
        if move.attempt == stuck:
            raise OSError("cannot be read now")
        return real(move)

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_end", fails)
        file_moves.settle_file_moves()
    assert w.where(b"other") == ["own:other.txt"] and w.where() == ["held"]
    file_moves.settle_file_moves()
    assert w.where() == [f"own:{REL}"] and w.held() == []


# ── the rule, asked again ─────────────────────────────────────────────────


def _taken_out_of_the_space(w: Files, monkeypatch, step: str) -> None:
    w.put(w.mine, REL)
    real, gone = getattr(file_moves, step), []

    def meanwhile(*args: Any, **kwargs: Any) -> Any:
        done = real(*args, **kwargs)
        if not gone:
            people.remove_member(w.family, w.reem, by=OWNER_ACCOUNT_ID)
            gone.append(True)
        return done

    with monkeypatch.context() as patch:
        patch.setattr(file_moves, step, meanwhile)
        assert w.refused(REL, w.mine, w.family) == "not_yours", "a person who left put a file in the space"
    assert w.where() == [f"own:{REL}"] and w.held() == []
    assert w.maker(w.family, REL) is None


@pytest.mark.parametrize("step", ["_copy_aside", "_write_record", "_claim"])
def test_a_member_taken_out_as_it_moves_moves_nothing(world: Files, monkeypatch, step) -> None:
    _taken_out_of_the_space(world, monkeypatch, step)


def test_control_a_rule_asked_once_moves_it_for_someone_who_left(world: Files, monkeypatch) -> None:
    real, asked = file_moves._rule, []

    def once(source: str, target: str, by: str) -> bool:
        if not asked:
            asked.append(real(source, target, by))
        return asked[0]

    monkeypatch.setattr(file_moves, "_rule", once)
    with pytest.raises(AssertionError):
        _taken_out_of_the_space(world, monkeypatch, "_claim")


def test_a_take_out_whose_maker_changed_as_it_moves_stays(world: Files, monkeypatch) -> None:
    w = world
    w.share(REL)
    real = file_moves._claim

    def meanwhile(*args: Any) -> None:
        real(*args)
        with shared_files._writing() as conn:
            conn.execute("UPDATE shared_files SET maker = ?", (w.omar,))

    monkeypatch.setattr(file_moves, "_claim", meanwhile)
    assert w.refused(REL, w.family, w.mine) == "changed_meanwhile"
    assert w.where() == [f"family:{REL}"]


# ── an erase as it moves ──────────────────────────────────────────────────


def _erased_as_it_moves(w: Files, monkeypatch, step: str, when: str) -> None:
    """Family is erased, in another thread, while the move is at *step*: the
    erase has closed the space when the step goes on."""
    w.put(w.mine, REL)
    home = shared_files if step == "record_move" else file_moves
    real = getattr(home, step)
    closed, erased, failed = threading.Event(), threading.Event(), []
    real_close = people.close_space

    def close(space: str, *, by: str) -> None:
        real_close(space, by=by)
        closed.set()

    def erase() -> None:
        try:
            space_data.erase_space(w.family, by=OWNER_ACCOUNT_ID)
        except Exception as exc:  # noqa: BLE001 -- told to the test's own thread
            failed.append(exc)
        finally:
            closed.set()
            erased.set()

    started: list[threading.Thread] = []

    def meanwhile(*args: Any, **kwargs: Any) -> Any:
        done = real(*args, **kwargs) if when == "after" else None
        if not started:
            started.append(threading.Thread(target=erase))
            started[0].start()
            assert closed.wait(30)
        return done if when == "after" else real(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(people, "close_space", close)
        patch.setattr(home, step, meanwhile)
        said = w.refused(REL, w.mine, w.family)
    started[0].join(90)
    assert erased.is_set() and not failed, failed
    assert said != "moved" and w.where() == [f"own:{REL}"], ("the file went with the space", said)
    assert not space_dir(w.family).exists(), "the erased space's folder is back"
    assert w.held() == [] and w.maker(w.family, REL) is None


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("step", ["_copy_aside", "_make_folders", "_write_record", "_claim", "_put_in_place",
                                  "record_move"])
def test_an_erase_of_the_target_as_it_moves_keeps_the_file(world: Files, monkeypatch, step, when) -> None:
    _erased_as_it_moves(world, monkeypatch, step, when)


def test_control_an_erase_that_does_not_wait_and_a_rule_asked_once(world: Files, monkeypatch) -> None:
    """Both taken out: the file is erased with a space it was never in, or
    the space's folder is back."""
    real, asked = file_moves._rule, []

    def once(source: str, target: str, by: str) -> bool:
        if not asked:
            asked.append(real(source, target, by))
        return asked[0]

    monkeypatch.setattr(file_moves, "_rule", once)
    monkeypatch.setattr(file_moves, "quiet", lambda space: None)
    monkeypatch.setattr(file_moves, "_is_open", lambda space: True)
    with pytest.raises(AssertionError):
        _erased_as_it_moves(world, monkeypatch, "_claim", "after")


@pytest.mark.parametrize(("step", "when", "placed"), CUTS)
def test_an_erase_after_a_cut_off_share_keeps_the_file_unless_it_moved(world: Files, monkeypatch, step, when, placed):
    w = world
    _cut(w, monkeypatch, "share", step, when)
    space_data.erase_space(w.family, by=OWNER_ACCOUNT_ID)
    over = step == "_drop"  # the move was over but for its folders
    assert w.where() == ([] if over else [f"own:{REL}"])
    assert not space_dir(w.family).exists()
    file_moves.settle_file_moves()
    assert w.held() == [] and w.maker(w.family, REL) is None


@pytest.mark.parametrize("step", ["_claim", "_put_in_place", "record_move"])
def test_an_erase_after_a_cut_off_take_out_takes_what_is_the_spaces(world: Files, monkeypatch, step) -> None:
    """Placed in her own space: it is hers. Not placed: it was the erased
    space's, and goes with it."""
    w = world
    _cut(w, monkeypatch, "take out", step, "after")
    space_data.erase_space(w.family, by=OWNER_ACCOUNT_ID)
    assert w.where() == ([] if step == "_claim" else [f"own:{REL}"])
    file_moves.settle_file_moves()
    assert w.held() == [] and not space_dir(w.family).exists()


def _no_word_of(path: str) -> None:
    for database in data_dir().glob("*.db*"):
        assert path.encode() not in database.read_bytes(), database.name


def test_no_path_is_left_in_a_store_after_an_erase(world: Files, monkeypatch) -> None:
    """The record of a move is in its space's folder, never a store's row."""
    w = world
    rel = "a-very-private-name.txt"
    w.put(w.mine, rel)
    with monkeypatch.context() as patch:
        _kill_at(patch, "_put_in_place", "after")
        with pytest.raises(Killed):
            w.move(rel, w.mine, w.family)
    _no_word_of(rel)
    w.share("second-private-name.txt", b"second")
    with pytest.raises(AssertionError):  # the control: a row with a path is found by this very look
        _no_word_of("second-private-name.txt")
    space_data.erase_space(w.family, by=OWNER_ACCOUNT_ID)
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(str(data_dir() / shared_files.STORE))) as conn:
        for table in ("shared_files", "files_taken_out"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# ── what travels ──────────────────────────────────────────────────────────


def test_an_export_and_a_bundle_never_carry_a_held_file(world: Files, monkeypatch, tmp_path) -> None:
    w = world
    _cut(w, monkeypatch, "share", "_put_in_place", "before")
    with monkeypatch.context() as patch:
        patch.setattr(file_moves, "_settle", lambda *_a, **_k: ([], True))
        counted = space_data.count_space(w.family)["files"]
        space_data.export_space(w.family, tmp_path / "held.zip")
        travelling = [p.name for _part, _where, p in space_move._moving_files(w.family)]
    assert counted == 0 and travelling == []
    with zipfile.ZipFile(tmp_path / "held.zip") as zipped:
        assert not [n for n in zipped.namelist() if "moving" in n]
    # The export itself ends what is left: her own space's holds the file again.
    space_data.export_space(w.mine, tmp_path / "mine.zip")
    with zipfile.ZipFile(tmp_path / "mine.zip") as zipped:
        assert [n for n in zipped.namelist() if n.endswith(REL)], "a file being moved is in no export"


def test_control_a_holding_folder_inside_the_spaces_folder_travels(world: Files, monkeypatch, tmp_path) -> None:
    """Where it was before the review: every walk would have to know it."""
    monkeypatch.setattr(file_moves, "moving_dir", lambda space: space_dir(space) / "moving")
    with pytest.raises(AssertionError):
        test_an_export_and_a_bundle_never_carry_a_held_file(world, monkeypatch, tmp_path)


# ── a backup, and a record nobody can read ────────────────────────────────


@pytest.mark.parametrize("step", ["_put_in_place", "record_move", "_drop"])
def test_a_restore_of_files_older_than_the_store_loses_nothing(world: Files, monkeypatch, step) -> None:
    """The files as they were in the middle of a move, the store as it was
    after it ended: the file is whole in one space."""
    w = world
    _cut(w, monkeypatch, "share", step, "before")
    spaces = space_dir(w.mine).parent
    copied = data_dir() / "copied"
    shutil.copytree(spaces, copied)
    file_moves.settle_file_moves()
    shutil.rmtree(spaces)
    shutil.copytree(copied, spaces)
    file_moves.settle_file_moves()
    _whole(w, "share", step != "_put_in_place")


@pytest.mark.parametrize("step", ["record_move", "_drop"])
def test_a_restore_of_a_store_older_than_the_files_records_the_maker(world: Files, monkeypatch, step) -> None:
    w = world
    _cut(w, monkeypatch, "share", step, "before")
    for name in (shared_files.STORE, shared_files.STORE + "-wal", shared_files.STORE + "-shm"):
        (data_dir() / name).unlink(missing_ok=True)
    file_moves.settle_file_moves()
    _whole(w, "share", True)


@pytest.mark.parametrize("record", [None, "", "{", json.dumps({"attempt": "x"}), "[]"])
def test_a_claimed_file_whose_record_cannot_be_read_goes_back(world: Files, monkeypatch, record) -> None:
    w = world
    _cut(w, monkeypatch, "share", "_put_in_place", "before")
    place = next(moving_dir(w.mine).glob("*/move.json"))
    place.unlink()
    if record is not None:
        place.write_text(record, encoding="utf-8")
    file_moves.settle_file_moves()
    assert [p.startswith("own:moved file ") for p in w.where()] == [True]
    file_moves.settle_file_moves()
    assert w.held() == []


@pytest.mark.parametrize(("field", "value"), [
    ("path", "../../escape.txt"), ("target_path", "../escape.txt"), ("made", ["../.."]), ("target", "../x"),
    ("source", "another-space"), ("path", "/etc/passwd"),
])
def test_a_record_that_names_another_place_is_not_followed(world: Files, monkeypatch, field, value) -> None:
    w = world
    _cut(w, monkeypatch, "share", "_put_in_place", "before")
    place = next(moving_dir(w.mine).glob("*/move.json"))
    place.write_text(json.dumps({**json.loads(place.read_text(encoding="utf-8")), field: value}), encoding="utf-8")
    file_moves.settle_file_moves()
    assert len(w.where()) == 1 and w.where()[0].startswith("own:moved file ")
    assert not (space_dir(w.mine).parent / "escape.txt").exists()


def test_a_folder_that_is_no_moves_is_left_alone(world: Files) -> None:
    w = world
    w.put(w.mine, REL)
    other = moving_dir(w.mine) / "not-an-attempt"
    other.mkdir(parents=True)
    (other / "copy").write_bytes(b"x")
    file_moves.settle_file_moves()
    assert (other / "copy").exists() and w.where() == [f"own:{REL}"]
    assert (w.root(w.mine) / REL).parent.name == "notes" and FILES == "files"
