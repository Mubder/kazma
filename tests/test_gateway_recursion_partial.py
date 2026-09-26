"""The gateway keeps a recursion Partial for "Proceed" and pauses the long task.

AGENTS.md §25: a turn that hits the recursion limit with salvaged progress
stores it as the thread's continue context and pauses a running long task
(the next message is a fresh command -- the 2026-08-19 Telegram desync). The
gateway did this inline, importing a name removed on 2026-09-04
(``record_budget_exhausted``) inside ``except Exception: pass``: the import
failed every time, so from then until 2026-09-26 no Partial was kept and no
long task paused on Telegram, Discord or Slack. The long-task tests called
the functions directly and never noticed. ``tests/test_imports.py`` now
checks every imported name; this drives the gateway's own code.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_a_partial_is_kept_for_proceed_and_the_long_task_pauses(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.agent.long_task import (
        consume_continue_context,
        disable_long_task,
        enable_long_task,
        long_task_status,
    )
    from kazma_gateway.agent_handler.graph import _settle_recursion_partial

    tid = "gw-telegram-partial-1"
    enable_long_task(tid, actor="tester", preset="mission")
    try:
        assert _settle_recursion_partial(tid, "Verified 6 of 8 domains.") is True
        status = long_task_status(tid)
        assert status["active"] is False and status.get("paused_reason") == "recursion"
        context = consume_continue_context(tid, user_text="Proceed")
        assert context and "Verified 6 of 8 domains." in context
    finally:
        disable_long_task(tid)


def test_without_a_long_task_the_partial_is_still_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.agent.long_task import consume_continue_context
    from kazma_gateway.agent_handler.graph import _settle_recursion_partial

    tid = "gw-telegram-partial-2"
    assert _settle_recursion_partial(tid, "Found A and B.") is False
    assert "Found A and B." in (consume_continue_context(tid, user_text="Proceed") or "")


def test_the_recursion_path_goes_through_it_and_never_swallows_silently():
    """The handler calls the function, and a failure there is a WARNING."""
    source = (REPO / "kazma-gateway" / "kazma_gateway" / "agent_handler" / "graph.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_settle_recursion_partial"]
    assert len(calls) == 1
    handler = next(t for t in ast.walk(tree) if isinstance(t, ast.Try)
                   and any(c in list(ast.walk(t)) for c in calls))
    body = handler.handlers[0].body
    assert not all(isinstance(s, ast.Pass) for s in body)  # negative: the old `pass`
    assert "logger.warning" in ast.unparse(handler.handlers[0])


def test_memory_command_counts_the_senders_facts(tmp_path, monkeypatch):
    """``/memory`` read a V1 agent memory through an import that no longer
    resolved and answered "?" -- it counts the current facts now."""
    import sqlite3

    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.memory.v2_health import count_current_facts

    db = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
    conn = sqlite3.connect(str(db))
    ensure_primary_schema(conn)
    for bid, tenant, until in (("b1", "default", None), ("b2", "default", None),
                               ("b3", "default", 5.0), ("b4", "telegram:9", None)):
        conn.execute(
            "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
            "valid_from, valid_until, ingested_at) VALUES (?,?,'user','p','set','o',1,?,1)",
            (bid, tenant, until),
        )
    conn.commit()
    conn.close()
    assert count_current_facts("default") == 2  # b3 is superseded
    assert count_current_facts("telegram:9") == 1
    assert count_current_facts() == 3


def test_nonstop_settings_come_from_the_install_yaml(tmp_path, monkeypatch):
    """``get_nonstop_config`` imported a ``load_config`` that never existed,
    so kazma.yaml's ``agent.nonstop`` was always ignored."""
    from kazma_core import config_loader, paths
    from kazma_core.agent.nonstop import get_nonstop_config

    (tmp_path / "kazma.yaml").write_text(
        "agent:\n  nonstop:\n    enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(paths, "_project_root", tmp_path)
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _NoStore())
    assert config_loader.install_yaml_section("agent", "nonstop") == {"enabled": False}
    assert get_nonstop_config().enabled is False
    (tmp_path / "kazma.yaml").write_text(
        "agent:\n  nonstop:\n    enabled: true\n", encoding="utf-8")
    assert get_nonstop_config().enabled is True


class _NoStore:
    def get(self, key, default=None):
        return default
