"""The chat stream's frames, the docs table and the package docstring agree.

``POST /api/chat/stream`` sends the frames ``kazma_ui/sse_chat`` emits and
every frame written to the thread's journal (``kazma_ui.delivery``: the
attached stream's live tail carries approvals decided elsewhere and other
tabs' turns). The docs table (``docs/docs/guide/api-and-extension-points.md``
section 3) still listed ``tool``/``args``/``is_error``, ``cost_usd`` and a
``message`` error field, and the docstring five frames of eighteen, long
after the code had moved on (found 2026-10-01).

From the source: every frame name, and the keys of every payload written
as a dict literal (directly or through a local variable). Every name must
have a table row and a docstring line, every literal key must be in its
row's fields, every row must name a frame the code sends, and the browser's
``dispatch`` must handle each one (``user_message`` is read from the
WebSocket). Negative controls: an undocumented frame, an undocumented
field and a stale row are each reported.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "docs" / "guide" / "api-and-extension-points.md"
PACKAGE = REPO / "kazma-ui" / "kazma_ui" / "sse_chat" / "__init__.py"
STREAMING_JS = REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "streaming.js"
AGENT_STORE_JS = REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "stores" / "agentStore.js"

#: Calls that send one frame: (function name, index of the event-name argument).
_FRAME_CALLS = {"emit_j": 0, "_sse_frame": 0, "_journal_fast_path": 1}
#: Fields the docs state for every journaled frame (the paragraph above the table).
_COMMON_FIELDS = {"seq", "turn_id", "replay", "capacity"}
#: Frames the chat page reads from its WebSocket, not from the SSE dispatcher.
_SOCKET_ONLY = {"user_message"}


def _sources() -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-ui/kazma_ui/*.py", "kazma-ui/kazma_ui/**/*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    return {f: (REPO / f).read_text(encoding="utf-8") for f in sorted(set(files)) if (REPO / f).is_file()}


def _dict_keys(node: ast.expr, func: ast.AST | None) -> set[str] | None:
    """The string keys of a payload written as a dict literal, directly or
    through a local variable; None when the source does not say."""
    if isinstance(node, ast.Dict):
        return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if isinstance(node, ast.Name) and func is not None:
        keys: set[str] = set()
        found = False
        for n in ast.walk(func):
            if (
                isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == node.id for t in n.targets)
            ):
                keys |= _dict_keys(n.value, None) or set()
                found = True
        return keys if found else None
    return None


def sent_frames(sources: dict[str, str]) -> dict[str, list[tuple[str, set[str] | None]]]:
    """event -> [(site, literal payload keys or None)] for every frame sent."""
    out: dict[str, list[tuple[str, set[str] | None]]] = {}

    def visit(node: ast.AST, path: str, func: ast.AST | None) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.Call):
                fn = child.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                index = _FRAME_CALLS.get(name) if "sse_chat/" in path else None
                if index is not None and len(child.args) > index + 1:
                    ev = child.args[index]
                    if isinstance(ev, ast.Constant) and isinstance(ev.value, str):
                        out.setdefault(ev.value, []).append(
                            (f"{path}:{child.lineno}", _dict_keys(child.args[index + 1], func))
                        )
                if name == "emit":  # a journal write: broker.emit(thread_id, {"type": ..., "data": ...})
                    for arg in child.args:
                        if not isinstance(arg, ast.Dict):
                            continue
                        fields = dict(zip(arg.keys, arg.values))
                        kind = next((v for k, v in fields.items()
                                     if isinstance(k, ast.Constant) and k.value == "type"), None)
                        data = next((v for k, v in fields.items()
                                     if isinstance(k, ast.Constant) and k.value == "data"), None)
                        if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
                            out.setdefault(kind.value, []).append(
                                (f"{path}:{child.lineno}", _dict_keys(data, func) if data is not None else None)
                            )
            visit(child, path, inner)

    for path, text in sources.items():
        if "/tests/" in path:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        visit(tree, path, None)
    return out


def documented_frames(doc: str) -> dict[str, set[str]]:
    """event -> the fields its row names, from the section 3 table."""
    start = doc.index("## 3. SSE event contract")
    end = doc.index("### 3.1", start)
    rows: dict[str, set[str]] = {}
    inherits: dict[str, str] = {}
    for line in doc[start:end].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) != 3 or not re.fullmatch(r"`[a-z_]+`", cells[0]):
            continue  # not a frame row (the header names `event:`)
        event = cells[0].strip("`")
        fields_cell = cells[2]
        if fields_cell.startswith("as `"):
            inherits[event] = fields_cell[len("as `"):].split("`")[0]
            continue
        rows[event] = set(re.findall(r"`([a-z_]+)`", fields_cell))
    for event, source in inherits.items():
        rows[event] = set(rows.get(source, set()))
    return rows


def docstring_frames(text: str) -> set[str]:
    doc = ast.get_docstring(ast.parse(text)) or ""
    return set(re.findall(r"^  ([a-z_]+)\s{2,}", doc, re.M))


def problems(sent: dict[str, list[tuple[str, set[str] | None]]], documented: dict[str, set[str]]) -> list[str]:
    found: list[str] = []
    for event, sites in sorted(sent.items()):
        if event not in documented:
            found.append(f"{event}: sent ({sites[0][0]}) but has no row in the SSE event contract table")
            continue
        allowed = documented[event] | _COMMON_FIELDS
        for site, keys in sites:
            extra = sorted((keys or set()) - allowed)
            if extra:
                found.append(f"{event}: {site} sends {extra}, which its row does not name")
    for event in sorted(set(documented) - set(sent)):
        found.append(f"{event}: has a row, but no code sends it")
    return found


# ── the gate ────────────────────────────────────────────────────────────────


def test_the_table_names_every_frame_and_field_the_code_sends() -> None:
    sent = sent_frames(_sources())
    assert {"token", "done", "approval_required", "hitl", "user_message"} <= set(sent), sorted(sent)
    found = problems(sent, documented_frames(DOC.read_text(encoding="utf-8")))
    assert not found, "The SSE event contract table and the code disagree:\n  " + "\n  ".join(found)


def test_the_package_docstring_lists_the_same_frames() -> None:
    sent = set(sent_frames(_sources()))
    listed = docstring_frames(PACKAGE.read_text(encoding="utf-8"))
    assert listed == sent, (
        f"kazma_ui/sse_chat/__init__.py's contract: missing {sorted(sent - listed)}, "
        f"stale {sorted(listed - sent)}"
    )


def test_the_browser_handles_every_frame() -> None:
    js = STREAMING_JS.read_text(encoding="utf-8")
    start = js.index("function dispatch(type, data)")
    handled = set(re.findall(r"case '([a-z_]+)':", js[start:start + 8000]))
    sent = set(sent_frames(_sources()))
    missing = sorted(sent - handled - _SOCKET_ONLY)
    assert not missing, f"streaming.js dispatch has no case for {missing}"
    store = AGENT_STORE_JS.read_text(encoding="utf-8")
    for event in _SOCKET_ONLY:
        assert f"'{event}'" in store or f'"{event}"' in store, f"agentStore.js does not read {event}"


def test_the_gate_sees_a_new_frame_a_new_field_and_a_stale_row() -> None:
    """Negative controls (§28)."""
    planted = {
        "kazma-ui/kazma_ui/sse_chat/_x.py": (
            "async def run(emit_j):\n"
            "    yield await emit_j('brand_new', {'x': 1})\n"
            "    payload = {'content': '', 'secret_field': 2}\n"
            "    yield await emit_j('token', payload)\n"
        )
    }
    sent = sent_frames(planted)
    found = problems(sent, {"token": {"content"}, "retired": {"a"}})
    assert any(f.startswith("brand_new: sent") for f in found), found
    assert any("token:" in f and "secret_field" in f for f in found), found
    assert any(f.startswith("retired: has a row") for f in found), found
