"""Memory V2 Phase 3 tests — cognitive loop (mutation, entity, procedural, decay).

Covers:
  - belief_mutation: functional supersede chain, set append, state transition,
    idempotency, audit log, memory_class derivation
  - entity_resolution: tier-1 exact match, high-stakes quarantine flag
  - procedural: Laplace confidence, record outcome, quarantine policy
  - macro_sleep: retention scoring, tier demotion, belief archival
  - recall integration: superseded beliefs excluded from recall

All tests use tmp_path + KAZMA_DATA_DIR override.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.memory import dual_write

    dual_write.reset_mirror()
    yield tmp_path
    dual_write.reset_mirror()


@pytest.fixture()
def dbs(isolated_data):
    """Open + initialize both DBs, return (primary_conn, ops_conn)."""
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    p = sqlite3.connect(primary_memory_db())
    p.row_factory = sqlite3.Row
    ensure_primary_schema(p)
    o = sqlite3.connect(memory_ops_db())
    ensure_ops_schema(o)
    yield p, o
    p.close()
    o.close()


# ── memory_class derivation (resolution #4) ───────────────────────────────


def test_derive_memory_class_deterministic():
    from kazma_core.memory.belief_mutation import derive_memory_class

    assert derive_memory_class("functional", 5) == "identity"
    assert derive_memory_class("functional", 4) == "identity"
    assert derive_memory_class("functional", 3) == "general"
    assert derive_memory_class("set", 2) == "ephemeral"
    assert derive_memory_class("set", 1) == "ephemeral"
    assert derive_memory_class("state", 4) == "general"  # non-functional can't be identity


# ── Belief mutation: functional supersede ─────────────────────────────────


def test_functional_supersede_chain(dbs):
    """Paris → London → Berlin: only Berlin stays active, full chain preserved."""
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    r1 = mutate_belief(p, "user", "lives_in", "Paris", ops_conn=o, importance=5, extraction_method="user_explicit")
    r2 = mutate_belief(p, "user", "lives_in", "London", ops_conn=o, importance=5, extraction_method="user_explicit")
    r3 = mutate_belief(p, "user", "lives_in", "Berlin", ops_conn=o, importance=5, extraction_method="user_explicit")

    assert r1["action"] == "supersede" and r1["superseded_id"] is None
    assert r2["action"] == "supersede" and r2["superseded_id"] == r1["belief_id"]
    assert r3["action"] == "supersede" and r3["superseded_id"] == r2["belief_id"]

    # Only Berlin is active
    active = [
        r["object"]
        for r in p.execute(
            "SELECT object FROM beliefs WHERE subject='user' AND predicate='lives_in' "
            "AND valid_until IS NULL"
        ).fetchall()
    ]
    assert active == ["Berlin"], f"only Berlin active, got {active}"

    # Full chain preserved (3 rows total)
    count = p.execute(
        "SELECT COUNT(*) FROM beliefs WHERE subject='user' AND predicate='lives_in'"
    ).fetchone()[0]
    assert count == 3


def test_functional_idempotent(dbs):
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    mutate_belief(p, "user", "name_is", "Alice", ops_conn=o, importance=5, extraction_method="user_explicit")
    r = mutate_belief(p, "user", "name_is", "Alice", ops_conn=o, importance=5, extraction_method="user_explicit")
    assert r["action"] == "noop"


def test_set_predicate_multi_valued(dbs):
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    s1 = mutate_belief(p, "user", "uses_tool", "git", ops_conn=o, importance=3)
    s2 = mutate_belief(p, "user", "uses_tool", "docker", ops_conn=o, importance=3)
    s3 = mutate_belief(p, "user", "uses_tool", "git", ops_conn=o, importance=3)

    assert s1["action"] == "append"
    assert s2["action"] == "append"
    assert s3["action"] == "noop"  # duplicate

    tools = {
        r["object"]
        for r in p.execute(
            "SELECT object FROM beliefs WHERE subject='user' AND predicate='uses_tool' "
            "AND valid_until IS NULL"
        ).fetchall()
    }
    assert tools == {"git", "docker"}


def test_audit_log_written(dbs):
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    mutate_belief(p, "user", "lives_in", "Paris", ops_conn=o, importance=5, extraction_method="user_explicit")
    mutate_belief(p, "user", "uses_tool", "git", ops_conn=o, importance=3)
    count = o.execute("SELECT COUNT(*) FROM memory_audit_log").fetchone()[0]
    assert count >= 2


def test_state_transition_logs_transition_event_type(dbs):
    """A state-predicate change records event_type='transition' (not 'supersede').

    Regression guard: ``_mutate_state`` used to reuse ``_mutate_functional``'s
    audit call, which mislabelled every state change as ``supersede``. Audit-log
    consumers filtering by event_type would have missed state transitions.
    """
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    # issue_status is a canonical state predicate (see _STATE_PREDICATES).
    r1 = mutate_belief(
        p, "task-1", "issue_status", "open",
        ops_conn=o, importance=4, extraction_method="user_explicit",
    )
    r2 = mutate_belief(
        p, "task-1", "issue_status", "closed",
        ops_conn=o, importance=4, extraction_method="user_explicit",
    )
    # The returned action reflects the state semantics...
    assert r1["action"] == "transition"
    assert r2["action"] == "transition" and r2["superseded_id"] == r1["belief_id"]

    # ...and the persisted audit row must carry the 'transition' label.
    o.row_factory = sqlite3.Row
    rows = o.execute(
        "SELECT event_type FROM memory_audit_log WHERE target_id=?",
        (r2["belief_id"],),
    ).fetchall()
    assert rows, "expected an audit row for the transitioned belief"
    assert rows[0]["event_type"] == "transition", (
        f"state transition mislabelled as {rows[0]['event_type']!r}"
    )


def test_memory_class_in_metadata(dbs):
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    r = mutate_belief(p, "user", "name_is", "Alice", ops_conn=o, importance=5, extraction_method="user_explicit")
    meta = json.loads(
        p.execute("SELECT metadata_json FROM beliefs WHERE id=?", (r["belief_id"],)).fetchone()["metadata_json"]
    )
    assert meta["memory_class"] == "identity"


# ── Entity resolution ─────────────────────────────────────────────────────


def test_entity_exact_match(dbs):
    from kazma_core.memory.entity_resolution import resolve_entity

    p, o = dbs
    r1 = resolve_entity(p, "John Smith", entity_type="person")
    r2 = resolve_entity(p, "John Smith", entity_type="person")
    assert r1["action"] == "create"
    assert r2["action"] == "exact_match"
    assert r1["canonical_id"] == r2["canonical_id"]


def test_high_stakes_entity_flagged(dbs):
    from kazma_core.memory.entity_resolution import resolve_entity

    p, o = dbs
    resolve_entity(p, "Jane Doe", entity_type="person")
    row = p.execute("SELECT is_high_stakes FROM entities WHERE id='jane_doe'").fetchone()
    assert row["is_high_stakes"] == 1


def test_low_stakes_entity_not_flagged(dbs):
    from kazma_core.memory.entity_resolution import resolve_entity

    p, o = dbs
    resolve_entity(p, "python", entity_type="tool")
    row = p.execute("SELECT is_high_stakes FROM entities WHERE id='python'").fetchone()
    assert row["is_high_stakes"] == 0


# ── Procedural DAGs ───────────────────────────────────────────────────────


def test_laplace_confidence_values():
    from kazma_core.memory.procedural import laplace_confidence

    assert laplace_confidence(0, 0) == 0.5
    assert abs(laplace_confidence(3, 4) - 4 / 6) < 1e-9
    assert abs(laplace_confidence(0, 10) - 1 / 12) < 1e-9


def test_procedural_record_and_quarantine(dbs):
    from kazma_core.memory.procedural import record_procedural_outcome

    p, o = dbs
    precond = {"task": "deploy", "lang": "python"}
    steps = [{"tool": "git_push"}, {"tool": "ci_wait"}]
    postcond = {"ci_green": True}

    o1 = record_procedural_outcome(
        p, name="deploy", description="deploy py", preconditions=precond,
        dag_steps=steps, postconditions=postcond, success=True,
    )
    assert o1["action"] == "created"
    assert o1["confidence"] > 0.5  # one success → (1+1)/(1+2) = 0.667

    # Drive it into quarantine with repeated failures
    for _ in range(6):
        record_procedural_outcome(
            p, name="deploy", description="deploy py", preconditions=precond,
            dag_steps=steps, postconditions=postcond, success=False,
        )
    row = p.execute(
        "SELECT status, confidence_score FROM procedural_dags WHERE id=?",
        (o1["dag_id"],),
    ).fetchone()
    assert row["status"] == "quarantine"
    assert row["confidence_score"] < 0.40


# ── Macro sleep / decay ───────────────────────────────────────────────────


def test_retention_identity_beats_ephemeral():
    from kazma_core.memory.macro_sleep import compute_retention

    ident = compute_retention(
        trust_weight=1.0, importance=5, access_count=10,
        age_seconds=86400 * 365, memory_class="identity",
    )
    ephem = compute_retention(
        trust_weight=1.0, importance=1, access_count=0,
        age_seconds=86400 * 30, memory_class="ephemeral",
    )
    assert ident > ephem


def test_macro_sleep_demotes_old_episodic(dbs):
    from kazma_core.memory.config import DEFAULT_MEMORY_CFG
    from kazma_core.memory.macro_sleep import run_macro_sleep

    p, o = dbs
    now = time.time()
    # Old, low-importance episodic episode → should archive
    p.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
        "tier, structural_importance, access_count, last_accessed, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("e_old", "default", "s1", 1, "old note", "episodic", 1, 0, now - 86400 * 60, now - 86400 * 60),
    )
    p.commit()
    stats = run_macro_sleep(p, cfg=DEFAULT_MEMORY_CFG, now=now)
    tier = p.execute("SELECT tier FROM episodes WHERE id='e_old'").fetchone()["tier"]
    assert tier == "archived"
    assert stats["demoted_episodic"] >= 1


def test_macro_sleep_promotes_important_episodic(dbs):
    from kazma_core.memory.config import DEFAULT_MEMORY_CFG
    from kazma_core.memory.macro_sleep import run_macro_sleep

    p, o = dbs
    now = time.time()
    # Important + frequently-accessed episodic → should promote to recall
    p.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
        "tier, structural_importance, access_count, last_accessed, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("e_hot", "default", "s1", 1, "important fact", "episodic", 4, 3, now - 3600, now - 7200),
    )
    p.commit()
    run_macro_sleep(p, cfg=DEFAULT_MEMORY_CFG, now=now)
    tier = p.execute("SELECT tier FROM episodes WHERE id='e_hot'").fetchone()["tier"]
    assert tier == "recall"


def test_macro_sleep_archives_old_superseded_beliefs(dbs):
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.config import DEFAULT_MEMORY_CFG
    from kazma_core.memory.macro_sleep import run_macro_sleep

    p, o = dbs
    # Create then supersede a belief, backdating the valid_until
    r = mutate_belief(p, "user", "lives_in", "Paris", ops_conn=o, importance=5)
    mutate_belief(p, "user", "lives_in", "London", ops_conn=o, importance=5)
    # Backdate the superseded (Paris) belief's valid_until to > 180 days ago
    old = now = time.time()
    p.execute(
        "UPDATE beliefs SET valid_until=? WHERE id=?",
        (old - 86400 * 200, r["belief_id"]),
    )
    p.commit()
    run_macro_sleep(p, cfg=DEFAULT_MEMORY_CFG, now=now)
    # Paris should be in the archive now
    archived = p.execute(
        "SELECT COUNT(*) FROM beliefs_archive WHERE id=?", (r["belief_id"],)
    ).fetchone()[0]
    assert archived == 1
    # And removed from the active beliefs table
    in_active = p.execute(
        "SELECT COUNT(*) FROM beliefs WHERE id=?", (r["belief_id"],)
    ).fetchone()[0]
    assert in_active == 0


# ── Recall integration ────────────────────────────────────────────────────


def test_recall_excludes_superseded_after_mutation(dbs):
    """End-to-end: mutate Paris→London, recall must show only London."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.recall import recall

    p, o = dbs
    mutate_belief(p, "user", "lives_in", "Paris", ops_conn=o, importance=5)
    mutate_belief(p, "user", "lives_in", "London", ops_conn=o, importance=5)
    # Bridge episode
    now = time.time()
    p.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
        "tier, created_at) VALUES (?,?,?,?,?,?,?)",
        ("e1", "default", "s1", 1, "I live in London now", "recall", now),
    )
    p.commit()
    result = recall("where do I live", conn=p, limit=5)
    lives = [h for h in result.beliefs if h.metadata.get("predicate") == "lives_in"]
    assert len(lives) == 1
    assert lives[0].metadata["object"] == "London"


# ── Never-supersede patterns (reminder protection) ────────────────────────


def test_reminder_predicates_never_supersede(dbs):
    """Time-bound predicates (reminder, scheduled, etc.) must be 'set' type.

    Multiple reminders with the same (subject, predicate) should coexist
    instead of superseding each other. This prevents the bug where a
    scheduled reminder belief was silently replaced by a later conversation.
    """
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    r1 = mutate_belief(p, "user", "has_reminder", "Grok reset Aug 3", ops_conn=o, importance=3)
    r2 = mutate_belief(p, "user", "has_reminder", "ZCode reset Aug 5", ops_conn=o, importance=3)
    r3 = mutate_belief(p, "user", "scheduled_event", "Meeting Monday", ops_conn=o, importance=3)

    assert r1["action"] == "append"
    assert r2["action"] == "append"
    assert r3["action"] == "append"

    active = p.execute(
        "SELECT object FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
    ).fetchall()
    objects = {row["object"] if isinstance(row, sqlite3.Row) else row[0] for row in active}
    assert "Grok reset Aug 3" in objects, "First reminder was superseded"
    assert "ZCode reset Aug 5" in objects, "Second reminder was superseded"
    assert "Meeting Monday" in objects, "Scheduled event was superseded"


def test_reminder_forced_set_even_with_explicit_functional(dbs):
    """Even if the LLM explicitly classifies a reminder as 'functional', it must be forced to 'set'."""
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    r1 = mutate_belief(
        p, "user", "has_reminder", "Event A",
        ops_conn=o, predicate_type="functional", importance=3,
    )
    r2 = mutate_belief(
        p, "user", "has_reminder", "Event B",
        ops_conn=o, predicate_type="functional", importance=3,
    )

    assert r1["action"] == "append", "Reminder forced to functional"
    assert r2["action"] == "append", "Second reminder superseded the first"

    count = p.execute(
        "SELECT COUNT(*) FROM beliefs WHERE predicate='has_reminder' "
        "AND valid_until IS NULL AND invalidated_at IS NULL"
    ).fetchone()[0]
    assert count == 2, f"Expected 2 active reminders, got {count}"


# ── Episode FTS binding correctness ───────────────────────────────────────


def test_episode_fts_binding_correct(isolated_data):
    """_episode_fts must not raise a binding error (tenant_id passed twice bug).

    Regression test for the bug where params had 2n+3 values but SQL had 2n+2
    placeholders, causing all episode searches to silently return [].
    """
    from kazma_core.memory.recall import _episode_fts
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    p = sqlite3.connect(primary_memory_db())
    p.row_factory = sqlite3.Row
    ensure_primary_schema(p)
    now = time.time()
    p.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
        "assistant_text, tier, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("e_fts1", "default", "s1", 1, "remind me about Grok reset", "Done, saved it", "episodic", now),
    )
    p.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
        "assistant_text, tier, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("e_fts2", "default", "s1", 2, "also ZCode reset please", "All set", "episodic", now),
    )
    p.commit()

    hits = _episode_fts(p, "Grok reset", "default", 10)
    assert len(hits) > 0, "Episode FTS returned empty — binding error likely"
    assert any("Grok" in h.content for h in hits), "Expected Grok episode in results"

    hits2 = _episode_fts(p, "ZCode", "default", 10)
    assert len(hits2) > 0, "Episode FTS returned empty for ZCode query"
    p.close()


# ── merged_into redirect: beliefs minted under a retired id land on canonical ─


def test_mutate_belief_follows_merged_into(dbs):
    """A belief written under a retired (merged-away) entity id must land on
    the canonical target. Reproduces the mubder→user re-orphaning bug:
    extraction kept minting `mubder has_project X` 17h after the merge
    because nothing read merged_into. The write-side redirect in
    mutate_belief (canonical_entity_id) is the fix.
    """
    import json
    from kazma_core.memory.belief_mutation import mutate_belief
    p, o = dbs

    # Set up: two entities, 'oldself' merged into 'user' (merged_into set).
    p.execute("INSERT OR IGNORE INTO entities (id, tenant_id, type, name) "
              "VALUES ('oldself','default','person','Old Self')")
    p.execute("UPDATE entities SET metadata_json=? WHERE id='oldself'",
              (json.dumps({"merged_into": "user"}),))
    p.commit()

    # Now write a belief with subject='oldself' (the retired id) — exactly
    # what extraction does when it encounters the old name post-merge.
    r = mutate_belief(p, "oldself", "has_project", "probe_canon", ops_conn=o,
                      predicate_type="set", tenant_id="default")
    assert r.get("belief_id"), f"no belief created: {r}"

    # The belief's subject MUST be 'user' (the canonical target), NOT 'oldself'.
    row = p.execute("SELECT subject FROM beliefs WHERE id=?", (r["belief_id"],)).fetchone()
    assert row["subject"] == "user", (
        f"belief minted under retired id {row['subject']!r}, expected 'user'"
    )

    # Cleanup the probe belief so other tests in this module are unaffected.
    p.execute("DELETE FROM beliefs WHERE id=?", (r["belief_id"],))
    p.execute("DELETE FROM entities WHERE id='oldself'")
    p.commit()


def test_canonical_entity_id_follows_chain(dbs):
    """canonical_entity_id follows a→b→c to the terminal id, and is a no-op
    for an id with no merge."""
    import json
    from kazma_core.memory.entity_resolution import canonical_entity_id
    p, _ = dbs

    # a → b → c chain
    for eid in ("chain_a", "chain_b", "chain_c"):
        p.execute("INSERT OR IGNORE INTO entities (id, tenant_id, type, name) "
                  f"VALUES ('{eid}','default','concept','{eid}')")
    p.execute("UPDATE entities SET metadata_json=? WHERE id='chain_a'",
              (json.dumps({"merged_into": "chain_b"}),))
    p.execute("UPDATE entities SET metadata_json=? WHERE id='chain_b'",
              (json.dumps({"merged_into": "chain_c"}),))
    p.commit()

    assert canonical_entity_id(p, "chain_a") == "chain_c", "chain not followed"
    assert canonical_entity_id(p, "chain_b") == "chain_c"
    assert canonical_entity_id(p, "chain_c") == "chain_c"  # terminal
    # No merge → unchanged.
    assert canonical_entity_id(p, "user") == "user"
    # Cleanup
    p.execute("DELETE FROM entities WHERE id IN ('chain_a','chain_b','chain_c')")
    p.commit()
