"""An async tool does its file reads, writes and deletes in a thread.

2026-11 functional audit, FA-139. The agent's async tools run on the server's
event loop, the one every chat stream shares: a ``Path.read_text`` of a large
Markdown source, a ``write_bytes`` of a generated image, a ``shutil.rmtree``
of a temporary folder held up every other chat for as long as the disk took.
Nine such calls sat directly in async tool bodies (``python_exec``,
``generate_image``, ``generate_pdf``/``generate_docx``, the skill installer,
the screenshot tool, the research DOCX export); each now runs through
``asyncio.to_thread``.

The gate reads every async function in the tool folders. A nested ``def`` is
skipped (it is judged where it is called, normally ``to_thread``); a planted
call is the negative control.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_FOLDERS = (
    "kazma-core/kazma_core/tools",
    "kazma-core/kazma_core/agent/tool_builtins",
    "kazma-core/kazma_core/agent_skills",
    "kazma-skills/kazma_skills",
)
_BLOCKING_METHODS = {"write_text", "read_text", "write_bytes", "read_bytes"}
_BLOCKING_SHUTIL = {"rmtree", "copytree", "copyfile", "copy2", "copy", "move"}


def _blocking_calls(source: str) -> list[tuple[int, str, str]]:
    """``(line, async function, call)`` for each blocking file call made
    directly in an async function's body."""
    found: list[tuple[int, str, str]] = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue

        def visit(node: ast.AST, fn_name: str = fn.name) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                if isinstance(child, ast.Call):
                    f = child.func
                    name = None
                    if isinstance(f, ast.Name) and f.id == "open":
                        name = "open"
                    elif isinstance(f, ast.Attribute) and f.attr in _BLOCKING_METHODS:
                        name = f.attr
                    elif (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                          and f.value.id == "shutil" and f.attr in _BLOCKING_SHUTIL):
                        name = f"shutil.{f.attr}"
                    if name:
                        found.append((child.lineno, fn_name, name))
                visit(child, fn_name)

        visit(fn)
    return found


def test_no_async_tool_does_file_io_on_the_loop():
    found: dict[str, list] = {}
    for folder in TOOL_FOLDERS:
        for path in sorted((ROOT / folder).rglob("*.py")):
            calls = _blocking_calls(path.read_text(encoding="utf-8"))
            if calls:
                found[path.relative_to(ROOT).as_posix()] = calls
    assert not found, f"run these with asyncio.to_thread: {found}"


def test_negative_control_a_planted_call_is_found():
    planted = (
        "async def tool(p):\n"
        "    data = p.read_text()\n"
        "    def later():\n"
        "        return p.read_bytes()\n"
        "    await asyncio.to_thread(later)\n"
        "    shutil.rmtree(p)\n"
        "    with open(p) as fh:\n"
        "        pass\n"
    )
    assert _blocking_calls(planted) == [(2, "tool", "read_text"), (6, "tool", "shutil.rmtree"),
                                        (7, "tool", "open")]
