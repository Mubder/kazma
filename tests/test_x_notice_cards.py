"""Every X notice is an operator card, and a failing loop names its cause (2026-10-28).

Live 2026-10-28 Telegram showed "X mentions loop failed. Inspect X Studio
health and the X operations runbook." -- no ``[Ops]`` heading or severity
like every other alert, and no cause (X's API was not answering). The X
notice outbox sent bare text; now it takes only a finished card: loop
health is ``[Ops]`` and names the last error, schedule/reply/thread notices
are ``[X]``.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.observability.alert_card import format_operator_card, is_operator_card
from kazma_core.x_api import health, notifications
from kazma_core.x_api.client import XApiError

_ROOT = Path(__file__).resolve().parents[1]
_X_API = _ROOT / "kazma-core" / "kazma_core" / "x_api"


def _outbox() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(notifications.SCHEMA)
    return conn


def _notices(monkeypatch) -> list[str]:
    """Route record_cycle's enqueue into a private outbox and return its messages."""
    conn = _outbox()

    class _Store:
        def _connection(self, transaction=False):
            class _Ctx:
                def __enter__(self_inner):
                    return conn

                def __exit__(self_inner, *exc):
                    conn.commit()
                    return False
            return _Ctx()

    from kazma_core.x_api import publication_store

    monkeypatch.setattr(publication_store, "get_publication_store", lambda: _Store())
    monkeypatch.setattr(health, "_cycles", {})
    return conn


def _messages(conn) -> list[str]:
    return [r["message"] for r in conn.execute("SELECT message FROM x_notification_outbox ORDER BY id")]


def test_a_failing_loop_is_an_ops_card_naming_its_cause(monkeypatch):
    conn = _notices(monkeypatch)
    for _ in range(3):
        health.record_cycle("mentions", success=False, interval=600, error="X API timed out.")
    (failed,) = _messages(conn)
    head, body = failed.split("\n", 1)
    assert head == "\U0001f7e1 [Ops] X mentions polling is failing"
    assert "3 runs in a row failed. Last error: X API timed out." in body
    health.record_cycle("mentions", success=True, interval=600)
    recovered = _messages(conn)[1]
    assert recovered.startswith("\U0001f7e2 [Ops] X mentions polling recovered")
    assert all(is_operator_card(m) for m in _messages(conn))


def test_the_mentions_loop_hands_its_error_to_the_notice(monkeypatch):
    conn = _notices(monkeypatch)
    from kazma_core.x_api import mentions_fire, stance

    monkeypatch.setattr(stance, "get_reply_config",
                        lambda: SimpleNamespace(poll_interval_s=60, can_draft=lambda: True))

    async def failing_poll(**_kw):
        raise XApiError("X API timed out.", transient=True)

    monkeypatch.setattr(mentions_fire, "poll_once", failing_poll)
    calls = {"n": 0}

    class _Stop(Exception):
        pass

    async def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise _Stop

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        asyncio.run(mentions_fire._loop(60.0))
    (failed,) = _messages(conn)
    assert "[Ops]" in failed and "Last error: X API timed out." in failed


def test_negative_control_the_old_bare_notice_is_refused():
    conn = _outbox()
    old = "X mentions loop failed. Inspect X Studio health and the X operations runbook."
    with pytest.raises(ValueError, match="operator card"):
        notifications.enqueue(conn, tenant="default", key="k1", message=old)
    notifications.enqueue(conn, tenant="default", key="k2",
                          message=format_operator_card("X", "info", "X reply needs review — s", "body"))
    assert len(_messages(conn)) == 1


def test_every_x_notice_writer_builds_a_card():
    """Each function that enqueues a notice builds it with format_operator_card."""
    writers = []
    for path in _X_API.glob("*.py"):
        if path.name == "notifications.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            if "enqueue" in names:
                writers.append(path.name + "::" + fn.name)
                assert names & {"format_operator_card", "_health_card"}, f"{path.name}::{fn.name} enqueues bare text"
    assert len(writers) >= 4, writers  # health, publication, reply, thread


def test_chat_apps_render_an_x_card_as_a_card():
    from kazma_gateway.adapters.telegram_bus import _format_operator_card_md

    card = format_operator_card("X", "error", "X schedule: Publication outcome unknown", "Operation: op-1")
    assert is_operator_card(card)
    assert _format_operator_card_md(card).split("\n", 1)[0].startswith("*")
