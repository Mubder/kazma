"""Every tool that changes something refuses before acting with
``RefusedBeforeEffect``, never with a returned error.

A mutating tool's returned error is read as "failed after invocation, its
effects unknown": the effect journal records it, ends the turn and forbids a
repeat. That is right for a failure inside the action (a send that broke half
way) and wrong for a refusal made before it (a missing argument, a policy, an
account not connected), which changed nothing. Live 2026-10-18 it ended two
turns in a row on Telegram: an approved ``shell_exec`` the host-shell policy
refused, then an approved ``browser_navigate`` this host cannot launch -- the
first fixed tool by tool, the second the same bug in the next tool.

The rule this file holds, for every registered tool that is not read-only:
an error the tool returns OUTSIDE an exception handler is a refusal made
before anything ran, so it must be raised as ``RefusedBeforeEffect`` instead.
An error returned inside a handler (the action raised) stays a returned error
and stays uncertain. Delegating tools are checked in the functions they call
(``DELEGATES``).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
import textwrap
from typing import Any

import pytest

from kazma_core.agent.tool_builtins import register_builtin_tools
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.safety.side_effects import is_read_only

#: Tools whose body hands the work to another function: that function holds
#: their refusals. "module:qualname", or ".name" for a function the tool's
#: closure holds (the memory tools' ``_mem_*``), resolved at test time.
DELEGATES: dict[str, list[str]] = {
    "computer_use": ["kazma_core.tools.computer_use:computer_use"],
    # Two hops down: the tool awaits file_apply_patch, which runs the sync
    # body in a thread. Missing from this map, its refusals were returned as
    # strings and failed the turn (audit 2026-10-26 AUD-008).
    "file_apply_patch": [
        "kazma_core.tools.file_apply_patch:file_apply_patch",
        "kazma_core.tools.file_apply_patch:_file_apply_patch_sync",
    ],
    "file_apply_patch_set": ["kazma_core.tools.file_apply_patch:file_apply_patch_set"],
    "file_write": ["kazma_core.tools.file_write:file_write"],
    "git_pull": ["kazma_skills.native.git_github_manager.tools:_git_sync"],
    "git_push": ["kazma_skills.native.git_github_manager.tools:_git_sync"],
    "memory_delete_entity": ["._mem_delete_entity"],
    "memory_invalidate": ["._mem_invalidate"],
    "memory_link_entities": ["._mem_link_entities"],
    "memory_merge_entities": ["._mem_merge_entities"],
    "memory_purge_empty_entities": ["._mem_purge_empty_entities"],
    "python_exec": ["kazma_core.tools.code_exec:python_exec"],
    "request_path_access": ["kazma_core.agent.tool_builtins.filesystem:_request_path_access_sync"],
}

_REFUSAL_HEADS = ("Error", "Safety:", "BLOCKED")
_REFUSAL_NAME = re.compile(r"err|refus|denied|blocked", re.IGNORECASE)


def _registry() -> LocalToolRegistry:
    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return registry


def _mutating_tools() -> dict[str, Any]:
    registry = _registry()
    return {name: tool.func for name, tool in sorted(registry._tools.items()) if not is_read_only(name)}


def _head(node: ast.AST) -> str:
    """The literal text a returned value starts with, if it starts with one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        return _head(node.values[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _head(node.left)
    return ""


def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _says_not_ok(node: ast.AST) -> bool:
    for inner in ast.walk(node):
        if isinstance(inner, ast.Dict):
            for key, value in zip(inner.keys, inner.values):
                if (isinstance(key, ast.Constant) and key.value == "ok"
                        and isinstance(value, ast.Constant) and value.value is False):
                    return True
    return False


def returned_refusals(func: Any) -> list[tuple[int, str]]:
    """(line, text) of each error the function returns outside an exception
    handler -- in its own body, not in functions nested inside it."""
    func = inspect.unwrap(func)
    source = textwrap.dedent(inspect.getsource(func))
    start = inspect.getsourcelines(func)[1]
    tree = ast.parse(source)
    root = tree.body[0]
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, in_handler: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
                continue
            if isinstance(child, ast.ExceptHandler):
                visit(child, True)
                continue
            if isinstance(child, ast.Return) and child.value is not None and not in_handler:
                head = _head(child.value)
                if head.startswith(_REFUSAL_HEADS):
                    found.append((child.lineno + start - 1, head[:80]))
                # A refusal a helper built ("scope_err", "refusal", "denied"):
                # the workspace and store guards hand theirs back this way.
                elif isinstance(child.value, ast.Name) and _REFUSAL_NAME.search(child.value.id):
                    found.append((child.lineno + start - 1, f"<{child.value.id}>"))
                # ... or a helper call that builds one ("denied_message(...)").
                elif isinstance(child.value, ast.Call) and _REFUSAL_NAME.search(_callee(child.value)):
                    found.append((child.lineno + start - 1, f"<{_callee(child.value)}()>"))
                # ... or a JSON answer saying {"ok": false}: the registry reads
                # it as a failed call too (tool_registry._json_reports_failure).
                elif _says_not_ok(child.value):
                    found.append((child.lineno + start - 1, '{"ok": false}'))
            visit(child, in_handler)

    visit(root, False)
    return found


def _resolve(spec: str, tool: Any = None) -> Any:
    import importlib

    if spec.startswith("."):
        return inspect.getclosurevars(inspect.unwrap(tool)).nonlocals[spec[1:]]
    module, _, qualname = spec.partition(":")
    obj: Any = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def returned_helpers(func: Any) -> list[Any]:
    """The module's own functions *func* returns the answer of (``return
    _output(result, "DOCX")``), outside an exception handler."""
    func = inspect.unwrap(func)
    root = ast.parse(textwrap.dedent(inspect.getsource(func))).body[0]
    found: list[Any] = []

    def visit(node: ast.AST, in_handler: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
                continue
            if isinstance(child, ast.ExceptHandler):
                visit(child, True)
                continue
            if isinstance(child, ast.Return) and child.value is not None and not in_handler:
                value = child.value.value if isinstance(child.value, ast.Await) else child.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    helper = func.__globals__.get(value.func.id)
                    if inspect.isfunction(helper) and helper.__module__ == func.__module__:
                        found.append(helper)
            visit(child, in_handler)

    visit(root, False)
    return found


def _refusals_through_helpers(func: Any, seen: set[Any] | None = None) -> list[tuple[int, str]]:
    """Refusals *func* returns, and those of the helpers whose answer it
    returns, to any depth. ``generate_docx`` returned ``_output(...)``, whose
    "Error: ..." the name-only rule could not see: live 2026-10-31 a broker
    outage was reported as "effects unknown, must not be repeated"."""
    seen = set() if seen is None else seen
    func = inspect.unwrap(func)
    if func in seen:
        return []
    seen.add(func)
    hits = returned_refusals(func)
    for helper in returned_helpers(func):
        hits += [(line, f"{helper.__name__}(): {text}")
                 for line, text in _refusals_through_helpers(helper, seen)]
    return hits


def _violations() -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for name, func in _mutating_tools().items():
        hits = _refusals_through_helpers(func)
        for spec in DELEGATES.get(name, []):
            hits += _refusals_through_helpers(_resolve(spec, func))
        if hits:
            out[name] = hits
    return out


def test_no_tool_that_changes_something_returns_a_refusal():
    violations = _violations()
    assert not violations, "\n".join(
        f"{name}: line {line}: {text!r}" for name, hits in violations.items() for line, text in hits)


def test_the_gate_sees_a_returned_refusal():
    """Negative control: the shape that ended the live turns."""

    async def tool(url: str) -> str:
        if not url:
            return "Error: No URL provided."
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            return f"Error: navigation failed — {exc}"  # inside the handler: allowed

    hits = returned_refusals(tool)
    assert [text for _, text in hits] == ["Error: No URL provided."]

    async def json_tool(post_id: str) -> str:
        if not post_id:
            return json.dumps({"ok": False, "error": "no id"})
        return json.dumps({"ok": True})

    assert [text for _, text in returned_refusals(json_tool)] == ['{"ok": false}']


def test_the_gate_lets_a_raised_refusal_through():
    async def tool(url: str) -> str:
        if not url:
            raise RefusedBeforeEffect("No URL provided.")
        return "ok"

    assert returned_refusals(tool) == []


def test_every_delegate_resolves():
    tools = _mutating_tools()
    for name, specs in DELEGATES.items():
        assert name in tools, f"{name} is not a registered tool that changes something"
        for spec in specs:
            assert callable(_resolve(spec, tools[name])), spec


_BROAD = {"Exception", "BaseException"}


def _caught(handler: ast.ExceptHandler) -> set[str] | None:
    """The names a handler catches; None for a bare ``except:``."""
    if handler.type is None:
        return None
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {n.id if isinstance(n, ast.Name) else getattr(n, "attr", "") for n in names}


def swallowed_refusals(func: Any) -> list[int]:
    """Lines raising ``RefusedBeforeEffect`` inside a ``try`` whose broad
    handler catches it first: the refusal comes back as a returned error, the
    uncertain shape again."""
    func = inspect.unwrap(func)
    source = textwrap.dedent(inspect.getsource(func))
    start = inspect.getsourcelines(func)[1]
    found: list[int] = []

    def broad(node: ast.Try) -> bool:
        for handler in node.handlers:
            caught = _caught(handler)
            if caught is None or caught & _BROAD:
                return True
            if "RefusedBeforeEffect" in caught:
                return False
        return False

    def walk(nodes: list[ast.stmt], guarded: bool) -> None:
        for node in nodes:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if isinstance(node, ast.Try):
                walk(node.body, guarded or broad(node))
                for handler in node.handlers:
                    walk(handler.body, guarded)
                walk(node.orelse, guarded)
                walk(node.finalbody, guarded)
                continue
            if (guarded and isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and _callee(node.exc) == "RefusedBeforeEffect"):
                found.append(node.lineno + start - 1)
            for field in ("body", "orelse", "handlers", "finalbody"):
                inner = getattr(node, field, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                    walk(inner, guarded)
            for case in getattr(node, "cases", []):
                walk(case.body, guarded)

    walk(ast.parse(source).body[0].body, False)
    return found


def test_no_refusal_is_caught_by_the_tools_own_broad_handler():
    tools = _mutating_tools()
    found = {}
    for name, func in tools.items():
        lines = swallowed_refusals(func)
        for spec in DELEGATES.get(name, []):
            lines += swallowed_refusals(_resolve(spec, func))
        if lines:
            found[name] = lines
    assert not found, found


def test_the_swallow_check_sees_a_broad_handler():
    """Negative control: raised inside ``try`` / ``except Exception``."""

    async def tool(url: str) -> str:
        try:
            if not url:
                raise RefusedBeforeEffect("No URL provided.")
            return "ok"
        except Exception as exc:
            return f"Error: {exc}"

    async def passed_through(url: str) -> str:
        try:
            if not url:
                raise RefusedBeforeEffect("No URL provided.")
            return "ok"
        except RefusedBeforeEffect:
            raise
        except Exception as exc:
            return f"Error: {exc}"

    assert len(swallowed_refusals(tool)) == 1
    assert swallowed_refusals(passed_through) == []


def _planted_output(result: Any) -> str:
    if not result:
        return "Error: the operation failed"
    return "done"


def _planted_tool(result: Any) -> str:
    return _planted_output(result)


def test_the_gate_follows_a_returned_helper():
    """Negative control: generate_docx's shape before 2026-10-31 -- the
    tool returns a helper's answer, and the helper returns the error."""
    assert returned_refusals(_planted_tool) == []  # the name-only rule missed it
    assert [text for _, text in _refusals_through_helpers(_planted_tool)] == [
        "_planted_output(): Error: the operation failed"]


@pytest.mark.parametrize("retryable,then", [(True, "call again in a few minutes"), (False, "change the request")])
def test_a_document_that_was_not_produced_is_an_ordinary_error(monkeypatch, retryable, then):
    """Live 2026-10-31: generate_docx while the document broker was down ended
    the turn as "effects unknown, must not be repeated" -- yet a failed
    operation deletes its export, and the same call worked once the broker
    was back."""
    from kazma_core.agent.tool_refusal import KnownEffectError
    from kazma_core.documents.models import DocumentResult
    from kazma_skills.native.document_generator import tools as generator

    async def generate(self, *args, **kwargs):
        return DocumentResult(ok=False, code="worker_isolation_unavailable",
                              message="The document broker does not answer", retryable=retryable)

    monkeypatch.setattr(generator.DocumentService, "generate", generate)
    with pytest.raises(KnownEffectError) as refused:
        asyncio.run(generator.generate_docx("Report", sections=[{"heading": "A", "body": "words"}]))
    text = refused.value.content()
    assert text.startswith("Error: no DOCX was written") and then in text
    assert "worker_isolation_unavailable" in text
