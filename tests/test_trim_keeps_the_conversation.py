"""The context trim keeps what the user asked before, not only the latest message.

Live 2026-10-18 (Telegram, a day-long chat building a PDF archive of the X
posts): once the chat was over its 24,000-token budget, the trim kept the
latest message and its last tool rounds and dropped every earlier turn.
"ارسل لي الملف هني" (send me the file here) then reached the model with
nothing before it: it listed the workspace, read the posts again and asked
which file was meant. The turns that made the PDF and named it were gone.
"""

from __future__ import annotations

from kazma_core.agent import turn_input
from kazma_core.agent.turn_input import trim_messages_deterministic

PDF = "kazma-data/documents/X-archive.pdf"


def _chat() -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "You are Kazma."}]
    msgs += [
        {"role": "user", "content": "اعمل لي ملف PDF فيه كل تغريدات الحساب"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a1", "name": "x_status", "args": {}}]},
        {"role": "tool", "tool_call_id": "a1", "content": "post " * 3000},
        {"role": "assistant", "content": f"PDF ready: {PDF} (4 pages)."},
        {"role": "user", "content": "جرب من جديد واقرا كل التغريدات"},
    ]
    for n in range(12):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"b{n}", "name": "x_status", "args": {}}]})
        msgs.append({"role": "tool", "tool_call_id": f"b{n}", "content": "page " * 800})
    msgs += [
        {"role": "assistant", "content": "Read all 67 posts; the archive covers every one."},
        {"role": "user", "content": "ارسل لي الملف هني"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "file_list", "args": {}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "README.md\nMETRICS.md"},
    ]
    return msgs


def _contents(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages)


def test_the_earlier_requests_and_replies_survive_the_trim():
    trimmed = trim_messages_deterministic(_chat(), max_tokens=3000, keep_last_tool_rounds=4)
    text = _contents(trimmed)
    assert "ارسل لي الملف هني" in text
    assert "اعمل لي ملف PDF فيه كل تغريدات الحساب" in text, "the goal the file came from"
    assert PDF in text, "the reply that named the file"
    assert "page page page page" not in text, "earlier tool output stays out"
    # In order: the earlier turns, then the latest question.
    users = [m["content"] for m in trimmed if m.get("role") == "user"]
    assert users[-1] == "ارسل لي الملف هني"
    # No orphan tool messages: every tool row follows its assistant call.
    for i, m in enumerate(trimmed):
        if m.get("role") == "tool":
            assert trimmed[i - 1].get("tool_calls") or trimmed[i - 1].get("role") == "tool"


def test_negative_control_the_old_trim_dropped_them(monkeypatch):
    monkeypatch.setattr(turn_input, "_with_conversation_skeleton", lambda trimmed, *a, **k: trimmed)
    text = _contents(trim_messages_deterministic(_chat(), max_tokens=3000, keep_last_tool_rounds=4))
    assert "اعمل لي ملف PDF" not in text and PDF not in text


def test_the_skeleton_only_spends_budget_that_is_left(monkeypatch):
    """Over budget without it (the trim's own last resort), it adds nothing;
    under budget, it stays under."""
    from kazma_core.summarizer import estimate_tokens

    chat = _chat()
    with_it = {b: trim_messages_deterministic(chat, max_tokens=b, keep_last_tool_rounds=4)
               for b in (900, 1500, 3000, 6000)}
    monkeypatch.setattr(turn_input, "_with_conversation_skeleton", lambda trimmed, *a, **k: trimmed)
    for budget, trimmed in with_it.items():
        bare = trim_messages_deterministic(chat, max_tokens=budget, keep_last_tool_rounds=4)
        if estimate_tokens(bare) >= budget:
            assert trimmed == bare
        else:
            assert estimate_tokens(trimmed) <= budget
