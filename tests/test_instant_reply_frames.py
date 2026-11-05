"""An answer given without the model closes its turn at once.

The chat client closes a turn from the ``done`` frame's ``content``. The
instant replies (``/research`` usage, ``/replay``, ``/reset``, ``/compact``,
``/swarm`` usage) sent their text in a token frame and a ``done`` without
it, so the turn's header said "Kazma is thinking..." until the reconciler's
next read, 6-10 seconds later (live, 2026-09-28). Every ``done`` frame the
SSE chat builds from a literal must carry ``content``.
"""

from __future__ import annotations

import ast
from pathlib import Path

SSE_DIR = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "sse_chat"


def _done_frames_without_content(tree: ast.AST, rel: str) -> list[str]:
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "_sse_frame" and len(node.args) >= 2:
            event, payload = node.args[0], node.args[1]
        elif name == "_journal_fast_path" and len(node.args) >= 3:
            event, payload = node.args[1], node.args[2]
        else:
            continue
        if not (isinstance(event, ast.Constant) and event.value == "done"):
            continue
        if not isinstance(payload, ast.Dict):
            continue  # built elsewhere: the streaming path's own terminal frames
        keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
        if "content" not in keys:
            missing.append(f"{rel}:{node.lineno}")
    return missing


def test_every_literal_done_frame_carries_its_text():
    files = sorted(SSE_DIR.glob("*.py"))
    assert len(files) >= 3  # not blind
    missing: list[str] = []
    seen = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        seen += sum(
            1 for n in ast.walk(tree)
            if isinstance(n, ast.Call) and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
            in ("_sse_frame", "_journal_fast_path")
        )
        missing += _done_frames_without_content(tree, path.name)
    assert seen > 10
    assert missing == [], "done frames without content: " + ", ".join(missing)


def test_the_old_research_usage_frame_is_caught():
    """Negative control: the /research usage reply as it was."""
    src = (
        "async def gen():\n"
        "    yield await _journal_fast_path(thread_id, 'token', {'content': usage})\n"
        "    yield await _journal_fast_path(thread_id, 'done', {'tokens': 0, 'cost': 0.0})\n"
    )
    assert _done_frames_without_content(ast.parse(src), "x.py") == ["x.py:3"]


def _done_frames_claiming_usage(tree: ast.AST, rel: str) -> list[str]:
    """Literal done frames that report a made-up token count or duration.

    An answer given without the model used no tokens and took no measured
    time. The /long, /plan, /yolo, /reset, /compact and /swarm usage replies
    said "1 tokens · $0.0000 · 100ms" under their text (live 2026-11-05):
    the client shows the numbers whenever they are above zero.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "_sse_frame" and len(node.args) >= 2:
            event, payload = node.args[0], node.args[1]
        elif name == "_journal_fast_path" and len(node.args) >= 3:
            event, payload = node.args[1], node.args[2]
        else:
            continue
        if not (isinstance(event, ast.Constant) and event.value == "done"):
            continue
        if not isinstance(payload, ast.Dict):
            continue
        for key, value in zip(payload.keys, payload.values):
            if not (isinstance(key, ast.Constant) and key.value in ("tokens", "duration_ms")):
                continue
            if isinstance(value, ast.Constant) and value.value not in (0, 0.0):
                found.append(f"{rel}:{node.lineno} {key.value}={value.value}")
    return found


def test_an_instant_reply_claims_no_usage():
    files = sorted(SSE_DIR.glob("*.py"))
    found: list[str] = []
    for path in files:
        found += _done_frames_claiming_usage(ast.parse(path.read_text(encoding="utf-8")), path.name)
    assert found == [], "instant replies reporting usage they did not have: " + ", ".join(found)


def test_the_old_long_status_frame_is_caught():
    """Negative control: the /long reply as it was."""
    src = (
        "async def gen():\n"
        "    yield await _journal_fast_path(thread_id, 'done', {'tokens': 1, 'cost': 0.0,"
        " 'duration_ms': 100, 'content': reply, 'capacity': True})\n"
    )
    assert _done_frames_claiming_usage(ast.parse(src), "x.py") == ["x.py:2 tokens=1", "x.py:2 duration_ms=100"]
