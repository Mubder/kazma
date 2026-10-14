"""No Knowledge SQLite work runs on the event loop (Knowledge audit
2026-10-13, KB-S06 and the KB-E13 loop bullet).

The agent's Knowledge tools, the gateway's ``/kb`` and the supervisor's
Knowledge block ran the SQLite store on the loop that serves every stream.
Here every statement the Knowledge store runs is traced, and one run on a
thread where an event loop is running fails the test.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from kazma_core.stores import knowledge as kb_module
from kazma_core.stores import knowledge_index as ki


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class _NoVectors:
    available = False
    identity = ""

    def drop(self):
        return False

    def query(self, *a, **k):
        return []


@pytest.fixture()
def traced(tmp_path, monkeypatch):
    on_loop: list[str] = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        target = str(args[0] if args else kwargs.get("database", ""))
        if "knowledge" in target:
            conn.set_trace_callback(lambda sql: on_loop.append(" ".join(sql.split())[:80]) if _on_loop() else None)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ki.KnowledgeIndex, "_vector_store_for", lambda self, library_id: _NoVectors())
    monkeypatch.setattr(ki.KnowledgeIndex, "_open_vector_store", lambda self, library_id: _NoVectors())
    ki.reset_knowledge_index()
    store = kb_module.KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    monkeypatch.setattr(kb_module, "_knowledge_store", store)
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict

    store.create_library("docs", "Docs", seed_url="https://docs.example.test/")
    store.update_library("docs", auto_inject=True)
    ki.get_knowledge_index().index("docs", [chunk_to_dict(c) for c in chunk_markdown_doc(
        "# Webhooks\n\nWebhooks retry delivery five times.\n", source_url="https://docs.example.test/w",
        library_id="docs")])
    on_loop.clear()
    yield SimpleNamespace(store=store, on_loop=on_loop, dir=tmp_path)
    ki.reset_knowledge_index()
    store.close()


def test_the_agent_tools_keep_the_store_off_the_loop(traced, monkeypatch):
    import inspect

    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.stores import knowledge_ingest

    async def no_fetch(*a, **k):
        return knowledge_ingest.IngestResult(pages_failed=1, errors=["offline"])

    monkeypatch.setattr(knowledge_ingest, "ingest_url", no_fetch)
    registry = LocalToolRegistry()
    register_builtin_tools(registry)

    async def run_all():
        for name, kwargs in (("knowledge_list_libraries", {}), ("knowledge_search", {"query": "webhooks"}),
                             ("knowledge_search", {"query": "webhooks", "library": "docs"}),
                             ("knowledge_create_library", {"library_id": "more", "name": "More"}),
                             ("knowledge_ingest_url", {"library_id": "docs",
                                                       "url": "https://docs.example.test/x"})):
            tool = registry.get_tool(name)
            if inspect.iscoroutinefunction(tool.func):
                await tool.func(**kwargs)
            else:  # a plain def runs in the registry's thread, never on the loop
                await asyncio.to_thread(tool.func, **kwargs)

    asyncio.run(run_all())
    assert traced.on_loop == []


def test_the_gateway_kb_keeps_the_store_off_the_loop(traced, monkeypatch):
    from kazma_core import config_store
    from kazma_gateway.agent_handler import commands
    from kazma_gateway.gateway import IncomingMessage

    journal = config_store.ConfigStore(str(traced.dir / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: journal)

    async def reply(*a, **k):
        return None

    monkeypatch.setattr(commands, "_send_model_reply", reply)

    async def run_all():
        for text in ("/kb", "/kb list", "/kb search docs webhooks", "/kb status docs", "/kb delete docs"):
            msg = IncomingMessage(platform="telegram", sender_id="telegram:1", text=text)
            await commands._try_kb_command(msg, None, None, "th-1")

    try:
        asyncio.run(run_all())
    finally:
        journal.close()
    assert traced.on_loop == []


def test_the_supervisor_builds_the_knowledge_block_off_the_loop(traced, monkeypatch):
    from kazma_core.agent.graph_supervisor import _knowledge_inject_for_turn

    async def turn():
        return await asyncio.to_thread(_knowledge_inject_for_turn, "webhooks retry delivery")

    inject = asyncio.run(turn())
    assert inject is not None and inject.hits
    assert traced.on_loop == []


def test_the_tracer_sees_a_store_call_on_the_loop(traced):
    """Negative control: the old tool's direct store call inside a coroutine."""

    async def old_tool():
        return traced.store.list_libraries()

    asyncio.run(old_tool())
    assert traced.on_loop
