"""Structural telemetry observes real terminal turns without changing them."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from kazma_core.observability import answer_quality as quality
from starlette.requests import Request


def messages(text, prompt="Explain the result."):
    return [{"role": "user", "content": prompt}, {"role": "assistant", "content": text}]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.config_store import get_config_store

    get_config_store().set("memory.enabled", False)
    get_config_store().set("agent.nonstop.ledger.enabled", False)


@pytest.mark.parametrize("prompt,draft,final,repaired,miss", [
    ("Explain in one paragraph.", "One.\n\nTwo.", "One. Two.", True, False),
    ("اشرح في فقرة واحدة.", "أولًا.\n\nثانيًا.", "أولًا. ثانيًا.", True, False),
    ("Explain in one paragraph.", "# Result\n\nStill pending.", "# Result\n\nStill pending.", False, True),
    ('Translate "in one paragraph".', "One.\n\nTwo.", "One.\n\nTwo.", False, False),
    ("Do not answer in one paragraph.", "One.\n\nTwo.", "One.\n\nTwo.", False, False),
    ("In one paragraph.", "- One\n- Two", "- One\n- Two", False, False),
    ("In one paragraph.", '{"count":3}', '{"count":3}', False, False),
    ("In one paragraph.", "```python\nprint(1)\n```", "```python\nprint(1)\n```", False, False),
])
def test_layout_signals_share_formatter_scope(prompt, draft, final, repaired, miss):
    result = quality.observe(messages(draft, prompt), messages(final, prompt), {})
    assert result["paragraph_repaired"] is repaired
    assert result["paragraph_miss"] is miss


@pytest.mark.parametrize("text,expected", [
    ("```plan\n- Inspect\n```\nDone.", True),
    ("~~~plan\n- افحص\n~~~", True),
    ("An inline ```plan example.", False),
    ("> ```plan\n> quoted", False),
    ("````markdown\n```plan\n- example\n```\n````", False),
    ("```plantuml\nAlice -> Bob\n```", False),
])
def test_plan_signal_does_not_treat_explanations_as_leaks(text, expected):
    assert quality.observe(messages(text), messages(text), {})["plan_block"] is expected


def test_failure_and_ambiguous_json_are_not_accuracy_grades():
    good_json = messages('{"path":"sample.txt"}')
    result = quality.observe(good_json, good_json, {"tool_argument_rechecks": 1})
    assert result["argument_recheck"]
    assert not result["empty_answer"]
    result = quality.observe(messages(""), messages("⚠️ Provider failed."), {"turn_failed": True})
    assert result["turn_failed"] and not result["empty_draft"] and not result["paragraph_miss"]
    assert quality.observe(messages(""), messages("Recovery notice."), {})["empty_draft"]
    assert quality.observe(messages(""), messages(""), {})["empty_answer"]


def test_durable_idempotent_content_free_and_retained(monkeypatch):
    from kazma_core.paths import data_dir

    state = {"thread_id": "owned-session", "tool_argument_rechecks": 1}
    source = messages("Secret answer API_KEY=do-not-store", "Secret question do-not-store")
    for _ in range(2):
        quality.record(source, source, state)
    # New connections/process restarts still see one durable row.
    result = quality.snapshot()
    assert result["totals"]["turns"] == result["totals"]["argument_recheck"] == 1
    assert "do-not-store" not in json.dumps(result)
    path = data_dir() / "answer_quality.db"
    with sqlite3.connect(path) as conn:
        assert "do-not-store" not in repr(conn.execute("SELECT * FROM answer_quality").fetchall())
        conn.execute("UPDATE answer_quality SET ts='2000-01-01T00:00:00+00:00'")
    assert quality.snapshot()["totals"]["turns"] == 0
    monkeypatch.setattr(quality, "MAX_ROWS", 2)
    for n in range(4):
        quality.record(messages(str(n)), messages(str(n)), {"thread_id": str(n)})
    assert quality.snapshot()["totals"]["turns"] == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM answer_quality").fetchone()[0] == 2


def test_concurrent_duplicate_record_is_one_row():
    def write(_):
        quality.record(messages("Answer."), messages("Answer."), {"thread_id": "same"})

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(write, range(10)))
    assert quality.snapshot()["totals"]["turns"] == 1


def test_correlation_distinguishes_identical_requests_and_deduplicates_resumes():
    from kazma_core.observability.correlation import bind_turn_id, reset_turn_id

    for turn in ["turn-one", "turn-one", "turn-two"]:
        token = bind_turn_id(turn)
        try:
            quality.record(messages("Answer."), messages("Answer."), {"thread_id": "same", "tool_argument_rechecks": 1})
        finally:
            reset_turn_id(token)
    result = quality.snapshot()
    assert result["totals"]["turns"] == 2
    assert {row["turn_id"] for row in result["recent"]} == {"turn-one", "turn-two"}


def test_unavailable_is_not_zero_healthy(monkeypatch):
    monkeypatch.setattr(quality, "_connect", lambda: (_ for _ in ()).throw(OSError("secret-path")))
    quality.record(messages("x"), messages("x"), {"thread_id": "same"})
    result = quality.snapshot()
    assert result["available"] is False and result["totals"] is None
    assert "secret-path" not in json.dumps(result)


@pytest.mark.asyncio
async def test_real_respond_boundary_records_repair_off_loop_and_preserves_answer(monkeypatch):
    from kazma_core.agent.graph_respond import respond_node

    loop_thread = threading.get_ident()
    real_record = quality.record
    seen = []

    def record(*args):
        seen.append(threading.get_ident())
        real_record(*args)

    monkeypatch.setattr(quality, "record", record)
    source = messages("Review approved.\n\nDeployment not authorized.", "Answer in one paragraph.")
    state = {"messages": source, "thread_id": "real-respond", "max_iterations": 15}
    result = await respond_node(state)
    assert result["messages"][-1]["content"] == "Review approved. Deployment not authorized."
    assert source[-1]["content"] == "Review approved.\n\nDeployment not authorized."
    assert seen and seen[0] != loop_thread
    assert quality.snapshot()["totals"]["paragraph_repaired"] == 1
    # Old boundary negative control: producing the same answer without
    # respond_node left no observation for a different thread.
    assert quality.snapshot()["totals"]["turns"] == 1


@pytest.mark.asyncio
async def test_monitor_storage_failure_does_not_break_terminal_delivery(monkeypatch):
    from kazma_core.agent.graph_respond import respond_node

    monkeypatch.setattr(quality, "_connect", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    source = messages("The status is unknown.")
    result = await respond_node({"messages": source, "thread_id": "locked", "max_iterations": 15})
    assert result["messages"][-1]["content"] == source[-1]["content"]


@pytest.mark.parametrize("role,status", [("viewer", 403), ("operator", 403), ("admin", 200)])
def test_quality_endpoint_is_admin_only(monkeypatch, role, status):
    import kazma_ui.auth as auth
    from kazma_ui.dashboard import answer_quality_status

    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "configured")
    monkeypatch.setattr(auth, "is_authenticated", lambda request, secret: True)
    monkeypatch.setattr(auth, "get_request_principal", lambda request: {"role": role, "source": "session"})
    request = Request({"type": "http", "method": "GET", "path": "/api/dashboard/answer-quality", "headers": []})
    assert answer_quality_status(request).status_code == status


def test_quality_endpoint_links_only_existing_chat(monkeypatch):
    import kazma_ui.auth as auth
    import kazma_ui.turn_runtime as runtime
    from kazma_ui.dashboard import answer_quality_status

    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    monkeypatch.setattr(runtime, "resolve_session_id", lambda tid: "known-chat" if tid == "known-thread" else "")
    quality.record(messages("x"), messages("x"), {"thread_id": "known-thread", "tool_argument_rechecks": 1})
    request = Request({"type": "http", "method": "GET", "path": "/api/dashboard/answer-quality", "headers": []})
    row = json.loads(answer_quality_status(request).body)["recent"][0]
    assert row["session_id"] == "known-chat" and row["signals"] == ["argument_recheck"]
