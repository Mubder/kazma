"""The fourth review of a space's move (Spaces S4-06, of b5d8330c7): L1 to L4.

Each test fails on b5d8330c7, but for the ones that say they are guards or
controls (L5 is a name in ``test_space_move_third_review.py``):

* **L1, an id this install makes later.** A bundle named the thread of a
  person's own direct chat (``gw-telegram-<id>``) or a library as the owner's
  space names one (``handbook``), with nothing under it yet: that person's
  chat belonged to the moved space from then on, and the owner's "Handbook"
  failed. And a chat made between the checks and its record's write was
  erased by its id, or taken over;
* **L2, an empty file left at the name** on a disk with no hard links: the
  space was ``space_exists`` from then on;
* **L3, a staged file a program holds** was reported as ``space_exists``;
* **L4, the mark's own name**: a bundle's folder of that name was a
  PermissionError, a person's own file of that name was dropped without a word.
"""

from __future__ import annotations

import errno
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from kazma_core import paths
from kazma_core.spaces import chat_apps, identity, people, scope, space_data, space_move, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_gateway.agent_handler.graph import _resolve_thread_in_place
from kazma_gateway.gateway import IncomingMessage

from tests._space_world import A, own_install
from tests.test_space_bundle_round_trip import PASSWORD, TARGET_KEY, entries_of, sealed_again
from tests.test_space_move_review import _bring_in, moved, target  # noqa: F401
from tests.test_space_move_third_review import _chat, _record_refuses, _said, _save, chats  # noqa: F401

HERS = "gw-telegram-4242"


def _refused_code(bundle: Path, owner: str) -> str:
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(bundle, owner)
    assert space_move.importing_spaces() == frozenset()
    return refused.value.code


# ── L1: the ids this install makes ──────────────────────────────────────


def _message(platform: str, sender: str, **meta: Any) -> IncomingMessage:
    return IncomingMessage(platform=platform, sender_id=sender, text="hello", context_metadata=meta)


#: A direct chat and its native threads, on every chat app.
MESSAGES = (
    _message("telegram", "telegram:4242"),
    _message("telegram", "telegram:12345678"),
    _message("telegram", "telegram:4242", message_thread_id=12345678),
    _message("telegram", "telegram:4242", thread_hint="gw-telegram-4242-topic-7"),
    _message("discord", "discord:998877665544332211"),
    _message("discord", "discord:99887766", discord_thread_id="12345678"),
    _message("slack", "slack:U0AB12CD"),
    _message("slack", "slack:U0AB12CD", thread_ts="1700000000.123456", message_ts="1"),
    _message("whatsapp", "whatsapp:966500000000"),
)


def _chat_of(message: IncomingMessage, space: str) -> str:
    """The thread the gateway finds for *message* in *space*, made by its own
    code (an install with nothing in it)."""
    return _resolve_thread_in_place(message, chat_apps._key_for(message.sender_id, space))


@pytest.mark.parametrize("message", MESSAGES, ids=lambda m: f"{m.platform}-{'-'.join(m.context_metadata) or 'direct'}")
def test_the_gateways_own_ids_are_known_for_whose_they_are(tmp_path, monkeypatch, message) -> None:
    """Made by the gateway's own code: a space's own chat is its own, the
    owner's and another space's are not."""
    ours, other = scope.new_space_id(), scope.new_space_id()
    with own_install(monkeypatch, tmp_path / "install"):
        owners, in_ours, in_other = (_chat_of(message, s) for s in (OWNER_SPACE_ID, ours, other))
    assert len({owners, in_ours, in_other}) == 3 and all(t.startswith("gw-") for t in (owners, in_ours, in_other))
    assert not chat_apps.foreign_chat_thread(in_ours, ours)
    assert chat_apps.foreign_chat_thread(owners, ours) and chat_apps.foreign_chat_thread(in_other, ours)


def test_a_chat_with_an_id_of_its_own_is_nobody_elses(tmp_path, monkeypatch) -> None:
    """A guard: a chat given a name in a space, a fork of one, a web chat."""
    from kazma_core.sessions.directory import create_named_session

    ours = scope.new_space_id()
    with own_install(monkeypatch, tmp_path / "install"):
        for sender in ("telegram:4242", f"telegram:4242--in-{ours}", f"telegram:grp-100123--in-{ours}", "slack:U0AB"):
            platform = sender.split(":")[0]
            made = create_named_session(platform=platform, sender_id=sender).thread_id
            assert not chat_apps.foreign_chat_thread(made, ours), made
    for made in ("web-0a1b2c3d4e5f", "fork-0a1b2c3d4e5f", "gw-0a1b2c3d4e5f", "", "thread-x"):
        assert not chat_apps.foreign_chat_thread(made, ours), made
    # Control: an eight-digit sender, topic or tail is no random end.
    for derived in ("gw-telegram-12345678", "gw-telegram-777-topic-12345678", "gw-discord-telegram_1-12345678"):
        assert chat_apps.foreign_chat_thread(derived, ours), derived


def _naming(chats: dict[str, Any], thread: str, path: Path) -> Path:  # noqa: F811
    entries = {n: d.replace(chats["thread"].encode(), thread.encode()) for n, d in entries_of(chats["bundle"]).items()}
    return sealed_again(entries, path)


@pytest.mark.parametrize("thread", [HERS, "gw-telegram-777-topic-12345678", "gw-telegram-4242--in-s0b0b0b0b0b0b"])
def test_a_bundle_naming_a_chat_apps_chat_is_refused(chats, target, tmp_path, thread) -> None:  # noqa: F811
    """Before: recorded as the moved space's, and that person's next message
    answered "belongs to another space"."""
    assert _refused_code(_naming(chats, thread, tmp_path / "t.kazmaspace"), target["person"]) == "foreign_row"
    assert threads.record_of(thread) is None
    assert threads.thread_home(thread) == OWNER_SPACE_ID


def test_the_spaces_own_chat_app_chat_moves(chats, target, tmp_path) -> None:  # noqa: F811
    """A guard: a shared space's group and direct chats carry the space."""
    thread = f"gw-telegram-grp-100123--in-{chats['space']}"
    _bring_in(_naming(chats, thread, tmp_path / "own.kazmaspace"), target["person"])
    assert threads.thread_home(thread) == chats["space"]
    assert _said(paths.space_checkpoints_db(chats["space"]), thread) == "moved words"


@pytest.mark.parametrize("store, table, column", [
    ("task_ledgers.db", "task_ledgers", "thread_id"),
    ("agent_artifacts.db", "agent_artifacts", "thread_id"),
    ("cron.db", "cron_jobs", "thread_id"),
    ("chat_sessions_test.db", "sessions", "thread_id"),
    ("chat_sessions_test.db", "sessions", "session_id"),
])
def test_a_row_naming_a_chat_apps_chat_is_refused(moved, target, tmp_path, store, table, column) -> None:  # noqa: F811
    """A row with no record of the chat in the bundle: a plan (one a thread),
    a scheduled job, a transcript."""
    entries = entries_of(moved["bundle"])
    name = f"stores/{store}/{table}.jsonl"
    row = {**json.loads(entries[name].decode("utf-8").splitlines()[0]), column: HERS}
    entries[name] += (json.dumps(row) + "\n").encode()
    assert _refused_code(sealed_again(entries, tmp_path / "row.kazmaspace"), target["person"]) == "foreign_row"


@pytest.mark.parametrize("library", ["handbook", "handbook.0a1b2c3d"], ids=["the owner's form", "another space's"])
def test_a_bundle_naming_a_library_of_another_space_is_refused(moved, target, tmp_path, library) -> None:  # noqa: F811
    from kazma_core.stores.knowledge import get_knowledge_store

    entries = entries_of(moved["bundle"])
    held = json.loads(entries["stores/knowledge.db/knowledge_libraries.jsonl"].decode("utf-8").splitlines()[0])["id"]
    for name in entries:
        if name.startswith("stores/knowledge.db/"):
            entries[name] = entries[name].replace(held.encode(), library.encode())
    assert _refused_code(sealed_again(entries, tmp_path / "lib.kazmaspace"), target["person"]) == "foreign_row"
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):  # the owner's own is made as before
        assert get_knowledge_store().create_library("Handbook", "Handbook")


# ── L1: a chat made here meanwhile ──────────────────────────────────────


def _meanwhile(monkeypatch, does: Any) -> None:
    """*does* happens here once the checks passed, before anything is
    written."""
    real = space_move._check_ids

    def _then(*a: Any, **kw: Any) -> None:
        real(*a, **kw)
        does()

    monkeypatch.setattr(space_move, "_check_ids", _then)


def test_a_chat_made_after_the_checks_is_not_erased(chats, target, monkeypatch) -> None:  # noqa: F811
    """Its record stands, so the bundle's is refused; the take-back erased its
    words and its record by its id."""
    owners = Path(paths.checkpoints_db())

    def _made() -> None:
        threads.record_thread(chats["thread"], OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
        _save(owners, chats["thread"], "the owner's words")

    _meanwhile(monkeypatch, _made)
    assert _refused_code(chats["bundle"], target["person"]) == "id_collides"
    assert _said(owners, chats["thread"]) == "the owner's words"
    assert threads.record_of(chats["thread"]).space_id == OWNER_SPACE_ID


def test_words_with_no_record_yet_are_not_taken(chats, target, monkeypatch) -> None:  # noqa: F811
    """Written after the checks with no record: taken as the space's."""
    owners = Path(paths.checkpoints_db())
    _meanwhile(monkeypatch, lambda: _save(owners, chats["thread"], "the owner's words"))
    assert _refused_code(chats["bundle"], target["person"]) == "id_collides"
    assert _said(owners, chats["thread"]) == "the owner's words"
    assert threads.record_of(chats["thread"]) is None and chats["space"] not in identity.read_registry()["spaces"]


def test_nor_by_a_stop_before_they_were_looked_for(chats, target, monkeypatch) -> None:  # noqa: F811
    """A stop right after the chats' records are written: the next start
    takes the records back, and nothing keyed by them."""
    owners = Path(paths.checkpoints_db())

    class Stopped(BaseException):
        pass

    real = space_move._write_rows

    def _then_stops(*a: Any, **kw: Any) -> None:
        real(*a, **kw)
        raise Stopped

    with monkeypatch.context() as patch:
        _meanwhile(patch, lambda: _save(owners, chats["thread"], "the owner's words"))
        patch.setattr(space_move, "_write_rows", _then_stops)
        patch.setattr(space_move, "_take_back", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("stopped")))
        with pytest.raises(Stopped):
            _bring_in(chats["bundle"], target["person"])
    assert threads.record_of(chats["thread"]).space_id == chats["space"]
    space_move.settle_at_start()
    assert threads.record_of(chats["thread"]) is None and space_move.importing_spaces() == frozenset()
    assert _said(owners, chats["thread"]) == "the owner's words"


def test_a_job_made_after_the_checks_keeps_its_record(moved, target, monkeypatch) -> None:  # noqa: F811
    """The same for an id of another kind: a scheduled job's events are found
    by its id."""
    job = json.loads(entries_of(moved["bundle"])["stores/cron.db/cron_jobs.jsonl"].decode().splitlines()[0])["job_id"]
    cron = Path(paths.data_dir()) / "cron.db"

    def _rows() -> tuple[int, int]:
        with closing(sqlite3.connect(str(cron))) as conn:
            return (conn.execute("SELECT COUNT(*) FROM cron_jobs WHERE job_id = ?", (job,)).fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM cron_events WHERE job_id = ?", (job,)).fetchone()[0])

    def _insert(conn: sqlite3.Connection, table: str, **given: Any) -> None:
        row = {}
        for _cid, name, ctype, notnull, default, pk in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
            if name in given:
                row[name] = given[name]
            elif notnull and default is None and not (pk and "INT" in (ctype or "").upper()):
                row[name] = 0 if "INT" in ctype.upper() or "REAL" in ctype.upper() else "x"
        conn.execute(f'INSERT INTO "{table}" ({", ".join(row)}) VALUES ({", ".join("?" * len(row))})',
                     tuple(row.values()))

    real = space_move._write_rows

    def _made_first(bundle: Any, targets: dict[tuple[str, str], Any], *a: Any, **kw: Any) -> int:
        if ("cron.db", "cron_jobs") in targets:  # after everything was asked again
            with closing(sqlite3.connect(str(cron))) as conn:
                _insert(conn, "cron_jobs", job_id=job, tenant_id=OWNER_SPACE_ID)
                _insert(conn, "cron_events", job_id=job, at=0.0, event="made")
                conn.commit()
        return real(bundle, targets, *a, **kw)

    monkeypatch.setattr(space_move, "_write_rows", _made_first)
    assert _refused_code(moved["bundle"], target["person"]) == "id_collides"
    assert _rows() == (1, 1)


# ── L2 and L3: a disk of another kind, a file a program holds ───────────


class _Posix:
    """``os`` as the code asks it on POSIX: the branch Windows never takes."""

    name = "posix"

    def __init__(self, *, links: bool) -> None:
        self._links = links

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def link(self, *a: Any) -> None:
        if not self._links:
            raise OSError(errno.EPERM, "no hard links")
        os.link(*a)


def _no_replace(monkeypatch, error: BaseException) -> None:
    real = Path.replace

    def _fails(self: Path, *a: Any) -> Any:
        if ".moving-" in self.name:
            raise error
        return real(self, *a)

    monkeypatch.setattr(Path, "replace", _fails)


def test_a_replace_that_fails_leaves_no_file_at_the_name(chats, target, monkeypatch) -> None:  # noqa: F811
    own = paths.space_checkpoints_db(chats["space"])
    with monkeypatch.context() as patch:
        patch.setattr(space_move, "os", _Posix(links=False))
        _no_replace(patch, OSError(errno.EIO, "the disk"))
        with pytest.raises(OSError):
            _bring_in(chats["bundle"], target["person"])
        assert not own.exists() and space_move.importing_spaces() == frozenset()
    _bring_in(chats["bundle"], target["person"])  # and it is not "here already"
    assert _said(own, chats["thread"]) == "moved words"


def test_nor_does_a_stop_between_the_claim_and_the_replace(chats, target, monkeypatch) -> None:  # noqa: F811
    own = paths.space_checkpoints_db(chats["space"])

    class Stopped(BaseException):
        pass

    def _cut(staged: Path, where: Path) -> None:
        where.touch(exist_ok=False)
        raise Stopped

    with monkeypatch.context() as patch:
        patch.setattr(space_move, "_land_new", _cut)
        patch.setattr(space_move, "_take_back", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("stopped")))
        with pytest.raises(Stopped):
            _bring_in(chats["bundle"], target["person"])
    assert own.exists() and own.stat().st_size == 0
    space_move.settle_at_start()
    assert not list(own.parent.glob(f"{own.name}*")) and space_move.importing_spaces() == frozenset()
    _bring_in(chats["bundle"], target["person"])
    assert _said(own, chats["thread"]) == "moved words"


def test_an_empty_file_the_import_did_not_make_stays(chats, target, monkeypatch) -> None:  # noqa: F811
    """A guard: one that appears at the name before the import lands there."""
    own = paths.space_checkpoints_db(chats["space"])
    real = space_move._land_new

    def _appears_first(staged: Path, where: Path) -> None:
        where.touch()
        real(staged, where)

    monkeypatch.setattr(space_move, "_land_new", _appears_first)
    assert _refused_code(chats["bundle"], target["person"]) == "space_exists"
    assert own.exists() and not list(own.parent.glob(f"{own.name}.moving*"))


@pytest.mark.parametrize("links", [True, False], ids=["hard links", "none"])
def test_a_file_lands_on_posix(chats, target, monkeypatch, links) -> None:  # noqa: F811
    """A guard: both branches, whole imports."""
    own = paths.space_checkpoints_db(chats["space"])
    monkeypatch.setattr(space_move, "os", _Posix(links=links))
    _bring_in(chats["bundle"], target["person"])
    assert _said(own, chats["thread"]) == "moved words" and not list(own.parent.glob(f"{own.name}.moving*"))


@pytest.mark.parametrize("times", [1, 99], ids=["for a moment", "to the end"])
def test_a_staged_file_a_program_holds_is_no_space_exists(chats, target, monkeypatch, times) -> None:  # noqa: F811
    own = paths.space_checkpoints_db(chats["space"])
    real = Path.unlink
    held = [times]

    def _held(self: Path, *a: Any, **kw: Any) -> None:
        if self.name.startswith(f"{own.name}.moving-") and self.exists() and held[0]:
            held[0] -= 1
            raise PermissionError("held open")
        real(self, *a, **kw)

    monkeypatch.setattr(space_move, "os", _Posix(links=True))
    monkeypatch.setattr(Path, "unlink", _held)
    _bring_in(chats["bundle"], target["person"])
    assert chats["space"] in identity.read_registry()["spaces"] and space_move.importing_spaces() == frozenset()
    assert _said(own, chats["thread"]) == "moved words"
    assert bool(list(own.parent.glob(f"{own.name}.moving*"))) is (times == 99)


# ── L4: the mark's own name ─────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "files/space/.kazma-arriving", "files/space/.kazma-arriving/x", "files/documents/.kazma-arriving/x",
    "files/space/.KAZMA-Arriving/x", "files/space/.kazma-arriving. /x"])
def test_a_bundle_holding_the_marks_name_is_refused(moved, target, tmp_path, name) -> None:  # noqa: F811
    """A folder was a PermissionError (500); a file was written over."""
    entries = entries_of(moved["bundle"])
    entries[name] = b"a person's words"
    assert _refused_code(sealed_again(entries, tmp_path / "x.kazmaspace"), target["person"]) == "reserved_name"
    assert A not in identity.read_registry()["spaces"] and not space_move._space_folders(A)["space"].exists()


def test_the_name_deeper_in_a_folder_is_a_persons_own(moved, target, tmp_path) -> None:  # noqa: F811
    """A guard: the mark is only ever at the top of a space's folder."""
    entries = entries_of(moved["bundle"])
    entries["files/space/files/.kazma-arriving"] = b"a person's words"
    _bring_in(sealed_again(entries, tmp_path / "x.kazmaspace"), target["person"])
    kept = space_move._space_folders(A)["space"] / "files" / ".kazma-arriving"
    assert kept.read_bytes() == b"a person's words"


def test_a_persons_file_of_that_name_is_never_left_behind_unsaid(tmp_path, monkeypatch) -> None:
    """Refused as the bundle is made, before the space's key is changed; a
    mark an import could not remove is Kazma's own, and stays."""
    from kazma_core.spaces import space_keys

    with own_install(monkeypatch, tmp_path / "install", vault_key=TARGET_KEY):
        space = people.create_shared_space("Ours", by=OWNER_ACCOUNT_ID)["id"]
        _chat(space, "t-ours", "ours")
        folder = space_move._space_folders(space)["space"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".kazma-arriving").write_text("a person's words", encoding="utf-8")
        changed: list[str] = []
        monkeypatch.setattr(space_keys, "change_key", lambda s, **_kw: changed.append(s))
        bundle = tmp_path / "ours.kazmaspace"
        with pytest.raises(space_move.SpaceMoveRefused) as refused:
            space_move.export_bundle(space, bundle, PASSWORD, by=OWNER_ACCOUNT_ID)
        assert refused.value.code == "reserved_name" and not changed and not list(tmp_path.glob("ours.kazmaspace*"))
        (folder / ".kazma-arriving").write_text("0000002a", encoding="ascii")
        space_move.export_bundle(space, bundle, PASSWORD, by=OWNER_ACCOUNT_ID)
    assert not [n for n in entries_of(bundle) if n.startswith("files/")]


def test_a_refused_import_still_leaves_nothing(chats, target, monkeypatch) -> None:  # noqa: F811
    """A guard: the take-back finds the space's rows by what is recorded here."""
    with monkeypatch.context() as patch:
        _record_refuses(patch)
        _refused_code(chats["bundle"], target["person"])
    assert threads.record_of(chats["thread"]) is None
    assert sum(item.rows for item in space_data._walk(chats["space"])[1]) == 0
