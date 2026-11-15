"""A tool offered in a read set never writes (2026-11-15).

A scheduled task booked with ``read`` tools and an A2A turn are offered the
read and safe tiers (``cron/capabilities.py``). ``crawl_site`` is read tier,
but saved every page it fetched into the workspace unless ``save`` was false
-- its default is true -- under a folder its caller chose: a "read-only" turn
wrote files. Now ``side_effects.READ_ONLY_ARGUMENTS`` names the arguments that
make a read tool write and the values that keep it from writing, and a read
set (``DeclaredToolExecutor(read_only=True)``) runs it with those values and
does not offer the arguments; the MCP server no longer calls such a tool
read-only.

The class: this file reads the code of every registered read- and safe-tier
tool -- its own function, the functions of its module it reaches, and one
step into any other of Kazma's functions it names -- for what it can write to
a file. A tool that can write must be in ``READ_ONLY_ARGUMENTS``, and each
write must sit under a test of one of its arguments there. A planted tool is
the gate's control; the executor without ``read_only`` is the behaviour's.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import textwrap
import types
from typing import Any

import pytest

from kazma_core.safety.side_effects import READ_ONLY_ARGUMENTS, read_only_arguments

_WRITE_METHODS = frozenset({"write_text", "write_bytes", "unlink", "rename", "touch", "symlink_to", "hardlink_to"})
_WRITE_FUNCTIONS = frozenset({
    ("os", "replace"), ("os", "rename"), ("os", "remove"), ("os", "unlink"), ("os", "link"), ("os", "symlink"),
    ("os", "open"), ("os", "makedirs"), ("shutil", "copy"), ("shutil", "copy2"), ("shutil", "copyfile"),
    ("shutil", "copyfileobj"), ("shutil", "move"), ("shutil", "copytree"), ("shutil", "rmtree"),
    ("tempfile", "mkstemp"), ("tempfile", "NamedTemporaryFile"),
})


def _mode_writes(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(set(node.value) & set("wax+"))


def write_primitive(call: ast.Call) -> str | None:
    """What *call* writes to a file, if it does."""
    func = call.func
    mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
    if isinstance(func, ast.Name):
        return f"open({ast.unparse(mode)})" if func.id == "open" and _mode_writes(mode) else None
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr in _WRITE_METHODS:
        return func.attr
    if func.attr in ("open", "fdopen"):
        first = call.args[0] if func.attr == "open" and call.args else mode
        if _mode_writes(first) or (func.attr == "fdopen" and _mode_writes(mode)):
            return f"{func.attr}(writes)"
    if isinstance(func.value, ast.Name) and (func.value.id, func.attr) in _WRITE_FUNCTIONS:
        return f"{func.value.id}.{func.attr}"
    return None


def _tree(fn: types.FunctionType) -> ast.AST | None:
    try:
        return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    except (OSError, TypeError, SyntaxError):
        return None


def _resolve(name: str, fn: types.FunctionType, tree: ast.AST) -> Any:
    """What *name* means in *fn*: an import inside it, a global, or a closure cell."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kazma"):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    try:
                        return getattr(importlib.import_module(node.module), alias.name, None)
                    except ImportError:
                        return None
    if name in fn.__globals__:
        return fn.__globals__[name]
    for cell_name, cell in zip(fn.__code__.co_freevars, fn.__closure__ or ()):
        if cell_name == name:
            try:
                return cell.cell_contents
            except ValueError:
                return None
    return None


def writes_of(fn: Any, *, _seen: set | None = None, _hops: int = 0) -> list[tuple[ast.AST, ast.AST, str]]:
    """``(tree, node, what)`` for each write *fn* can reach: *node* is where, in
    *fn*'s own source (*tree*), the write is made or the writing function named."""
    fn = inspect.unwrap(fn)
    seen = set() if _seen is None else _seen
    if not isinstance(fn, types.FunctionType) or fn in seen:
        return []
    seen.add(fn)
    tree = _tree(fn)
    if tree is None:
        return []
    found: list[tuple[ast.AST, ast.AST, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            what = write_primitive(node)
            if what:
                found.append((tree, node, f"{fn.__qualname__}: {what}"))
        elif isinstance(node, ast.Name) and node.id != fn.__name__:
            target = _resolve(node.id, fn, tree)
            if not isinstance(target, types.FunctionType) or not target.__module__.startswith("kazma"):
                continue
            same_module = target.__module__ == fn.__module__
            if not same_module and _hops >= 1:
                continue
            inner = writes_of(target, _seen=seen, _hops=_hops + (0 if same_module else 1))
            if inner:
                found.append((tree, node, f"{fn.__qualname__} -> {inner[0][2]}"))
    return found


def _guards(tree: ast.AST, node: ast.AST) -> set[str]:
    """The names tested by every ``if`` whose body holds *node*."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    names: set[str] = set()
    child, current = node, parents.get(node)
    while current is not None:
        if isinstance(current, ast.If | ast.IfExp | ast.While) and child is not current.test \
                and child not in getattr(current, "orelse", ()):
            names |= {n.id for n in ast.walk(current.test) if isinstance(n, ast.Name)}
        child, current = current, parents.get(current)
    return names


def findings(tools: dict[str, Any], declared: dict[str, dict[str, Any]]) -> list[str]:
    """Each read tool that can write where none of its declared arguments
    holds the write back."""
    out = []
    for name, func in sorted(tools.items()):
        for tree, node, what in writes_of(func):
            keep = set(declared.get(name, {}))
            if not keep & _guards(tree, node):
                out.append(f"{name}: {what} (line {getattr(node, 'lineno', '?')})")
    return out


def _read_tools() -> dict[str, Any]:
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.safety.hitl import get_tool_tier

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    tools = {name: tool.func for name, tool in registry._tools.items() if get_tool_tier(name) in ("read", "safe")}
    assert len(tools) > 40, "the registry the cron and A2A read sets are made from"
    return tools


def test_no_read_tool_can_write_except_through_a_declared_argument() -> None:
    found = findings(_read_tools(), READ_ONLY_ARGUMENTS)
    assert not found, (
        "a read- or safe-tier tool can write a file: give it a write tier, or declare the argument that "
        f"keeps it from writing in kazma_core.safety.side_effects.READ_ONLY_ARGUMENTS: {found}")


def test_every_declaration_is_a_read_tool_that_can_write_and_takes_the_argument() -> None:
    tools = _read_tools()
    for name, forced in READ_ONLY_ARGUMENTS.items():
        assert name in tools, f"{name}: not a registered read tool"
        params = inspect.signature(inspect.unwrap(tools[name])).parameters
        assert set(forced) <= set(params), f"{name} takes no {set(forced) - set(params)}"
        assert writes_of(tools[name]), f"{name} writes nothing: its declaration is stale"


def test_negative_control_the_gate_finds_a_planted_write() -> None:
    import linecache

    source = textwrap.dedent(inspect.cleandoc('''
        from pathlib import Path

        def _save(target, text):
            Path(target).write_text(text)

        def read_page(url, save=True):
            if save:
                _save("page.md", url)
            return url

        def read_plain(url):
            with open("notes.md", "a") as fh:
                fh.write(url)
            return url
    '''))
    file = "<kazma_planted>"
    linecache.cache[file] = (len(source), None, source.splitlines(keepends=True), file)
    code = compile(source, file, "exec")
    planted: dict[str, Any] = {"__name__": "kazma_planted"}
    exec(code, planted)
    tools = {"read_page": planted["read_page"], "read_plain": planted["read_plain"]}
    found = findings(tools, {})
    assert any(f.startswith("read_page:") for f in found) and any(f.startswith("read_plain:") for f in found)
    # Declared with the argument its write sits under, only the other is left.
    assert [f.split(":")[0] for f in findings(tools, {"read_page": {"save": False}})] == ["read_plain"]
    # crawl_site itself, declared with nothing: its save is found.
    from kazma_core.tools.web_research import crawl_site

    assert findings({"crawl_site": crawl_site}, {}), "crawl_site writes when save is true"


# ── the behaviour: a read set runs crawl_site without writing ───────────


def test_read_only_arguments_override_the_callers() -> None:
    assert read_only_arguments("crawl_site", {"start_url": "u", "save": True, "path_prefix": "x"}) == {
        "start_url": "u", "save": False, "path_prefix": "x"}
    assert read_only_arguments("web_search", {"query": "q"}) == {"query": "q"}


@pytest.fixture
def crawl(monkeypatch, tmp_path):
    from kazma_core.web_acquire.result import FetchResult

    wr = importlib.import_module("kazma_core.tools.web_research")

    async def public(*args: Any, **kwargs: Any) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async def fetch(url: str, **kwargs: Any) -> FetchResult:
        return FetchResult(ok=True, url=url, text="Complete article", links_complete=True)

    monkeypatch.setattr("kazma_core.web_acquire.dns_worker.validate_public_url", public)
    monkeypatch.setattr(wr, "_central_fetch", fetch)
    monkeypatch.setattr(wr, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(wr, "_safe_workspace_path", lambda name, source: tmp_path / name)

    class _Inner:
        async def execute(self, name: str, arguments: dict[str, Any] | None = None, *a: Any, **k: Any) -> Any:
            return {"content": await wr.crawl_site(**(arguments or {}))}

    return tmp_path, _Inner()


@pytest.mark.parametrize("read_only, files", [(True, 0), (False, 1)])
def test_a_read_set_crawls_without_writing(crawl, read_only, files) -> None:
    """``read_only=False`` is the control: the executor as it was writes the page."""
    from kazma_core.agent_runner import DeclaredToolExecutor

    workspace, inner = crawl
    executor = DeclaredToolExecutor(inner, allowed=["crawl_site"], read_only=read_only)
    out = asyncio.run(executor.execute("crawl_site", {
        "start_url": "https://docs.example/", "max_pages": 1, "delay_ms": 0, "save": True,
        "path_prefix": "planted", "respect_robots": False}))
    assert "Crawl index" in out["content"]
    assert len(list(workspace.rglob("*.md"))) == files


def test_the_cron_read_graph_offers_crawl_site_without_save() -> None:
    from unittest.mock import patch

    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.agent_runner import DeclaredToolExecutor, KazmaAgent
    from kazma_core.cron.capabilities import cron_fire_graph

    class _Agent:
        def __init__(self) -> None:
            self.tools = LocalToolRegistry(include_builtins=True)
            self.llm = self.cost_breaker = self.authority = self.tracer = object()
            self.system_prompt = "sys"
            self._snapshot_recorder = None
            self.config = type("C", (), {"raw": {}})()

        def build_child_graph(self, **kw: Any) -> Any:
            return KazmaAgent.build_child_graph(self, **kw)

    for capabilities, read_only in (("read", True), ("read_write", False)):
        with patch("kazma_core.agent.graph_builder.build_supervisor_graph", return_value=object()) as build, \
                patch("kazma_core.safety.hitl.get_hitl_config", return_value={"enabled": True}):
            cron_fire_graph(_Agent(), capabilities=capabilities)
        kwargs = build.call_args.kwargs
        executor = kwargs["tool_executor"]
        assert isinstance(executor, DeclaredToolExecutor) and executor.read_only is read_only
        (crawl,) = [d for d in kwargs["tool_definitions"] if d.get("function", d).get("name") == "crawl_site"]
        offered = set(crawl.get("function", crawl)["parameters"]["properties"])
        assert ("save" in offered) is not read_only


def test_mcp_does_not_call_a_tool_that_writes_when_asked_read_only() -> None:
    from kazma_core.mcp import server

    assert server._read_only("web_search") is True
    assert server._read_only("crawl_site") is False
