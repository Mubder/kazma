"""Static gates that would each have caught a finding in the 2026-08-29 audit.

These sit alongside ``tests/test_imports.py`` in the pre-commit hook: fast,
AST-only, no app boot. Each gate closes a *class* of defect rather than one
instance, because every one of them was found by grepping the tree rather than
by a failing test.

1. ``test_no_blocking_db_driver_in_async``  — audit F-06.
   A synchronous ``sqlite3.connect`` inside ``async def`` pins the event loop
   that also serves every SSE and WebSocket stream.

2. ``test_no_bare_create_task``             — audit F-07.
   ``asyncio`` holds only a weak reference to a task, so a discarded
   ``create_task(...)`` result can be garbage-collected mid-run.
   ``kazma_core.background.spawn_background`` retains it.

3. ``test_every_registered_tool_has_a_tier`` — audit F-04.
   HITL default-denies anything it cannot classify, so an untiered tool would
   start prompting for approval on a read. This keeps ``TOOL_TIERS``
   exhaustive.

4. ``test_no_unfenced_web_tool_output``      — audit F-09.
   Tools returning remote-authored text must fence it as untrusted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

PRODUCT_DIRS = [
    REPO_ROOT / "kazma-core" / "kazma_core",
    REPO_ROOT / "kazma-ui" / "kazma_ui",
    REPO_ROOT / "kazma-gateway" / "kazma_gateway",
    REPO_ROOT / "kazma-cli" / "kazma_cli",
    REPO_ROOT / "kazma-tui" / "kazma_tui",
    REPO_ROOT / "kazma-skills" / "kazma_skills",
]


def _product_files() -> list[Path]:
    out: list[Path] = []
    for base in PRODUCT_DIRS:
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts or "_tests" in str(p) or "/tests/" in p.as_posix():
                continue
            out.append(p)
    return out


def _rel(p: Path) -> str:
    return p.relative_to(REPO_ROOT).as_posix()


def _dotted(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    if isinstance(func, ast.Name):
        return func.id
    return None


# ── 1. Blocking DB driver inside async def (F-06) ────────────────────────

BLOCKING_CALLS = {"sqlite3.connect"}

#: ``(file, function)`` pairs that are deliberately exempt, each with a reason.
BLOCKING_ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "kazma-core/kazma_core/memory/worker_bootstrap.py",
        "_handle_micro_consolidation",
    ): (
        "Interleaves an awaited LLM call with its SQLite work, so it cannot be "
        "offloaded wholesale. Its synchronous queries are a single indexed "
        "lookup by episode id."
    ),
}


def test_no_blocking_db_driver_in_async():
    """Synchronous DB drivers must not run on the event loop (audit F-06)."""
    offenders: list[str] = []
    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        stack: list[str | None] = []

        class Visitor(ast.NodeVisitor):
            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                stack.append(None)  # a sync def re-enters the threadpool
                self.generic_visit(node)
                stack.pop()

            def visit_Lambda(self, node: ast.Lambda) -> None:
                stack.append(None)
                self.generic_visit(node)
                stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                if stack and stack[-1] is not None:
                    name = _dotted(node.func)
                    if name in BLOCKING_CALLS:
                        key = (_rel(path), stack[-1])
                        if key not in BLOCKING_ALLOWLIST:
                            offenders.append(
                                f"{_rel(path)}:{node.lineno} async def "
                                f"{stack[-1]} calls {name}"
                            )
                self.generic_visit(node)

        Visitor().visit(tree)

    assert not offenders, (
        "Blocking DB call inside async def — this stalls the event loop shared "
        "by every SSE/WebSocket stream (audit F-06).\n"
        "Fix: drop `async` (FastAPI threadpools sync handlers), or wrap the "
        "blocking section in `await asyncio.to_thread(...)`.\n  "
        + "\n  ".join(offenders)
    )


# ── 2. Fire-and-forget asyncio tasks (F-07) ──────────────────────────────

def test_no_bare_create_task():
    """Background tasks must be retained via ``spawn_background`` (audit F-07)."""
    offenders: list[str] = []
    allowed_files = {
        "kazma-core/kazma_core/background.py",  # defines the helper
    }
    for path in _product_files():
        rel = _rel(path)
        if rel in allowed_files:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # A bare expression statement discards the task object, leaving the
            # loop's weak reference as the only one.
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            if _dotted(node.value.func) in ("asyncio.create_task", "asyncio.ensure_future"):
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "Fire-and-forget asyncio task: the event loop keeps only a weak "
        "reference, so this can be garbage-collected mid-run (audit F-07).\n"
        "Fix: `from kazma_core.background import spawn_background` and call "
        "`spawn_background(coro, name=...)`, or assign the task to a variable "
        "that outlives it.\n  " + "\n  ".join(offenders)
    )


# ── 3. Exhaustive HITL tool tiers (F-04) ─────────────────────────────────

def test_every_registered_tool_has_a_tier():
    """Every registered tool must be classified in ``TOOL_TIERS`` (audit F-04)."""
    pytest.importorskip("kazma_core.agent.tool_builtins")
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.safety.hitl import TOOL_TIERS

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    untiered = sorted(set(registry._tools) - set(TOOL_TIERS))

    assert not untiered, (
        "Tool registered with no entry in kazma_core.safety.hitl.TOOL_TIERS. "
        "HITL default-denies unclassified tools (audit F-04), so this would "
        "prompt for approval on every call.\n"
        "Fix: add each to TOOL_TIERS as 'read' / 'write' / 'danger'; anything "
        "destructive, outbound, or credential-touching is 'danger' and also "
        "belongs in CANONICAL_DANGER_TOOLS.\n  " + ", ".join(untiered)
    )


def test_danger_tools_are_gated():
    """Every 'danger' tier tool must actually require approval (audit F-04)."""
    from kazma_core.safety.hitl import TOOL_TIERS, get_hitl_config, requires_approval

    cfg = get_hitl_config({})
    ungated = sorted(
        name
        for name, tier in TOOL_TIERS.items()
        if tier == "danger" and not requires_approval(name, cfg)
    )
    assert not ungated, (
        "Tools tiered 'danger' that do not require HITL approval:\n  "
        + ", ".join(ungated)
    )


# ── 4. Untrusted tool output must be fenced (F-09) ───────────────────────

FENCED_TOOL_MODULES = [
    "kazma-core/kazma_core/tools/read_url.py",
    "kazma-core/kazma_core/tools/web_search.py",
]


@pytest.mark.parametrize("rel", FENCED_TOOL_MODULES)
def test_no_unfenced_web_tool_output(rel):
    """Tools returning remote-authored text must fence it (audit F-09)."""
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "fence_untrusted" in src or "format_untrusted_block" in src, (
        f"{rel} returns fetched web content but never wraps it in an untrusted "
        "fence. A fetched page is the largest source of attacker-controlled "
        "text in the system (audit F-09).\n"
        "Fix: `from kazma_core.safety.prompt_fence import fence_untrusted` and "
        "return `fence_untrusted(text, source=...)`."
    )
