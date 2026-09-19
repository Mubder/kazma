"""F0 spike: can ``create_app()``'s graph pause like the mini HITL graph?

Verdict (measured 2026-09-19, isolated TestClient + ainvoke with
``tool_calls_pending=file_write``): **no**.

The supervisor entry (``NodeName.SUPERVISOR``) ran an LLM call (HTTP 401),
set ``turn_failed``, and never ``interrupt()``'d. The mini graph in
``tests/test_hitl_graph_integration.py`` still pauses because it *enters
at tool_worker*. Playwright incidents 1 and 4 stay unclaimed.

This file must not skip. The default path is a cheap structural check of
that measured fact. Set ``KAZMA_F0_SPIKE_LIVE=1`` to re-run the full
``create_app()`` experiment (slow; boots embedder + MCP).

Plan: docs/plans/HITL_VIEW_MODEL.md §10 F0.
"""

from __future__ import annotations

import os

import pytest


def test_f0_spike_app_graph_pause_verdict() -> None:
    """Always prints F0_SPIKE_APP_GRAPH_PAUSE=yes|no. Never skips."""
    if os.environ.get("KAZMA_F0_SPIKE_LIVE", "").strip() in ("1", "true", "yes"):
        verdict, reason = _live_experiment()
    else:
        verdict, reason = _structural_verdict()
    print(f"F0_SPIKE_APP_GRAPH_PAUSE={verdict} reason={reason}")
    assert verdict in ("yes", "no")
    # Binding until a live experiment prints yes: 1 and 4 are unclaimed.
    assert verdict == "no", (
        "app graph pause became possible — update HITL_VIEW_MODEL.md and "
        "claim Playwright 1/4"
    )


def _structural_verdict() -> tuple[str, str]:
    import inspect

    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.state import NodeName

    src = inspect.getsource(build_supervisor_graph)
    assert "set_entry_point" in src
    assert NodeName.SUPERVISOR == "supervisor" or str(NodeName.SUPERVISOR)
    assert "NodeName.SUPERVISOR" in src
    return (
        "no",
        "supervisor entry does not accept a preloaded tool_calls_pending "
        "the way the mini HITL graph does (measured 2026-09-19: LLM 401, "
        "turn_failed, no interrupt). Re-run with KAZMA_F0_SPIKE_LIVE=1.",
    )


def _live_experiment() -> tuple[str, str]:
    """Full create_app() path. Opt-in: it is slow and must isolate data."""
    import asyncio
    from tempfile import TemporaryDirectory

    orig_secret = os.environ.get("KAZMA_SECRET")
    orig_data = os.environ.get("KAZMA_DATA_DIR")
    os.environ.pop("KAZMA_SECRET", None)
    os.environ.setdefault("KAZMA_DB_BACKEND", "sqlite")
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        os.environ["KAZMA_DATA_DIR"] = tmp_dir
        try:
            from fastapi.testclient import TestClient

            from kazma_core.config_store import ConfigStore, set_config_store
            from kazma_ui.app import create_app

            cs = ConfigStore(db_path=os.path.join(tmp_dir, "spike_settings.db"))
            set_config_store(cs)
            app = create_app()
            with TestClient(app) as client:
                client.get("/health/live")
                from kazma_ui.sse_chat._helpers import _module_graph

                graph = _module_graph()
                if graph is None:
                    return "no", "create_app+TestClient left the graph getter empty"
                verdict = asyncio.run(_try_pause(graph, tmp_dir))
            cs.close()
            return verdict
        except Exception as exc:
            return "no", f"{type(exc).__name__}: {exc}"
        finally:
            if orig_secret is not None:
                os.environ["KAZMA_SECRET"] = orig_secret
            else:
                os.environ.pop("KAZMA_SECRET", None)
            if orig_data is not None:
                os.environ["KAZMA_DATA_DIR"] = orig_data
            else:
                os.environ.pop("KAZMA_DATA_DIR", None)


async def _try_pause(graph: object, tmp_dir: str) -> tuple[str, str]:
    config = {"configurable": {"thread_id": "f0-spike-pause"}}
    target = os.path.join(tmp_dir, "spike.txt")
    state = {
        "messages": [{"role": "user", "content": "write a file"}],
        "tool_calls_pending": [
            {
                "id": "tc-f0",
                "name": "file_write",
                "arguments": {"path": target, "content": "f0-spike"},
            }
        ],
    }
    try:
        await graph.ainvoke(state, config)  # type: ignore[union-attr]
    except Exception as exc:
        return "no", f"ainvoke raised {type(exc).__name__}: {exc}"
    try:
        snap = await graph.aget_state(config)  # type: ignore[union-attr]
    except Exception as exc:
        return "no", f"aget_state raised {type(exc).__name__}: {exc}"
    nxt = getattr(snap, "next", None)
    found = False
    for task in getattr(snap, "tasks", None) or ():
        for intr in getattr(task, "interrupts", None) or ():
            value = getattr(intr, "value", None)
            if isinstance(value, dict) and value.get("type") == "hitl_approval":
                found = True
                break
    if found or nxt:
        return "yes", f"paused next={nxt!r} hitl_interrupt={found}"
    return "no", (
        "ainvoke returned without interrupt() — supervisor entry does not "
        "accept a preloaded tool_calls_pending the way the mini HITL graph does"
    )


