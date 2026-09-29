"""A chat's turn notes, through the real web chat route (turn_notes).

Four turns in one chat on the unified-turn harness; the active workspace is
switched between the first two, and the first two carry the IDE chat's
open-file context. The model's inputs are recorded at the provider boundary.

Before 2026-09-29, measured on this route: the fourth turn's model received
sixteen system notes (four "LATEST USER MESSAGE PRIORITY", four task ledgers,
three recall blocks), the second turn's environment block still named the
workspace the user had switched away from, and the IDE context was glued to
the question -- so memory, the chat store and the chat's title kept "The user
has this file open in the IDE: ..." as what the user said.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

IDE_A = "The user has this file open in the IDE:\nFile: alpha.py\nLanguage: python"
IDE_B = "The user has this file open in the IDE:\nFile: beta.py\nLanguage: python"
QUESTIONS = (
    "first question about the alpha repository",
    "second question about the beta repository",
    "third question: what changed in beta?",
    "fourth question: summarize the beta repository",
)


class _Recorder:
    """Answers every call with "ok" and keeps what the model was given."""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    def respond(self, messages: list[Any]) -> Any:
        from kazma_core.llm_provider import LLMResponse

        self.calls.append([dict(m) for m in messages or [] if isinstance(m, dict)])
        return LLMResponse(content="ok", finish_reason="stop", model="harness-model")

    def chat_call(self, question: str) -> list[dict[str, Any]]:
        """The supervisor's call for *question*: its last message is it."""
        for call in self.calls:
            users = [m for m in call if m.get("role") == "user"]
            if users and users[-1].get("content") == question:
                return call
        raise AssertionError(f"no model call answered {question!r}")


def _turn(client: Any, base: str, text: str, context: str = "") -> None:
    body = {"message": text, "session_id": "turn-notes-chat"}
    if context:
        body["context"] = context
    with client.stream("POST", f"{base}/api/chat/stream", json=body, timeout=120) as r:
        assert r.status_code == 200, r.status_code
        for line in r.iter_lines():
            if line.startswith("event: done"):
                break


def _roots(call: list[dict[str, Any]]) -> list[str]:
    return [
        line.split("`")[1]
        for m in call if m.get("role") == "system"
        for line in str(m.get("content") or "").splitlines()
        if "Workspace root:" in line and "`" in line
    ]


def test_each_turn_sees_its_own_notes_once(tmp_path: Path) -> None:
    import httpx

    from kazma_core.turn_notes import note_kind

    alpha, beta = tmp_path / "repo-alpha", tmp_path / "repo-beta"
    alpha.mkdir()
    beta.mkdir()
    rec = _Recorder()
    with unified_turn_server(rec) as h:  # type: ignore[arg-type]
        from kazma_core.stores import get_workspace_store

        ws = get_workspace_store()
        wa = ws.create_workspace("alpha", str(alpha))
        wb = ws.create_workspace("beta", str(beta))
        ws.set_active_workspace(wa["id"])
        with httpx.Client() as client:
            client.get(f"{h.base}/chat")
            _turn(client, h.base, QUESTIONS[0], IDE_A)
            ws.set_active_workspace(wb["id"])
            _turn(client, h.base, QUESTIONS[1], IDE_B)
            _turn(client, h.base, QUESTIONS[2])
            _turn(client, h.base, QUESTIONS[3])

        from kazma_ui.session_manager import get_session_manager

        stored = [
            m.get("content") for m in get_session_manager().get("turn-notes-chat").messages
            if m.get("role") == "user"
        ]

    # The question is what the user typed: in the chat store and in every
    # model call, memory's extraction included.
    assert stored == list(QUESTIONS)
    for call in rec.calls:
        for m in call:
            if m.get("role") == "user":
                assert "has this file open in the IDE" not in str(m.get("content")), m

    first, second, fourth = (rec.chat_call(q) for q in (QUESTIONS[0], QUESTIONS[1], QUESTIONS[3]))

    def ide_files(call: list[dict[str, Any]]) -> list[str]:
        return [
            line.strip() for m in call if note_kind(m) == "ide_context"
            for line in str(m["content"]).splitlines() if line.strip().startswith("File:")
        ]

    # The IDE context reaches the model as this turn's note, and only this turn's.
    assert ide_files(first) == ["File: alpha.py"]
    assert ide_files(second) == ["File: beta.py"]
    assert ide_files(fourth) == []
    # A workspace switch reaches the chat's next turn.
    assert _roots(first) == [str(alpha)]
    assert _roots(second) == [str(beta)]
    assert _roots(fourth) == [str(beta)]
    # One note of each kind, however long the chat.
    kinds = [note_kind(m) for m in fourth if note_kind(m)]
    assert len(kinds) == len(set(kinds)), sorted(kinds)
    assert {"working_memory", "latest_message_priority", "environment"} <= set(kinds)
