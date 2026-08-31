"""file_search must offload Path.rglob/stat/read_text off the asyncio loop."""

from __future__ import annotations

import ast
from pathlib import Path


def test_file_search_uses_to_thread() -> None:
    src = Path("kazma-core/kazma_core/agent/tool_builtins/filesystem.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "file_search":
            found = True
            text = ast.get_source_segment(src, node) or ast.unparse(node)
            assert "to_thread" in text, (
                "file_search must asyncio.to_thread its rglob/stat/read_text body; "
                "running it on the selector loop stalls SSE/WS/health."
            )
            # Negative control: a synthetic body without to_thread fails the check.
            fake = "async def file_search():\n    root.rglob(glob)\n"
            assert "to_thread" not in fake
    assert found, "file_search not found in filesystem.py"


def test_serve_py_does_not_spawn_module_uvicorn() -> None:
    """`python -m uvicorn` on Windows hardcodes ProactorEventLoop."""
    src = Path("serve.py").read_text(encoding="utf-8")
    assert "uvicorn.run" in src
    assert "uvicorn_loop_factory" in src
    assert "subprocess.Popen" not in src
    # Negative control: the old subprocess argv is exactly the trap.
    old = '"-m",\n            "uvicorn"'
    assert '"-m", "uvicorn"' not in src
    assert old not in src
