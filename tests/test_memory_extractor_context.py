"""The deep pass is told the turn's day and the turn before it (framework
audit F2 step 2, part C2).

The extractor was given the turn's text and nothing else, so "We moved to
Rotterdam last month" was stored with no time (the recorded reply was
``lives_in rotterdam``), and "yes, that one" could not be read. Now the
memory worker gives the model the turn's day in the operator's zone and the
remembered turn before it in the same chat (context only: "learn nothing
from it"); the model gives a fact's start as ``since`` worked out from the
day; Kazma keeps a real date no later than the turn beside the fact
(``metadata.started``), never in place of when it was said (W1), and recall
shows it. Negative controls: the worker without the context sends neither;
a fact written without its start shows the month it was said.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from kazma_core.memory import belief_extractor, recall as recall_mod, worker_bootstrap
from kazma_core.memory.belief_extractor import _since_value, _started_on, turn_message
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.limits import DEEP_PASS_PREVIOUS_ANSWER, DEEP_PASS_PREVIOUS_QUESTION
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

#: 2026-05-31 22:30 UTC: still the 31st in London, the 1st of June in Kuwait.
LATE_ON_THE_31ST = 1780266600.0


def test_the_message_names_the_day_and_the_turn_before_bounded():
    message = turn_message("Yes, that one. We moved there last month.", "Noted.", said_on="2026-05-31",
                           previous=("Is Rotterdam nice?" + "q" * 2000, "It is." + "z" * 2000))
    assert message.startswith("Date of this turn: 2026-05-31 (Sunday)\n\n"
                              "The turn before, to understand this one (learn nothing from it):\nUser: Is Rotterdam")
    before, this = message.split("\n\nThis turn:\n")
    assert this.startswith("User: Yes, that one. We moved there last month.\nAssistant: Noted.")
    assert before.count("q") == DEEP_PASS_PREVIOUS_QUESTION - len("Is Rotterdam nice?")
    assert before.count("z") == DEEP_PASS_PREVIOUS_ANSWER - len("It is.")
    # With neither, the message is form 1's, exactly.
    assert turn_message("I live in Lisbon.") == "User: I live in Lisbon.\nAssistant: "
    assert turn_message("I live in Lisbon.", previous=("", "")) == "User: I live in Lisbon.\nAssistant: "


def test_the_prompt_asks_for_the_start_and_learns_only_from_this_turn():
    prompt = belief_extractor._EXTRACT_SYSTEM
    assert '"since"' in prompt and 'Learn only\n  from "This turn"' in prompt
    assert "never guess one" in prompt


@pytest.mark.parametrize(("given", "kept"), [
    ("2026-04", "2026-04"), ("2019", "2019"), ("2026-05-17", "2026-05-17"), (" 2026-04 ", "2026-04"),
    ("last month", None), ("2026-02-30", None), ("2026-13", None), ("1850", None), ("", None), (None, None),
    ("قبل سنتين", None), ("2026-4", None), ("٢٠٢٦-٠٤", "2026-04"), ("۲۰۲۶", "2026"),
])
def test_a_start_is_a_real_date_never_words(given, kept):
    assert _since_value(given) == kept


def test_a_start_after_the_turn_is_never_kept():
    assert _started_on("2026-05-31", LATE_ON_THE_31ST, UTC) == "2026-05-31"
    assert _started_on("2026-06", LATE_ON_THE_31ST, UTC) is None
    assert _started_on("2026-06", LATE_ON_THE_31ST, ZoneInfo("Asia/Kuwait")) == "2026-06"
    assert _started_on("2027", LATE_ON_THE_31ST, UTC) is None
    assert _started_on("2026", LATE_ON_THE_31ST, UTC) == "2026"
    assert _started_on(None, LATE_ON_THE_31ST, UTC) is None


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr(recall_mod, "_memory_zone", lambda: UTC)
    monkeypatch.setattr(belief_extractor, "_turn_zone", lambda: ZoneInfo("Asia/Kuwait"))
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), isolation_level=None)
    ensure_ops_schema(ops)
    yield conn, ops
    conn.close()
    ops.close()


def _episode(conn, eid, turn, user, answer, *, at, tenant="default", session="chat-1", tier="episodic"):
    conn.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, created_at, "
        "tier) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (eid, tenant, session, turn, user, answer, at, tier))


def _asked(memory, episode_id: str, *, context: bool = True) -> list[str]:
    """What the real memory worker sends the model for *episode_id*."""
    sent: list[str] = []

    class Client:
        async def chat(self, messages, **_kw):
            sent.append(messages[-1]["content"])
            return json.dumps({"beliefs": []})

    registry = type("R", (), {"get_client": lambda self: Client()})()
    patches = [mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry)]
    if not context:
        patches.append(mock.patch.object(worker_bootstrap, "_turn_context", lambda *_a: {}))
    with patches[0]:
        if len(patches) > 1:
            with patches[1]:
                assert asyncio.run(worker_bootstrap._handle_micro_consolidation({"episode_id": episode_id}))
        else:
            assert asyncio.run(worker_bootstrap._handle_micro_consolidation({"episode_id": episode_id}))
    return sent


def test_the_worker_gives_the_model_the_day_and_the_turn_before(memory):
    conn, _ops = memory
    _episode(conn, "e1", 1, "Is Rotterdam nice?", "It is, by the water.", at=LATE_ON_THE_31ST - 3600)
    _episode(conn, "e2", 2, "My code is 4412.", "", at=LATE_ON_THE_31ST - 1800, tier="forgotten")
    _episode(conn, "e9", 2, "Another space's turn.", "", at=LATE_ON_THE_31ST - 600, tenant="space_b")
    _episode(conn, "e3", 3, "Yes, that one. We moved there last month.", "Welcome!", at=LATE_ON_THE_31ST)
    [message] = _asked(memory, "e3")
    # The day in the operator's zone (Kuwait: already the 1st of June), the
    # chat's remembered turn before (never a forgotten one, never another
    # space's), and the turn itself.
    assert message.startswith("Date of this turn: 2026-06-01 (Monday)")
    assert "User: Is Rotterdam nice?\nAssistant: It is, by the water." in message
    assert "4412" not in message and "Another space" not in message
    assert message.endswith("This turn:\nUser: Yes, that one. We moved there last month.\nAssistant: Welcome!")


def test_control_the_worker_without_the_context_sends_neither(memory):
    conn, _ops = memory
    _episode(conn, "e1", 1, "Is Rotterdam nice?", "It is.", at=LATE_ON_THE_31ST - 3600)
    _episode(conn, "e3", 2, "Yes, that one. We moved there last month.", "", at=LATE_ON_THE_31ST)
    [message] = _asked(memory, "e3", context=False)
    assert "Date of this turn" not in message and "Rotterdam nice" not in message


def _apply(memory, beliefs, *, at=LATE_ON_THE_31ST, user_text="We moved to Rotterdam last month."):
    conn, ops = memory
    belief_extractor._apply_beliefs_to_v2(beliefs, conn, ops, session_id="chat-1", turn=1, tenant_id="default",
                                          now=at, private=True, user_text=user_text)


def _block(conn, question="Where do I live?") -> str:
    result = recall_mod.recall(question, conn=conn, tenant_id="default", local_only=True)
    return recall_mod.format_recall_block(result)


def test_the_start_is_kept_beside_the_fact_and_shown(memory):
    conn, _ops = memory
    _apply(memory, [{"subject": "user", "predicate": "lives_in", "predicate_type": "functional",
                     "object": "Rotterdam", "since": "2026-04", "confidence": 0.9}])
    row = conn.execute("SELECT valid_from, json_extract(metadata_json, '$.started') FROM beliefs").fetchone()
    # When it was said stays the turn's time (W1); the start sits beside it.
    assert row[0] == LATE_ON_THE_31ST and row[1] == "2026-04"
    assert "[since Apr 2026] user lives in Rotterdam" in _block(conn)


def test_control_without_its_start_a_fact_shows_the_month_it_was_said(memory):
    conn, _ops = memory
    _apply(memory, [{"subject": "user", "predicate": "lives_in", "predicate_type": "functional",
                     "object": "Rotterdam", "confidence": 0.9}])
    assert "[since May 2026] user lives in Rotterdam" in _block(conn)


def test_a_start_in_the_future_or_in_words_is_not_kept(memory):
    conn, _ops = memory
    _apply(memory, [{"subject": "user", "predicate": "lives_in", "predicate_type": "functional",
                     "object": "Rotterdam", "since": "2027-01", "confidence": 0.9},
                    {"subject": "user", "predicate": "works_at", "predicate_type": "functional",
                     "object": "Zain", "since": "last month", "confidence": 0.9}])
    assert [r[0] for r in conn.execute("SELECT json_extract(metadata_json, '$.started') FROM beliefs")] == [None, None]


def test_the_same_fact_again_gains_its_start_and_keeps_its_source(memory):
    """The quick pass reads "ساكن في الدوحة من 2019" as the user's own words
    with no start; the deep pass reads the same value with "since": the
    row keeps the user's source and gains the start -- never over one it
    holds."""
    conn, _ops = memory
    mutate_belief(conn, "user", "lives_in", "الدوحة", predicate_type="functional", extraction_method="user_stated",
                  source_session="chat-1", source_turn=1, now=LATE_ON_THE_31ST, private=True)
    _apply(memory, [{"subject": "user", "predicate": "lives_in", "predicate_type": "functional",
                     "object": "الدوحة", "since": "2019", "confidence": 0.9}], user_text="ساكن في الدوحة من 2019.")
    _apply(memory, [{"subject": "user", "predicate": "lives_in", "predicate_type": "functional",
                     "object": "الدوحة", "since": "2020", "confidence": 0.9}], user_text="ساكن في الدوحة من 2020.")
    rows = conn.execute("SELECT extraction_method, json_extract(metadata_json, '$.started') FROM beliefs "
                        "WHERE valid_until IS NULL").fetchall()
    assert [tuple(r) for r in rows] == [("user_stated", "2019")]


def test_control_the_start_is_lost_when_the_writer_drops_it(memory, monkeypatch):
    """Negative control: the writer handing no start to the store (as before
    this piece) keeps none, for a new fact and for the same fact again."""
    conn, _ops = memory
    monkeypatch.setattr(belief_extractor, "_started_on", lambda *_a: None)
    _apply(memory, [{"subject": "user", "predicate": "lives_in", "predicate_type": "functional",
                     "object": "Rotterdam", "since": "2026-04", "confidence": 0.9}])
    assert "[since Apr 2026]" not in _block(conn)
    assert [r[0] for r in conn.execute("SELECT json_extract(metadata_json, '$.started') FROM beliefs")] == [None]
