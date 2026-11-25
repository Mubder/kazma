"""``scripts/scan_old_sessions.py`` files a chat's facts in the chat's own
space, under the person who asked (Spaces; found by the chat move, 25 Nov 2026).

It read every space's chats and filed all their facts under one
``--tenant-id``, the owner's by default; it named no speaker, so in a shared
space one member's "my city" was everyone's; it never read whether Spaces is
on; and on a Postgres install it read a leftover SQLite file.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from kazma_core.memory import speakers
from kazma_core.spaces import identity, people, preconditions, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_ui import session_manager

from tests._space_world import own_install

ROOT = Path(__file__).resolve().parents[1]
CITIES = {"en": "My favorite city is {city}.", "ar": "مدينتي المفضلة هي {city}."}


def _scan():
    spec = importlib.util.spec_from_file_location("_scan_spaces", ROOT / "scripts" / "scan_old_sessions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_embedder(monkeypatch):
    """What is filed where is asked here, never what it means."""
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda *_a, **_k: None)


class Install:
    def __init__(self, data: Path) -> None:
        self.data = data
        self.store = session_manager.SessionManager(db_path=str(data / "chat_sessions.db"))
        self.reem = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        self.omar = people.create_person("Omar", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        self.family = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)["id"]
        for person in (self.reem, self.omar):
            people.add_member(self.family, person["id"], by=OWNER_ACCOUNT_ID)

    def chat(self, space: str, *said: tuple[str, str]) -> str:
        """A chat of *space*: (author, text) of each question."""
        session = f"s{uuid.uuid4().hex[:8]}"
        who = next((author for author, _t in said if author), OWNER_ACCOUNT_ID)
        with scope.bind_scope(space, who):
            row = self.store.get_or_create(session, thread_id=f"t-{session}")
            for author, text in said:
                row.add_message("user", text, **({"author": author} if author else {}))
                row.add_message("assistant", "Noted.")
            self.store.put(row)
        threads.record_thread(f"t-{session}", space, who)
        return session

    def scan(self, *argv: str) -> None:
        scan = _scan()
        before = sys.argv
        sys.argv = ["scan", *argv]
        try:
            asyncio.run(scan.main_async())
        finally:
            sys.argv = before

    def facts(self) -> set[tuple[str, str, str]]:
        """(space, under whom, what) of every fact the scan filed."""
        with closing(sqlite3.connect(str(self.data / "memory_state.db"))) as conn:
            try:
                return {(r[0], r[1], r[2].lower()) for r in conn.execute(
                    "SELECT tenant_id, subject, object FROM beliefs WHERE extraction_method = 'retroactive_scan'")}
            except sqlite3.OperationalError:
                return set()

    def cities(self) -> set[tuple[str, str, str]]:
        return {f for f in self.facts() if any(c in f[2] for c in ("muscat", "salalah", "nizwa", "مسقط", "صلالة", "نزوى"))}


@pytest.fixture
def install(monkeypatch, tmp_path):
    monkeypatch.setattr(preconditions, "missing", lambda **_k: [])
    monkeypatch.setattr(threads, "_spaces", {})
    with own_install(monkeypatch, tmp_path / "one") as data:
        # The switch is stored: the script's process reads it itself.
        monkeypatch.setattr(identity, "enforcement_enabled", lambda: True)
        made = Install(data)
        try:
            yield made
        finally:
            made.store.close()


def _said(lang: str, city: str) -> str:
    return CITIES[lang].format(city=city)


NAMES = {"en": ("Muscat", "Salalah", "Nizwa"), "ar": ("مسقط", "صلالة", "نزوى")}


@pytest.mark.parametrize("lang", NAMES)
def test_each_chat_is_filed_in_its_own_space_under_its_speaker(install, lang):
    one, two, three = NAMES[lang]
    install.chat(OWNER_SPACE_ID, (OWNER_ACCOUNT_ID, _said(lang, one)))
    install.chat(install.reem["personal_space"], (install.reem["id"], _said(lang, two)))
    install.chat(install.family, (install.reem["id"], _said(lang, two)), (install.omar["id"], _said(lang, three)))
    install.scan("--source", "chat_sessions")
    assert install.cities() == {
        (OWNER_SPACE_ID, "user", one.lower()),
        (install.reem["personal_space"], "user", two.lower()),
        (install.family, speakers.entry_id(install.reem["id"]), two.lower()),
        (install.family, speakers.entry_id(install.omar["id"]), three.lower()),
    }
    before = install.facts()
    install.scan("--source", "chat_sessions")
    assert install.facts() == before, "run again, it files nothing"


def test_a_nameless_turn_of_a_shared_space_files_no_first_person_fact(install):
    install.chat(install.family, ("", _said("en", "Muscat")))
    install.scan("--source", "chat_sessions")
    assert install.cities() == set()


def test_a_person_who_left_the_space_gets_no_facts_there_again(install):
    install.chat(install.family, (install.omar["id"], _said("en", "Nizwa")),
                 (install.reem["id"], _said("en", "Salalah")))
    people.remove_member(install.family, install.omar["id"], by=OWNER_ACCOUNT_ID)
    install.scan("--source", "chat_sessions")
    assert {f[1] for f in install.cities()} == {speakers.entry_id(install.reem["id"])}


def test_one_space_can_be_named(install):
    install.chat(OWNER_SPACE_ID, (OWNER_ACCOUNT_ID, _said("en", "Muscat")))
    install.chat(install.reem["personal_space"], (install.reem["id"], _said("en", "Salalah")))
    install.scan("--source", "chat_sessions", "--tenant-id", install.reem["personal_space"])
    assert {f[0] for f in install.cities()} == {install.reem["personal_space"]}


def test_a_forgotten_chat_stays_out_in_its_own_space(install):
    from kazma_core.memory import forget

    session = install.chat(install.reem["personal_space"], (install.reem["id"], _said("en", "Salalah")))
    with scope.bind_scope(install.reem["personal_space"], install.reem["id"]):
        forget.set_chat_remembered(f"t-{session}", False, tenant_id=install.reem["personal_space"])
    install.chat(install.reem["personal_space"], (install.reem["id"], _said("en", "Muscat")))
    install.scan("--source", "chat_sessions")
    assert {f[2] for f in install.cities()} == {"muscat"}


def test_the_same_chat_id_scanned_in_another_space_is_not_this_one_s(install):
    """The "already scanned" check is of the space."""
    session = install.chat(OWNER_SPACE_ID, (OWNER_ACCOUNT_ID, _said("en", "Muscat")))
    install.scan("--source", "chat_sessions")
    with closing(sqlite3.connect(str(install.data / "memory_state.db"))) as conn, conn:
        conn.execute("UPDATE beliefs SET tenant_id = 'another-space' WHERE source_session = ?", (session,))
    install.scan("--source", "chat_sessions")
    assert (OWNER_SPACE_ID, "user", "muscat") in install.facts()


def _step(install: Install, thread: str, space: str, text: str, state_space: str = "") -> None:
    state = {"messages": [{"role": "user", "content": text}, {"role": "assistant", "content": "Noted."}]}
    if state_space:
        state["tenant_id"] = state_space
    with closing(sqlite3.connect(str(install.data / "snapshots.db"))) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS snapshots (id TEXT, thread_id TEXT, state_json TEXT, timestamp TEXT)")
        conn.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                     (uuid.uuid4().hex, thread, json.dumps(state), datetime.now(UTC).isoformat()))
    if space:
        threads.record_thread(thread, space, install.reem["id"])


def test_a_saved_step_is_filed_where_its_thread_is(install):
    mine = install.reem["personal_space"]
    _step(install, "step-mine", mine, _said("en", "Salalah"))
    _step(install, "step-family", install.family, _said("en", "Muscat"))  # a step names no author
    _step(install, "step-old", "", _said("en", "Nizwa"))  # from before Spaces: the owner's (N19)
    install.scan("--source", "snapshots")
    assert install.cities() == {(mine, "user", "salalah"), (OWNER_SPACE_ID, "user", "nizwa")}


def test_one_account_is_scanned_as_before(monkeypatch, tmp_path):
    """Spaces off: the owner's chats, under ``user``, whoever they name."""
    with own_install(monkeypatch, tmp_path / "off") as data:
        monkeypatch.setattr(scope, "_enforced", False)
        monkeypatch.setattr(identity, "enforcement_enabled", lambda: False)
        store = session_manager.SessionManager(db_path=str(data / "chat_sessions.db"))
        try:
            row = store.get_or_create("one", thread_id="t-one")
            row.add_message("user", _said("en", "Muscat"))
            row.add_message("assistant", "Noted.")
            store.put(row)
        finally:
            store.close()
        made = Install.__new__(Install)
        made.data = data
        made.scan()
        assert made.cities() == {(OWNER_SPACE_ID, "user", "muscat")}


def test_a_switch_that_cannot_be_read_stops_the_scan(install, monkeypatch):
    def unreadable():
        raise identity.IdentityUnavailable("the settings store is down")

    install.chat(install.family, (install.reem["id"], _said("en", "Muscat")))
    monkeypatch.setattr(identity, "enforcement_enabled", unreadable)
    with pytest.raises(identity.IdentityUnavailable):
        install.scan()
    assert install.facts() == set()


def test_the_store_in_use_is_read_and_a_waiting_save_counts(install):
    """Through the one reader (Postgres too); the SQLite file was opened by name."""
    scan = _scan()
    session = install.chat(OWNER_SPACE_ID, (OWNER_ACCOUNT_ID, _said("en", "Muscat")))
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        row = install.store.get(session)
        row.add_message("user", _said("en", "Nizwa"))
        row.add_message("assistant", "Noted.")
        row.updated_at = datetime.now(UTC).isoformat()
        install.store._spool_session(row, OSError("the database refused it"))
    (found,) = scan.load_sessions()
    assert [t[0] for t in scan.extract_turns(found["messages"])] == [_said("en", "Muscat"), _said("en", "Nizwa")]
    assert "sqlite3.connect(chat_db" not in (ROOT / "scripts/scan_old_sessions.py").read_text(encoding="utf-8")


def test_the_newest_are_scanned_first_and_a_limit_holds(install):
    scan = _scan()
    made = [install.chat(OWNER_SPACE_ID, (OWNER_ACCOUNT_ID, _said("en", "Muscat"))) for _ in range(3)]
    assert [s["session_id"] for s in scan.load_sessions(max_sessions=2)] == made[:0:-1]
    newest = scan.load_sessions()[0]["updated_at"]
    assert [s["session_id"] for s in scan.load_sessions(since=newest)] == [made[-1]]


def test_control_one_tenant_for_all_files_everyone_s_under_the_owner(install, monkeypatch):
    """The old script: every chat under one tenant, no speaker."""
    from kazma_core.memory.task_queue import TaskBinding

    scan = _scan()
    monkeypatch.setattr(scan, "_binding", lambda space, _author: TaskBinding(space=space))
    install.chat(install.reem["personal_space"], (install.reem["id"], _said("en", "Salalah")))
    (session,) = scan.load_sessions()
    with closing(sqlite3.connect(str(install.data / "memory_state.db"), isolation_level=None)) as primary, \
            closing(sqlite3.connect(str(install.data / "memory_ops.db"), isolation_level=None)) as ops:
        from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

        primary.row_factory = sqlite3.Row
        ensure_primary_schema(primary)
        ensure_ops_schema(ops)
        asyncio.run(scan.scan_session(primary, ops, {**session, "tenant_id": OWNER_SPACE_ID}))
    assert install.cities() == {(OWNER_SPACE_ID, "user", "salalah")}, "the fault"
