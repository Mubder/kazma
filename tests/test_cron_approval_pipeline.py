"""Cron approval-delivery pipeline — the 2026-08-27 incident class.

Agent-rescheduled cron jobs were born with an empty delivery_target (no
gateway context inside a cron-fired turn), so every result message died
with 'target_id must be platform:id format'. These tests pin the four fix
layers: parent-context inheritance at creation, _deliver's repair chain,
denial notifications, and the scheduling context plumbing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import importlib

import kazma_core.cron.scheduler as sched
from kazma_core.cron.scheduler import ScheduledJob, get_cron_parent


def _sm_mod():
    """The real kazma_core.tools.send_message MODULE (the package attribute
    of that name resolves to the function — patch via sys.modules)."""
    return importlib.import_module("kazma_core.tools.send_message")


# ── Item 1: cron-parent context + schedule_task inheritance ───────────


def test_cron_parent_context_roundtrip():
    token = sched._cron_parent_ctx.set(
        {"job_id": "cron-1", "delivery_target": "telegram:123", "platform": "telegram", "thread_id": "t"}
    )
    try:
        parent = get_cron_parent()
        assert parent and parent["delivery_target"] == "telegram:123"
    finally:
        sched._cron_parent_ctx.reset(token)
    assert get_cron_parent() is None  # outside a cron execution


def test_cron_parent_empty_dict_is_none():
    token = sched._cron_parent_ctx.set({})
    try:
        assert get_cron_parent() is None
    finally:
        sched._cron_parent_ctx.reset(token)


@pytest.mark.asyncio
async def test_schedule_task_inherits_parent_target(monkeypatch):
    """A cron-fired turn (no gateway ContextVar) reschedules → the new job
    inherits the firing job's delivery_target — never born targetless."""
    captured: dict[str, object] = {}

    class _Sched:
        async def schedule(self, **kw):
            captured.update(kw)
            return {"job_id": "new-1"}

    import kazma_skills.native.task_scheduler_cron.tools as tst

    monkeypatch.setattr(
        "kazma_core.cron.scheduler.get_cron_scheduler", lambda: _Sched()
    )
    monkeypatch.setattr(_sm_mod(), "get_current_delivery_target", lambda: "")
    token = sched._cron_parent_ctx.set(
        {"job_id": "cron-parent", "delivery_target": "telegram:1804015016",
         "platform": "telegram", "thread_id": "th"}
    )
    try:
        out = await tst.schedule_task("5m", "post tweet 7/8")
    finally:
        sched._cron_parent_ctx.reset(token)
    assert '"job_id": "new-1"' in out
    assert captured["delivery_target"] == "telegram:1804015016"
    assert captured["platform"] == "telegram"


@pytest.mark.asyncio
async def test_schedule_task_uses_operator_telegram_when_contextvar_empty(monkeypatch):
    """Web SSE/WS bookings have no inbound telegram chat — use swarm/allowed."""
    captured: dict[str, object] = {}

    class _Sched:
        async def schedule(self, **kw):
            captured.update(kw)
            return {"job_id": "new-web"}

    import kazma_skills.native.task_scheduler_cron.tools as tst

    monkeypatch.setattr(
        "kazma_core.cron.scheduler.get_cron_scheduler", lambda: _Sched()
    )
    monkeypatch.setattr(_sm_mod(), "get_current_delivery_target", lambda: "")
    monkeypatch.setattr(
        _sm_mod(), "operator_telegram_target", lambda: "telegram:1804015016"
    )
    out = await tst.schedule_task("2m", "Test schedule")
    assert '"job_id": "new-web"' in out
    assert captured["delivery_target"] == "telegram:1804015016"
    assert captured["platform"] == "telegram"


@pytest.mark.asyncio
async def test_schedule_task_fails_closed_when_undeliverable(monkeypatch):
    """Never persist target=(none) — that job fires then UNDELIVERABLE."""
    called = []

    class _Sched:
        async def schedule(self, **kw):
            called.append(kw)
            return {"job_id": "should-not"}

    import kazma_skills.native.task_scheduler_cron.tools as tst

    monkeypatch.setattr(
        "kazma_core.cron.scheduler.get_cron_scheduler", lambda: _Sched()
    )
    monkeypatch.setattr(_sm_mod(), "get_current_delivery_target", lambda: "")
    monkeypatch.setattr(_sm_mod(), "operator_telegram_target", lambda: "")
    out = await tst.schedule_task("2m", "Test schedule")
    payload = __import__("json").loads(out)
    assert payload["ok"] is False
    assert "delivery" in payload["error"].lower()
    assert not called


def test_is_valid_delivery_target():
    from kazma_core.tools.send_message import is_valid_delivery_target

    assert is_valid_delivery_target("telegram:1804015016")
    assert not is_valid_delivery_target("")
    assert not is_valid_delivery_target("be9fc11e-a15b-408f-bd35-1356714da9c3")
    assert not is_valid_delivery_target("telegram:")
    assert not is_valid_delivery_target(None)


def test_web_gateway_block_stamps_operator_telegram(monkeypatch):
    from kazma_core.tools.send_message import web_gateway_block

    monkeypatch.setattr(
        _sm_mod(), "operator_telegram_target", lambda: "telegram:1804015016"
    )
    monkeypatch.setattr(_sm_mod(), "get_current_delivery_target", lambda: None)
    gw = web_gateway_block("thread-1")
    assert gw["platform"] == "web"
    assert gw["thread_id"] == "thread-1"
    assert gw["delivery_target"] == "telegram:1804015016"
    assert set(gw) <= {"thread_id", "display_name", "platform", "delivery_target"}


def test_sse_and_ws_stamp_web_gateway():
    """The documented _gateway fallback is dead unless Web writes it."""
    from tests._module_source import module_source
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sse = module_source(root / "kazma-ui" / "kazma_ui" / "sse_chat" / "__init__.py")
    ws = (root / "kazma-ui" / "kazma_ui" / "routes" / "ws_chat.py").read_text(
        encoding="utf-8"
    )
    assert "web_gateway_block" in sse
    assert 'input_state["_gateway"]' in sse
    assert "web_gateway_block" in ws
    assert 'input_state["_gateway"]' in ws


# ── Item 2: _deliver repair chain ─────────────────────────────────────


def _job(target: str = "", thread: str = "th-1") -> ScheduledJob:
    return ScheduledJob(
        job_id="cron-x", prompt="post tweet", timing="5m",
        platform="telegram", thread_id=thread, delivery_target=target,
    )


class _Store:
    def __init__(self, sibling: str = "") -> None:
        self.sibling = sibling
        self.repaired: tuple[str, str] | None = None

    async def sibling_delivery_target(self, thread_id: str) -> str:
        return self.sibling

    async def update_delivery_target(self, job_id: str, target: str) -> None:
        self.repaired = (job_id, target)


@pytest.mark.asyncio
async def test_deliver_adopts_and_persists_sibling_target(monkeypatch):
    sent: list[tuple[str, str, str]] = []

    async def fake_send(target, text, backend=None, **kw):
        sent.append((target, text, str(backend)))

    monkeypatch.setattr(_sm_mod(), "send_message", fake_send)
    s = sched.CronScheduler.__new__(sched.CronScheduler)
    s._store = _Store(sibling="telegram:1804015016")

    job = _job(target="", thread="th-1")
    await s._deliver(job, "result text")
    assert sent and sent[0][0] == "telegram:1804015016"  # adopted sibling
    assert s._store.repaired == ("cron-x", "telegram:1804015016")  # healed row


@pytest.mark.asyncio
async def test_deliver_valid_target_untouched(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_send(target, text, backend=None, **kw):
        sent.append((target, text))

    monkeypatch.setattr(_sm_mod(), "send_message", fake_send)
    s = sched.CronScheduler.__new__(sched.CronScheduler)
    s._store = _Store(sibling="telegram:OTHER")
    await s._deliver(_job(target="telegram:111"), "hello")
    assert sent == [("telegram:111", "hello")]
    assert s._store.repaired is None  # valid row never touched


@pytest.mark.asyncio
async def test_deliver_undeliverable_logs_critical_never_crashes(monkeypatch, caplog):
    monkeypatch.setattr(_sm_mod(), "send_message", None)
    s = sched.CronScheduler.__new__(sched.CronScheduler)
    s._store = _Store(sibling="")  # no sibling, no session
    with caplog.at_level("CRITICAL", logger="kazma_core.cron.scheduler"):
        await s._deliver(_job(target="", thread="ghost"), "text")
    assert any("UNDELIVERABLE" in r.message for r in caplog.records)


# ── Item 3: denial notification ───────────────────────────────────────


@pytest.mark.asyncio
async def test_denial_notifies_cron_parent(monkeypatch):
    notified: list[tuple[str, str]] = []

    async def fake_send(target, text, backend=None, **kw):
        notified.append((target, text))

    monkeypatch.setattr(_sm_mod(), "send_message", fake_send)
    from kazma_core.swarm.safety import _notify_cron_denial

    token = sched._cron_parent_ctx.set(
        {"job_id": "cron-7", "delivery_target": "telegram:1804015016",
         "platform": "telegram", "thread_id": "t"}
    )
    try:
        await _notify_cron_denial("x_post", "denied or timed out")
    finally:
        sched._cron_parent_ctx.reset(token)
    assert notified and notified[0][0] == "telegram:1804015016"
    assert "x_post" in notified[0][1] and "NOT executed" in notified[0][1]


@pytest.mark.asyncio
async def test_denial_notification_silent_outside_cron(monkeypatch):
    called = []

    async def fake_send(*a, **kw):
        called.append(a)

    monkeypatch.setattr(_sm_mod(), "send_message", fake_send)
    from kazma_core.swarm.safety import _notify_cron_denial

    await _notify_cron_denial("x_post", "denied")
    assert not called  # normal gateway turns: no extra message


# ── memory q-filter normalization (2026-08-27 quirk) ──────────────────


def test_memory_q_filter_underscore_insensitive():
    """memory_list_beliefs(q=…) used a literal LIKE — 'memory system'
    returned 0 while FTS memory_search matched user_memory_system. The
    filter now normalizes _/- to spaces on both sides."""
    from kazma_core.agent.tool_builtins import _qnorm

    assert _qnorm("memory system") == "memory system"
    assert _qnorm("Memory_System") == "memory system"
    assert _qnorm("  prefers--dark__mode ") == "prefers dark mode"
    # tool_builtins is a package since audit O5 — module_source reads the
    # whole package, not just its __init__ shim.
    from tests._module_source import module_source

    src = module_source(__import__("kazma_core.agent.tool_builtins", fromlist=["x"]).__file__)
    assert "REPLACE(REPLACE(subject,'_',' '),'-',' ')" in src
    assert "REPLACE(REPLACE(e.name,'_',' '),'-',' ')" in src
