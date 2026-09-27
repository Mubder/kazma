"""The shutdown hook closes what exists and builds nothing.

Live 2026-09-27: ``_on_shutdown`` called ``get_session_manager()`` to close
the chat-session manager. A server nobody had opened the web UI on had none,
so the accessor BUILT one -- every chat session loaded from Postgres, on the
event loop -- and the guard's 60 s grace ran out first: the server was killed
mid-shutdown and the owner paged. The model registry's and the message bus's
accessors build on a miss too.

Every accessor ``KazmaAppBuilder._on_shutdown`` calls is found from the source
and resolved to its definition through the hook's own imports; one that can
build -- its body assigns a module global -- fails. The ``peek_*`` accessors
return what exists, or None.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "kazma-ui" / "kazma_ui" / "app.py"
PACKAGES = {
    "kazma_core": REPO / "kazma-core",
    "kazma_ui": REPO / "kazma-ui",
    "kazma_gateway": REPO / "kazma-gateway",
}


def _method(src: str, cls: str, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                    return item
    raise AssertionError(f"{cls}.{name} not found")


def _definition(module: str, name: str) -> ast.FunctionDef | None:
    top = module.split(".")[0]
    if top not in PACKAGES:
        return None
    path = PACKAGES[top] / (module.replace(".", "/") + ".py")
    if not path.is_file():
        path = PACKAGES[top] / module.replace(".", "/") / "__init__.py"
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def accessors_that_build(hook: ast.AST) -> list[str]:
    """``module.name`` of each accessor the hook calls whose body can build
    what it returns (assigns a module global)."""
    imported: dict[str, tuple[str, str]] = {}
    for node in ast.walk(hook):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported[alias.asname or alias.name] = (node.module, alias.name)
    found = []
    for node in ast.walk(hook):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        local = node.func.id
        if local not in imported:
            continue
        module, name = imported[local]
        if not name.lstrip("_").startswith(("get_", "peek_")):
            continue  # an action (close_*, stop_*) may reset its globals; it builds nothing
        fn = _definition(module, name)
        if fn is not None and any(isinstance(n, ast.Global) for n in ast.walk(fn)):
            found.append(f"{module}.{name}")
    return sorted(set(found))


def test_the_shutdown_hook_builds_nothing():
    hook = _method(APP.read_text(encoding="utf-8"), "KazmaAppBuilder", "_on_shutdown")
    assert accessors_that_build(hook) == [], (
        "_on_shutdown calls an accessor that builds on a miss; use the peek_* one"
    )


def test_the_gate_sees_the_hooks_accessors():
    """Not blind: the hook's accessors are found and resolved."""
    hook = _method(APP.read_text(encoding="utf-8"), "KazmaAppBuilder", "_on_shutdown")
    names = {
        node.func.id
        for node in ast.walk(hook)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"peek_session_manager", "peek_model_registry", "peek_message_bus"} <= names


def test_negative_control_the_old_hook_is_caught():
    src = '''
class KazmaAppBuilder:
    async def _on_shutdown(self):
        from kazma_ui.session_manager import get_session_manager
        from kazma_core.model_registry import get_model_registry
        from kazma_core.swarm.bus import get_message_bus
        from kazma_core.cron.scheduler import get_cron_scheduler
        from kazma_core.llm_ledger_alias import close_llm_ledger

        get_session_manager().close()
        await get_model_registry().close()
        get_message_bus()
        get_cron_scheduler()
'''
    hook = _method(src, "KazmaAppBuilder", "_on_shutdown")
    assert accessors_that_build(hook) == [
        "kazma_core.model_registry.get_model_registry",
        "kazma_core.swarm.bus.get_message_bus",
        "kazma_ui.session_manager.get_session_manager",
    ]


def test_peek_accessors_never_build(monkeypatch):
    from kazma_core import model_registry
    from kazma_core.swarm import bus
    from kazma_ui import session_manager

    monkeypatch.setattr(session_manager, "_session_manager", None)
    monkeypatch.setattr(model_registry, "_registry", None)
    monkeypatch.setattr(bus, "_bus", None)
    assert session_manager.peek_session_manager() is None
    assert model_registry.peek_model_registry() is None
    assert bus.peek_message_bus() is None
    assert session_manager._session_manager is None  # noqa: SLF001
    assert model_registry._registry is None  # noqa: SLF001
    assert bus._bus is None  # noqa: SLF001
