"""A change the user stated that waits for their yes is shown beside its fact
in recall (the memory plan's finding 17, 2026-11-22).

Finding 15 puts a bare move ("I moved to Porto last month", "انتقلت إلى بورتو
الشهر الماضي") to the user as a question over the home they stated: a pending
change, "Yes, change it" / "No, keep it". Recall read current facts only, so
until the user answered, the next turn's recall showed the held home and
nothing said a change waited. In the real-use suite run twice after finding
16 (08c21984e), "Where do I live now?" was answered with the old home in 3
of 4 language-runs.

Now an open pending change on a shown fact is printed in the fact's line,
inside the data fence ("changed, not confirmed yet, to: Porto"), and a
recall rule outside the fence says what it means: the user's newest word,
not confirmed yet. Each statement goes through the product's quick pass into
a private memory database; the real ``recall`` and ``format_recall_block``
build the block a turn sends. Today's recall, which reads no change, is the
negative control.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory import changes, recall as recall_mod
from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync
from kazma_core.memory.recall import WAITING_CHANGE, WAITING_ENDED, format_recall_block, recall
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

TENANT = "default"
#: (the home held, the move put to the user as a question, the question asked).
CASES = [
    (("I live in Paris.", "Paris"), ("We moved to Rotterdam last month.", "Rotterdam"), "Where do I live now?"),
    (("ساكن في الكويت.", "الكويت"), ("انتقلنا إلى الرياض الشهر الماضي.", "الرياض"), "وين أسكن الحين؟"),
]


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)


@pytest.fixture
def store(tmp_path):
    primary = sqlite3.connect(tmp_path / "memory_state.db", isolation_level=None)
    primary.row_factory = sqlite3.Row
    ensure_primary_schema(primary)
    ops = sqlite3.connect(tmp_path / "memory_ops.db", isolation_level=None)
    ensure_ops_schema(ops)
    told = {"n": 0}

    def tell(text: str) -> None:
        told["n"] += 1
        extract_and_apply_beliefs_sync(primary, ops, text, "Noted.", session_id="s1", turn=told["n"],
                                       tenant_id=TENANT, now=1_790_000_000.0 + told["n"] * 86400, private=True)

    def block(question: str, tenant: str = TENANT) -> str:
        return format_recall_block(recall(question, conn=primary, tenant_id=tenant, local_only=True))

    yield tell, block, primary, ops
    primary.close()
    ops.close()


def _fact_line(block: str, held: str) -> str:
    lines = [line for line in block.splitlines() if held in line]
    assert lines, block
    return lines[0]


@pytest.mark.parametrize(("held", "moved", "question"), CASES)
def test_a_change_waiting_for_the_users_yes_is_shown_beside_its_fact(store, held, moved, question):
    tell, block, primary, _ops = store
    tell(held[0])
    tell(moved[0])
    assert [c["new_value"] for c in changes.list_changes(primary, tenant_id=TENANT)] == [moved[1]]
    shown = block(question)
    assert _fact_line(shown, held[1]).endswith(f"{WAITING_CHANGE} {moved[1]}")
    rules = shown.split("<kazma:data", 1)[0]
    assert WAITING_CHANGE in rules and moved[1] not in rules  # Kazma's words outside the fence, the value in it
    # The user's last word leads (two of three real-use answers that led with
    # the held home were judged wrong), still asked, never for a tool.
    assert "answer with what the user said last" in rules and "Slack" in rules


@pytest.mark.parametrize(("held", "moved", "question"), CASES)
def test_control_today_s_recall_shows_only_the_held_home(store, monkeypatch, held, moved, question):
    """Recall reading no change: the held home alone, in both languages --
    what the model answered with in the real-use runs."""
    monkeypatch.setattr(recall_mod, "_waiting_changes", lambda _conn, _ids, _tenant: {})
    tell, block, _primary, _ops = store
    tell(held[0])
    tell(moved[0])
    shown = block(question)
    assert held[1] in shown and moved[1] not in shown and WAITING_CHANGE not in shown


@pytest.mark.parametrize(("held", "moved", "question"), CASES)
@pytest.mark.parametrize("answer", ["confirm", "dismiss"])
def test_a_decided_change_is_no_longer_shown_as_waiting(store, held, moved, question, answer):
    """Answered "Yes, change it": the new home is the fact. Answered "No,
    keep it": the held home. Nothing waits either way."""
    tell, block, primary, ops = store
    tell(held[0])
    tell(moved[0])
    pending = changes.list_changes(primary, tenant_id=TENANT)[0]
    if answer == "confirm":
        changes.confirm(primary, pending["id"], tenant_id=TENANT, actor="test", private=True)
    else:
        changes.dismiss(primary, pending["id"], tenant_id=TENANT, actor="test", ops_conn=ops)
    shown = block(question)
    assert (moved[1] if answer == "confirm" else held[1]) in shown
    assert WAITING_CHANGE not in shown


def test_another_tenants_change_is_never_shown(store):
    tell, block, primary, _ops = store
    tell("I live in Paris.")
    fact = primary.execute("SELECT id FROM beliefs WHERE predicate = 'lives_in'").fetchone()[0]
    primary.execute(
        "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, old_belief_id, old_value, "
        "new_value, created_at) VALUES ('mc_other', 'someone_else', ?, 'open', 'user', 'lives_in', ?, 'Paris', "
        "'Oslo', 1790000000)", (changes.PENDING, fact))
    shown = block("Where do I live now?")
    assert "Paris" in shown and "Oslo" not in shown and WAITING_CHANGE not in shown


def test_an_ending_that_waits_says_so():
    from kazma_core.memory.recall import RecallHit, _dated_line

    hit = RecallHit(id="b1", content="user lives in Paris", score=1.0, kind="belief", source="fts5",
                    metadata={"waiting": ""})
    assert _dated_line(hit, "user lives in Paris") == f"user lives in Paris; {WAITING_ENDED}"


def test_a_store_without_the_change_log_still_dates_its_facts(store):
    """A memory database from before the change log: the facts are shown,
    dated, with nothing waiting."""
    tell, block, primary, _ops = store
    tell("I live in Paris.")
    primary.execute("DROP TABLE memory_changes")
    shown = block("Where do I live now?")
    assert "Paris" in shown and "since" in shown and WAITING_CHANGE not in shown
