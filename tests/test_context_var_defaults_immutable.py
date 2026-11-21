"""A ContextVar's default is never an object a caller can change.

SonarCloud review, 2026-11-21 (python:S8508). ``ContextVar(..., default={})``
evaluates the ``{}`` once: every context that never set the variable reads
the SAME dict, so a name written into what ``.get()`` returned outside a
recall would be read by every later recall in every thread and task. Recall's
member names (``memory.recall._ENTRY_NAMES``) were declared that way; nothing
wrote into the default yet, so it was a leak waiting for its first writer.
The default is now None, read through ``recall._entry_names()``, which gives
an empty mapping that cannot be written.

The gate reads every ``ContextVar(...)`` in the product packages and
``scripts/``: a ``default=`` that is a dict, list or set (literal,
comprehension or constructor call) fails it. The behavioural half writes into
what recall reads outside a recall and checks another context never sees it;
the old declaration's shape is the negative control.
"""

from __future__ import annotations

import ast
import contextvars
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Calls that make a new mutable object each time they are evaluated: as a
#: default, evaluated once, they make one object every context shares.
_MUTABLE_CALLS = frozenset({
    "dict", "list", "set", "bytearray", "defaultdict", "OrderedDict", "Counter", "deque",
})
_MUTABLE_NODES = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)


def _files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", "kazma-*", "scripts"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [rel for rel in out.split("\0")
            if rel.endswith(".py") and "/tests/" not in rel and "_tests/" not in rel]


def _is_context_var(call: ast.Call) -> bool:
    func = call.func
    return (isinstance(func, ast.Name) and func.id == "ContextVar") or (
        isinstance(func, ast.Attribute) and func.attr == "ContextVar")


def _mutable(node: ast.AST) -> bool:
    if isinstance(node, _MUTABLE_NODES):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        return name in _MUTABLE_CALLS
    return False


def mutable_defaults(source: str, rel: str = "<source>") -> list[str]:
    """``file:line`` of every ContextVar whose default is a mutable object."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and _is_context_var(node)):
            continue
        default = next((kw.value for kw in node.keywords if kw.arg == "default"), None)
        if default is None and len(node.args) > 1:
            default = node.args[1]
        if default is not None and _mutable(default):
            found.append(f"{rel}:{node.lineno}")
    return found


def test_no_context_var_has_a_mutable_default():
    found = []
    for rel in _files():
        found += mutable_defaults((ROOT / rel).read_text(encoding="utf-8"), rel)
    assert not found, (
        "a ContextVar's default is one object every context shares; use None (or a tuple, a "
        f"frozenset, a MappingProxyType) and read it through a helper: {found}")


def test_control_the_old_recall_declaration_fails_the_gate():
    old = (
        "import contextvars\n"
        "_ENTRY_NAMES: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(\n"
        "    'kazma_recall_entry_names', default={})\n"
    )
    assert mutable_defaults(old) == ["<source>:2"]
    for shape in ("default=[]", "default=set()", "default=dict()", "default={k: 1 for k in ()}",
                  "default=collections.OrderedDict()"):
        assert mutable_defaults(f"from contextvars import ContextVar\nv = ContextVar('v', {shape})\n")
    for shape in ("default=None", "default=()", "default=frozenset()", "default=MappingProxyType({})",
                  "default=''"):
        assert not mutable_defaults(f"from contextvars import ContextVar\nv = ContextVar('v', {shape})\n")


def _plant(names) -> None:
    try:
        names["member_ali"] = "planted"
    except TypeError:
        pass  # what cannot be written cannot be planted


def test_a_name_written_outside_a_recall_never_reaches_another():
    from kazma_core.memory import recall

    row = {"subject": "member_ali", "predicate": "lives_in", "object": "doha"}
    contextvars.Context().run(lambda: _plant(recall._entry_names()))
    text = contextvars.Context().run(recall._format_belief_text, row)
    assert "planted" not in text
    assert text == "member ali lives in doha"
    # Inside a recall, its own names are read; after it, none are.
    ctx = contextvars.copy_context()

    def one_recall() -> str:
        token = recall._ENTRY_NAMES.set({"member_ali": "Ali"})
        try:
            return recall._format_belief_text(row)
        finally:
            recall._ENTRY_NAMES.reset(token)

    assert ctx.run(one_recall) == "Ali lives in doha"
    assert contextvars.Context().run(recall._format_belief_text, row) == "member ali lives in doha"


def test_control_the_old_default_carries_a_write_into_every_context():
    old: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("old_entry_names", default={})  # noqa: B039 -- the control
    contextvars.Context().run(lambda: _plant(old.get()))
    assert contextvars.Context().run(old.get) == {"member_ali": "planted"}
