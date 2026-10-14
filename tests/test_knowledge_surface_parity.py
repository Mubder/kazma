"""One parity matrix for the Knowledge Library's surfaces (Knowledge audit
2026-10-13, KQ02).

Web API, chat apps (``/kb``), the agent's tools, MCP (``kazma mcp``, which
publishes the tool registry) and the TUI, against list, create, ingest,
search, delete and status. Each cell names what serves the operation there,
or ``None`` with the reason it is absent; the tests check every named entry
exists and every absent one is absent, and that every surface refuses a
multi-user install the same way.
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: operation -> surface -> what serves it (None: absent, the reason beside it).
MATRIX: dict[str, dict[str, object]] = {
    "list": {"web": ("GET", "/api/kb/libraries"), "chat": ["list", ""],
             "tools": ["knowledge_list_libraries"], "mcp": ["knowledge_list_libraries"], "tui": None},
    "create": {"web": ("POST", "/api/kb/libraries"),
               "chat": None,  # /kb add and /kb crawl create the library once admitted
               "tools": ["knowledge_create_library"], "mcp": ["knowledge_create_library"], "tui": None},
    "ingest": {"web": ("POST", "/api/kb/ingest"), "chat": ["add", "ingest", "crawl", "refresh"],
               "tools": ["knowledge_ingest_url", "knowledge_ingest_site"],
               "mcp": ["knowledge_ingest_url", "knowledge_ingest_site"], "tui": None},
    "search": {"web": ("POST", "/api/kb/search"), "chat": ["search"], "tools": ["knowledge_search"],
               "mcp": ["knowledge_search"], "tui": None},
    "delete": {"web": ("DELETE", "/api/kb/libraries/{library_id}"), "chat": ["delete"],
               "tools": None,  # deliberate: no agent tool deletes a library (KB-S07, option 1)
               "mcp": None, "tui": None},
    "status": {"web": ("GET", "/api/kb/jobs/{job_id}"), "chat": ["status"],
               "tools": None,  # a crawl the agent starts reports its own outcome
               "mcp": None, "tui": None},
}

ABSENT_REASONS = {
    "tui": "The TUI has no Knowledge panel; the docs say so (tests/test_knowledge_docs_claims.py).",
}


def _web_routes() -> set[tuple[str, str]]:
    from kazma_ui.kb_api import create_kb_router

    return {(m, r.path) for r in create_kb_router().routes for m in r.methods}


def _chat_subcommands() -> set[str]:
    from tests.test_slash_help_and_menu import kb_handled_subcommands

    return kb_handled_subcommands((REPO / "kazma-gateway/kazma_gateway/agent_handler/commands.py").read_text(
        encoding="utf-8"))


def _registry():
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return registry


def _tool_names() -> set[str]:
    return {name for name in _registry()._tools if name.startswith("knowledge_")}


def _mcp_names(monkeypatch) -> set[str]:
    from kazma_core.mcp import server

    registry = _registry()
    monkeypatch.setattr("kazma_core.agent.tool_registry.get_tool_registry", lambda: registry)
    monkeypatch.delenv("KAZMA_MCP_TOOLS", raising=False)
    tools = server.build_tool_list(server.ApprovalPath(gated=False, reason="parity test"))
    return {t["name"] for t in tools if t["name"].startswith("knowledge_")}


def _tui_mentions_knowledge() -> bool:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "kazma-tui/**.py"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    return any("/api/kb" in (REPO / rel).read_text(encoding="utf-8") for rel in listed if (REPO / rel).exists())


def matrix_problems(matrix, *, web, chat, tools, mcp, tui) -> list[str]:
    problems = []
    for op, row in matrix.items():
        cell = row["web"]
        if cell is not None and tuple(cell) not in web:
            problems.append(f"{op}/web: {cell} is not a route")
        cell = row["chat"]
        if cell is not None and not set(cell) <= chat:
            problems.append(f"{op}/chat: {sorted(set(cell) - chat)} not handled")
        for surface, have in (("tools", tools), ("mcp", mcp)):
            cell = row[surface]
            if cell is not None and not set(cell) <= have:
                problems.append(f"{op}/{surface}: {sorted(set(cell) - have)} missing")
        if row["tui"] is not None or tui:
            problems.append(f"{op}/tui: the TUI is declared absent")
    declared_tools = {n for row in matrix.values() for n in (row["tools"] or [])}
    if tools - declared_tools:
        problems.append(f"tools not in the matrix: {sorted(tools - declared_tools)}")
    if {"delete", "remove"} & {part for n in tools for part in n.split("_")}:
        problems.append("an agent tool deletes a library, but the matrix says none does")
    return problems


def test_the_matrix_matches_every_surface(monkeypatch):
    problems = matrix_problems(MATRIX, web=_web_routes(), chat=_chat_subcommands(), tools=_tool_names(),
                               mcp=_mcp_names(monkeypatch), tui=_tui_mentions_knowledge())
    assert problems == []


def test_the_matrix_check_catches_drift(monkeypatch):
    """Negative control: a tool nobody declared, a route that went away, and
    the old /kb that never reached delete."""
    tools = _tool_names() | {"knowledge_delete_library"}
    web = _web_routes() - {("POST", "/api/kb/search")}
    problems = matrix_problems(MATRIX, web=web, chat={"", "list", "search", "status"}, tools=tools,
                               mcp=_mcp_names(monkeypatch), tui=False)
    assert "search/web: ('POST', '/api/kb/search') is not a route" in problems
    assert any(p.startswith("delete/chat") for p in problems)
    assert "tools not in the matrix: ['knowledge_delete_library']" in problems


# ── every surface refuses a multi-user install the same way ──────────────


@pytest.fixture()
def multi_user(monkeypatch):
    import kazma_core.security.platform_rbac as rbac

    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [{"username": "a"}])


def _call(tool, **kwargs):
    out = tool.func(**kwargs)
    return asyncio.run(out) if inspect.isawaitable(out) else out


TOOL_CALLS = {
    "knowledge_list_libraries": {},
    "knowledge_create_library": {"library_id": "docs", "name": "Docs"},
    "knowledge_ingest_url": {"library_id": "docs", "url": "https://docs.example.test/"},
    "knowledge_ingest_site": {"library_id": "docs", "seed_url": "https://docs.example.test/"},
    "knowledge_search": {"query": "webhooks"},
}


def test_every_tool_refuses_a_multi_user_install(multi_user):
    from kazma_core.stores.knowledge_lifecycle import MULTI_USER_REFUSAL

    registry = _registry()
    assert set(TOOL_CALLS) == _tool_names()
    for name, kwargs in TOOL_CALLS.items():
        assert MULTI_USER_REFUSAL in _call(registry.get_tool(name), **kwargs), name


def test_without_the_rule_a_tool_served_a_multi_user_install(multi_user, monkeypatch):
    """Negative control: the tools' rule switched off."""
    from kazma_core.agent.tool_builtins import knowledge as tools_module
    from kazma_core.stores.knowledge_lifecycle import MULTI_USER_REFUSAL

    monkeypatch.setattr(tools_module, "_refusal", lambda: None)
    out = _call(_registry().get_tool("knowledge_list_libraries"))
    assert MULTI_USER_REFUSAL not in out
