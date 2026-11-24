"""The third review of a space's move (Spaces S4-06, of b55865291).

Each test fails on b55865291, but for the ones that say they are guards:

* **a refused import deleted another space's chats**: the checkpoint file's
  name lost the underscores at its ends, so a space ``v_`` had ``v``'s file;
  a bundle could name it, nothing looked whether the file was there, and the
  take-back (at the next start too) deleted it. Now an id is one Kazma makes,
  a file is one space's, a file that is there counts as the space's being
  here, and a take-back deletes a file or a folder only when it carries the
  import's own mark (the journal's);
* **a bundle naming a chat of the owner's from before Spaces** (no record,
  N19) made that chat the space's, and a take-back deleted it;
* the checkpoint file goes last; a state nested too deep, a state kept in an
  unknown way and a number that is none are refusals; a landed import is not
  called taken back; what connects an account is named.

The document job is in ``test_space_move_second_review.py``, the egress rules
in ``test_engine_egress_filter.py`` and ``test_container_engine_setup.py``.
"""

from __future__ import annotations

import base64
import itertools
import json
import logging
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from kazma_core import paths
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope, space_data, space_move, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_world import A, own_install
from tests.test_space_bundle_round_trip import PASSWORD, TARGET_KEY, entries_of, sealed_again
from tests.test_space_move_review import _bring_in, _refused, moved, target  # noqa: F401


# ── a chat's saved state, as the gateway keeps it ───────────────────────


def _save(path: Path, thread: str, said: str) -> None:
    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.sqlite import SqliteSaver

    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(path), check_same_thread=False)) as conn:
        saver = SqliteSaver(conn, serde=kazma_checkpoint_serde())
        saver.setup()
        state = empty_checkpoint()
        state["channel_values"] = {"said": said}
        saver.put({"configurable": {"thread_id": thread, "checkpoint_ns": ""}}, state,
                  {"source": "loop", "step": 1}, {})


def _said(path: Path, thread: str) -> str:
    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from langgraph.checkpoint.sqlite import SqliteSaver

    if not path.is_file():
        return "(no file)"
    with closing(sqlite3.connect(str(path), check_same_thread=False)) as conn:
        found = SqliteSaver(conn, serde=kazma_checkpoint_serde()).get_tuple(
            {"configurable": {"thread_id": thread, "checkpoint_ns": ""}})
    return found.checkpoint["channel_values"]["said"] if found else ""


def _chat(space: str, thread: str, said: str) -> Path:
    threads.record_thread(thread, space, OWNER_ACCOUNT_ID)
    path = paths.space_checkpoints_db(space)
    _save(path, thread, said)
    return path


@pytest.fixture(scope="module")
def chats(tmp_path_factory) -> dict[str, Any]:
    """A shared space with one chat, made into a bundle."""
    from kazma_core.spaces import preconditions

    folder = tmp_path_factory.mktemp("third-source")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(preconditions, "missing", lambda **_kw: [])
        with own_install(patch, folder / "install"):
            space = people.create_shared_space("Movers", by=OWNER_ACCOUNT_ID)["id"]
            thread = f"web-{uuid.uuid4().hex[:12]}"
            _chat(space, thread, "moved words")
            bundle = folder / "chats.kazmaspace"
            space_move.export_bundle(space, bundle, PASSWORD, by=OWNER_ACCOUNT_ID)
    return {"space": space, "thread": thread, "bundle": bundle}


def _renamed(chats: dict[str, Any], space: str, path: Path) -> Path:
    """The same bundle, of a space of the id *space*."""
    old = chats["space"]
    entries = {name.replace(old, space): data.replace(old.encode(), space.encode())
               for name, data in entries_of(chats["bundle"]).items()}
    return sealed_again(entries, path)


def _record_refuses(patch: pytest.MonkeyPatch) -> None:
    def _refuses(*_a: Any, **_kw: Any) -> None:
        raise identity.IdentityError("bad_space_kind", "no")

    patch.setattr(people, "record_moved_space", _refuses)


def _journaled(space: str, **more: Any) -> None:
    get_config_store().set(space_move.JOURNAL_KEY, {space: {"by": OWNER_ACCOUNT_ID, "started_at": "", **more}},
                           category="runtime")


# ── one file, one space ─────────────────────────────────────────────────


def _ids() -> list[str]:
    """Every space id the identity store would take, in the shapes that can
    meet: short ones over every separator, and long ones around the length
    where the name becomes a digest."""
    short = [s for n in range(1, 6) for s in map("".join, itertools.product("a0_-", repeat=n))
             if scope.valid_space_id(s)]
    long = [base + tail for n in range(44, 63) for base in ("a" * n,) for tail in ("", "_", "__", "-", "b")]
    return sorted({i for i in (*short, *long) if scope.valid_space_id(i)})


def _meeting(file_of: Any) -> list[tuple[str, str]]:
    """Two ids whose files are one, on a file system that folds case too."""
    held: dict[str, str] = {}
    met: list[tuple[str, str]] = []
    for space in _ids():
        name = Path(file_of(space)).name.lower()
        if name in held:
            met.append((held[name], space))
        held.setdefault(name, space)
    return met


def _before(space_id: str) -> str:
    """The file name as it was on b55865291."""
    import hashlib

    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", space_id).strip("_") or "x"
    if len(safe) > 48:
        safe = f"{safe[:32]}_{hashlib.sha256(space_id.encode('utf-8')).hexdigest()[:16]}"
    return f"checkpoints_{safe}.db"


def test_no_two_space_ids_have_one_checkpoint_file() -> None:
    assert len(_ids()) > 300
    assert _meeting(paths.space_checkpoints_db) == []
    # Control: the name before met, ``v`` and ``v_`` among them.
    assert ("a", "a_") in _meeting(_before) and _before("v") == _before("v_")


def test_an_id_that_does_not_end_in_an_underscore_keeps_its_file() -> None:
    """A guard: the ids Kazma makes, and the tenants of before Spaces (a
    retired memory mode's, an identity provider's) in the shapes named here.
    One that ends in an underscore has a file of its own now, not the one it
    had: that is the fix."""
    assert paths.space_checkpoints_db("acme_").name == "checkpoints_acme_.db" != _before("acme_")
    for held in (scope.new_space_id(), "telegram", "telegram:12345", "discord:987/65", "Alice", "acme-corp",
                 "a.very/odd tenant", "x" * 60, "telegram:" + "9" * 60):
        assert paths.space_checkpoints_db(held).name == _before(held), held


def test_an_id_kazma_makes_is_known_and_is_its_own_files() -> None:
    made = {scope.new_space_id() for _ in range(200)}
    assert len(made) == 200 and all(scope.made_space_id(i) and scope.valid_space_id(i) for i in made)
    assert all(paths.space_checkpoints_db(i).name == f"checkpoints_{i}.db" for i in made)
    for other in ("", "default", "s", "telegram", "s0a0a0a0a0a0a_", "S0A0A0A0A0A0A", "s0a0a0a0a0a0", "s0a0a0a0a0a0a0",
                  "s0a0a0a0a0a0g", " s0a0a0a0a0a0a", "s0a0a0a0a0a0a\n", None, 7):
        assert not scope.made_space_id(other), other


def test_every_space_made_here_has_such_an_id(tmp_path, monkeypatch) -> None:
    with own_install(monkeypatch, tmp_path / "install"):
        person = people.create_person("newcomer", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
        people.create_shared_space("Ours", by=OWNER_ACCOUNT_ID)
        identity.create_space("Plain", created_by=person)
        made = [s for s in identity.read_registry()["spaces"] if s != scope.OWNER_SPACE_ID]
        assert len(made) == 3 and all(scope.made_space_id(s) for s in made), made


# ── another space's chats ───────────────────────────────────────────────


def test_a_bundle_of_an_id_kazma_does_not_make_is_refused(chats, target, tmp_path, monkeypatch) -> None:  # noqa: F811
    """``v_``: its import wrote into ``v``'s file, and its take-back deleted
    the file -- a refusal by the identity store is enough."""
    victim = people.create_shared_space("Victim", by=OWNER_ACCOUNT_ID)["id"]
    held = _chat(victim, "t-victim", "the victim's words")
    bundle = _renamed(chats, f"{victim}_", tmp_path / "evil.kazmaspace")
    with monkeypatch.context() as patch:
        _record_refuses(patch)
        with pytest.raises(space_move.SpaceMoveRefused) as refused:
            _bring_in(bundle, target["person"])
    assert _said(held, "t-victim") == "the victim's words"
    assert refused.value.code == "not_a_bundle"
    with pytest.raises(space_move.SpaceMoveRefused) as refused:  # and one that would succeed
        _bring_in(bundle, target["person"])
    assert refused.value.code == "not_a_bundle" and f"{victim}_" not in identity.read_registry()["spaces"]


def test_the_next_start_deletes_no_other_spaces_chats(target) -> None:  # noqa: F811
    """The same, by a journal left behind."""
    victim = people.create_shared_space("Victim", by=OWNER_ACCOUNT_ID)["id"]
    held = _chat(victim, "t-victim", "the victim's words")
    _journaled(f"{victim}_")
    space_move.settle_at_start()
    assert _said(held, "t-victim") == "the victim's words"


def test_a_file_of_the_spaces_name_counts_as_here(chats, target) -> None:  # noqa: F811
    """A ``checkpoints_<tenant>.db`` a retired memory mode left is someone
    else's, whatever it holds: an import never writes into it."""
    left = paths.space_checkpoints_db(chats["space"])
    _save(left, "telegram:42", "an old tenant's words")
    was = left.read_bytes()
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(chats["bundle"], target["person"])
    assert refused.value.code == "space_exists" and left.read_bytes() == was
    assert space_move.importing_spaces() == frozenset()


def test_a_file_that_appears_meanwhile_is_never_written_over(chats, target, monkeypatch) -> None:  # noqa: F811
    """Between the check and the write: refused, and the take-back leaves it."""
    left = paths.space_checkpoints_db(chats["space"])
    real = space_move._write_rows

    def _appears_first(*a: Any, **kw: Any) -> int:
        _save(left, "t-theirs", "their words")
        return real(*a, **kw)

    monkeypatch.setattr(space_move, "_write_rows", _appears_first)
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(chats["bundle"], target["person"])
    assert refused.value.code == "space_exists" and _said(left, "t-theirs") == "their words"
    assert [p.name for p in left.parent.glob(f"{left.name}.moving*")] == []


def test_a_take_back_deletes_only_what_the_import_made(chats, target) -> None:  # noqa: F811
    """A journal entry, and a file and folders that do not carry its mark."""
    space = chats["space"]
    left = paths.space_checkpoints_db(space)
    _save(left, "t-theirs", "their words")
    folders = space_move._space_folders(space)
    for folder in folders.values():
        folder.mkdir(parents=True)
        (folder / "note.txt").write_text("theirs", encoding="utf-8")
    _journaled(space, mark=0x2A)
    space_move.settle_at_start()
    assert _said(left, "t-theirs") == "their words"
    assert all((f / "note.txt").read_text(encoding="utf-8") == "theirs" for f in folders.values())
    assert space_move.importing_spaces() == frozenset()


def test_what_an_import_made_carries_its_mark_until_it_lands(moved, target, monkeypatch) -> None:  # noqa: F811
    seen: dict[str, Any] = {}
    real = people.record_moved_space

    def _look(space_id: str, *a: Any, **kw: Any) -> None:
        mark = get_config_store().get(space_move.JOURNAL_KEY)[space_id]["mark"]
        head = paths.space_checkpoints_db(space_id).read_bytes()[68:72]
        folder = space_move._space_folders(space_id)["space"]
        seen.update(mark=mark, file=int.from_bytes(head, "big"),
                    folder=(folder / ".kazma-arriving").read_text(encoding="ascii"))
        real(space_id, *a, **kw)

    monkeypatch.setattr(people, "record_moved_space", _look)
    _bring_in(moved["bundle"], target["person"])
    assert seen["mark"] and seen["file"] == seen["mark"] and int(seen["folder"], 16) == seen["mark"]
    assert not list(space_move._space_folders(A)["space"].glob(".kazma*"))


def test_the_checkpoint_file_goes_last(moved, target, monkeypatch) -> None:  # noqa: F811
    """A program holds it open (Windows): the rest of the space is gone
    already, the journal keeps the file for the next start (b55865291: the
    settings, the secrets and the files stayed until then)."""
    from kazma_core.security.vault import get_vault

    own = paths.space_checkpoints_db(A)
    real = Path.unlink

    def _held(self: Path, *a: Any, **kw: Any) -> None:
        if self == own:
            raise PermissionError("held open")
        real(self, *a, **kw)

    with monkeypatch.context() as patch:
        _record_refuses(patch)
        patch.setattr(Path, "unlink", _held)
        with pytest.raises(space_move.SpaceMoveRefused):
            _bring_in(moved["bundle"], target["person"])
    assert own.exists() and space_move.importing_spaces() == {A}
    assert sum(item.rows for item in space_data._walk(A)[1]) == 0
    assert not get_vault()._space_rows(A) and not space_data._settings_keys(A, [])
    assert not any(folder.exists() for folder in space_move._space_folders(A).values())
    space_move.settle_at_start()
    assert not own.exists() and space_move.importing_spaces() == frozenset()


def test_a_space_erased_here_moves_back(tmp_path, monkeypatch) -> None:
    """A guard: the file its erase left, emptied, is its own (the record says
    so) -- the gateway may hold it open, so it is written into, and left when
    the import is taken back."""
    with own_install(monkeypatch, tmp_path / "install", vault_key=TARGET_KEY):
        person = people.create_person("returns", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
        space = people.create_shared_space("Away", by=OWNER_ACCOUNT_ID)["id"]
        own = _chat(space, "t-away", "away and back")
        bundle = tmp_path / "away.kazmaspace"
        space_move.export_bundle(space, bundle, PASSWORD, by=OWNER_ACCOUNT_ID)
        space_data.erase_space(space, by=OWNER_ACCOUNT_ID)
        assert own.exists() and _said(own, "t-away") == ""
        with monkeypatch.context() as patch:
            _record_refuses(patch)
            with pytest.raises(space_move.SpaceMoveRefused):
                _bring_in(bundle, person)
        assert own.exists() and _said(own, "t-away") == "" and space_move.importing_spaces() == frozenset()
        # One that holds a row is someone's, whatever the record says.
        _save(own, "t-theirs", "their words")
        with pytest.raises(space_move.SpaceMoveRefused) as refused:
            _bring_in(bundle, person)
        assert refused.value.code == "space_exists" and _said(own, "t-theirs") == "their words"
        with closing(sqlite3.connect(str(own))) as conn:
            conn.execute("DELETE FROM checkpoints")
            conn.commit()
        _bring_in(bundle, person)
        assert _said(own, "t-away") == "away and back" and space in identity.read_registry()["spaces"]


# ── the owner's chats from before Spaces ────────────────────────────────


def test_a_bundle_naming_a_chat_from_before_spaces_is_refused(chats, target, monkeypatch) -> None:  # noqa: F811
    """It has no record: the owner's (N19). Brought in, the bundle recorded
    it as the space's, and taken back it was deleted."""
    owners = Path(paths.checkpoints_db())
    _save(owners, chats["thread"], "the owner's words")
    with monkeypatch.context() as patch:
        _record_refuses(patch)
        with pytest.raises(space_move.SpaceMoveRefused) as refused:
            _bring_in(chats["bundle"], target["person"])
    assert _said(owners, chats["thread"]) == "the owner's words"
    assert refused.value.code == "id_collides"
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(chats["bundle"], target["person"])
    assert refused.value.code == "id_collides"
    assert threads.record_of(chats["thread"]) is None, "the owner's chat was made the space's"


def test_a_bundle_naming_a_chat_with_state_here_is_refused(chats, target) -> None:  # noqa: F811
    get_config_store().set(f"long_task.{chats['thread']}", {"goal": "the owner's"}, category="runtime")
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(chats["bundle"], target["person"])
    assert refused.value.code == "id_collides"
    assert get_config_store().get(f"long_task.{chats['thread']}") == {"goal": "the owner's"}


# ── states, rows and numbers ────────────────────────────────────────────

_DEEP_JSON = b"[" * 5000 + b"]" * 5000


def _b64(blob: bytes) -> dict[str, str]:
    return {"base64": base64.b64encode(blob).decode("ascii")}


@pytest.mark.parametrize("change", [
    {"type": "json", "value": _b64(_DEEP_JSON)},
    {"type": "msgpack", "value": _b64(b"\x91" * 1000 + b"\xc0")},
    {"type": "pickle", "value": _b64(b"\x80\x04N.")},
    {"type": None},
    {"type": "yaml", "value": _b64(b"a: 1")},
], ids=["json too deep", "msgpack too deep", "pickle", "no type", "unknown type"])
def test_a_state_that_is_none_is_refused(moved, target, tmp_path, change) -> None:  # noqa: F811
    entries = entries_of(moved["bundle"])
    name = "stores/checkpoints/writes.jsonl"
    row = json.loads(entries[name].decode("utf-8").splitlines()[0])
    entries[name] += (json.dumps({**row, "idx": 9, **change}) + "\n").encode()
    _refused(sealed_again(entries, tmp_path / "state.kazmaspace"), target["person"], "not_a_bundle")


@pytest.mark.parametrize("line", [
    lambda row: json.dumps({**row, "id": "e_deep", "user_text": _DEEP_JSON.decode()}),
    lambda row: json.dumps({**row, "id": "e_meta", "metadata_json": '{"said_by": ' + _DEEP_JSON.decode() + "}"}),
    lambda row: '{"id": ' + _DEEP_JSON.decode() + "}",
    lambda row: json.dumps({**row, "id": "e_nan"}).replace('"e_nan"', "NaN"),
    lambda row: '{"id": "e_cut", ',
], ids=["a text", "its JSON column", "the line", "not a number", "not JSON"])
def test_a_row_that_is_none_is_refused(moved, target, tmp_path, line) -> None:  # noqa: F811
    entries = entries_of(moved["bundle"])
    name = "stores/memory_state.db/episodes.jsonl"
    row = json.loads(entries[name].decode("utf-8").splitlines()[0])
    entries[name] += (line(row) + "\n").encode()
    _refused(sealed_again(entries, tmp_path / "row.kazmaspace"), target["person"], "not_a_bundle")


def test_a_chat_nested_too_deep_is_left_out_of_a_bundle(tmp_path, monkeypatch) -> None:
    """Counted, and the bundle is made (b55865291: RecursionError)."""
    with own_install(monkeypatch, tmp_path / "install"):
        space = people.create_shared_space("Deep", by=OWNER_ACCOUNT_ID)["id"]
        own = _chat(space, "t-deep", "deep")
        _chat(space, "t-plain", "plain")
        with closing(sqlite3.connect(str(own))) as conn:
            conn.execute("UPDATE checkpoints SET type = 'json', checkpoint = ? WHERE thread_id = 't-deep'",
                         (_DEEP_JSON,))
            conn.commit()
        bundle = tmp_path / "deep.kazmaspace"
        manifest = space_move.export_bundle(space, bundle, PASSWORD, by=OWNER_ACCOUNT_ID)
    assert manifest["left_out"]["checkpoints:checkpoints"] == 1
    kept = [json.loads(line) for line in entries_of(bundle)["stores/checkpoints/checkpoints.jsonl"].splitlines()]
    assert [r["thread_id"] for r in kept] == ["t-plain"]


@pytest.mark.parametrize("number, code", [
    ("NaN", "not_a_bundle"), ("Infinity", "not_a_bundle"), ("-Infinity", "not_a_bundle"),
    ("1e999", "bad_setting"), ("1" + "0" * 400, "bad_setting"), ("9007199254740993", "bad_setting"),
    ("true", "bad_setting")])
def test_a_number_that_is_none_is_refused(moved, target, tmp_path, number, code) -> None:  # noqa: F811
    entries = entries_of(moved["bundle"])
    settings = json.loads(entries["settings.json"])
    settings[f"tenant.{A}.connectors.x.oauth2.expires_at"] = {"category": "connectors", "value": "@number@"}
    entries["settings.json"] = json.dumps(settings).replace('"@number@"', number).encode()
    _refused(sealed_again(entries, tmp_path / "number.kazmaspace"), target["person"], code)


def test_an_accounts_number_is_one_too(moved, target, tmp_path) -> None:  # noqa: F811
    entries = entries_of(moved["bundle"])
    settings = json.loads(entries["settings.json"])
    settings[f"tenant.{A}.connectors.x.oauth2.account"] = {
        "category": "connectors", "value": {"id": "42", "followers": "@number@"}}
    settings.pop(f"tenant.{A}.connectors.x.account", None)
    key = next(k for k, kind in space_move._MOVED_SETTINGS.items() if kind == "account")
    settings[f"tenant.{A}.{key}"] = settings.pop(f"tenant.{A}.connectors.x.oauth2.account")
    entries["settings.json"] = json.dumps(settings).replace('"@number@"', "1e999").encode()
    _refused(sealed_again(entries, tmp_path / "account.kazmaspace"), target["person"], "bad_setting")


def test_a_setting_nested_too_deep_is_refused(moved, target, tmp_path) -> None:  # noqa: F811
    entries = entries_of(moved["bundle"])
    settings = json.loads(entries["settings.json"])
    settings["self_improvement.space_evolution"] = {
        "category": "learned", "value": {A: {"agents": {"supervisor": {"soul": "", "history": ["@deep@"]}}}}}
    deep = "[" * 900 + "]" * 900
    entries["settings.json"] = json.dumps(settings).replace('"@deep@"', deep).encode()
    _refused(sealed_again(entries, tmp_path / "lessons.kazmaspace"), target["person"], "bad_setting")


# ── a landed import ─────────────────────────────────────────────────────


def test_a_landed_import_is_not_called_taken_back(target, caplog) -> None:  # noqa: F811
    """A stop between the identity store and the journal's last write."""
    space = people.create_shared_space("Landed", by=OWNER_ACCOUNT_ID)["id"]
    own = _chat(space, "t-landed", "it is here")
    folder = space_move._space_folders(space)["space"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".kazma-arriving").write_text("00000005", encoding="ascii")
    _journaled(space, mark=5)
    with caplog.at_level(logging.INFO, logger="kazma_core.spaces.space_move"):
        assert space_move.settle_unfinished_imports() == []
    assert space_move.importing_spaces() == frozenset() and space in identity.read_registry()["spaces"]
    assert _said(own, "t-landed") == "it is here" and not (folder / ".kazma-arriving").exists()
    said = [r.getMessage() for r in caplog.records if space in r.getMessage()]
    assert not any("was taken back" in text for text in said) and any("had landed" in text for text in said)


# ── what connects an account ────────────────────────────────────────────

_FINISHES_ELSEWHERE = "the sign-in finishes in its callback, which binds it (oauth_sign_in.attempt_scope)"
_REMOVES = "it removes what a person gave"
NOT_CONNECTING = {
    "POST /api/calendar/oauth/google/disconnect": _REMOVES,
    "POST /api/calendar/oauth/google/start": _FINISHES_ELSEWHERE,
    "POST /api/calendar/oauth/microsoft/device/start": "it asks for a code; the mail card's poll keeps the answer",
    "POST /api/calendar/oauth/microsoft/disconnect": _REMOVES,
    "POST /api/calendar/oauth/microsoft/start": _FINISHES_ELSEWHERE,
    "POST /api/email/accounts/{alias}/remove": _REMOVES,
    "POST /api/email/gmail/disconnect": _REMOVES,
    "POST /api/email/oauth/gmail/start": _FINISHES_ELSEWHERE,
    "POST /api/email/oauth/microsoft/device/start": "it asks for a code; the poll keeps the answer",
    "POST /api/email/oauth/microsoft/disconnect": _REMOVES,
    "POST /api/email/oauth/microsoft/start": _FINISHES_ELSEWHERE,
    "POST /api/email/protocol/disconnect": _REMOVES,
    "POST /api/github/oauth/revoke": _REMOVES,
    "POST /api/github/oauth/start": _FINISHES_ELSEWHERE,
    "POST /api/x/disconnect": _REMOVES,
    "POST /api/x/oauth2/sign-out": _REMOVES,
    "POST /api/x/oauth2/start": _FINISHES_ELSEWHERE,
}


def _account_routes(routes: list[tuple[str, str]]) -> list[str]:
    """Every request that changes an account of a space, of *routes*."""
    from kazma_core.security import platform_rbac

    return sorted({
        f"{method} {path}" for method, path in routes
        if method in ("POST", "PUT", "PATCH", "DELETE")
        and platform_rbac._under(path, platform_rbac._SPACE_ACCOUNT_PREFIXES)
        and not platform_rbac._under(path, platform_rbac._INSTALL_ACCOUNT_PIECES)})


def _unsaid_routes(routes: list[tuple[str, str]], said: dict[str, str]) -> list[str]:
    from kazma_core.security.platform_rbac import CONNECT_ROUTES

    connect = {f"{method} {path}" for method, path in CONNECT_ROUTES}
    found = _account_routes(routes)
    return ([f"says nothing: {r}" for r in found if r not in connect and r not in said]
            + [f"said twice: {r}" for r in sorted(connect & set(said))]
            + [f"no such route: {r}" for r in sorted((connect | set(said)) - set(found))])


def test_every_request_that_changes_an_account_says_whether_it_connects(monkeypatch) -> None:
    from kazma_core.security.platform_rbac import CONNECT_ROUTES, connects_an_account
    from kazma_ui.app import create_app

    from tests.test_route_scopes import served_routes

    routes = served_routes(create_app())
    assert not _unsaid_routes(routes, NOT_CONNECTING)
    # Controls: a new route, and a route named and declared, are found.
    assert _unsaid_routes([*routes, ("POST", "/api/email/pigeon/connect")], NOT_CONNECTING) == [
        "says nothing: POST /api/email/pigeon/connect"]
    assert _unsaid_routes(routes, {**NOT_CONNECTING, "POST /api/github/token": "planted"}) == [
        "said twice: POST /api/github/token"]
    monkeypatch.setattr(scope, "_enforced", True)
    assert all(connects_an_account(path, method) for method, path in CONNECT_ROUTES)
    assert not any(connects_an_account(*reversed(r.split(" ", 1))) for r in NOT_CONNECTING)
    monkeypatch.setattr(scope, "_enforced", False)
    assert not any(connects_an_account(path, method) for method, path in CONNECT_ROUTES)


# ── whose sign-in moves a space ─────────────────────────────────────────


@pytest.mark.parametrize("principal, may", [
    ({"source": "api_token", "role": "operator", "user_id": "", "actor": "a token"}, False),
    ({"source": "api_token", "role": "viewer", "user_id": "", "actor": "a token"}, False),
    ({"source": "api_token", "role": "admin", "user_id": "", "actor": "a token"}, True),
    ({"source": "secret", "role": "admin", "user_id": "", "actor": "secret"}, True),
], ids=["operator token", "viewer token", "admin token", "the secret"])
def test_no_token_below_admin_acts_as_the_owner_of_a_space(tmp_path, monkeypatch, principal, may) -> None:
    """Every API token is the owner's account: an operator token, handed to
    another agent, moved the owner's shared space out -- its secrets too --
    and could export, read and delete it."""
    from tests.test_space_bundle_round_trip import _client

    with own_install(monkeypatch, tmp_path / "install"):
        space = people.create_shared_space("Ours", by=OWNER_ACCOUNT_ID)["id"]
        _chat(space, "t-ours", "ours")
        sealed = {"password": PASSWORD}
        asks = (("POST", f"/api/spaces/{space}/move", sealed), ("POST", f"/api/spaces/{space}/export", sealed),
                ("GET", f"/api/spaces/{space}/counts", None), ("GET", f"/api/spaces/{space}/members", None),
                ("DELETE", f"/api/spaces/{space}", None))
        with _client(principal) as client:
            for method, path, body in asks:
                answer = client.request(method, path, json=body)
                if may:
                    assert answer.status_code == 200, (method, path, answer.text)
                else:
                    assert answer.status_code == 403 and answer.json()["error"] == "own_sign_in_only", (path, answer)
        assert (space in identity.read_registry()["spaces"]) is not may
