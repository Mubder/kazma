"""What a route's endpoint can reach in its own module, read from source once.

Two route gates (``tests/test_install_wide_writes.py``,
``tests/test_request_body_limits.py``) ask, for every route of the built app,
the endpoint's source plus every function of its module it calls, to any
depth. Each re-parsed the whole module for every route, about 600 times
(``settings.py`` alone is thousands of lines), and on CI's runner a single
test passed pytest's 120-second limit and took its process down (2026-11-15).
A module is parsed once here, with each function's source and the names it
calls.
"""

from __future__ import annotations

import ast
import functools
import inspect
import sys
import textwrap
from typing import Any

__all__ = ["reach"]

_Def = tuple[str, frozenset[str]]


def _called_names(node: ast.AST) -> frozenset[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name:
                names.add(name)
    return frozenset(names)


@functools.lru_cache(maxsize=None)
def _module_defs(module_name: str) -> dict[str, list[_Def]]:
    """Every function of *module_name*: its source and the names it calls."""
    module = sys.modules.get(module_name)
    if module is None:
        return {}
    try:
        text = inspect.getsource(module)
        tree = ast.parse(text)
    except (OSError, TypeError, SyntaxError):
        return {}
    defs: dict[str, list[_Def]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = ast.get_source_segment(text, node) or ""
            defs.setdefault(node.name, []).append((segment, _called_names(node)))
    return defs


def reach(endpoint: Any) -> str:
    """The endpoint's source and every function of its module it calls, to
    any depth, joined by newlines (``""`` when its source cannot be read)."""
    try:
        source = textwrap.dedent(inspect.getsource(endpoint))
    except (OSError, TypeError):
        return ""
    try:
        calls = _called_names(ast.parse(source))
    except SyntaxError:
        return source
    defs = _module_defs(getattr(endpoint, "__module__", "") or "")
    seen = {getattr(endpoint, "__name__", "")}
    texts, todo = [source], [calls]
    while todo:
        for name in todo.pop():
            if name in seen or name not in defs:
                continue
            seen.add(name)
            for segment, called in defs[name]:
                texts.append(segment)
                todo.append(called)
    return "\n".join(texts)
