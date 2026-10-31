"""A crawl seeded on a private network is refused at admission (2026-10-15).

Found 2026-10-14: a crawl seeded with ``http://127.0.0.1/...`` was admitted
-- its library and its journal row created -- and the crawl then refused its
own seed before discovery, so the library stayed empty and the page showed
no refusal. Every surface that starts a crawl now meets the address check
acquisition makes (``web_acquire.dns_worker.validate_public_url``, every
address the host resolves to, in the owned resolver child) before a library
or a journal row exists: ``security_blocked`` for a private, loopback or
link-local address, ``connect_error`` (transient) for a name that resolves
to nothing. A single-page ingest already checked the address before it
created its library; it is held here on every surface too.

No network: literal addresses are judged without a lookup, and a name is
resolved by a stand-in for the resolver.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

from tests._tool_answer import answer_now
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core import config_store
from kazma_core.security import ssrf
from kazma_core.stores import knowledge as kb_module
from kazma_core.stores import knowledge_index as ki
from kazma_core.stores import knowledge_ingest
from kazma_core.web_acquire import crawl_jobs
from kazma_gateway.agent_handler import commands
from kazma_gateway.gateway import IncomingMessage
from kazma_ui import kb_api

REPO = Path(__file__).resolve().parents[1]

#: Seeds acquisition refuses, each a different way: loopback, a private
#: range, the cloud metadata address, IPv6 loopback, and a name whose every
#: address is private (resolved by the stand-in below).
PRIVATE_SEEDS = [
    "http://127.0.0.1/docs",
    "http://10.0.0.5:8080/wiki",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/docs",
    "http://intranet.corp.test/docs",
]


class _NoVectors:
    available = False
    identity = ""

    def drop(self):
        return False

    def query(self, *a, **k):
        return []


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    journal = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: journal)
    monkeypatch.setattr(ki.KnowledgeIndex, "_vector_store_for", lambda self, library_id: _NoVectors())
    monkeypatch.setattr(ki.KnowledgeIndex, "_open_vector_store", lambda self, library_id: _NoVectors())
    ki.reset_knowledge_index()
    store = kb_module.KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    monkeypatch.setattr(kb_module, "_knowledge_store", store)
    # The resolver: an intranet name resolves to a private address, a public
    # one to a public address, anything else to nothing.
    names = {"intranet.corp.test": ["10.1.2.3"], "docs.public.test": ["93.184.216.34"]}
    monkeypatch.setattr(ssrf, "_resolve_host_ips", lambda host: names.get(host, []))
    replies: list[str] = []

    async def reply(msg, session_store, manager, thread_id, text):
        replies.append(text)

    monkeypatch.setattr(commands, "_send_model_reply", reply)
    app = FastAPI()
    app.include_router(kb_api.create_kb_router())
    yield SimpleNamespace(store=store, journal=journal, client=TestClient(app), replies=replies)
    ki.reset_knowledge_index()
    store.close()
    journal.close()


def _nothing_left(kb, library: str) -> None:
    assert kb.store.get_library(library) is None, "no library is created for a refused seed"
    assert not [job for job in crawl_jobs.inspect_jobs() if job["library_id"] == library], \
        "no journal row is created for a refused seed"


def _say(text: str) -> None:
    msg = IncomingMessage(platform="telegram", sender_id="telegram:1", text=text)
    assert asyncio.run(commands._try_kb_command(msg, None, None, "th-1"))


def _tools():
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return registry


# ── every surface that starts a crawl ─────────────────────────────────────


@pytest.mark.parametrize("seed", PRIVATE_SEEDS)
def test_the_web_crawl_refuses_a_private_seed(kb, seed):
    answer = kb.client.post("/api/kb/ingest", json={"library_id": "docs", "url": seed, "mode": "site"})
    body = answer.json()
    assert answer.status_code == 422 and body["code"] == "security_blocked", body
    assert body["transient"] is False
    _nothing_left(kb, "docs")


def test_the_web_refresh_refuses_a_private_seed(kb):
    kb.store.create_library("docs", "Docs", seed_url="http://127.0.0.1/docs")
    answer = kb.client.post("/api/kb/libraries/docs/refresh")
    assert answer.status_code == 422 and answer.json()["code"] == "security_blocked"
    assert not crawl_jobs.inspect_jobs()


def test_a_name_that_resolves_nowhere_is_a_transient_connect_error(kb):
    answer = kb.client.post("/api/kb/ingest", json={"library_id": "docs", "url": "https://gone.invalid/",
                                                    "mode": "site"})
    body = answer.json()
    assert body["code"] == "connect_error" and body["transient"] is True, body
    _nothing_left(kb, "docs")


@pytest.mark.parametrize("seed", PRIVATE_SEEDS[:2])
def test_the_chat_crawl_refuses_a_private_seed(kb, seed):
    _say(f"/kb crawl docs {seed}")
    assert kb.replies[-1].startswith("⚠️ The crawl was refused:"), kb.replies
    _nothing_left(kb, "docs")


def test_the_chat_refresh_refuses_a_private_seed(kb):
    kb.store.create_library("docs", "Docs", seed_url="http://127.0.0.1/docs")
    _say("/kb refresh docs")
    assert kb.replies[-1].startswith("⚠️ The refresh was refused:"), kb.replies
    assert not crawl_jobs.inspect_jobs()


@pytest.mark.parametrize("seed", PRIVATE_SEEDS[:2])
def test_the_agent_tool_refuses_a_private_seed(kb, seed):
    tool = _tools().get_tool("knowledge_ingest_site")
    out = answer_now(tool.func, library_id="docs", seed_url=seed, max_pages=2)
    assert out.startswith("Error:") and "crawl seed refused" in out, out
    _nothing_left(kb, "docs")


# ── a single page, on every surface ───────────────────────────────────────


def test_a_page_on_a_private_address_is_refused_on_every_surface(kb):
    seed = "http://127.0.0.1/page"
    answer = kb.client.post("/api/kb/ingest", json={"library_id": "web", "url": seed, "mode": "page"})
    assert answer.status_code == 422 and answer.json()["library_created"] is False
    _say(f"/kb add chat {seed}")
    assert kb.replies[-1].startswith("⚠️"), kb.replies
    out = answer_now(_tools().get_tool("knowledge_ingest_url").func, library_id="tool", url=seed)
    assert out.startswith("Error:"), out
    for library in ("web", "chat", "tool"):
        _nothing_left(kb, library)


# ── the control: a public seed is admitted, and the old admission ──────────


def test_a_public_seed_is_admitted(kb, monkeypatch):
    """Control: the check refuses private seeds, not crawls."""
    async def crawl(library_id, url, *, max_pages=None, job_id=None, _prepared=None, **kw):
        _prepared[0].finish("completed")
        yield SimpleNamespace(phase="done")

    monkeypatch.setattr(knowledge_ingest, "ingest_site", crawl)
    answer = kb.client.post("/api/kb/ingest", json={"library_id": "docs", "url": "https://docs.public.test/",
                                                    "mode": "site"})
    assert answer.status_code == 200 and answer.json()["ok"] is True, answer.json()
    assert kb.store.get_library("docs") is not None


def test_the_old_admission_left_an_empty_library(kb, monkeypatch):
    """Negative control: without the address check at admission (the code
    before 2026-10-15) the private seed is admitted, its library created,
    and the crawl fails on the seed."""
    async def old_admission(seed_url):
        return None

    async def crawl(library_id, url, *, max_pages=None, job_id=None, _prepared=None, **kw):
        _prepared[0].finish("failed")  # what the crawl did on its seed
        yield SimpleNamespace(phase="error")

    monkeypatch.setattr(knowledge_ingest, "_admit_public_seed", old_admission)
    monkeypatch.setattr(knowledge_ingest, "ingest_site", crawl)
    answer = kb.client.post("/api/kb/ingest", json={"library_id": "docs", "url": "http://127.0.0.1/docs",
                                                    "mode": "site"})
    assert answer.status_code == 200 and answer.json()["ok"] is True
    library = kb.store.get_library("docs")
    assert library is not None and library["chunk_count"] == 0


# ── class gate: every crawl and ingest starter, admission first ───────────

#: Each function of product code that starts a crawl or a page ingest, and
#: how its library comes to exist. ``admission``: it calls
#: ``_prepare_ingest_site`` (the seed's checks, the address check last, then
#: the journal row) before any ``create_library``, and hands the admitted job
#: to ``ingest_site(_prepared=...)``. ``page``: ``ingest_url`` checks the
#: address and fetches the page before it creates the library, and the
#: caller creates none. ``crawl``: the background half of an admission
#: starter, running the job it admitted.
STARTERS = {
    ("kazma-ui/kazma_ui/kb_api.py", "_start_site"): "admission",
    ("kazma-ui/kazma_ui/kb_api.py", "_run"): "crawl",
    ("kazma-ui/kazma_ui/kb_api.py", "_ingest_page"): "page",
    ("kazma-gateway/kazma_gateway/agent_handler/commands.py", "_kb_run_crawl"): "admission",
    ("kazma-gateway/kazma_gateway/agent_handler/commands.py", "_run"): "crawl",
    ("kazma-gateway/kazma_gateway/agent_handler/commands.py", "_try_kb_command"): "page",
    ("kazma-core/kazma_core/agent/tool_builtins/knowledge.py", "knowledge_ingest_site"): "admission",
    ("kazma-core/kazma_core/agent/tool_builtins/knowledge.py", "knowledge_ingest_url"): "page",
    # The daily crawl check (its private store): tests/test_crawl_canary.py
    # holds its refusal of a private seed.
    ("kazma-core/kazma_core/web_acquire/canary.py", "crawl"): "admission",
}
_STARTER_CALLS = ("_prepare_ingest_site", "ingest_site", "ingest_url")


def _called(node: ast.Call) -> str:
    return getattr(node.func, "id", getattr(node.func, "attr", ""))


def _starters(sources: dict[str, str]) -> dict[tuple[str, str], list[str]]:
    """``(file, innermost function) -> problems`` for every product
    function that calls a crawl or ingest starter."""
    found: dict[tuple[str, str], list[str]] = {}
    for rel, text in sources.items():
        tree = ast.parse(text)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nested = {id(n) for inner in ast.walk(fn) if inner is not fn
                      and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                      for n in ast.walk(inner)}
            own = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and id(n) not in nested]
            if not any(_called(c) in _STARTER_CALLS for c in own):
                continue
            kind = STARTERS.get((rel, fn.name), "undeclared")
            problems = [] if kind != "undeclared" else ["undeclared starter"]
            every = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
            creates = [c.lineno for c in every if _called(c) == "create_library"]
            admits = [c.lineno for c in every if _called(c) == "_prepare_ingest_site"]
            if kind == "admission":
                if not admits:
                    problems.append("starts a crawl without _prepare_ingest_site")
                elif creates and min(creates) < min(admits):
                    problems.append("creates the library before the admission")
            if kind == "page" and creates:
                problems.append("a page ingest creates its library itself, before the fetch")
            for call in every:
                if _called(call) == "ingest_site" and not any(k.arg == "_prepared" for k in call.keywords):
                    problems.append("ingest_site without the admitted job (_prepared=)")
            found[(rel, fn.name)] = problems
    return found


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "kazma-*/**.py"], cwd=REPO, capture_output=True,
                            text=True, check=True).stdout.split()
    return {rel: (REPO / rel).read_text(encoding="utf-8") for rel in listed
            if (REPO / rel).exists() and "_tests/" not in rel and "/tests/" not in rel
            and not rel.endswith("stores/knowledge_ingest.py")}  # the starters' own module


def test_every_crawl_starter_is_admitted_before_its_library_exists():
    found = _starters(_product_sources())
    assert set(found) == set(STARTERS), (
        "a surface starts a crawl or an ingest: declare it in STARTERS and test its refusal of a "
        f"private seed -- found {sorted(set(found) - set(STARTERS))}, gone {sorted(set(STARTERS) - set(found))}")
    assert all(not problems for problems in found.values()), found


def test_the_gate_sees_a_library_created_before_admission():
    """Negative control: the shape before KB-S09 (the library created first)
    and a crawl started without its admitted job."""
    planted = {"kazma-ui/kazma_ui/kb_api.py": (
        "async def _start_site(lib_id, url):\n"
        "    store.create_library(lib_id, name=lib_id, seed_url=url)\n"
        "    prepared = await _prepare_ingest_site(lib_id, url)\n"
        "    async for u in ingest_site(lib_id, url):\n"
        "        pass\n")}
    [problems] = _starters(planted).values()
    assert "creates the library before the admission" in problems
    assert "ingest_site without the admitted job (_prepared=)" in problems
    new = {"kazma-ui/kazma_ui/new_surface.py": "async def crawl(u):\n    await ingest_url('x', u)\n"}
    assert _starters(new) == {("kazma-ui/kazma_ui/new_surface.py", "crawl"): ["undeclared starter"]}
