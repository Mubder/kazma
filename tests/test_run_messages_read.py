"""What run supervision says reads right where it lands (live test, 2026-10-25).

The owner's first live test made a swarm task time out and found three faults:
``/runs`` on Telegram printed "swarmtask:task-..." (Markdown read the ``_`` as
italics) and "taskcompleted" -- the engine's last event -- where the failure's
reason belonged; and the diagnosis on the ops channels repeated its first line
and stopped mid-word at the ordinary alert's 600 characters.
"""

from __future__ import annotations

import asyncio
import time

from kazma_core.observability import ops_alerts
from kazma_core.runs import notify
from kazma_core.runs.contract import Run, RunState
from kazma_core.runs.text import describe_run, format_listing
from kazma_gateway.telegram_format import md_to_tg_html


def _failed_swarm_task() -> Run:
    return Run(
        run_id="swarm_task:task-e59dac70", kind="swarm_task", native_id="task-e59dac70",
        title="check kazma.ai", state=RunState.FAILED, native_state="timeout",
        started_at=time.time() - 60, progress_note="task_completed",
        detail="Task timed out after 5.0s.", actions=("retry",),
    )


def test_a_failed_run_shows_its_reason_not_its_last_step():
    listing = format_listing([_failed_swarm_task()], {})
    assert "Task timed out after 5.0s." in listing
    assert "task_completed" not in listing


def test_a_failed_run_says_its_reason_once_in_detail():
    shown = describe_run(_failed_swarm_task())
    assert shown.count("Task timed out after 5.0s.") == 1


def test_run_ids_survive_telegrams_markdown():
    html = md_to_tg_html(format_listing([_failed_swarm_task()], {}))
    assert "swarm_task:task-e59dac70" in html
    assert "<i>" not in html


def test_the_old_line_lost_the_underscore_on_telegram():
    """Negative control: the id as plain text is italicized away."""
    html = md_to_tg_html("❌ Swarm task: check kazma.ai · Failed · task_completed · swarm_task:task-e59dac70")
    assert "swarm_task:task-e59dac70" not in html


def test_a_chat_cards_ids_survive_telegrams_markdown():
    row = {"condition": "failed", "kind": "swarm_task", "title": "check kazma.ai",
           "run_id": "swarm_task:task-e59dac70", "id": "inc_6a806f4889cf", "opened_at": time.time()}
    html = md_to_tg_html(notify._card_text(row))
    assert "swarm_task:task-e59dac70" in html and "inc_6a806f4889cf" in html


def test_a_diagnosis_on_the_ops_channels_is_whole_and_titled_once(monkeypatch):
    sent: list[tuple] = []

    def capture(key, title, detail="", **kwargs):
        sent.append((title, detail, kwargs))
        return True

    monkeypatch.setattr(ops_alerts, "alert", capture)
    diagnosis = "The likely cause is a 5-second timeout. " * 40 + "Retry is the safer action."
    row = {"id": "inc_1", "run_id": "swarm_task:t", "origin": {}}
    asyncio.run(notify.send_diagnosis(row, diagnosis))
    (title, detail, kwargs), = sent
    assert title.startswith("🔎 Diagnosis of swarm_task:t")
    assert title not in detail
    body = ops_alerts._format("info", title, detail, 0, 0, kwargs["max_detail"])
    assert body.rstrip().endswith("Retry is the safer action.")


def test_an_ordinary_alert_is_still_cut_at_its_default():
    """Negative control: the default cut is what stopped the diagnosis."""
    long = "x" * 2000
    assert "x" * 601 not in ops_alerts._format("info", "t", long, 0, 0)
    assert "x" * 2000 in ops_alerts._format("info", "t", long, 0, 0, 3500)
