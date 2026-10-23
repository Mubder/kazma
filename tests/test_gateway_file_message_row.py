"""A chat-app message with a file keeps its row in the web transcript.

Live 2026-10-23 (Telegram): a PDF sent to the chat carries its parsed excerpt
in a ``<kazma:data … untrusted>`` fence, and the transcript projection dropped
any user message holding a fence as an inject. With no user row after it, the
previous answer was the trailing assistant row; the next turn's approval card
made it look unfinished (``resolve_reply_turn``, "via=live gate"), and that
turn's answer was written over it -- the 17:16 answer vanished from the chat
in the web UI.
"""

from __future__ import annotations

from types import SimpleNamespace

from kazma_core.safety.prompt_fence import format_untrusted_block

from kazma_gateway.agent_handler import graph as gateway_graph


def _file_message() -> dict:
    fenced = format_untrusted_block("المؤسسة العامة للتأمينات الاجتماعية …", source="document_attachment")
    return {
        "role": "user",
        "content": (
            "[file]\n[Attached: cert.pdf (application/pdf) — parsed contents:\n"
            f"{fenced}]\n[Full document at: kazma-data/attachments/cert.pdf — use read_document]"
        ),
    }


def _old_convert(messages):
    """The projection as it was: a fence anywhere drops the message."""
    out = []
    for m in messages:
        text = str(m.get("content") or "").strip()
        if m.get("role") in ("system", "tool") or not text:
            continue
        if "<kazma:data" in text and "untrusted" in text:
            continue
        out.append({"role": m["role"], "content": text})
    return out


def test_a_file_message_keeps_its_row_without_the_fenced_excerpt():
    rows = gateway_graph._convert_messages_to_dicts([_file_message()])
    assert len(rows) == 1 and rows[0]["role"] == "user"
    assert "cert.pdf" in rows[0]["content"]
    assert "<kazma:data" not in rows[0]["content"]
    assert "التأمينات" not in rows[0]["content"]  # the excerpt stays out
    assert _old_convert([_file_message()]) == []  # negative control


def test_an_inject_that_is_only_a_fence_is_still_left_out():
    inject = {"role": "user", "content": format_untrusted_block("lesson", source="self_improvement")}
    assert gateway_graph._convert_messages_to_dicts([inject]) == []


def _resolve_after(transcript, monkeypatch) -> str:
    from kazma_ui import reply_sink

    reply_sink.reset_reply_turns()
    monkeypatch.setattr(reply_sink, "_store", lambda: SimpleNamespace(get=lambda _sid: SimpleNamespace(messages=transcript)))
    # The NEW turn's approval card is the live gate on the thread.
    monkeypatch.setattr(reply_sink, "_thread_has_live_gate", lambda _tid: True)
    try:
        return reply_sink.resolve_reply_turn("gw-telegram-x", "gw-telegram-x")
    finally:
        reply_sink.reset_reply_turns()


def test_the_next_turns_card_no_longer_takes_over_the_previous_answer(monkeypatch):
    earlier = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "the 17:16 answer", "turn_id": "bc7ca315d278"},
    ]
    projected = gateway_graph._convert_messages_to_dicts([_file_message()])
    assert _resolve_after(earlier + projected, monkeypatch) != "bc7ca315d278"
    # Negative control: the old projection leaves no user row, and the
    # previous answer's row is adopted.
    old = _old_convert([_file_message()])
    assert _resolve_after(earlier + old, monkeypatch) == "bc7ca315d278"
