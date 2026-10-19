"""After an approval, the chat gets what the resumed step said -- nothing older.

Live 2026-10-20 (Telegram): a turn that ended "⚠️ generate_pdf: ... must not
be repeated" was followed by a new request whose steps only called tools and
paused on approval cards. After each approval the gateway looked for "the
latest assistant text" in the whole history, found the last turn's failure,
and sent it again -- three times in a turn where generate_pdf never ran.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kazma_gateway.agent_handler import hitl

OLD_FAILURE = "⚠️ generate_pdf: The tool reported a failure after invocation."


def _history(*, after_resume: list[dict]) -> list[dict]:
    return [
        {"role": "user", "content": "Make a PDF"},
        {"role": "assistant", "content": OLD_FAILURE},
        {"role": "user", "content": "Make it again"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "file_write"}]},
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
        *after_resume,
    ]


def test_a_step_that_said_nothing_sends_nothing_old():
    messages = _history(after_resume=[
        {"role": "assistant", "content": "", "tool_calls": [{"id": "b", "name": "convert_document"}]}])
    assert hitl._new_assistant_text(messages, []) == ""


def test_negative_control_the_old_search_found_the_last_turns_failure():
    messages = _history(after_resume=[])
    old = next(m["content"] for m in reversed(messages) if m.get("role") == "assistant" and m.get("content"))
    assert old == OLD_FAILURE


def test_narration_said_before_the_card_is_not_sent_again():
    before = _history(after_resume=[]) + [{"role": "assistant", "content": "Writing the file now.",
                                           "tool_calls": [{"id": "c", "name": "file_write"}]}]
    after = before + [{"role": "tool", "tool_call_id": "c", "content": "ok"},
                      {"role": "assistant", "content": "Done: the PDF is ready."}]
    said = hitl._turn_assistant_texts(before)
    assert hitl._new_assistant_text(before, said) == ""
    assert hitl._new_assistant_text(after, said) == "Done: the PDF is ready."


class _Graph:
    def __init__(self, before, after):
        self._before, self._after = before, after

    async def aget_state(self, config):
        return SimpleNamespace(values={"messages": self._before}, next=(), tasks=())


@pytest.mark.anyio
async def test_the_delivery_sends_only_what_the_resume_added(monkeypatch):
    before = _history(after_resume=[])
    after = _history(after_resume=[{"role": "assistant", "content": "", "tool_calls": [{"id": "b"}]}])

    async def _invoke(graph, cmd, config, *, thread_id):
        return {"messages": after}

    async def _no_interrupt(graph, config):
        return None

    monkeypatch.setattr("kazma_ui.turn_runtime.invoke_turn", _invoke)
    monkeypatch.setattr(hitl, "_check_graph_interrupt", _no_interrupt)
    state, chained = await hitl._run_resume(_Graph(before, after), "cmd", {}, "t1")

    sent = []

    class _Manager:
        async def send(self, message):
            sent.append(message.text)

    await hitl._deliver_resume(_Manager(), platform="telegram", target_id="telegram:1", ctx={},
                               thread_id="t1", result_state=state, chained=chained,
                               fallback_text="✅ Approved — continuing.")
    assert sent == ["✅ Approved — continuing."], sent
