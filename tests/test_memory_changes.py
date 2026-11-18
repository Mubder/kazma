"""A fact the user stated changes only in ways the user can see and take back
(2026-11, the memory framework audit's F1, second half: the store).

The first half made a correction the user says of themself replace their
fact ("We moved to Rotterdam" over "I live in Paris"). Two cases were left:

* the replacement was silent: nothing showed what changed, and nothing could
  take it back;
* a correction the strict reading could not credit to the user ("Moved to
  Rotterdam last month": no subject) was refused, silently, for good.

``memory/changes.py`` records each: an update (undoable) in the same
transaction as the replacement, a pending change (confirm or dismiss) where
the trust gate refused and no one else is named. "My sister moved to
Rotterdam" records nothing. Forget takes a change with its fact or its turn.
Through the worker's own deep-pass path, as the first half's tests are.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from types import SimpleNamespace

import pytest
from kazma_core.memory import changes
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.schema_v2 import ensure_primary_schema


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    dual_write._reset_mirror()
    yield SimpleNamespace(dir=tmp_path, db=db)
    dual_write._reset_mirror()
    db.close()


def _fact(predicate: str, value: str) -> dict:
    return {"subject": "user", "predicate": predicate, "predicate_type": "functional", "object": value}


def _turn(mem, monkeypatch, *, first: str, then: str, model: list[dict], predicate: str = "lives_in",
          turn: int = 2) -> str:
    """The user's earlier statement (``user_stated``), then the worker's deep
    pass of the turn *then*, the model's reply written for the test. Returns
    the turn's episode id."""
    from kazma_core.memory import worker_bootstrap
    from kazma_core.memory.dual_write import _episode_id, mirror_episode

    turn_time = time.time() - 600
    if first:
        conn = sqlite3.connect(str(mem.dir / "memory_state.db"), isolation_level=None)
        mutate_belief(conn, "user", predicate, first, now=turn_time - 600, extraction_method="user_stated",
                      source_session="s1", source_turn=1)
        conn.close()
    mirror_episode(session_id="s1", turn_number=turn, user_text=then, assistant_text="Noted.",
                   created_at=turn_time)

    async def llm(user_text, assistant_text="", *, use_llm=True, ignore_filler=False, vocabulary=None,
                  entities=None):
        return ([dict(b, confidence=0.9, importance=4) for b in model],
                {"skipped_filler": False, "source": "llm", "applied": 0, "rejected": 0, "actions": []})

    monkeypatch.setattr("kazma_core.memory.belief_extractor.extract_beliefs_for_turn", llm)
    eid = _episode_id("s1", turn, then)
    assert asyncio.run(worker_bootstrap._handle_micro_consolidation({"episode_id": eid})) is True
    return eid


def _current(mem, predicate: str = "lives_in") -> list[tuple[str, str]]:
    return [(r["object"], r["extraction_method"]) for r in mem.db.execute(
        "SELECT object, extraction_method FROM beliefs WHERE subject='user' AND predicate=? "
        "AND valid_until IS NULL AND invalidated_at IS NULL", (predicate,))]


def _changes(mem, state: str | None = None) -> list[dict]:
    states = (state,) if state else ("open", "confirmed", "dismissed", "undone", "superseded", "expired")
    return changes.list_changes(mem.db, tenant_id="default", states=states)


# ── updates ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("first", "then", "model", "new"), [
    ("Paris", "We moved to Rotterdam last month.", [_fact("lives_in", "rotterdam")], "Rotterdam"),
    ("جدة", "انتقلنا إلى الرياض الشهر الماضي.", [_fact("lives_in", "الرياض")], "الرياض"),
])
def test_a_replaced_user_fact_is_an_update_the_user_can_undo(mem, monkeypatch, first, then, model, new):
    _turn(mem, monkeypatch, first=first, then=then, model=model)
    assert _current(mem) == [(new, "user_stated")]
    [change] = _changes(mem, "open")
    assert (change["kind"], change["old_value"], change["new_value"]) == ("update", first, new)
    assert (change["source_session"], change["source_turn"]) == ("s1", 2)

    out = changes.undo(mem.db, change["id"], tenant_id="default", actor="test")
    assert out["ok"]
    assert _current(mem) == [(first, "user_stated")]
    assert _changes(mem, "undone")[0]["id"] == change["id"]
    with pytest.raises(changes.ChangeRefusedError) as refused:
        changes.undo(mem.db, change["id"], tenant_id="default")
    assert refused.value.code == "not_open"


def test_the_speaker_rules_accepted_cost_is_an_update_the_user_can_undo(mem):
    """The decided cost of the speaker rule (clause_subject: a sentence end
    clears who was named): "صديقتي معلمة. ساكنة في جدة" is read as the
    user's. It replaces the user's own Riyadh -- shown, and taken back by
    one undo."""
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync

    mutate_belief(mem.db, "user", "lives_in", "الرياض", extraction_method="user_stated", source_session="s1",
                  source_turn=1, now=time.time() - 600)
    extract_and_apply_beliefs_sync(mem.db, None, "صديقتي معلمة. ساكنة في جدة", session_id="s1", turn=2)
    assert [v for v, _ in _current(mem)] == ["جدة"]
    [change] = _changes(mem, "open")
    assert (change["kind"], change["old_value"], change["new_value"]) == ("update", "الرياض", "جدة")
    changes.undo(mem.db, change["id"], tenant_id="default")
    assert [v for v, _ in _current(mem)] == ["الرياض"]


def test_the_deep_pass_refining_its_own_turn_is_no_update(mem):
    """The quick pass and the deep pass of ONE turn: no update to show."""
    mutate_belief(mem.db, "user", "lives_in", "Kuwait City", extraction_method="user_stated",
                  source_session="s1", source_turn=1)
    mutate_belief(mem.db, "user", "lives_in", "Kuwait", extraction_method="user_stated",
                  source_session="s1", source_turn=1)
    assert _changes(mem) == []


def test_undo_is_refused_once_the_fact_changed_again(mem):
    now = time.time()
    mutate_belief(mem.db, "user", "lives_in", "Paris", extraction_method="user_stated", source_session="a",
                  source_turn=1, now=now - 300)
    mutate_belief(mem.db, "user", "lives_in", "Rotterdam", extraction_method="user_stated", source_session="a",
                  source_turn=2, now=now - 200)
    first = _changes(mem, "open")[0]
    mutate_belief(mem.db, "user", "lives_in", "Lisbon", extraction_method="user_stated", source_session="a",
                  source_turn=3, now=now - 100)
    with pytest.raises(changes.ChangeRefusedError) as refused:
        changes.undo(mem.db, first["id"], tenant_id="default")
    assert refused.value.code == "replaced_since"
    assert _current(mem) == [("Lisbon", "user_stated")]


def test_another_tenants_change_reads_as_unknown(mem):
    now = time.time()
    for turn, value in ((1, "Paris"), (2, "Rotterdam")):
        mutate_belief(mem.db, "user", "lives_in", value, extraction_method="user_stated", tenant_id="t2",
                      source_session="a", source_turn=turn, now=now - 300 + turn)
    [change] = changes.list_changes(mem.db, tenant_id="t2")
    assert changes.list_changes(mem.db, tenant_id="default") == []
    with pytest.raises(changes.ChangeRefusedError) as refused:
        changes.undo(mem.db, change["id"], tenant_id="default")
    assert refused.value.code == "not_found"


# ── pending changes ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("then", "model", "spelled"), [
    ("Moved to Rotterdam last month.", [_fact("lives_in", "rotterdam")], "Rotterdam"),
    ("No, Lisbon.", [_fact("lives_in", "lisbon")], "Lisbon"),
    ("انتقلت إلى الرياض الشهر الماضي.", [_fact("lives_in", "الرياض")], "الرياض"),
])
def test_a_correction_with_no_subject_is_put_to_the_user(mem, monkeypatch, then, model, spelled):
    _turn(mem, monkeypatch, first="Paris", then=then, model=model)
    assert _current(mem) == [("Paris", "user_stated")]  # refused, as before...
    [change] = _changes(mem, "open")  # ...and no longer silent
    assert (change["kind"], change["old_value"], change["new_value"]) == ("pending", "Paris", spelled)

    out = changes.confirm(mem.db, change["id"], tenant_id="default", actor="test")
    assert out["ok"]
    assert _current(mem) == [(spelled, "user_explicit")]
    states = {(c["kind"], c["state"]) for c in _changes(mem)}
    assert ("pending", "confirmed") in states and ("update", "open") in states  # and it can be undone


def test_a_dismissed_correction_changes_nothing(mem, monkeypatch):
    _turn(mem, monkeypatch, first="Paris", then="Moved to Rotterdam last month.",
          model=[_fact("lives_in", "rotterdam")])
    [change] = _changes(mem, "open")
    assert changes.dismiss(mem.db, change["id"], tenant_id="default")["ok"]
    assert _current(mem) == [("Paris", "user_stated")]
    assert _changes(mem, "open") == []


@pytest.mark.parametrize("then", [
    "My sister moved to Rotterdam last month.",
    "أختي انتقلت إلى روتردام.",
    "My manager يشتغل في Rotterdam now.",
    "Translate to Dutch: I moved to Rotterdam.",
])
def test_someone_elses_words_record_nothing(mem, monkeypatch, then):
    """The model files it under the user, as a careless model would."""
    _turn(mem, monkeypatch, first="Paris", then=then, model=[_fact("lives_in", "Rotterdam")])
    assert _current(mem) == [("Paris", "user_stated")]
    assert _changes(mem) == []


def test_one_pending_change_per_value(mem, monkeypatch):
    _turn(mem, monkeypatch, first="Paris", then="Moved to Rotterdam last month.",
          model=[_fact("lives_in", "rotterdam")])
    _turn(mem, monkeypatch, first="", then="Rotterdam is lovely, moved to Rotterdam in May.",
          model=[_fact("lives_in", "Rotterdam")], turn=3)
    assert len(_changes(mem, "open")) == 1


def test_the_users_own_words_answer_a_pending_change(mem, monkeypatch):
    _turn(mem, monkeypatch, first="Paris", then="Moved to Rotterdam last month.",
          model=[_fact("lives_in", "rotterdam")])
    _turn(mem, monkeypatch, first="", then="I live in Rotterdam now.", model=[_fact("lives_in", "Rotterdam")],
          turn=3)
    states = {(c["kind"], c["state"]) for c in _changes(mem)}
    assert ("pending", "superseded") in states and ("update", "open") in states


# ── forget, bounds ────────────────────────────────────────────────────────


def test_forgetting_the_turn_takes_its_changes(mem, monkeypatch):
    from kazma_core.memory import forget

    eid = _turn(mem, monkeypatch, first="Paris", then="We moved to Rotterdam last month.",
                model=[_fact("lives_in", "rotterdam")])
    assert _changes(mem)
    assert forget.forget_episode(eid, tenant_id="default")["ok"]
    assert _changes(mem) == []


def test_forgetting_the_users_fact_takes_the_pending_change(mem, monkeypatch):
    from kazma_core.memory import forget

    _turn(mem, monkeypatch, first="Paris", then="Moved to Rotterdam last month.",
          model=[_fact("lives_in", "rotterdam")])
    assert _changes(mem, "open")
    forget.forget_mentions("Paris", tenant_id="default")
    assert _changes(mem) == []


def test_an_unanswered_pending_change_expires_and_decided_ones_are_bounded(mem, monkeypatch):
    long_ago = time.time() - (changes.PENDING_DAYS + 1) * 86400
    stated = mutate_belief(mem.db, "user", "lives_in", "Paris", extraction_method="user_stated",
                           source_session="a", source_turn=1, now=long_ago - 10)
    refused = mutate_belief(mem.db, "user", "lives_in", "Rotterdam", extraction_method="llm_inferred",
                            source_session="a", source_turn=2, now=long_ago)
    assert refused.get("blocked") == "lower_trust_source" and stated["belief_id"] == refused["belief_id"]
    assert changes.record_pending(mem.db, tenant_id="default", refused=refused, new_value="Rotterdam",
                                  user_text="Moved to Rotterdam.", session="a", turn=2, now=long_ago)
    monkeypatch.setattr(changes, "KEEP_DECIDED", 3)
    now = time.time()
    for n in range(6):
        mem.db.execute(
            "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, created_at, decided_at) "
            "VALUES (?, 'default', 'pending', 'dismissed', 'user', 'pet', ?, ?)", (f"mc_old{n}", now - 50, now - 50 + n))
    mem.db.commit()
    # Any later change prunes: here another fact of the user's, replaced.
    for turn, place in ((3, "Acme"), (4, "Initech")):
        mutate_belief(mem.db, "user", "works_at", place, predicate_type="functional",
                      extraction_method="user_stated", source_session="a", source_turn=turn, now=now - 10 + turn)
    pending = [c for c in _changes(mem) if c["kind"] == "pending" and c["predicate"] == "lives_in"]
    assert [c["state"] for c in pending] == ["expired"]
    decided = [c for c in _changes(mem) if c["state"] != "open"]
    assert len(decided) == 3
    assert {c["id"] for c in decided} >= {"mc_old5", "mc_old4"}


# ── the Memory page's routes ──────────────────────────────────────────────


def _client(mem, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.memory.schema_v2 import ensure_ops_schema
    from kazma_ui import memory_api

    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("kazma_core.tenant_isolation.multi_user_or_production", lambda: False)
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(mem.dir / "memory_ops.db"))
    ops = sqlite3.connect(str(mem.dir / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    app = FastAPI()
    memory_api.mount_memory_api(app)  # with the operator-action recorder: real statuses
    return TestClient(app)


def test_the_routes_list_confirm_and_undo_with_real_statuses(mem, monkeypatch):
    _turn(mem, monkeypatch, first="Paris", then="Moved to Rotterdam last month.",
          model=[_fact("lives_in", "rotterdam")])
    c = _client(mem, monkeypatch)
    [pending] = c.get("/api/memory/v2/changes").json()["changes"]
    assert (pending["kind"], pending["old_value"], pending["new_value"]) == ("pending", "Paris", "Rotterdam")

    r = c.post(f"/api/memory/v2/changes/{pending['id']}/confirm")
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert _current(mem) == [("Rotterdam", "user_explicit")]
    [update] = c.get("/api/memory/v2/changes").json()["changes"]
    assert update["kind"] == "update"

    r = c.post(f"/api/memory/v2/changes/{update['id']}/undo")
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert _current(mem) == [("Paris", "user_stated")]
    assert c.get("/api/memory/v2/changes").json()["changes"] == []
    decided = {(x["kind"], x["state"]) for x in c.get("/api/memory/v2/changes?state=decided").json()["changes"]}
    assert decided == {("pending", "confirmed"), ("update", "undone")}

    again = c.post(f"/api/memory/v2/changes/{update['id']}/undo")
    assert (again.status_code, again.json()["error"]) == (409, "not_open")
    assert c.post("/api/memory/v2/changes/mc_nope/dismiss").status_code == 404
    assert c.get("/api/memory/v2/changes?state=everything").status_code == 422


def test_the_dismiss_route_leaves_the_fact(mem, monkeypatch):
    _turn(mem, monkeypatch, first="جدة", then="انتقلت إلى الرياض الشهر الماضي.",
          model=[_fact("lives_in", "الرياض")])
    c = _client(mem, monkeypatch)
    [pending] = c.get("/api/memory/v2/changes").json()["changes"]
    assert c.post(f"/api/memory/v2/changes/{pending['id']}/dismiss").json()["ok"]
    assert _current(mem) == [("جدة", "user_stated")]
    assert c.get("/api/memory/v2/changes").json()["changes"] == []


# ── the chat apps' /memory changes ────────────────────────────────────────


def _memory_command(mem, monkeypatch, text: str) -> str:
    from kazma_gateway.slash_commands import _cmd_memory

    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(mem.dir / "memory_ops.db"))
    return _cmd_memory(text, {"memory_tenant": "default", "thread_id": "chat-1"})


def test_the_chat_command_lists_confirms_and_undoes(mem, monkeypatch):
    import re

    _turn(mem, monkeypatch, first="Paris", then="Moved to Rotterdam last month.",
          model=[_fact("lives_in", "rotterdam")])
    listed = _memory_command(mem, monkeypatch, "/memory changes")
    assert "lives in: Kazma kept Paris; you may have said Rotterdam" in listed
    ref = re.search(r"/memory confirm (mc_[0-9a-f]+)", listed).group(1)
    assert len(ref) == changes.REF_CHARS

    # undo takes an update, not a waiting correction
    assert "not what this change takes" in _memory_command(mem, monkeypatch, f"/memory undo {ref}")
    done = _memory_command(mem, monkeypatch, f"/memory confirm {ref}")
    assert done.startswith("✅ Changed: lives in is now Rotterdam.")
    assert _current(mem) == [("Rotterdam", "user_explicit")]
    again = re.search(r"/memory undo (mc_[0-9a-f]+)", done).group(1)
    assert _memory_command(mem, monkeypatch, f"/memory undo {again}") == "↩️ Undone: lives in is Paris again."
    assert _current(mem) == [("Paris", "user_stated")]
    assert _memory_command(mem, monkeypatch, f"/memory undo {again}") == "🔁 That change was already decided."
    assert "nothing is waiting" in _memory_command(mem, monkeypatch, "/memory changes")


def test_the_chat_command_refuses_a_short_or_unknown_id(mem, monkeypatch):
    _turn(mem, monkeypatch, first="Paris", then="Moved to Rotterdam last month.",
          model=[_fact("lives_in", "rotterdam")])
    assert "No change `mc_1`" in _memory_command(mem, monkeypatch, "/memory dismiss mc_1")
    assert "No change" in _memory_command(mem, monkeypatch, "/memory dismiss mc_ffffffff")
    assert "Which change?" in _memory_command(mem, monkeypatch, "/memory dismiss")
    assert _current(mem) == [("Paris", "user_stated")]


def test_the_chat_command_is_an_admins():
    from kazma_gateway.slash_commands import changes_global_config

    for text in ("/memory changes", "/memory undo mc_1a2b3c4d", "/memory confirm x", "/memory dismiss x"):
        assert changes_global_config(text), text
    for text in ("/memory", "/memory off", "/memory on"):
        assert not changes_global_config(text), text


# ── negative control: the refusal as it was ───────────────────────────────


def test_control_without_the_change_log_a_refusal_was_silent(mem, monkeypatch):
    monkeypatch.setattr(changes, "record_pending", lambda *a, **k: None)
    monkeypatch.setattr(changes, "record_update", lambda *a, **k: "")
    _turn(mem, monkeypatch, first="Paris", then="Moved to Rotterdam last month.",
          model=[_fact("lives_in", "rotterdam")])
    _turn(mem, monkeypatch, first="", then="We moved to Lisbon.", model=[_fact("lives_in", "Lisbon")], turn=3)
    assert _current(mem) == [("Lisbon", "user_stated")]
    assert _changes(mem) == []
