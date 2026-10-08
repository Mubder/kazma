"""A turn's system notes are replaced every turn, never stacked (turn_notes).

Measured 2026-09-29 through the real web chat route: on a chat's fourth
turn the model received sixteen system notes -- four "LATEST USER MESSAGE
PRIORITY", four task ledgers, three memory-recall blocks -- and the
environment block of the workspace the user had switched away from, because
the transport's fresh block was dropped by a dedupe on the first 80
characters. The route itself is driven in ``tests/e2e/test_turn_notes_route.py``.

A note is recognised by how its producer begins it, so every kind is checked
against its producer here: a producer that changes its opening fails this
file instead of quietly stacking again.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

from kazma_core.turn_notes import TURN_NOTE_KINDS, note_kind, strip_turn_notes

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "kazma-core" / "kazma_core"


# ── Every kind is its producer's note ────────────────────────────────────


def _built_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Each kind's note, built by the code that writes it."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.agent.intent.policy import _plan_note_for
    from kazma_core.agent.approval_facts import approval_scope_note
    from kazma_core.agent.intent.types import ActKind, EntitySet
    from kazma_core.agent.long_task import consume_continue_context, store_continue_context
    from kazma_core.agent.research_policy import deep_research_route_hint
    from kazma_core.agent.task_ledger import TaskLedger, format_ledger_block
    from kazma_core.agent.turn_input import (
        format_working_memory_anchor,
        latest_turn_priority_note,
        proposal_nudge,
    )
    from kazma_core.ide.env_context import _build_env_context_sync
    from kazma_core.language_lock import language_lock_message
    from kazma_core.memory.procedural import format_procedural_hints
    from kazma_core.memory.recall import RecallHit, RecallResult, format_recall_block
    from kazma_core.memory.transcript_recall import format_transcript_block
    from kazma_core.safety.prompt_fence import format_untrusted_block

    store_continue_context("turn-notes-t1", summary="Verified 6 of 8 domains.")
    notes = {
        "approval_scope": approval_scope_note(
            [{"id": "call_previous", "name": "file_write"}], [],
            approved=False, approved_ids=None, mode="human",
        )["content"],
        "working_memory": format_working_memory_anchor(active_goal="Ship v0.4"),
        "latest_message_priority": latest_turn_priority_note(),
        "task_ledger": format_ledger_block(TaskLedger(thread_id="t", goal="Ship v0.4")),
        "memory_recall": format_recall_block(RecallResult(
            beliefs=[RecallHit(id="b1", content="tasks-api release day Friday", score=0.9,
                               kind="belief", source="belief_fts")],
            episodes=[],
        )),
        "past_chats": format_transcript_block([{
            "title": "Release planning", "created_at": "2026-09-20",
            "matched": ["release"], "snippet": "Releases go out on Fridays.",
        }]),
        "procedural_hints": format_procedural_hints([{
            "name": "release", "confidence": 0.8, "total_trials": 3,
            "description": "tag and push", "steps": ["git tag"],
        }]),
        "knowledge": format_untrusted_block("chunk", source="knowledge"),
        "self_improvement": format_untrusted_block("delta", source="self_improvement"),
        "ide_context": format_untrusted_block("File: api.py", source="ide_context"),
        "environment": _build_env_context_sync(),
        "language_lock": language_lock_message("هل يمكنك مراجعة ملاحظات الإصدار من فضلك؟"),
        "continue_context": consume_continue_context("turn-notes-t1", user_text="Proceed") or "",
        "intent_plan": _plan_note_for(ActKind.RESEARCH_DEEP, {}, EntitySet()),
        "outbound_drafts": proposal_nudge("Draft five tweets about the release and post them") or "",
        "deep_research_route": deep_research_route_hint(
            "Do deep research on vector databases and write a comprehensive report"
        ) or "",
    }
    return notes


#: Notes written inline, where they are used: the module that writes each.
_INLINE = {
    "ui_workbench": CORE / "agent" / "graph_supervisor.py",
    "continuity": CORE / "agent" / "graph_supervisor.py",
    "budget_check": CORE / "agent" / "graph_supervisor.py",
    "active_skill": REPO / "kazma-gateway" / "kazma_gateway" / "agent_handler" / "graph.py",
    "unrestricted_notice": CORE / "agent" / "long_task.py",
}


def _literal_openings(path: Path) -> list[str]:
    """Every string literal in *path*, as it begins (an f-string by its
    first literal part)."""
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
            out.append(str(node.values[0].value))
    return out


def _unrecognised(notes: dict[str, str]) -> list[str]:
    return [f"{kind}: {text[:60]!r}" for kind, text in notes.items() if note_kind(
        {"role": "system", "content": text}) != kind]


def _product_openings() -> list[str]:
    out: list[str] = []
    for pkg in ("kazma-core/kazma_core", "kazma-gateway/kazma_gateway", "kazma-ui/kazma_ui"):
        for path in (REPO / pkg).rglob("*.py"):
            if "tests" not in path.parts:
                out.extend(_literal_openings(path))
    return out


def _dead_prefixes(built: dict[str, str], openings: list[str]) -> list[str]:
    """Registered openings that no note begins with: nothing writes them."""
    starts = list(built.values()) + openings
    return [p for prefixes in TURN_NOTE_KINDS.values() for p in prefixes
            if not any(s.lstrip().startswith(p) for s in starts)]


def test_every_kind_is_how_its_producer_begins_its_note(tmp_path, monkeypatch) -> None:
    built = _built_notes(tmp_path, monkeypatch)
    assert all(built.values()), [k for k, v in built.items() if not v]
    assert not _unrecognised(built)
    for kind, path in _INLINE.items():
        openings = _literal_openings(path)
        assert any(o.startswith(TURN_NOTE_KINDS[kind]) for o in openings), (
            f"{path.name} no longer begins a note with {TURN_NOTE_KINDS[kind]}: "
            "update turn_notes.TURN_NOTE_KINDS, or the note stacks again"
        )
    assert set(built) | set(_INLINE) == set(TURN_NOTE_KINDS), (
        "a kind with no producer checked here (or a producer with no kind)"
    )
    assert not _dead_prefixes(built, _product_openings())


def test_a_prefix_nothing_writes_is_caught(tmp_path, monkeypatch) -> None:
    """Negative control for the dead-prefix check."""
    built = _built_notes(tmp_path, monkeypatch)
    monkeypatch.setitem(TURN_NOTE_KINDS, "made_up", ("KAZMA NOTE NOBODY WRITES:",))
    assert _dead_prefixes(built, _product_openings()) == ["KAZMA NOTE NOBODY WRITES:"]


def test_a_producer_that_changes_its_opening_is_caught(tmp_path, monkeypatch) -> None:
    """Negative control: the check above fails on a note it cannot recognise."""
    built = _built_notes(tmp_path, monkeypatch)
    built["environment"] = "## Kazma environment\n- Workspace root: `/w`"
    assert _unrecognised(built) == ["environment: '## Kazma environment\\n- Workspace root: `/w`'"]


def test_what_is_not_a_turn_note_is_never_recognised() -> None:
    for content in (
        "You are Kazma, a helpful assistant.",
        "[CONTEXT SUMMARY] 4 assistant turns including 8 enumerated draft items",
        "Turn aborted by the user.",
        "Some text mentioning LANGUAGE LOCK in the middle.",
    ):
        assert note_kind({"role": "system", "content": content}) is None, content
    assert note_kind({"role": "user", "content": "LANGUAGE LOCK please"}) is None


# ── A new turn starts without the last turn's notes ──────────────────────


class _Snapshot:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.values = {"messages": messages}


class _Graph:
    """A graph whose checkpoint holds *messages*."""

    checkpointer = object()

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages

    async def aget_state(self, _config: Any) -> _Snapshot:
        return _Snapshot([dict(m) for m in self._messages])


def _sys(content: str) -> dict[str, Any]:
    return {"role": "system", "content": content}


ENV_A = "## You are Kazma (this process)\n- x\n## Active Workspace\n- **Workspace root:** `/repos/alpha`"
ENV_B = "## You are Kazma (this process)\n- x\n## Active Workspace\n- **Workspace root:** `/repos/beta`"
BASE = "You are Kazma, a personal assistant."
SUMMARY = "[CONTEXT SUMMARY] Dropped 4 turns: the draft list and its approval."


def _history() -> list[dict[str, Any]]:
    from kazma_core.safety.prompt_fence import format_untrusted_block

    return [
        _sys(BASE),
        _sys("LATEST USER MESSAGE PRIORITY: turn two"),
        _sys("[KAZMA:TASK-LEDGER]\nTask: turn two"),
        _sys("LATEST USER MESSAGE PRIORITY: turn one"),
        _sys("[KAZMA:TASK-LEDGER]\nTask: turn one"),
        _sys(ENV_A),
        _sys(format_untrusted_block("File: alpha.py", source="ide_context")),
        _sys(format_untrusted_block("old chunk", source="knowledge")),
        _sys("LANGUAGE LOCK (this turn ONLY): ENGLISH"),
        _sys("⚠️ **Unrestricted expired** (idle timeout). Begin your reply with this notice verbatim."),
        _sys(SUMMARY),
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
    ]


def _build(system_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from kazma_core.agent.turn_input import build_turn_messages

    return asyncio.run(build_turn_messages(
        _Graph(_history()), {"configurable": {"thread_id": "t"}},
        user_text="third question", system_messages=system_messages,
    ))


def _this_turn() -> list[dict[str, Any]]:
    from kazma_core.safety.prompt_fence import format_untrusted_block

    return [
        _sys(BASE),
        _sys(ENV_B),
        _sys(format_untrusted_block("File: beta.py", source="ide_context")),
        _sys("LANGUAGE LOCK (this turn ONLY): ARABIC"),
    ]


def test_a_new_turn_starts_without_the_last_turns_notes() -> None:
    out = _build(_this_turn())
    system = [m["content"] for m in out if m["role"] == "system"]
    kinds = [note_kind(m) for m in out if m["role"] == "system"]
    assert [k for k in kinds if k] == ["environment", "ide_context", "language_lock"]
    assert ENV_B in system and ENV_A not in system
    assert any("beta.py" in s for s in system) and not any("alpha.py" in s for s in system)
    assert not any("ENGLISH" in s for s in system)
    assert not any("Unrestricted expired" in s for s in system)
    # What is not a turn note stays: the base prompt (once) and the summary.
    assert system.count(BASE) == 1 and SUMMARY in system
    assert out[0]["content"] == BASE
    # The conversation is untouched and the question is last.
    assert [(m["role"], m["content"]) for m in out if m["role"] != "system"] == [
        ("user", "first question"), ("assistant", "first answer"),
        ("user", "second question"), ("assistant", "second answer"),
        ("user", "third question"),
    ]


def test_the_old_rebuild_keeps_the_stale_workspace(monkeypatch) -> None:
    """Negative control: without the strip, turn two's workspace is lost."""
    import kazma_core.agent.turn_input as turn_input

    monkeypatch.setattr(turn_input, "strip_turn_notes", lambda messages: list(messages))
    system = [m["content"] for m in _build(_this_turn()) if m["role"] == "system"]
    assert ENV_A in system and ENV_B in system
    assert sum(1 for s in system if s.startswith("LATEST USER MESSAGE PRIORITY")) == 2


def test_one_note_of_a_kind_per_turn() -> None:
    notes = [_sys(ENV_B), _sys(ENV_A)]
    system = [m["content"] for m in _build(notes) if m["role"] == "system"]
    assert ENV_B in system and ENV_A not in system


def test_strip_keeps_everything_that_is_not_a_turn_note() -> None:
    history = _history()
    kept = strip_turn_notes(history)
    assert not any(note_kind(m) for m in kept)
    assert [m for m in history if note_kind(m) is None] == kept


@pytest.mark.parametrize("gateway", [False, True])
@pytest.mark.parametrize("approved", [False, True])
def test_new_request_drops_previous_approval_instruction_but_keeps_tool_evidence(
    gateway: bool, approved: bool,
) -> None:
    from kazma_core.agent.approval_facts import approval_scope_note
    from kazma_core.agent.turn_input import build_turn_messages
    from kazma_gateway.agent_handler.graph import _rebuild_turn_messages

    call = {"id": "call_previous", "name": "file_write"}
    evidence = {
        "role": "tool", "tool_call_id": call["id"],
        "content": "File written" if approved else "Denied by user; not executed",
    }
    assistant_call = {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": call["id"], "type": "function", "function": {
            "name": call["name"], "arguments": '{"path":"old.txt","content":"old"}',
        }}],
    }
    note = approval_scope_note(
        [call], [{"tool_call_id": call["id"], "is_error": False}],
        approved=approved, approved_ids=None, mode="human",
    )
    history = [_sys(BASE), {"role": "user", "content": "Write old.txt"},
               assistant_call, evidence, note, {"role": "assistant", "content": "Previous turn finished"}]
    graph = _Graph(history)
    config = {"configurable": {"thread_id": "approval-turn-scope"}}
    new_text = "New independent request: write new.txt and ask for its own approval"
    if gateway:
        out = asyncio.run(_rebuild_turn_messages(
            graph, config, [{"role": "user", "content": new_text}], new_text,
        ))
    else:
        out = asyncio.run(build_turn_messages(graph, config, user_text=new_text))
    assert note not in out
    assert not any(note_kind(message) == "approval_scope" for message in out)
    assert assistant_call in out and evidence in out
    assert out[-1] == {"role": "user", "content": new_text}
    # Loading a new turn must not rewrite the paused/completed checkpoint.
    assert note in graph._messages


def test_unregistered_approval_scope_replays_the_previous_decision(monkeypatch) -> None:
    from kazma_core.agent.approval_facts import approval_scope_note
    from kazma_core.agent.turn_input import build_turn_messages

    note = approval_scope_note(
        [{"id": "previous", "name": "file_write"}], [],
        approved=False, approved_ids=None, mode="human",
    )
    monkeypatch.delitem(TURN_NOTE_KINDS, "approval_scope")
    out = asyncio.run(build_turn_messages(
        _Graph([_sys(BASE), note]), {"configurable": {"thread_id": "old"}},
        user_text="New independent request",
    ))
    assert note in out  # The live defect before this kind was registered.


# ── The prompt cache keeps a turn's notes out of its stable prefix ──────


def test_turn_notes_are_dynamic_the_environment_stays_stable() -> None:
    from kazma_core.prompt_cache import pack_system_messages

    packed = pack_system_messages([
        _sys(BASE), _sys(ENV_B), _sys("[KAZMA:TASK-LEDGER]\nTask: now"),
        _sys("LATEST USER MESSAGE PRIORITY: now"),
        {"role": "user", "content": "hi"},
    ])
    stable, dynamic = packed[0]["content"], packed[1]["content"]
    assert BASE in stable and ENV_B in stable
    assert "TASK-LEDGER" in dynamic and "LATEST USER MESSAGE PRIORITY" in dynamic
    assert "TASK-LEDGER" not in stable


# ── The gateway rebuild keeps the turn's note and its message's parts ────


def _gateway_rebuild(turn: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from kazma_gateway.agent_handler.graph import _rebuild_turn_messages

    return asyncio.run(_rebuild_turn_messages(
        _Graph(_history()), {"configurable": {"thread_id": "t"}}, turn, "fallback"))


def test_the_continue_context_reaches_the_model_on_the_gateway() -> None:
    cont = "[LONG-TASK CONTINUE CONTEXT — prior turn hit a budget limit]\nVerified 6 of 8.\n[/LONG-TASK CONTINUE CONTEXT]"
    out = _gateway_rebuild([_sys(cont), {"role": "user", "content": "Proceed"}])
    assert [m["content"] for m in out if note_kind(m) == "continue_context"] == [cont]
    assert out[-1] == {"role": "user", "content": "Proceed"}
    assert not any(note_kind(m) == "task_ledger" for m in out)


def test_a_photo_reaches_the_model_as_parts_on_the_gateway() -> None:
    parts = [
        {"type": "text", "text": "What is on this receipt?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ"}},
    ]
    out = _gateway_rebuild([{"role": "user", "content": parts}])
    assert out[-1]["role"] == "user" and out[-1]["content"] == parts
    assert not any(isinstance(m.get("content"), str) and "base64" in m["content"] for m in out)
