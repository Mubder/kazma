"""A finished tool call says how it ended, on the approval-resume path too.

Live 2026-11-05 (an Arabic DOCX turn): a call that failed after the owner
pressed Approve showed "Done" on the live page, while the stored turn -- and
so a reload -- said "Failed". The stream turns every ``on_tool_end`` into a
stored row and a ``tool_result`` frame on two paths: the main stream and the
leg that resumes after an approval. The main path's frame carried ``state``;
the resume leg's did not, and chat.js reads a frame without ``state`` as
done. The resume leg also stored ``str(output)``, which for a LangChain
``ToolMessage`` is its repr ("content='...' name=... tool_call_id=...").

Both paths now call ``_tool_end_fields``. This file drives that function and
holds every ``tool_result`` frame of the SSE package to carrying ``state``.
Negative control: the resume leg as it was fails the gate, and its old
output conversion stores the repr.
"""

from __future__ import annotations

import ast
from pathlib import Path

from langchain_core.messages import ToolMessage

from kazma_ui.sse_chat._streaming import _tool_end_fields

ROOT = Path(__file__).resolve().parents[1]
SSE_PACKAGE = ROOT / "kazma-ui" / "kazma_ui" / "sse_chat"


def test_an_error_is_a_failed_row():
    assert _tool_end_fields({"output": "boom", "error": "ValueError: boom"}) == ("boom", "failed")
    assert _tool_end_fields({"output": "ok"}) == ("ok", "done")


def test_the_output_is_the_tool_text_not_a_repr():
    msg = ToolMessage(content="عشرة ملفات", name="file_search", tool_call_id="call_1")
    assert _tool_end_fields({"output": msg}) == ("عشرة ملفات", "done")
    assert _tool_end_fields({"output": {"content": "x"}}) == ("x", "done")
    out, _ = _tool_end_fields({"output": {"rows": 2}})
    assert out == '{"rows": 2}'


def test_nothing_returned_is_empty_and_long_output_is_capped():
    assert _tool_end_fields({"output": None}) == ("", "done")
    assert _tool_end_fields(None) == ("", "done")
    out, _ = _tool_end_fields({"output": "a" * 9000})
    assert len(out) == 5000


def _frames_without_state(source: str) -> list[int]:
    """Lines of ``emit_j("tool_result", {...})`` calls whose payload has no state."""
    missing = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "emit_j" or len(node.args) < 2:
            continue
        kind = node.args[0]
        if not (isinstance(kind, ast.Constant) and kind.value == "tool_result"):
            continue
        payload = node.args[1]
        keys = {k.value for k in getattr(payload, "keys", []) if isinstance(k, ast.Constant)}
        if "state" not in keys:
            missing.append(node.lineno)
    return missing


def test_every_tool_result_frame_carries_its_state():
    seen = 0
    problems = []
    for path in sorted(SSE_PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        seen += source.count('"tool_result"')
        for line in _frames_without_state(source):
            problems.append(f"{path.relative_to(ROOT)}:{line}")
    assert seen, "the SSE package sends no tool_result frame any more: re-point this gate"
    assert not problems, (
        "a tool_result frame without 'state' paints a failed call as done: " + ", ".join(problems)
    )


def test_both_on_tool_end_paths_use_the_one_conversion():
    source = (SSE_PACKAGE / "_streaming.py").read_text(encoding="utf-8")
    assert source.count("_tool_end_fields(") >= 3, (
        "the main stream and the resume leg must both convert on_tool_end with _tool_end_fields"
    )


# The resume leg before 2026-11-05.
OLD_RESUME = '''
async def leg(_d, _ev, emit_j, _durable, content_acc):
    _out = str(_d.get("output") or "")[:5000]
    _durable.note({
        "type": "tool",
        "name": str(_ev.get("name") or "tool"),
        "call_id": str(_ev.get("run_id") or ""),
        "result": _out,
        "state": "failed" if _d.get("error") else "done",
    })
    yield await emit_j("tool_result", {
        "tool_name": str(_ev.get("name") or "tool"),
        "tool_call_id": str(_ev.get("run_id") or ""),
        "result": _out,
    })
'''


def test_negative_control_the_old_resume_leg():
    assert _frames_without_state(OLD_RESUME), "the gate no longer sees a frame without state"
    msg = ToolMessage(content="done", name="send_file", tool_call_id="call_9")
    old_out = str({"output": msg}.get("output") or "")[:5000]
    assert old_out != "done" and "tool_call_id" in old_out, old_out
