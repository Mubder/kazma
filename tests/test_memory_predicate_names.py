"""One fact, one predicate name (plan W6, ``kazma_core/memory/predicates.py``).

The extracting model names predicates freely, and a fact under two names is
two facts: measured on the live install on 2026-09-27, eight subjects held two
conflicting current values that way ("daily_tweet_cap" 16 and
"tweet_daily_cap" 8; "version" 0.10.0 and "version_is" 0.11.0) and seven held
one value twice ("timezone" / "timezone_is"). Names that are the same words
(order, filler words and a plural "s" aside) are now one name: a new fact
takes the name its subject already uses, and reconsolidation retires what is
already stored twice. Names that differ by a word are left apart -- by meaning,
"grok_next_reset" and "grok_personal_next_reset" (two accounts) are closer
than most true pairs.
"""

from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

import pytest

from kazma_core.memory import predicates
from kazma_core.memory.schema_v2 import ensure_primary_schema


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    c = sqlite3.connect(str(tmp_path / "memory_state.db"))
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    yield c
    c.close()


def _say(conn, predicate, value, *, ptype="functional", method="llm_inferred", at=None, subject="user"):
    from kazma_core.memory.belief_mutation import mutate_belief

    return mutate_belief(conn, subject, predicate, value, predicate_type=ptype,
                         extraction_method=method, now=at, private=True)


def _current(conn, subject="user"):
    return sorted(
        (r["predicate"], r["object"]) for r in conn.execute(
            "SELECT predicate, object FROM beliefs WHERE subject = ? "
            "AND valid_until IS NULL AND invalidated_at IS NULL", (subject,))
    )


# ── The rule ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("a, b", [
    ("timezone", "timezone_is"),
    ("daily_tweet_cap", "tweet_daily_cap"),
    ("founder_of", "is_founder_of"),
    ("admin_grok_next_reset", "grok_admin_next_reset"),
    ("completed_phase", "completed_phases"),
    ("Brand Name Rejected", "rejected_brand_name"),
])
def test_names_of_the_same_words_are_one_name(a, b):
    assert predicates._predicate_key(a) == predicates._predicate_key(b)


@pytest.mark.parametrize("a, b", [
    ("grok_next_reset", "grok_personal_next_reset"),  # two accounts
    ("slack_id", "slack_user_id"),                     # a word apart: the prompt's job
    ("telegram_chat_id", "telegram_user_id"),          # not the same id
    ("address", "addresses_count"),
])
def test_names_a_word_apart_are_not(a, b):
    assert predicates._predicate_key(a) != predicates._predicate_key(b)


# ── A new fact takes the name its subject uses ────────────────────────────


def test_a_new_value_under_another_name_updates_the_fact(conn):
    now = time.time()
    _say(conn, "tweet_daily_cap", "8", at=now - 60)
    _say(conn, "daily_tweet_cap", "16", at=now)
    assert _current(conn) == [("tweet_daily_cap", "16")]
    history = conn.execute(
        "SELECT object FROM beliefs WHERE predicate = 'tweet_daily_cap' AND valid_until IS NOT NULL"
    ).fetchall()
    assert [r[0] for r in history] == ["8"]


def test_without_the_rule_the_second_name_holds_a_second_value(conn, monkeypatch):
    """Negative control: the live install's shape."""
    monkeypatch.setattr(predicates, "canonical_predicate", lambda conn, *, predicate, **kw: predicate)
    now = time.time()
    _say(conn, "tweet_daily_cap", "8", at=now - 60)
    _say(conn, "daily_tweet_cap", "16", at=now)
    assert _current(conn) == [("daily_tweet_cap", "16"), ("tweet_daily_cap", "8")]


def test_the_same_value_under_another_name_is_written_once(conn):
    _say(conn, "timezone", "Asia/Kuwait")
    _say(conn, "timezone_is", "Asia/Kuwait")
    assert _current(conn) == [("timezone", "Asia/Kuwait")]


def test_the_user_s_word_still_outranks_an_inference(conn):
    """The trust gate holds through the rename: an inferred value under
    another name does not replace what the user said."""
    now = time.time()
    _say(conn, "tweet_daily_cap", "8", method="user_explicit", at=now - 60)
    _say(conn, "daily_tweet_cap", "16", method="llm_inferred", at=now)
    assert ("tweet_daily_cap", "8") in _current(conn)
    assert ("daily_tweet_cap", "16") not in _current(conn)


def test_a_set_under_another_name_appends(conn):
    _say(conn, "brand_name_rejected", "fitnx", ptype="set")
    _say(conn, "rejected_brand_name", "mostfit", ptype="set")
    assert _current(conn) == [("brand_name_rejected", "fitnx"), ("brand_name_rejected", "mostfit")]


def test_another_type_or_other_words_keep_their_own_name(conn):
    _say(conn, "completed_phase", "phase_1", ptype="set")
    _say(conn, "completed_phases", "3", ptype="functional")
    _say(conn, "grok_next_reset", "2026-10-01")
    _say(conn, "grok_personal_next_reset", "2026-10-05")
    assert {p for p, _ in _current(conn)} == {
        "completed_phase", "completed_phases", "grok_next_reset", "grok_personal_next_reset",
    }


# ── What is already stored twice ──────────────────────────────────────────


def _raw(conn, bid, predicate, value, *, ptype="functional", method="llm_inferred", at=1000.0,
         subject="user"):
    """Insert as the pre-W6 writers left it: no renaming."""
    conn.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "structural_importance, source_trust_weight, extraction_method, valid_from, ingested_at) "
        "VALUES (?, 'default', ?, ?, ?, ?, 0.8, 2, 1.0, ?, ?, ?)",
        (bid, subject, predicate, ptype, value, method, at, at),
    )
    conn.commit()


def _live_shapes(conn):
    _raw(conn, "cap_old", "daily_tweet_cap", "16", at=1000.0)
    _raw(conn, "cap_new", "tweet_daily_cap", "8", at=2000.0)
    _raw(conn, "role_user", "role", "solo_maintainer", method="user_explicit", at=1000.0)
    _raw(conn, "role_llm", "role_is", "ceo", at=2000.0)
    _raw(conn, "tz_1", "timezone", "asia/kuwait", ptype="set", at=1000.0)
    _raw(conn, "tz_2", "timezone_is", "Asia/Kuwait", ptype="set", at=2000.0)
    _raw(conn, "rej_1", "brand_name_rejected", "fitnx", ptype="set")
    _raw(conn, "rej_2", "rejected_brand_name", "mostfit", ptype="set")
    # Different facts that happen to share a value: never touched.
    _raw(conn, "flag_1", "hitl_approval_enabled", "true")
    _raw(conn, "flag_2", "registering_x_developer_app", "true")


def test_reconsolidation_retires_one_fact_stored_under_two_names(conn):
    _live_shapes(conn)
    retired = set(predicates.merge_same_word_predicates(conn, tenant_id="default"))
    assert retired == {"cap_old", "tz_2"}
    current = {r[0] for r in conn.execute(
        "SELECT id FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL")}
    assert {"cap_new", "role_user", "role_llm", "tz_1", "rej_1", "rej_2", "flag_1", "flag_2"} <= current
    assert predicates.merge_same_word_predicates(conn, tenant_id="default") == []  # idempotent


def test_the_users_word_against_an_inference_is_left_as_it_is(conn):
    """The live admin-reset case: the user stated Sept 1 in August, a
    September turn's extraction (labelled inferred) says Sept 29. Keeping the
    user's would retire the newer date; keeping the newer would let an
    inference overrule the user. Both stay, with their dates."""
    _raw(conn, "said", "admin_grok_next_reset", "2026-09-01 14:36", method="user_explicit", at=1000.0)
    _raw(conn, "read", "grok_admin_next_reset", "2026-09-29T14:36", at=2000.0)
    assert predicates.merge_same_word_predicates(conn, tenant_id="default") == []


def test_the_merge_is_the_words_rule_and_nothing_else(conn, monkeypatch):
    """Negative control: with each name its own word set, nothing merges."""
    _live_shapes(conn)
    monkeypatch.setattr(predicates, "_predicate_key", lambda p: frozenset({p or ""}))
    assert predicates.merge_same_word_predicates(conn, tenant_id="default") == []


def test_the_reconsolidation_sweep_runs_it(conn):
    from kazma_core.memory.global_reconsolidation import run_global_reconsolidation

    _live_shapes(conn)
    stats = run_global_reconsolidation(conn, tenant_id="default", reembed_limit=0)
    assert stats["same_fact_other_name_retired"] == 2
    assert stats["duplicate_beliefs_merged"] >= 2


# ── The extractor is shown the names in use ───────────────────────────────


def test_the_extractor_is_asked_to_reuse_the_names_in_use(monkeypatch):
    import asyncio

    from kazma_core.memory import belief_extractor

    seen: list = []

    class _Client:
        async def chat(self, messages):
            seen.append(messages)
            return '{"beliefs": []}'

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: SimpleNamespace(get_client=lambda: _Client()),
    )
    asyncio.run(belief_extractor.extract_beliefs_with_llm(
        "my timezone is Kuwait", "Noted.", vocabulary=["timezone", "works_at"]))
    system, user = seen[0][0]["content"], seen[0][1]["content"]
    assert "Predicates already in use: timezone, works_at" in user
    assert "Reuse a predicate" in system and "internal" in system


def test_the_vocabulary_is_the_most_used_current_names(conn):
    for i in range(3):
        _raw(conn, f"w{i}", "works_at", f"acme{i}", ptype="set")
    _raw(conn, "t0", "timezone", "asia/kuwait")
    _raw(conn, "gone", "lives_in", "paris")
    conn.execute("UPDATE beliefs SET invalidated_at = 1 WHERE id = 'gone'")
    assert predicates.predicate_vocabulary(conn, tenant_id="default") == ["works_at", "timezone"]
