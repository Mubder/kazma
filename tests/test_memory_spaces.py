"""Memory: the space is the tenant, always (Spaces S1-09).

The matrix (``tests/_memory_space_cases.py``) calls every memory route and
tool as a member of one space with another's ids, and
``tests/test_memory_default_is_a_space.py`` does it as the owner. This file
holds the parts of the memory audit's checklist that are not a route
(``docs/audits/AUDIT_MEMORY_FRAMEWORK_2026-11.md``, section 5.2), each with
the old behaviour as its negative control:

* one account in two spaces: each space's About me and forget ledger are its
  own (item 2);
* a remembered turn's id carries its space: the same turn in two spaces is
  two memories, each recalled in its own (item 6);
* writers that passed no tenant write for the space they run in (item 5);
* the memory worker: the tenants it visits, a task bound to the space it
  names, every memory sweep run with Spaces enforced on a store that holds
  two spaces (item 4);
* a row is never written under the "no tenant" sentinel, and the operator
  trail names the space acted in (item 8);
* memory kept under a tenant that is no space is counted (item 9).
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

A, B = "alpha", "beta"


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    """A private memory store, two spaces of the owner, Spaces enforced."""
    from kazma_core.config_store import get_config_store
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    get_config_store().delete(identity.REGISTRY_KEY)
    for space_id in (A, B):
        identity.create_space(space_id.title(), created_by=OWNER_ACCOUNT_ID, space_id=space_id)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.commit()
    dual_write._reset_mirror()
    monkeypatch.setattr(scope, "_enforced", True)
    yield SimpleNamespace(db=db, ops=ops, mp=monkeypatch, tmp=tmp_path)
    dual_write._reset_mirror()
    ops.close()
    db.close()
    get_config_store().delete(identity.REGISTRY_KEY)


def _in(space: str):
    return scope.bind_scope(space, OWNER_ACCOUNT_ID)


def _turn(space: str, session: str, n: int, question: str, answer: str = "noted") -> str | None:
    from kazma_core.memory.dual_write import mirror_episode

    return mirror_episode(session_id=session, turn_number=n, user_text=question,
                          assistant_text=answer, tenant_id=space)


def _tenants_of(mem, sql: str, *params: Any) -> list[str]:
    return sorted(str(r[0]) for r in mem.db.execute(sql, params))


# ── item 2: one account, two spaces ──────────────────────────────────────


def test_each_spaces_about_me_is_its_own(mem) -> None:
    from kazma_core.memory.profile import about_for_turn, set_about

    assert set_about(OWNER_SPACE_ID, "I am the owner.")["ok"]
    assert set_about(A, "We are team alpha.")["ok"]
    assert about_for_turn(A)["about"] == "We are team alpha."
    assert about_for_turn(B)["about"] == ""  # not the owner's
    assert about_for_turn(OWNER_SPACE_ID)["about"] == "I am the owner."


def test_control_without_spaces_a_tenant_with_none_was_shown_the_owners(mem) -> None:
    from kazma_core.memory.profile import about_for_turn, set_about

    mem.mp.setattr(scope, "_enforced", False)
    assert set_about(OWNER_SPACE_ID, "I am the owner.")["ok"]
    assert about_for_turn(B)["about"] == "I am the owner."


def test_a_spaces_forget_ledger_is_its_own(mem) -> None:
    from kazma_core.memory import forget

    # The owner keeps the chat "shared-key" out of memory, in the owner's space.
    assert forget.set_chat_remembered("shared-key", False, tenant_id=OWNER_SPACE_ID)["ok"]
    assert not forget.chat_remembered("shared-key", tenant_id=OWNER_SPACE_ID)
    # A chat of that name in another space is still remembered there.
    assert forget.chat_remembered("shared-key", tenant_id=B)
    assert _turn(B, "shared-key", 1, "where do we meet on Tuesday")
    assert _turn(OWNER_SPACE_ID, "shared-key", 1, "where do we meet on Tuesday") is None


def test_control_without_spaces_the_owners_ledger_reached_every_tenant(mem) -> None:
    from kazma_core.memory import forget

    mem.mp.setattr(scope, "_enforced", False)
    assert forget.set_chat_remembered("shared-key", False, tenant_id=OWNER_SPACE_ID)["ok"]
    assert not forget.chat_remembered("shared-key", tenant_id=B)


# ── item 6: a turn's id carries its space ────────────────────────────────


def test_the_same_turn_in_two_spaces_is_two_memories(mem) -> None:
    from kazma_core.memory.dual_write import episode_ids
    from kazma_core.memory.recall import recall

    question = "which supplier makes the pump housings"
    ids = {space: _turn(space, "chat-1", 1, question, f"the answer of {space}") for space in (A, B, OWNER_SPACE_ID)}
    assert all(ids.values()) and len(set(ids.values())) == 3
    rows = {str(r["tenant_id"]): str(r["id"]) for r in mem.db.execute(
        "SELECT id, tenant_id FROM episodes WHERE session_id = 'chat-1'")}
    assert rows == {space: ids[space] for space in (A, B, OWNER_SPACE_ID)}
    # The owner's id is the one it always had.
    assert ids[OWNER_SPACE_ID] == episode_ids("chat-1", 1, question)[0] == episode_ids(
        "chat-1", 1, question, OWNER_SPACE_ID)[0]
    for space in (A, B):
        found = recall(question, tenant_id=space)
        shown = " ".join(str(e) for e in found.episodes)
        other = B if space == A else A
        assert f"the answer of {space}" in shown and f"the answer of {other}" not in shown


def test_control_an_id_with_no_space_wrote_the_turn_for_the_first_space_only(mem) -> None:
    from kazma_core.memory import dual_write

    real = dual_write.episode_ids

    def plain_only(session_id, turn, content, tenant_id=None):
        plain = real(session_id, turn, content, OWNER_SPACE_ID)[0]
        return (plain, plain)

    mem.mp.setattr(dual_write, "episode_ids", plain_only)
    mem.mp.setattr(dual_write, "_id_in_tenant", lambda conn, ids, tenant: (ids[0], False))
    question = "which supplier makes the pump housings"
    first, second = _turn(A, "chat-1", 1, question), _turn(B, "chat-1", 1, question)
    assert first == second  # "written" twice
    assert _tenants_of(mem, "SELECT tenant_id FROM episodes WHERE session_id = 'chat-1'") == [A]


def test_a_turn_stored_under_the_older_id_is_not_written_twice(mem) -> None:
    """An older multi-user tenant holds rows under the id with no space."""
    from kazma_core.memory.dual_write import episode_ids

    question = "what is the door code of the workshop"
    spaced, plain = episode_ids("chat-9", 1, question, A)
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at) "
        "VALUES (?, ?, 'chat-9', 1, ?, 'episodic', 1.0)", (plain, A, question))
    mem.db.commit()
    assert _turn(A, "chat-9", 1, question) == plain
    assert mem.db.execute("SELECT COUNT(*) FROM episodes WHERE session_id = 'chat-9'").fetchone()[0] == 1
    # The id is taken by A: the same turn in the owner's space gets the other form.
    theirs = _turn(OWNER_SPACE_ID, "chat-9", 1, question)
    assert theirs and theirs != plain
    assert _tenants_of(mem, "SELECT tenant_id FROM episodes WHERE session_id = 'chat-9'") == [A, OWNER_SPACE_ID]
    assert spaced != plain


def test_the_same_export_imported_into_two_spaces_gives_two_sets(mem) -> None:
    from kazma_core.memory.history_import import History, import_history, read_export

    from tests._memory_space_cases import _export

    path = mem.tmp / "export.zip"
    path.write_bytes(_export("zanzibar"))
    history: History = read_export(path)
    for space in (A, B):
        with _in(space):
            report = import_history(history)
        assert report.written == 1 and report.already == 0, (space, report.as_dict())
    assert _tenants_of(mem, "SELECT tenant_id FROM episodes WHERE user_text LIKE '%zanzibar%'") == [A, B]
    with _in(A):
        again = import_history(history)
    assert again.written == 0 and again.already == 1


def test_recovery_recognises_both_forms_of_an_id(mem) -> None:
    from kazma_core.memory.dual_write import episode_ids
    from kazma_core.memory.rehydrate import _Candidate, _ids_for

    row = {"id": "x", "tenant_id": A, "session_id": "chat-1", "turn_number": 2, "metadata_json": "{}"}

    class _Row(dict):
        def keys(self):  # sqlite3.Row's shape
            return super().keys()

    cand = _Candidate(user="the question", assistant="", summary="", source="chat")
    assert set(_ids_for(_Row(row), cand)) == set(episode_ids("chat-1", 2, "the question", A))


# ── item 5: writers that passed no tenant ────────────────────────────────


def test_a_tools_recorded_outcome_is_its_spaces(mem) -> None:
    from kazma_core.agent import tool_registry

    with _in(A):
        tool_registry._record_procedural_outcome("file_search", {"query": "x"}, success=True)
    tool_registry._PROCEDURAL_QUEUE.join()
    assert _tenants_of(mem, "SELECT tenant_id FROM procedural_dags WHERE name = 'file_search'") == [A]
    # No space bound, Spaces enforced: recorded for no one.
    tool_registry._record_procedural_outcome("shell_exec", {"command": "x"}, success=True)
    tool_registry._PROCEDURAL_QUEUE.join()
    assert _tenants_of(mem, "SELECT tenant_id FROM procedural_dags WHERE name = 'shell_exec'") == []


def test_a_swarm_result_is_stored_for_the_space_it_ran_in(mem) -> None:
    from kazma_core.memory.swarm_bridge import log_evolution_v2, store_swarm_result

    with _in(B):
        assert store_swarm_result("coder", "task-1", "Task: tidy the docs\nResult: tidied three pages")
        assert log_evolution_v2("coder", "task-1", "prefers small commits", "tidied the docs in three commits")
    assert _tenants_of(mem, "SELECT DISTINCT tenant_id FROM episodes WHERE id LIKE 'ep_%'") == [B]
    # Nothing bound: nothing stored (it went to the owner's).
    assert store_swarm_result("coder", "task-2", "Task: another one\nResult: also done") is None
    assert _tenants_of(mem, "SELECT DISTINCT tenant_id FROM episodes WHERE id LIKE 'ep_%'") == [B]


def test_the_import_command_writes_the_owners_space(mem, capsys) -> None:
    from kazma_cli.history_import import main

    from tests._memory_space_cases import _export

    path = mem.tmp / "export.zip"
    path.write_bytes(_export("marrakesh"))
    assert main([str(path), "-y"]) == 0
    assert _tenants_of(mem, "SELECT tenant_id FROM episodes WHERE user_text LIKE '%marrakesh%'") == [OWNER_SPACE_ID]


# ── item 4: the memory worker ────────────────────────────────────────────


def test_the_worker_visits_every_tenant_that_holds_memory_and_every_space(mem) -> None:
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.worker_bootstrap import memory_tenants

    # A tenant with facts and no turn; a space with nothing yet; an old tenant.
    mutate_belief(mem.db, "user", "lives_in", "Muscat", tenant_id="facts-only", private=True)
    _turn("telegram", "old-chat", 1, "an old per-platform memory")
    assert memory_tenants() == [OWNER_SPACE_ID, "telegram", "facts-only", A, B] or set(memory_tenants()) == {
        OWNER_SPACE_ID, "telegram", "facts-only", A, B}
    assert memory_tenants()[0] == OWNER_SPACE_ID


def test_a_tenant_list_that_cannot_be_read_is_a_failure_not_the_owners_alone(mem) -> None:
    from kazma_core.memory import worker_bootstrap

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    with mem.mp.context() as patched:
        patched.setattr(sqlite3, "connect", broken)
        with pytest.raises(sqlite3.OperationalError):
            worker_bootstrap.memory_tenants()


def test_a_queued_task_runs_in_the_space_it_names(mem) -> None:
    from kazma_core.memory.task_queue import _task_space
    from kazma_core.tenant_context import get_current_tenant_id

    with _task_space({"tenant_id": B}):
        assert get_current_tenant_id() == B and scope.require_space_id() == B
    with _task_space({"tenant_id": OWNER_SPACE_ID}):
        assert scope.require_space_id() == OWNER_SPACE_ID
    with _task_space({}):
        assert get_current_tenant_id() is None
    # Not enforced: the owner's own tasks run with nothing bound, as before.
    mem.mp.setattr(scope, "_enforced", False)
    with _task_space({"tenant_id": OWNER_SPACE_ID}):
        assert get_current_tenant_id() is None
    with _task_space({"tenant_id": "telegram"}):
        assert get_current_tenant_id() == "telegram"


_SWEEPS = (
    "_converge_forgets", "_repair_memory_vectors", "_recover_erased_memories",
    "_retire_knowledge_promotions", "_reconcile_memory_turns", "_sync_memory_mirror",
    "_sync_memory_graph", "_queue_topic_summaries", "_repair_entity_counts",
)


def _seed_two_spaces(mem) -> None:
    from kazma_core.memory.belief_mutation import mutate_belief

    for space in (A, B):
        _turn(space, f"chat-{space}", 1, f"the {space} team meets on Tuesday", "noted")
        mutate_belief(mem.db, "user", "works_at", f"{space} works", tenant_id=space, private=True)
    mem.db.commit()


@pytest.mark.parametrize("sweep", _SWEEPS)
def test_a_memory_sweep_runs_with_spaces_enforced_and_nothing_bound(mem, sweep, caplog) -> None:
    """The worker has no request: a sweep that asks for "the" space fails
    every pass once Spaces is enforced. Each one runs here on a store that
    holds two spaces, and leaves both as they were or better."""
    from kazma_core.memory import worker_bootstrap

    _seed_two_spaces(mem)
    before = {space: mem.db.execute(
        "SELECT COUNT(*) FROM episodes WHERE tenant_id = ?", (space,)).fetchone()[0] for space in (A, B)}
    assert scope.current_scope() is None
    with caplog.at_level("WARNING"):
        getattr(worker_bootstrap, sweep)()
    # A sweep that swallowed "no space is bound" did nothing and said so in
    # the log: that is a failure here too.
    said = [r.getMessage() for r in caplog.records if r.levelname in ("WARNING", "ERROR", "CRITICAL")]
    assert not said, (sweep, said)
    after = {space: mem.db.execute(
        "SELECT COUNT(*) FROM episodes WHERE tenant_id = ?", (space,)).fetchone()[0] for space in (A, B)}
    assert all(after[space] >= before[space] >= 1 for space in (A, B)), (sweep, before, after)


def test_every_memory_sweep_of_the_worker_is_in_the_list_above() -> None:
    """A new memory sweep joins the enforcement run the day it is added."""
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "kazma-core/kazma_core/memory/worker_bootstrap.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    table = next(n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
                 and getattr(getattr(n, "target", None) or n.targets[0], "id", "") == "_MAINTENANCE_SWEEPS")
    memory = [
        elt.elts[1].id for elt in table.value.elts
        if isinstance(elt, ast.Tuple) and ("memory" in elt.elts[0].value or elt.elts[0].value in (
            "forget convergence", "retired knowledge promotions", "entity count repair"))
    ]
    assert sorted(memory) == sorted(_SWEEPS)


# ── item 8: the sentinel and the trail ───────────────────────────────────


def test_no_row_is_written_under_the_no_tenant_sentinel(mem) -> None:
    from kazma_core.memory.schema_v2 import NO_TENANT

    with pytest.raises(sqlite3.IntegrityError, match="no tenant"):
        mem.db.execute(
            "INSERT INTO memory_profile (tenant_id, about, updated_at) VALUES (?, 'x', 1.0)", (NO_TENANT,))
    mem.db.execute("INSERT INTO memory_profile (tenant_id, about, updated_at) VALUES ('ok', 'x', 1.0)")
    with pytest.raises(sqlite3.IntegrityError, match="no tenant"):
        mem.db.execute("UPDATE memory_profile SET tenant_id = ? WHERE tenant_id = 'ok'", (NO_TENANT,))
    with pytest.raises(sqlite3.IntegrityError, match="no tenant"):
        mem.ops.execute(
            "INSERT INTO memory_audit_log (id, tenant_id, timestamp, event_type, target_table, target_id) "
            "VALUES ('a1', ?, 1.0, 'x', 'memory', '-')", (NO_TENANT,))
    guarded = {str(r[0]) for r in mem.db.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'kazma_no_tenant_insert_%'")}
    assert {"episodes", "beliefs", "entities", "memory_profile", "memory_summaries"} <= guarded


def test_control_without_the_guard_a_row_was_written_for_no_one(mem) -> None:
    from kazma_core.memory.schema_v2 import NO_TENANT

    for (name,) in mem.db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'kazma_no_tenant_%'").fetchall():
        mem.db.execute(f"DROP TRIGGER {name}")
    mem.db.execute(
        "INSERT INTO memory_profile (tenant_id, about, updated_at) VALUES (?, 'x', 1.0)", (NO_TENANT,))
    assert mem.db.execute(
        "SELECT COUNT(*) FROM memory_profile WHERE tenant_id = ?", (NO_TENANT,)).fetchone()[0] == 1


def test_the_operator_trail_names_the_space_acted_in(mem, caplog) -> None:
    from kazma_ui import memory_actions

    with caplog.at_level("INFO", logger="kazma_ui.memory_actions"):
        memory_actions._record("PUT /api/memory/v2/profile", [], 200, [], acted_in=B)
        memory_actions._record("PUT /api/memory/v2/profile", [], 200, [])
    rows = [str(r[0]) for r in mem.ops.execute(
        "SELECT tenant_id FROM memory_audit_log WHERE event_type = 'operator_action' ORDER BY rowid")]
    assert rows == [B, OWNER_SPACE_ID]
    assert f"tenant={B}" in caplog.text


# ── item 9: memory under a tenant that is no space ───────────────────────


def test_memory_outside_every_space_is_counted_table_by_table(mem) -> None:
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.profile import set_about
    from kazma_core.memory.tenant_view import tenants_outside_spaces

    _seed_two_spaces(mem)
    _turn("telegram", "old-chat", 1, "an old per-platform memory")
    _turn("telegram", "old-chat", 2, "another one")
    mutate_belief(mem.db, "user", "lives_in", "Salalah", tenant_id="telegram", private=True)
    set_about("telegram:42", "An old per-user About me.")
    mem.db.commit()
    report = tenants_outside_spaces(mem.db)
    by_tenant = {row["tenant"]: row for row in report["tenants"]}
    assert set(by_tenant) == {"telegram", "telegram:42"}  # the spaces and the owner's are not listed
    assert by_tenant["telegram"]["memories"] == 2 and by_tenant["telegram"]["facts"] == 1
    assert by_tenant["telegram"]["rows"]["episodes"] == 2
    assert by_tenant["telegram:42"]["rows"] == {"memory_profile": 1}
    assert by_tenant["telegram:42"]["memories"] == 0
    assert report["legacy_mode"] is None
    assert "the Postgres mirror" in report["copies_not_counted"]


# ── item 10: memory's copies outside the data folder ─────────────────────


def _external_rule_problems(stores, rules) -> list[str]:
    from kazma_core import store_registry as reg

    problems = [f"{s.name}: no space rule" for s in stores if s.name not in rules]
    problems += [f"{name}: a rule for no store" for name in rules if name not in {s.name for s in stores}]
    for name, rule in rules.items():
        if isinstance(rule, tuple):
            problems += [f"{name}: {key} is no store of the registry" for key in rule if key not in reg.SPACE_RULES]
            continue
        if rule.kind not in reg.SPACE_RULE_KINDS or rule.today not in reg.SPACE_TODAY_STATES:
            problems.append(f"{name}: a kind or state the registry does not know")
        if rule.kind != "row" and not rule.why:
            problems.append(f"{name}: says nothing about how a space is kept apart")
        if rule.kind == "row" and not (rule.content or rule.why):
            problems.append(f"{name}: a row store with neither content columns nor a reason")
    return problems


def test_every_copy_of_memory_outside_the_data_folder_declares_its_space_rule() -> None:
    from kazma_core.memory import memory_stores

    assert _external_rule_problems(memory_stores.EXTERNAL_STORES, memory_stores.EXTERNAL_SPACE_RULES) == []


def test_the_mirror_columns_the_rule_names_exist() -> None:
    import re
    from pathlib import Path

    from kazma_core.memory import memory_stores

    source = (Path(__file__).resolve().parents[1]
              / "kazma-core/kazma_core/memory/state_backend.py").read_text(encoding="utf-8")
    rule = memory_stores.EXTERNAL_SPACE_RULES["Postgres state mirror (kazma_episodes, kazma_beliefs)"]
    for table, columns in rule.content.items():
        made = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\s*\)", source, re.S)
        assert made, table
        assert "tenant_id" in made.group(1)
        for column in columns:
            assert re.search(rf"\b{column}\b", made.group(1)), (table, column)


def test_control_an_external_copy_with_no_rule_is_found() -> None:
    from dataclasses import replace

    from kazma_core import store_registry as reg
    from kazma_core.memory import memory_stores

    planted = (*memory_stores.EXTERNAL_STORES,
               replace(memory_stores.EXTERNAL_STORES[1], name="a second vector index"))
    assert _external_rule_problems(planted, memory_stores.EXTERNAL_SPACE_RULES) == [
        "a second vector index: no space rule"]
    rules = {**memory_stores.EXTERNAL_SPACE_RULES,
             "remote vector index": reg.SpaceRule("derived", today="apart"),
             "chat store and checkpoints": ("no_such.db",)}
    assert len(_external_rule_problems(memory_stores.EXTERNAL_STORES, rules)) == 2


# ── items 7 and 11: what a recall channel hands on ───────────────────────


def test_a_candidate_of_another_space_stops_before_it_is_read_by_id(mem) -> None:
    """A channel that answered with another space's memory (a remote vector
    index is asked for one tenant and trusted): nothing of it is shown."""
    from kazma_core.memory import recall as recall_mod
    from kazma_core.memory.recall import RecallHit, recall

    question = "which supplier makes the pump housings"
    theirs = _turn(B, "chat-b", 1, question, "Nordvalve, says beta")
    _turn(A, "chat-a", 1, "an unrelated note about lunch", "noted")

    def leaky(conn, q, engine, tenant_id, limit):
        return [RecallHit(id=theirs, content="", score=0.99, source="dense")]

    mem.mp.setattr(recall_mod, "_episode_dense", leaky)
    found = recall(question, tenant_id=A)
    assert found.degraded is None  # it ran: an empty answer from a failed recall proves nothing
    assert "Nordvalve" not in " ".join(str(e) for e in found.episodes)
    assert theirs not in {getattr(e, "id", "") for e in found.episodes}


def test_control_without_the_tenant_check_the_other_spaces_memory_was_shown(mem) -> None:
    from kazma_core.memory import recall as recall_mod
    from kazma_core.memory.recall import RecallHit, recall

    question = "which supplier makes the pump housings"
    theirs = _turn(B, "chat-b", 1, question, "Nordvalve, says beta")
    mem.mp.setattr(recall_mod, "_episode_dense",
                   lambda conn, q, engine, tenant_id, limit: [RecallHit(id=theirs, content="", score=0.99, source="dense")])
    mem.mp.setattr(recall_mod, "_own_episodes", lambda conn, tenant_id, hits: hits)
    mem.mp.setattr(recall_mod, "_similarities", lambda conn, ids, qvec, **k: {i: 0.95 for i in ids})
    mem.mp.setattr(recall_mod, "_encode_query", lambda q: [0.1, 0.2])
    found = recall(question, tenant_id=A)
    assert "Nordvalve" in " ".join(str(e) for e in found.episodes)


_CHANNEL_QUESTION = "what does the team know about the Nordvalve pump housings"


def _seed_channels(mem) -> None:
    """Everything recall can find, held by beta alone: a turn, a fact drawn
    from it, a weekly summary and About me. Alpha holds one unrelated turn."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.profile import set_about

    _turn(B, "chat-b", 1, "the Nordvalve pump housings arrive on Tuesday", "noted: Nordvalve, Tuesday")
    mutate_belief(mem.db, "team", "buys pump housings from", "Nordvalve", tenant_id=B,
                  source_session="chat-b", source_turn=1, private=True)
    mem.db.execute(
        "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, title, "
        "summary_text, status, created_at, updated_at) VALUES "
        "('sum-b', ?, '2026-W40', 1.0, 2.0, 'Nordvalve pump housings', "
        "'The team settled on Nordvalve for the pump housings.', 'active', 1.0, 1.0)", (B,))
    mem.db.commit()
    set_about(B, "We buy our pump housings from Nordvalve.")
    _turn(A, "chat-a", 1, "an unrelated note about lunch", "noted")
    mem.db.commit()


def test_no_recall_channel_answers_one_space_from_another(mem) -> None:
    from kazma_core.memory.profile import about_for_turn
    from kazma_core.memory.recall import recall

    _seed_channels(mem)
    theirs = recall(_CHANNEL_QUESTION, tenant_id=B, explain=True)
    assert theirs.degraded is None
    seen_b = " ".join(str(x) for x in (*theirs.episodes, *theirs.beliefs, *getattr(theirs, "summaries", ())))
    assert "Nordvalve" in seen_b  # the seed is findable: the check below means something
    for space in (A, OWNER_SPACE_ID):
        found = recall(_CHANNEL_QUESTION, tenant_id=space, explain=True)
        assert found.degraded is None
        shown = " ".join(str(x) for x in (*found.episodes, *found.beliefs, *getattr(found, "summaries", ())))
        assert "Nordvalve" not in shown, (space, shown[:300])
        assert "Nordvalve" not in str(getattr(found, "context", "") or "")
        assert "Nordvalve" not in about_for_turn(space)["about"]


def test_the_past_chats_fallback_reads_the_askers_space_only(mem) -> None:
    """The fallback an empty recall runs reads the chat store by tenant."""
    import inspect

    from kazma_core.memory import chat_history

    source = inspect.getsource(chat_history._search_sqlite) + inspect.getsource(chat_history._search_postgres)
    assert source.count("tenant_id = ") >= 2
    spool = inspect.getsource(chat_history.search_sessions) if hasattr(chat_history, "search_sessions") else ""
    assert not spool or "tenant_id" in spool


# ── item 1: a turn's memory is its step's space ──────────────────────────

_TURN_CODE = (
    "kazma-core/kazma_core/agent/graph_supervisor.py",
    "kazma-core/kazma_core/agent/graph_tool_worker.py",
    "kazma-core/kazma_core/agent/graph_respond.py",
    "kazma-core/kazma_core/agent/graph_builder.py",
    "kazma-core/kazma_core/time_travel.py",
    "kazma-ui/kazma_ui/turn_abort.py",
    "kazma-ui/kazma_ui/thread_rewind.py",
    "kazma-gateway/kazma_gateway/agent_handler/graph.py",
)


def _state_tenant_fallbacks(source: str) -> list[int]:
    """Lines that read a state's tenant and fall back to a literal:
    ``state.get("tenant_id") or "default"`` and ``state.get("tenant_id", "default")``."""
    import ast

    def reads_tenant(node) -> bool:
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and bool(node.args) and isinstance(node.args[0], ast.Constant) and node.args[0].value == "tenant_id")

    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if any(reads_tenant(v) for v in node.values) and any(
                    isinstance(v, ast.Constant) and isinstance(v.value, str) for v in node.values):
                found.append(node.lineno)
        elif reads_tenant(node) and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            found.append(node.lineno)
    return found


def test_turn_code_reads_its_memory_tenant_through_the_steps_space() -> None:
    """The supervisor, the tool worker, respond, Time Travel and the rewinds
    read memory for ``space_of_state(state)``: the state's own tenant while
    Spaces is not enforced, the bound scope's once it is. Each read
    ``state.get("tenant_id") or "default"``, which a state could set."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    problems = [
        f"{rel}:{line}" for rel in _TURN_CODE
        for line in _state_tenant_fallbacks((root / rel).read_text(encoding="utf-8"))
    ]
    assert not problems, problems
    assert sum("space_of_state(" in (root / rel).read_text(encoding="utf-8") for rel in _TURN_CODE) >= 6


def test_the_turn_code_gate_sees_a_planted_fallback() -> None:
    planted = (
        "def node(state):\n"
        "    tenant = str(state.get('tenant_id') or 'default')\n"
        "    other = state.get('tenant_id', 'default')\n"
        "    fine = state.get('tenant_id')\n"
    )
    assert sorted(_state_tenant_fallbacks(planted)) == [2, 3]


def test_a_steps_space_is_the_bound_scopes_once_spaces_is_enforced(mem) -> None:
    state = {"tenant_id": B}
    with _in(A):
        assert scope.space_of_state(state) == A  # a state cannot name another space
    mem.mp.setattr(scope, "_enforced", False)
    assert scope.space_of_state(state) == B  # as before Spaces: the state's own tenant
    assert scope.space_of_state({}) == OWNER_SPACE_ID
