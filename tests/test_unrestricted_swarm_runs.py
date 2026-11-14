"""A long swarm task from an ``/unrestricted`` chat runs and comes back.

Live 2026-11-14 (Telegram, under ``/unrestricted``): a data-collection prompt
went to a swarm worker. The chat's turn waited on it with ``check_swarm_task``
(90 s a call, "call again to keep waiting"); the third wait tripped the
stagnation guard and the turn ended "partial". The worker ran out of its 40
rounds reading every page 2,000 characters at a time, and its forced report
was cut off at 16,384 tokens by hidden reasoning. Nothing sent the result to
the chat. The prompt's two "Scope" lines were still ``<FILL IN, ...>``, and
the model ran the task on the template's examples.

Each test shows the old behaviour failing first where it can.
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.agent import tool_loop_breaker as tlb
from kazma_core.agent.unrestricted import is_unrestricted, unrestricted_scope

# ── The wait is progress, not a loop ─────────────────────────────────────

def _round(call_id: str, name: str, content: str) -> tuple[list[dict], list[dict]]:
    call = {"id": call_id, "name": name, "arguments": {"task_id": "task-1", "wait_seconds": 90}}
    result = {"tool_call_id": call_id, "name": name, "content": content, "outcome": "ok"}
    return [call], [result]


def _three_rounds(content: str, name: str = "check_swarm_task", *, signatures=tlb.stagnation_signatures):
    sigs: list[str] = []
    for i in range(3):
        calls, results = _round(f"c{i}", name, content)
        sigs = signatures(sigs, calls, results)
    return tlb.detect_stagnation(sigs)


def _old_signatures(previous, calls, results, keep=24):
    outcome = {str(r.get("tool_call_id")): str(r.get("outcome", "")) for r in results}
    sigs = list(previous)
    for call in calls:
        if outcome.get(str(call.get("id")), "") in ("policy", "user_deny", "empty", "refused"):
            continue
        sigs.append(tlb.tool_signature(call["name"], call.get("arguments") or {}))
    return sigs[-keep:]


STILL = f"{tlb.STILL_RUNNING_PREFIX} task-1 is still running (elapsed: 275.0s, timeout: 900s)."


def test_three_real_waits_are_not_a_loop():
    assert _three_rounds(STILL) is None


def test_negative_control_the_old_window_ended_the_turn_on_the_third_wait():
    assert _three_rounds(STILL, signatures=_old_signatures) is not None


def test_a_repeated_call_that_is_not_a_wait_still_trips():
    assert _three_rounds("Task task-1 completed.") is not None


def test_another_tool_cannot_pass_for_a_wait():
    # A fetched page that happens to begin with the marker is not a wait.
    assert _three_rounds(STILL, name="read_url") is not None


def test_the_tool_worker_uses_the_window_function():
    import inspect

    from kazma_core.agent import graph_tool_worker

    source = inspect.getsource(graph_tool_worker)
    assert "stagnation_signatures(" in source
    assert 'sigs.append(tool_signature(' not in source


def _engine_with_running_task(monkeypatch, tmp_path, metadata=None):
    import kazma_core.swarm.engine as engine_mod
    from kazma_core.agent.tool_builtins.system import register_system_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.swarm.config import SwarmConfig
    from kazma_core.swarm.engine import SwarmEngine
    from kazma_core.swarm.task import SwarmTask, TaskStatus
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore(str(tmp_path / "swarm_tasks.db"))
    monkeypatch.setattr(engine_mod, "_swarm_engine",
                        SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store))
    task = SwarmTask(prompt="collect shops", workers=["researcher"], metadata=metadata or {})
    task.status = TaskStatus.RUNNING
    store.persist_task(task)
    reg = LocalToolRegistry(include_builtins=False)
    register_system_tools(reg)
    return store, task, reg


def test_check_swarm_task_marks_its_still_running_answer(monkeypatch, tmp_path):
    store, task, reg = _engine_with_running_task(monkeypatch, tmp_path)
    try:
        out = asyncio.run(reg.execute("check_swarm_task", {"task_id": task.id, "wait_seconds": 0}))
        content = str(out.get("content", ""))
        assert tlb._is_progress_wait(content, "check_swarm_task"), content
        assert "end your turn" in content
    finally:
        store.close()


# ── /unrestricted reaches the worker ─────────────────────────────────────

def test_dispatch_time_limit_under_unrestricted():
    from kazma_core.agent.tool_builtins.system import _dispatch_timeout

    assert _dispatch_timeout(900) == 900
    assert _dispatch_timeout(99999) == 1800  # the ordinary ceiling
    assert _dispatch_timeout(900, unrestricted=True) == 3600
    assert _dispatch_timeout(99999, unrestricted=True) == 6 * 3600


def test_worker_budget_follows_the_bound_answer(monkeypatch):
    from kazma_core.swarm import worker

    monkeypatch.setattr("kazma_core.agent.long_task._baseline_iterations", lambda: 15)
    assert worker.worker_iteration_budget() == 15
    with unrestricted_scope(True):
        assert is_unrestricted()
        assert worker.worker_iteration_budget() == worker.UNRESTRICTED_WORKER_ROUNDS
    with unrestricted_scope(False):
        assert worker.worker_iteration_budget() == 15
    assert worker.worker_iteration_budget() == 15


def test_a_thread_is_unrestricted_only_when_unified_and_active(monkeypatch):
    from kazma_core.agent import unrestricted

    answers = {
        "t-on": {"active": True, "unified": True},
        "t-long": {"active": True, "unified": False},
        "t-paused": {"active": False, "paused": True, "unified": True},
    }
    import kazma_core.agent.long_task as long_task

    monkeypatch.setattr(long_task, "long_task_status",
                        lambda thread_id: answers.get(thread_id, {"active": False}))
    assert unrestricted._thread_is_unrestricted("t-on")
    assert not unrestricted._thread_is_unrestricted("t-long")
    assert not unrestricted._thread_is_unrestricted("t-paused")
    assert not unrestricted._thread_is_unrestricted(None)


def test_the_swarm_binds_the_tasks_answer_around_the_worker():
    import inspect

    from kazma_core.swarm import worker_dispatch

    source = inspect.getsource(worker_dispatch)
    assert 'context.metadata["unrestricted"]' in source
    assert "unrestricted_scope(unrestricted)" in source


def test_the_forced_report_turns_hidden_reasoning_off():
    import inspect

    from kazma_core.swarm import worker

    source = inspect.getsource(worker.InProcessWorker.dispatch)
    final = source[source.index("Max iterations hit"):]
    assert 'reasoning="off"' in final[:3000]


def test_an_unrestricted_worker_reads_whole_results():
    import inspect

    from kazma_core.swarm import worker

    source = inspect.getsource(worker.InProcessWorker.dispatch)
    assert "truncator.process(" in source and "_TRUNCATOR.process(" not in source


# ── A finished task goes back to its chat ────────────────────────────────

class _Result:
    def __init__(self, status="success", output="", error=None):
        self.status = status
        self.aggregated_output = output
        self.synthesized_output = None
        self.worker_results = []
        self.error = error
        self.duration_seconds = 600.0


class _Task:
    def __init__(self, metadata):
        self.id = "task-abc"
        self.metadata = metadata


TELEGRAM = {"source": "chat", "source_delivery_target": "telegram:42", "source_platform": "telegram"}


def test_only_a_task_from_a_chat_app_is_sent_back():
    from kazma_core.swarm.chat_delivery import chat_target

    assert chat_target(TELEGRAM) == ("telegram", "telegram:42")
    # A web turn's delivery target is the operator's Telegram: kept there.
    assert chat_target({**TELEGRAM, "source_platform": "web"}) is None
    assert chat_target({"source": "api", "source_delivery_target": "telegram:42"}) is None
    assert chat_target({"source": "chat", "source_delivery_target": "web:1"}) is None


def test_a_long_result_goes_as_a_file():
    from kazma_core.swarm.chat_delivery import INLINE_MAX_CHARS
    from kazma_core.swarm.chat_delivery import _delivery_message as delivery_message

    text, attachment = delivery_message(_Task(TELEGRAM), _Result(output="short answer"))
    assert "short answer" in text and attachment is None
    long_output = '{"shops": []}' + "x" * (INLINE_MAX_CHARS + 10)
    text, attachment = delivery_message(_Task(TELEGRAM), _Result(output=long_output))
    assert attachment is not None and attachment["data"].decode() == long_output
    assert "attached file" in text
    text, attachment = delivery_message(_Task(TELEGRAM), _Result(status="failed", error="no workers"))
    assert "no workers" in text and attachment is None


def _deliver(monkeypatch, handed: bool, answer: str = "sent:telegram:42"):
    from kazma_core.swarm import chat_delivery

    sent: list[tuple] = []

    async def fake_backend(target, text, **kwargs):
        sent.append((target, "telegram", text, kwargs))
        return answer

    import kazma_core.tools.send_message as send_module

    # The gateway's backend for the platform, as a stand-in.
    monkeypatch.setitem(send_module._message_backends, "telegram", fake_backend)
    monkeypatch.setattr(chat_delivery, "HANDOVER_GRACE_S", 0.01)
    task = _Task(dict(TELEGRAM))
    task.id = f"task-{handed}-{answer[:4]}"
    if handed:
        chat_delivery.note_result_handed(task.id)

    async def run():
        assert chat_delivery.deliver_finished_task(task, _Result(output="12 shops"))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if sent or handed:
                await asyncio.sleep(0.05)
                break
    asyncio.run(run())
    return sent, chat_delivery._result_was_handed(task.id)


def test_a_finished_task_is_sent_to_its_chat(monkeypatch):
    sent, handed = _deliver(monkeypatch, handed=False)
    assert len(sent) == 1
    target, backend, text, _ = sent[0]
    assert (target, backend) == ("telegram:42", "telegram") and "12 shops" in text
    assert handed


def test_a_result_the_turn_already_read_is_not_sent_again(monkeypatch):
    sent, _ = _deliver(monkeypatch, handed=True)
    assert sent == []


def test_a_refused_send_is_not_recorded_as_handed(monkeypatch):
    sent, handed = _deliver(monkeypatch, handed=False, answer="Error: no adapter")
    assert len(sent) == 1 and not handed


def test_the_engine_sends_every_ending_but_a_pause():
    import inspect

    from kazma_core.swarm import engine

    source = inspect.getsource(engine.SwarmEngine._finalize_task)
    paused = source.index('if status == "paused":')
    assert source.index("deliver_finished_task(task, result)") > paused


# ── A template with blanks is asked about, not guessed ────────────────────

LIVE_SCOPE = (
    "## Scope\n\n- Area: `<FILL IN, e.g. Jahra Industrial Area>`\n"
    "- Categories: `<FILL IN, e.g. car parts, tyres, hardware and tools>`\n"
)


@pytest.mark.parametrize("text, expected", [
    (LIVE_SCOPE, 2),
    ("المنطقة: <اكتب المنطقة هنا>", 1),
    ("Hello {{name}}, [FILL IN date]", 2),
    ("compare a<b and c>d; list[str]; <div class=x>; f(x) = {y}", 0),
])
def test_unfilled_blanks_in_both_languages(text, expected):
    from kazma_core.agent.turn_input import unfilled_blanks

    assert len(unfilled_blanks(text)) == expected


def test_the_priority_note_asks_for_the_blanks():
    from kazma_core.agent.turn_input import latest_turn_priority_note, unfilled_blanks

    note = latest_turn_priority_note(blanks=unfilled_blanks(LIVE_SCOPE))
    assert note.startswith("LATEST USER MESSAGE PRIORITY")  # still its registered turn-note kind
    assert "UNFILLED BLANKS" in note and "Jahra Industrial Area" in note
    assert "UNFILLED BLANKS" not in latest_turn_priority_note()


def test_the_supervisor_passes_the_blanks():
    import inspect

    from kazma_core.agent import graph_supervisor

    assert "blanks=unfilled_blanks(last_user_content)" in inspect.getsource(graph_supervisor)
