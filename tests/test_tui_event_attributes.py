"""Every attribute a TUI handler reads from its event exists on that event.

The Traces tab read ``event.coordinate`` from ``DataTable.RowHighlighted``
and ``RowSelected``, which carry ``cursor_row``: with any trace to show, the
first highlight raised AttributeError and took the app down, and no test saw
it because the TUI's tests had no traces (2026-10-01). Such a handler fails
only when its event fires with data, so this reads the source instead: for
every function in kazma_tui whose parameter is annotated with a Textual
message class, each ``param.attr`` it reads must be an attribute of that
class -- one it defines, a dataclass field, or a ``self.<attr> =`` its
``__init__`` (or a base's) assigns, read from Textual's own source.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from pathlib import Path

from textual.message import Message

REPO = Path(__file__).resolve().parents[1]
TUI = REPO / "kazma-tui" / "kazma_tui"


def message_attributes(cls: type) -> set[str]:
    attrs = set(dir(cls))
    for klass in cls.__mro__:
        attrs.update(getattr(klass, "__dataclass_fields__", {}))
        attrs.update(klass.__dict__.get("__annotations__", {}))
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        try:
            source = textwrap.dedent(inspect.getsource(init))
        except (OSError, TypeError):
            continue
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                    and isinstance(node.value, ast.Name) and node.value.id == "self"):
                attrs.add(node.attr)
    return attrs


def unknown_event_attributes(source: str, namespace: dict, where: str) -> list[str]:
    """``where:line func: param.attr (Class)`` for each read the class lacks."""
    found = []
    for func in ast.walk(ast.parse(source)):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in [*func.args.args, *func.args.kwonlyargs]:
            if arg.annotation is None:
                continue
            try:
                cls = eval(ast.unparse(arg.annotation), dict(namespace))  # noqa: S307 - our own source
            except Exception:  # noqa: BLE001 - not a class we can resolve: not an event
                continue
            if not (isinstance(cls, type) and issubclass(cls, Message)):
                continue
            known = message_attributes(cls)
            for node in ast.walk(func):
                if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                        and isinstance(node.value, ast.Name) and node.value.id == arg.arg
                        and node.attr not in known):
                    found.append(f"{where}:{node.lineno} {func.name}: "
                                 f"{arg.arg}.{node.attr} ({cls.__qualname__})")
    return found


def test_tui_handlers_read_only_what_their_events_carry():
    problems = []
    for path in sorted(TUI.rglob("*.py")):
        rel = path.relative_to(TUI.parent).with_suffix("")
        module = importlib.import_module(".".join(rel.parts))
        problems += unknown_event_attributes(path.read_text(encoding="utf-8"), vars(module),
                                             path.relative_to(REPO).as_posix())
    assert problems == []


def test_a_missing_event_attribute_is_found():
    """The negative control: the Traces tab's handler as it was."""
    from textual.widgets import DataTable, Input

    source = (
        "def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):\n"
        "    row = event.coordinate.row\n"
        "    ok = event.cursor_row, event.row_key, event.data_table\n"
        "def on_input_changed(self, event: Input.Changed):\n"
        "    value = event.value, event.input.id\n"
        "def not_a_handler(self, n: int):\n"
        "    return n.bit_length()\n"
    )
    assert unknown_event_attributes(source, {"DataTable": DataTable, "Input": Input}, "x") == [
        "x:2 on_data_table_row_highlighted: event.coordinate (DataTable.RowHighlighted)",
    ]
