"""Same-session continuity: session history can outrank thin checkpoints."""

from __future__ import annotations

import pytest

from kazma_core.agent.turn_input import (
    build_turn_messages,
    contentful_turn_count,
    extract_store_focus_query,
    is_memory_store_intent,
    is_short_continuation,
    normalize_history_messages,
)


def test_is_short_continuation():
    assert is_short_continuation("Proceed") is True
    assert is_short_continuation("try now") is True
    assert is_short_continuation("continue") is True
    assert is_short_continuation("clean up memory garbage please") is False


def test_is_memory_store_intent_shipx_paste():
    from kazma_core.agent.turn_input import (
        is_memory_graph_cleanup_intent,
        is_multi_part_memory_work,
    )

    msg = (
        "Now read this and add it to the ShipX memory:\n\n"
        "Overview of ShipX\n"
        "ShipX is an end-to-end Kuwaiti commerce platform...\n"
        + ("x" * 700)
    )
    assert is_memory_store_intent(msg) is True
    focus = extract_store_focus_query(msg)
    assert "ShipX" in focus or "shipx" in focus.lower()
    # Not a pure Q&A about reminders
    assert is_memory_store_intent("When is my ZCode reset?") is False
    cleanup = (
        "Entities is still not so perfect, it is so messy, the true concept "
        "I want kazma parts aligned Mubder → Kazma → related entities like ShipX"
    )
    assert is_memory_graph_cleanup_intent(cleanup) is True
    assert is_memory_store_intent(cleanup) is False


def test_rewrite_mention_memory_is_not_store_intent():
    """Regression (2026-08-26): 'rewrite the text and mention the memory' +
    a long paste used to classify as MEMORY STORE TASK — the supervisor then
    told the model to mutate stored beliefs instead of rewriting the text.
    A bare 'memory' mention in a paste head is NOT a storage target.
    """
    rewrite = (
        "Please rewrite the text below and mention the memory system in "
        "the conclusion:\n\n" + ("y" * 700)
    )
    assert is_memory_store_intent(rewrite) is False
    # Short form too
    assert is_memory_store_intent("rewrite the text and mention the memory") is False
    # Explicit store phrasing still classifies (verb → memory target)
    keep = "Here is the doc, save it into memory:\n\n" + ("z" * 700)
    assert is_memory_store_intent(keep) is True
    # "Memory:" paste header still classifies
    header = "Memory:\nKazma is a multi-platform agent framework...\n" + ("w" * 700)
    assert is_memory_store_intent(header) is True


def test_multi_part_pat_github_not_exclusive_graph_cleanup():
    """Regression: PAT + read repos + store under Mubder→kazma + compare.

    Must NOT classify as pure graph-cleanup (that forced 100-step merge loops
    and GraphRecursionError on Telegram).
    """
    from kazma_core.agent.turn_input import (
        is_memory_graph_cleanup_intent,
        is_memory_store_intent,
        is_multi_part_memory_work,
        latest_turn_priority_note,
    )

    ar = (
        "زين انا حافظ الـ PAT توكن للجيت هب ابيك تقرأ مشاريعي اللي هم "
        "ShipX - KCA - Kazma وتحفظهم او تحفض الجزء الجديد بالذاكره ويكون "
        "مرتبط Graph بهالطريقه: Mubder -> kazma -> new info. او بدال كاظمه "
        "الـ KCA OR SHIPX بحيث الجراف ما يكون فيه Junk وبعدها تقارن المشاريع "
        "وتقولي شنو اقدر استفيد من الايميل هذا؟"
    )
    assert is_multi_part_memory_work(ar) is True
    assert is_memory_graph_cleanup_intent(ar) is False
    assert is_memory_store_intent(ar) is True
    note = latest_turn_priority_note(multi_part=True, store_intent=True, focus="ShipX KCA Kazma")
    assert "MULTI-PART" in note
    assert "CLEANUP TASK" not in note or "only graph cleanup" in note.lower()

    # Pure hierarchy+messy still cleanup
    pure = (
        "Entities is still messy. Clean junk true/false and align "
        "Mubder -> kazma. Delete duplicate entities."
    )
    assert is_memory_graph_cleanup_intent(pure) is True
    assert is_multi_part_memory_work(pure) is False


def test_contentful_ignores_empty_pending():
    msgs = [
        {"role": "user", "content": "clean memory junk entities"},
        {"role": "assistant", "content": "", "pending": True},
        {"role": "user", "content": "Proceed"},
    ]
    assert contentful_turn_count(normalize_history_messages(msgs)) == 2


@pytest.mark.asyncio
async def test_session_richer_than_checkpoint_wins():
    class _Graph:
        checkpointer = object()

        async def aget_state(self, config):
            class Snap:
                values = {
                    "messages": [
                        {"role": "system", "content": "sys"},
                        {"role": "user", "content": "Proceed"},
                    ]
                }

            return Snap()

    session = [
        {"role": "user", "content": "But there is so garbage in the memory: ShipX true false"},
        {
            "role": "assistant",
            "content": "I will use memory_list_entities to clean junk nodes.",
        },
        {"role": "user", "content": "Try now"},
        {"role": "assistant", "content": "⚠️ empty"},
        {"role": "user", "content": "Proceed"},
    ]
    out = await build_turn_messages(
        _Graph(),
        {"configurable": {"thread_id": "t1"}},
        user_text="Proceed",
        fallback_history=session,
    )
    texts = [m.get("content") for m in out if m.get("role") == "user"]
    assert any("garbage" in str(t) for t in texts)
    assert texts[-1] == "Proceed"


@pytest.mark.asyncio
async def test_empty_checkpoint_uses_session():
    class _Graph:
        checkpointer = object()

        async def aget_state(self, config):
            class Snap:
                values = {"messages": []}

            return Snap()

    session = [
        {"role": "user", "content": "fix memory after restart"},
        {"role": "assistant", "content": "Working on it."},
    ]
    out = await build_turn_messages(
        _Graph(),
        {"configurable": {"thread_id": "t1"}},
        user_text="continue",
        fallback_history=session,
    )
    assert any(
        "memory" in str(m.get("content") or "").lower() and m.get("role") == "user"
        for m in out
    )
    assert out[-1]["content"] == "continue"
