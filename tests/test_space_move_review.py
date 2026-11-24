"""The review of a space's move (Spaces S4-06, ``spaces/space_move.py``).

The first version (052dd4be3) had none of the owner's four decisions and
left eight findings open. Each test here fails on it.

**The owner's decisions**

* **Only the owner arrives.** Another member's memory entry, their work and
  their share of the documents do not travel; what they wrote stays the
  space's, written by ``identity.DEPARTED_ACCOUNT``, whose work is refused
  here. A bundle carrying any of it is refused.
* **One AEAD seals the bundle**, over every byte: the entries' names and
  which entries there are. A changed, cut, grown or re-listed file does not
  open; no name is in the clear.
* **Live work arrives stopped**: a scheduled job, a swarm task, a research
  session, an X post due to publish, an approval card a chat waited on.
* **No ledger row crosses**: the model-spend ledger (a $5,000 row dated today
  counted toward the daily limit), the forget journal, the action record.

**The findings**: a space's own checkpoint file arrives in its own file here
(1), the scheduled jobs' outboxes never arrive (2), a chat's state never
arrives and a setting is taken only in the forms a space's own take, each
value checked (3), another member's connected account stays (4), the seal
is the whole file's (5), live work and spend (6), another member's work (7),
and the entries are read as streams, nothing plain is written to a temp
folder, an export too big to bring in is refused, and the guide says the
project folder's files stay (8).
"""

from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from kazma_core import paths
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope, space_data, space_move
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_world import A, _where_word, own_install, plant_world
from tests.test_space_bundle_round_trip import PASSWORD, TARGET_KEY, entries_of, sealed_again

DEPARTED = identity.DEPARTED_ACCOUNT


def _store_path(name: str) -> Path:
    stores = dict(space_data._store_paths())
    if name == "chat_sessions.db" and name not in stores:
        name = "chat_sessions_test.db"  # the transcript store under pytest
    return stores[name]


def _clone(store: str, table: str, key: str, changes: dict[str, Any]) -> dict[str, Any]:
    """A copy of one of space A's planted rows of *table*, with *changes*
    (a fresh *key*): every NOT NULL column already filled."""
    with closing(sqlite3.connect(str(_store_path(store)))) as conn:
        conn.row_factory = sqlite3.Row
        rule = space_data._table_rule(store, table, space_data._columns(conn, table))
        clause, params = space_data._where(rule, space_data._context(A, space_data._store_paths()))[0]
        row = dict(conn.execute(f'SELECT * FROM "{table}" WHERE {clause} LIMIT 1', params).fetchone())
        row.update({key: f"{key}-{uuid.uuid4().hex[:8]}"}, **changes)
        conn.execute(f'INSERT INTO "{table}" ({", ".join(row)}) VALUES ({", ".join("?" * len(row))})',
                     tuple(row.values()))
        conn.commit()
    return row


def _a_thread() -> str:
    from kazma_core.spaces import threads

    return sorted(threads.space_threads(A)[1])[0]


def _paused_at_a_card(thread: str, asker: str = "", names: tuple[str, ...] = ()) -> str:
    """Space A's own checkpoint file, holding *thread* paused at an approval
    card (an interrupt waiting, as LangGraph writes it) that names *asker*,
    its answerers and their *names*; its state names who spoke. Returns its
    id."""
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Interrupt

    from kazma_core.checkpoint_serde import kazma_checkpoint_serde

    gate = f"intr-{uuid.uuid4().hex[:12]}"
    with closing(sqlite3.connect(str(paths.space_checkpoints_db(A)), check_same_thread=False)) as conn:
        saver = SqliteSaver(conn, serde=kazma_checkpoint_serde())
        saver.setup()
        state = empty_checkpoint()
        state["channel_values"] = {"_post_turn_memory": {"said_by": asker, "turns": [1, 2]},
                                   "opened": {"said_by": OWNER_ACCOUNT_ID}}
        config = saver.put({"configurable": {"thread_id": thread, "checkpoint_ns": ""}}, state,
                           {"source": "loop", "step": 1, "asker": asker}, {})
        card = {"tool": "x_post", "asker": asker, "answered_by": [asker, OWNER_ACCOUNT_ID],
                "answered_by_names": list(names)}
        saver.put_writes(config, [("__interrupt__", (Interrupt(value=card, id=gate),))], "task-1")
    return gate


@pytest.fixture(scope="module")
def moved(tmp_path_factory) -> dict[str, Any]:
    """Space A of S1-27's world with a second member, Maya, who has an
    entry, work, a share and a connection of her own; the owner's own live
    work, a ledger row, a chat's state, a chat paused at a card. Made into a
    bundle by the owner."""
    from kazma_core.security.vault import get_vault

    folder = tmp_path_factory.mktemp("review-source")
    from kazma_core.spaces import preconditions

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        # As the root conftest does for each test: a second account may be made.
        patch.setattr(preconditions, "missing", lambda **_kw: [])
        world_gen = plant_world(patch, folder)
        world = next(world_gen)
        try:
            maya = people.create_person(f"maya-{uuid.uuid4().hex[:4]}", "a-long-enough-password",
                                        by=OWNER_ACCOUNT_ID)["id"]
            people.add_member(A, maya, by=OWNER_ACCOUNT_ID)
            entry = f"member_{maya}"
            _clone("memory_state.db", "beliefs", "id", {"subject": entry, "object": "diabetic"})
            _clone("memory_state.db", "entities", "id", {"id": entry})
            _clone("memory_state.db", "memory_member_profile", "account", {"account": maya, "about": "maya-about"})
            # The owner's own entry: a fact filed under it arrives as the
            # new owner's.
            _clone("memory_state.db", "beliefs", "id",
                   {"subject": f"member_{OWNER_ACCOUNT_ID}", "object": "owner-fact"})
            owner_about = _clone("memory_state.db", "memory_member_profile", "account",
                                 {"account": OWNER_ACCOUNT_ID, "about": "owner-about"})
            maya_job = _clone("cron.db", "cron_jobs", "job_id", {"creator": maya, "status": "pending"})
            owner_job = _clone("cron.db", "cron_jobs", "job_id", {"creator": OWNER_ACCOUNT_ID, "status": "pending",
                                                                    "delivery_target": "telegram:old-chat"})
            maya_task = _clone("swarm_tasks.db", "swarm_tasks", "id",
                               {"metadata": json.dumps({"tenant_id": A, "asker": maya}), "status": "pending"})
            owner_task = _clone("swarm_tasks.db", "swarm_tasks", "id", {"status": "running", "metadata": json.dumps(
                {"tenant_id": A, "asker": OWNER_ACCOUNT_ID, "source_delivery_target": "telegram:old-chat"})})
            maya_research = _clone("research_sessions.db", "research_sessions", "id", {"asker": maya})
            owner_research = _clone("research_sessions.db", "research_sessions", "id",
                                    {"asker": OWNER_ACCOUNT_ID, "status": "running"})
            maya_acl = _clone("documents.db", "document_acl", "document_id", {"principal_id": maya})
            thread = _a_thread()
            chat = _clone("chat_sessions.db", "sessions", "session_id", {"thread_id": thread, "messages": json.dumps(
                [{"role": "user", "content": "from maya", "author": maya},
                 {"role": "user", "content": "from the owner", "author": OWNER_ACCOUNT_ID}])})
            owner_post = _clone("x_publications.db", "x_operations", "id", {
                "state": "scheduled", "metadata": json.dumps({"booker": OWNER_ACCOUNT_ID}),
                "idempotency_key": f"idem-{uuid.uuid4().hex[:8]}"})
            _clone("x_publications.db", "x_composers", "actor", {"actor": maya})
            with closing(sqlite3.connect(str(_store_path("llm_calls.db")))) as conn:
                conn.execute("UPDATE llm_calls SET cost_usd = 5000, ts = ?, asker = ? WHERE space = ?",
                             (time.time(), OWNER_ACCOUNT_ID, A))
                conn.commit()
            with scope.bind_scope(A, maya):
                get_vault().for_space(A).store(f"space:{A}:email.gmail.refresh_token", "maya-token-secret")
                get_config_store().set(f"tenant.{A}.connectors.github.token", "maya-github-secret",
                                       category="connectors")
            # The hourly check refreshes her tokens as the space's maker, and
            # so does an owner's own turn: her account stays hers.
            with scope.bind_scope(A, OWNER_ACCOUNT_ID):
                get_vault().for_space(A).store(f"space:{A}:email.gmail.refresh_token", "maya-token-secret-2")
                get_config_store().set(f"tenant.{A}.connectors.github.token", "maya-github-secret-2",
                                       category="connectors")
                get_vault().for_space(A).store(f"space:{A}:own-secret", "the-owners-secret")
            get_config_store().set(f"yolo.{thread}", {"until": 4102444800}, category="runtime")
            get_config_store().set("active_thread.telegram:4242", thread, category="runtime")
            from kazma_core.spaces import threads

            waiting = f"thread-waiting-{uuid.uuid4().hex[:6]}"
            threads.record_thread(waiting, A, OWNER_ACCOUNT_ID)
            gate = _paused_at_a_card(waiting, maya, ("Maya Alnasser",))
            source_key = {r["key_id"] for r in get_vault()._space_rows(A)}
            bundle = folder / "a.kazmaspace"
            manifest = space_move.export_bundle(A, bundle, PASSWORD, by=OWNER_ACCOUNT_ID)
            # What is left on the old install: its secrets, and their key now.
            left = {"key_ids": {r["key_id"] for r in get_vault()._space_rows(A)}, "before": source_key,
                    "held": [str(r[0]) for r in get_vault()._conn.execute(
                        "SELECT key_id FROM space_keys WHERE space_id = ?", (A,))]}
            with scope.bind_scope(A, OWNER_ACCOUNT_ID):
                left["opens"] = get_vault().for_space(A).retrieve(f"space:{A}:own-secret")
            left["recorded"] = [e for e in identity.read_registry()["events"]
                                if e.get("kind") == "space_key_changed" and e.get("space") == A]
        finally:
            world_gen.close()
    return {"bundle": bundle, "manifest": manifest, "maya": maya, "words": world["words"], "thread": thread,
            "waiting": waiting, "gate": gate, "maya_job": maya_job, "owner_job": owner_job, "maya_task": maya_task,
            "owner_task": owner_task, "maya_research": maya_research, "owner_research": owner_research,
            "maya_acl": maya_acl, "chat": chat, "owner_post": owner_post, "owner_about": owner_about,
            "folder": folder, "left": left}


@pytest.fixture
def target(tmp_path, monkeypatch) -> Any:
    with own_install(monkeypatch, tmp_path / "target", vault_key=TARGET_KEY) as data:
        person = people.create_person(f"mover-{uuid.uuid4().hex[:4]}", "a-long-enough-password",
                                      by=OWNER_ACCOUNT_ID)
        yield {"data": data, "person": person["id"]}


def _bring_in(bundle: Path, owner: str) -> dict[str, Any]:
    return space_move.import_bundle(bundle, PASSWORD, owner=owner, by=OWNER_ACCOUNT_ID)


def _refused(bundle: Path, owner: str, code: str) -> None:
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(bundle, owner)
    assert refused.value.code == code, (refused.value.code, str(refused.value))
    _ctx, found = space_data._walk(A)
    assert sum(item.rows for item in found) == 0
    assert A not in identity.read_registry()["spaces"]
    # Nothing of it, not even an empty file of its own (46f200bcb left one).
    assert not list(paths.space_checkpoints_db(A).parent.glob(f"checkpoints_{A}.db*"))


def _rows(store: str, table: str, where: str = "1", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    path = paths.space_checkpoints_db(A) if store == space_move.CHECKPOINTS else _store_path(store)
    with closing(sqlite3.connect(str(path))) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f'SELECT * FROM "{table}" WHERE {where}', params)]


# ── only the owner arrives ──────────────────────────────────────────────


def test_only_the_owner_arrives(moved, target) -> None:
    """Maya's entry, her About me, her job, task, research, share and
    composer stay; what she wrote is the departed person's; her id is
    nowhere here."""
    owner = target["person"]
    _bring_in(moved["bundle"], owner)
    assert _where_word(moved["maya"]) == {}, "Maya's id arrived"
    assert _where_word("maya-about") == {} and not _rows("memory_state.db", "beliefs", "object = 'diabetic'")
    assert not _rows("cron.db", "cron_jobs", "job_id = ?", (moved["maya_job"]["job_id"],))
    assert not _rows("swarm_tasks.db", "swarm_tasks", "id = ?", (moved["maya_task"]["id"],))
    assert not _rows("research_sessions.db", "research_sessions", "id = ?", (moved["maya_research"]["id"],))
    assert not _rows("documents.db", "document_acl", "document_id = ?", (moved["maya_acl"]["document_id"],))
    # What she wrote is the space's, by someone who left; the owner's own
    # words are the new owner's here.
    chat = _rows("chat_sessions.db", "sessions", "session_id = ?", (moved["chat"]["session_id"],))[0]
    authors = {m["content"]: m["author"] for m in json.loads(chat["messages"])}
    assert authors == {"from maya": DEPARTED, "from the owner": owner}
    about = _rows("memory_state.db", "memory_member_profile", "account = ?", (owner,))
    assert [r["about"] for r in about] == ["owner-about"]
    # The new owner is the space's only member; a departed person's work
    # does not run here as anyone.
    reg = identity.read_registry()
    assert [m for m in reg["memberships"] if m["space"] == A] == [{"account": owner, "space": A, "role": "owner"}]
    with pytest.raises(identity.AskerLeft):
        identity.work_scope(A, DEPARTED)


def test_a_bundle_carrying_another_members_entry_work_or_a_person_here_is_refused(moved, target, tmp_path) -> None:
    entries = entries_of(moved["bundle"])

    def with_row(store: str, table: str, change: dict[str, Any]) -> Path:
        changed = dict(entries)
        name = f"stores/{store}/{table}.jsonl"
        row = json.loads(changed[name].decode("utf-8").splitlines()[0])
        row.update(change)
        changed[name] = changed[name] + (json.dumps(row) + "\n").encode("utf-8")
        return sealed_again(changed, tmp_path / f"{uuid.uuid4().hex[:6]}.kazmaspace")

    owner = target["person"]
    _refused(with_row("memory_state.db", "beliefs", {"id": "b-planted", "subject": "member_someone"}),
             owner, "foreign_member")
    _refused(with_row("cron.db", "cron_jobs", {"job_id": "j-planted", "creator": DEPARTED}), owner, "members_work")
    _refused(with_row("cron.db", "cron_jobs", {"job_id": "j-planted", "creator": "pother"}), owner, "members_work")
    # A person of this install, named by a row: never who a bundle says.
    here = people.create_person(f"here-{uuid.uuid4().hex[:4]}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    _refused(with_row("thread_scopes.db", "thread_scopes", {"thread_id": "t-planted", "opener": here["id"]}),
             owner, "foreign_person")


# ── one AEAD over the whole file ────────────────────────────────────────


def test_the_whole_file_is_sealed(moved, target, tmp_path) -> None:
    raw = moved["bundle"].read_bytes()
    assert raw.startswith(b"KZSEALED")
    header = json.loads(raw[12:12 + int.from_bytes(raw[8:12], "big")])
    assert header["kdf"] == "scrypt" and header["n"] >= 2**17
    for name in (b"secrets.jsonl", b"keys.json", b"settings.json", b"bundle.json", b"stores/", b"files/"):
        assert name not in raw  # nothing of the zip in the clear: names, the set of entries
    owner = target["person"]
    for label, broken in (("a byte changed", raw[:500] + bytes([raw[500] ^ 1]) + raw[501:]),
                          ("cut short", raw[:-100]),
                          ("grown", raw + b"\x00" * 64),
                          ("a chunk dropped", raw[:-(65536 + 16)] if len(raw) > 70000 else raw[:-20])):
        path = tmp_path / f"{label.replace(' ', '-')}.kazmaspace"
        path.write_bytes(broken)
        with pytest.raises(space_move.SpaceMoveRefused) as refused:
            _bring_in(path, owner)
        assert refused.value.code == "changed", (label, refused.value.code)
    altered = bytearray(raw)
    altered[20] ^= 1  # inside the header: its parameters are bound into every chunk
    path = tmp_path / "header.kazmaspace"
    path.write_bytes(bytes(altered))
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(path, owner)
    assert refused.value.code in ("not_a_bundle", "wrong_password", "changed")
    assert A not in identity.read_registry()["spaces"]


def test_an_entry_dropped_or_added_inside_the_seal_is_refused(moved, target, tmp_path) -> None:
    """With the password, the zip is changed and sealed again, its list of
    entries left as it was: a secrets entry renamed imported 0 of 2 secrets
    on 052dd4be3."""
    entries = entries_of(moved["bundle"])
    renamed = dict(entries)
    renamed["secrets-renamed.jsonl"] = renamed.pop("secrets.jsonl")
    _refused(sealed_again(renamed, tmp_path / "renamed.kazmaspace", relist=False), target["person"], "changed")
    dropped = {k: v for k, v in entries.items() if k != "keys.json"}
    _refused(sealed_again(dropped, tmp_path / "dropped.kazmaspace", relist=False), target["person"], "changed")
    # Re-listed to match, a missing secrets entry still disagrees with the
    # count the manifest holds.
    manifest_kept = {k: v for k, v in entries.items() if k != "secrets.jsonl"}
    _refused(sealed_again(manifest_kept, tmp_path / "relisted.kazmaspace"), target["person"], "changed")


# ── live work arrives stopped ───────────────────────────────────────────


def test_live_work_arrives_stopped(moved, target) -> None:
    from kazma_core.safety import hitl_gates

    owner = target["person"]
    taken = _bring_in(moved["bundle"], owner)
    job = _rows("cron.db", "cron_jobs", "job_id = ?", (moved["owner_job"]["job_id"],))[0]
    assert job["status"] == "cancelled" and job["last_outcome"] == "moved"
    assert job["delivery_target"] == "", "never the old install's address; the new owner has linked none"
    task = _rows("swarm_tasks.db", "swarm_tasks", "id = ?", (moved["owner_task"]["id"],))[0]
    meta = json.loads(task["metadata"])
    assert task["status"] == "cancelled" and meta["interrupted_by"] == "moved"
    assert meta["execution_outcome"] == "unknown" and "source_delivery_target" not in meta
    research = _rows("research_sessions.db", "research_sessions", "id = ?", (moved["owner_research"]["id"],))[0]
    assert research["status"] == "interrupted"
    post = _rows("x_publications.db", "x_operations", "id = ?", (moved["owner_post"]["id"],))[0]
    assert post["state"] == "awaiting_approval"
    # The card the chat waited on was asked on the other install: expired here.
    assert taken["cards_expired"] == 1
    gate = hitl_gates.gate_for(moved["gate"])
    assert gate is not None and gate.state == "timeout" and gate.thread_id == moved["waiting"]


def test_a_crawl_does_not_travel(moved, target) -> None:
    _bring_in(moved["bundle"], target["person"])
    assert not any(key.startswith("acquisition.") for group in get_config_store().get_all().values()
                   for key in group)


# ── no ledger row crosses ───────────────────────────────────────────────


def test_no_ledger_row_crosses(moved, target, tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    from kazma_core.observability import llm_ledger

    owner = target["person"]
    _bring_in(moved["bundle"], owner)
    for store, table in (("llm_calls.db", "llm_calls"), ("activity.db", "activity"),
                         ("memory_forget_journal.db", "forget_journal")):
        if store in dict(space_data._store_paths()):
            column = "space" if table in ("llm_calls", "activity") else "tenant_id"
            assert not _rows(store, table, f'"{column}" = ?', (A,)), table
    spent = llm_ledger.usage_of(owner, datetime.now(UTC) - timedelta(days=1)) or {}
    assert float(spent.get("cost") or 0) == 0.0


def test_a_bundle_carrying_a_ledger_is_refused(moved, target, tmp_path) -> None:
    owner = target["person"]
    entries = entries_of(moved["bundle"])
    entries["stores/llm_calls.db/llm_calls.jsonl"] = (json.dumps(
        {"space": A, "asker": OWNER_ACCOUNT_ID, "cost_usd": 5000, "ts": time.time()}) + "\n").encode("utf-8")
    _refused(sealed_again(entries, tmp_path / "ledger.kazmaspace"), owner, "not_moved")


# ── the findings ────────────────────────────────────────────────────────


def test_1_the_spaces_own_checkpoints_arrive_in_its_own_file(moved, target) -> None:
    """The waiting chat's checkpoints were kept in the space's own file
    (``checkpoints_<space>.db``), which 052dd4be3 refused as a store this
    install does not keep."""
    _bring_in(moved["bundle"], target["person"])
    assert _rows(space_move.CHECKPOINTS, "checkpoints", "thread_id = ?", (moved["waiting"],))
    assert _rows(space_move.CHECKPOINTS, "checkpoints", "thread_id = ?", (moved["thread"],))


def test_1_a_bundle_naming_another_checkpoint_file_is_refused(moved, target, tmp_path) -> None:
    """It could write into another tenant's file."""
    entries = entries_of(moved["bundle"])
    entries["stores/checkpoints_other.db/checkpoints.jsonl"] = entries.pop("stores/checkpoints/checkpoints.jsonl")
    _refused(sealed_again(entries, tmp_path / "family.kazmaspace"), target["person"], "unknown_table")


def test_2_the_scheduled_jobs_outboxes_never_arrive(moved, target, tmp_path) -> None:
    owner = target["person"]
    _bring_in(moved["bundle"], owner)
    assert not _rows("cron.db", "cron_deliveries", "tenant_id = ?", (A,))
    assert not _rows("cron.db", "cron_alerts")
    for table in ("cron_deliveries", "cron_alerts"):
        assert f"stores/cron.db/{table}.jsonl" not in moved["manifest"]["entries"]


def test_2_a_bundle_carrying_an_outbox_is_refused(moved, target, tmp_path) -> None:
    entries = entries_of(moved["bundle"])
    job = json.loads(entries["stores/cron.db/cron_jobs.jsonl"].decode("utf-8").splitlines()[0])
    entries["stores/cron.db/cron_alerts.jsonl"] = (json.dumps(
        {"id": 1, "job_id": job["job_id"], "ops_key": "x", "title": "spoofed", "severity": "error"}) + "\n").encode()
    _refused(sealed_again(entries, tmp_path / "alerts.kazmaspace"), target["person"], "not_moved")


def test_2_a_bundle_carrying_waiting_messages_is_refused(moved, target, tmp_path) -> None:
    """A delivery taken here would be sent, to whatever address it names."""
    entries = entries_of(moved["bundle"])
    job = json.loads(entries["stores/cron.db/cron_jobs.jsonl"].decode("utf-8").splitlines()[0])
    entries["stores/cron.db/cron_deliveries.jsonl"] = (json.dumps(
        {"id": 1, "job_id": job["job_id"], "tenant_id": A, "target": "telegram:4242", "content": "spoofed",
         "state": "pending"}) + "\n").encode()
    _refused(sealed_again(entries, tmp_path / "deliveries.kazmaspace"), target["person"], "not_moved")


def test_3_a_chats_state_never_arrives(moved, target) -> None:
    _bring_in(moved["bundle"], target["person"])
    held = {key for group in get_config_store().get_all().values() for key in group}
    assert not any(key.startswith(space_data._CHAT_STATE_PREFIXES) for key in held)
    settings = json.loads(entries_of(moved["bundle"])["settings.json"])
    assert all(key.startswith(f"tenant.{A}.") or key in dict(space_data._BY_SPACE_MAPPINGS) for key in settings)


@pytest.mark.parametrize("key, value, code", [
    ("active_thread.telegram:4242", "thread", "foreign_setting"),
    ("yolo.THREAD", {"until": 4102444800}, "foreign_setting"),
    ("session.THREAD", {"owner": "x"}, "foreign_setting"),
    (f"tenant.{A}.mcp.servers", [{"name": "x", "command": "calc.exe"}], "foreign_setting"),
    (f"tenant.{A}.research.papers_index", [], "foreign_setting"),
    (f"tenant.{A}.connectors.x.ai_usage", {"calls": 0}, "foreign_setting"),
    (f"tenant.{A}.connectors.x.drafting.reference_files", ["C:/Users/admin/.ssh/id_ed25519"], "foreign_setting"),
    (f"tenant.{A}.connectors.x.reply.summoners", ["someone"], "foreign_setting"),
    (f"tenant.{A}.connectors.x.ai", {"provider": "x", "model": "y"}, "foreign_setting"),
    (f"tenant.{A}.connectors.x.oauth2.expires_at", "soon", "bad_setting"),
    (f"tenant.{A}.connectors.x.account", {"id": {"nested": 1}}, "bad_setting"),
    (f"tenant.{A}.connectors.x.oauth2.access_token", "plain-token", "bad_setting"),
    (f"tenant.{A}.connectors.x.api_key", f"vault://cfg:tenant.{A}.connectors.x.api_key", "missing_secret"),
])
def test_3_a_setting_is_taken_only_in_the_spaces_own_forms(moved, target, tmp_path, key, value, code) -> None:
    entries = entries_of(moved["bundle"])
    settings = json.loads(entries["settings.json"])
    settings[key.replace("THREAD", moved["thread"])] = {"category": "runtime", "value": value}
    entries["settings.json"] = json.dumps(settings).encode("utf-8")
    _refused(sealed_again(entries, tmp_path / "setting.kazmaspace"), target["person"], code)


def test_4_another_members_connected_account_stays(moved, target) -> None:
    from kazma_core.security.vault import get_vault

    _bring_in(moved["bundle"], target["person"])
    vault = get_vault().for_space(A)
    assert vault.retrieve(f"space:{A}:own-secret") == "the-owners-secret"
    assert vault.retrieve(f"space:{A}:email.gmail.refresh_token") is None
    assert get_config_store().get(f"tenant.{A}.connectors.github.token") is None
    # A secret with no record of who stored it, from a space someone else
    # was in: it stays too (here, the planted word's).
    assert vault.retrieve(f"space:{A}:{moved['words'][A]}") is None
    assert _where_word("maya-token-secret") == {} and _where_word("maya-github-secret") == {}


def _stamp(name: str) -> str:
    from kazma_core.security.vault import STORED_BY

    with closing(sqlite3.connect(str(paths.vault_db_path()))) as conn:
        meta = json.loads(conn.execute("SELECT metadata FROM secrets WHERE name = ?", (name,)).fetchone()[0])
    return meta.get(STORED_BY, "")


@pytest.mark.parametrize("name", ["space:stamped:email.gmail.refresh_token",
                                  "cfg:tenant.stamped.connectors.x.oauth2.refresh_token"])
def test_4_who_connected_an_account_is_recorded_once(tmp_path, monkeypatch, name) -> None:
    """The first write says who stored a secret; a refresh -- with nobody
    bound, as the space's maker (the hourly check), in another member's
    turn -- keeps it, and only connecting the account again changes it
    (46f200bcb: every write with a person bound took it over)."""
    from kazma_core.security.vault import get_vault
    from kazma_core.spaces.connected_accounts import connecting

    with own_install(monkeypatch, tmp_path / "stamp"):
        identity.create_space("Stamped", created_by=OWNER_ACCOUNT_ID, space_id="stamped")
        vault = get_vault().for_space("stamped")
        with scope.bind_scope("stamped", "pperson"):
            vault.store(name, "v1")
        assert _stamp(name) == "pperson"
        vault.store(name, "v2")  # a refresh: no person
        with scope.bind_scope("stamped", OWNER_ACCOUNT_ID):  # (space, its acting account): the hourly check
            vault.store(name, "v3")
        with scope.bind_scope("stamped", "pother"):  # a refresh in another member's turn
            vault.store(name, "v4")
        assert _stamp(name) == "pperson"
        with scope.bind_scope("stamped", "pother"), connecting():  # signed in again, by another member
            vault.store(name, "v4")  # even with the value it has
        assert _stamp(name) == "pother"
        # One with no record -- written before there was one -- is nobody's
        # because someone refreshed it.
        with closing(sqlite3.connect(str(paths.vault_db_path()))) as conn:
            conn.execute("UPDATE secrets SET metadata = '{}' WHERE name = ?", (name,))
            conn.commit()
        with scope.bind_scope("stamped", OWNER_ACCOUNT_ID):
            vault.store(name, "v5")
        assert _stamp(name) == ""
        assert vault.retrieve(name) == "v5"


def test_6_7_another_members_work_never_runs_as_the_new_owner(moved, target) -> None:
    """Their job, task and research do not arrive (above); a departed
    person's document job is refused as theirs."""
    _bring_in(moved["bundle"], target["person"])
    assert identity.asker_left(A, DEPARTED)
    jobs = _rows("cron.db", "cron_jobs", "tenant_id = ?", (A,))
    assert jobs and all(j["creator"] in ("", target["person"]) for j in jobs)


def test_8_nothing_plain_is_written_to_a_temp_folder(moved, target, monkeypatch, tmp_path) -> None:
    def no_temp(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a plain copy was written to a temp folder")

    monkeypatch.setattr(tempfile, "mkdtemp", no_temp)
    monkeypatch.setattr(tempfile, "mkstemp", no_temp)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", no_temp)
    taken = _bring_in(moved["bundle"], target["person"])
    assert taken["space"] == A


def test_8_an_export_too_big_to_bring_in_is_refused(tmp_path, monkeypatch) -> None:
    with own_install(monkeypatch, tmp_path / "big"):
        identity.create_space("Big", created_by=OWNER_ACCOUNT_ID, space_id="big")
        from kazma_core.spaces import files as space_files

        folder = space_files.space_dir("big") / "files"
        folder.mkdir(parents=True)
        (folder / "large.bin").write_bytes(b"x" * 4096)
        monkeypatch.setattr(space_move, "MAX_BUNDLE_BYTES", 1024)
        with pytest.raises(space_move.SpaceMoveRefused) as refused:
            space_move.export_bundle("big", tmp_path / "big.kazmaspace", PASSWORD, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "too_big"
    assert not list(tmp_path.glob("big.kazmaspace*"))


def test_8_an_entry_past_its_bound_is_refused_unread(moved, target, monkeypatch) -> None:
    monkeypatch.setattr(space_move, "MAX_JSON_BYTES", 64)
    _refused(moved["bundle"], target["person"], "too_big")


def test_8_the_guide_says_the_project_folders_files_stay() -> None:
    guide = (Path(__file__).resolve().parents[1] / "docs/docs/guide/spaces.md").read_text(encoding="utf-8")
    start = guide.index("**Move a shared space to another Kazma.**")
    section = guide[start:guide.index("- **Delete a shared space.**", start)]
    assert "project folder's files stay on this machine" in section
    for said in ("Only you arrive with it", "arrives stopped", "never travel", "sealed whole"):
        assert said in section, said


def test_only_an_owner_moves_a_space(tmp_path, monkeypatch) -> None:
    with own_install(monkeypatch, tmp_path / "owners"):
        member = people.create_person("member-only", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
        shared = people.create_shared_space("Shared", by=OWNER_ACCOUNT_ID)
        people.add_member(shared["id"], member, by=OWNER_ACCOUNT_ID)
        with pytest.raises(space_move.SpaceMoveRefused) as refused:
            space_move.export_bundle(shared["id"], tmp_path / "x.kazmaspace", PASSWORD, by=member)
    assert refused.value.code == "not_the_owner"


def test_every_table_a_space_keeps_says_how_it_moves(tmp_path, monkeypatch) -> None:
    """Every table of every row store that holds a space's rows has a move
    rule: a new one with none refuses every move, never travels unsaid."""
    from tests._space_world import _make_stores

    with own_install(monkeypatch, tmp_path / "rules"):
        _make_stores()
        missing = []
        for name, path in space_data._store_paths():
            store = space_move._logical_store(name, path)
            with closing(sqlite3.connect(str(path))) as conn:
                for table, virtual in space_data._tables(conn):
                    rule = space_data._table_rule(name, table, space_data._columns(conn, table),
                                                  virtual_fts=virtual)
                    if rule is None or rule.how in ("keep", "follows") or store is None:
                        continue
                    if (store, table) not in space_move.MOVE_RULES:
                        missing.append(f"{store}:{table}")
    assert not missing, missing
    stopped = {key for key, rule in space_move.MOVE_RULES.items() if rule.how == "stopped"}
    assert stopped == set(space_move._STOPS)


def test_control_a_rule_said_nowhere_refuses_the_move(moved, target, monkeypatch) -> None:
    monkeypatch.delitem(space_move.MOVE_RULES, ("agent_artifacts.db", "agent_artifacts"))
    _refused(moved["bundle"], target["person"], "unknown_table")


def test_the_departed_marker_is_no_account() -> None:
    reg = {"accounts": {}, "events": []}
    assert identity._was_a_person(reg, DEPARTED)
    assert identity._account_from_local_user({"username": DEPARTED, "user_id": DEPARTED})["id"] != DEPARTED


def test_base64_keys_are_inside_only(moved) -> None:
    keys = json.loads(entries_of(moved["bundle"])["keys.json"])
    assert keys and all(len(base64.b64decode(k["key"])) == 32 for k in keys)
