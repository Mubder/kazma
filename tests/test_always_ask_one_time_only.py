"""An always-ask tool takes a one-time approval only -- never a grant.

Live 2026-10-16 12:25 UTC: Kazma drafted a metrics post and called x_post. Its
approval card hid "YOLO session" and said "This tool always requires approval
-- YOLO and session grants cannot skip it", but still offered "Allow tool
(session)". A click on it approved THIS call (the post was published) and
stored a 30-minute grant that could never apply (``requires_approval`` checks
``ALWAYS_HITL_TOOLS`` first). The owner read the button as "let it use the
tool; it will still ask me before posting".

Now the card offers only Approve once / Deny for such a tool (the interrupt
says ``always_ask``), the approve route refuses an "allow for this chat" or
YOLO answer before anything is claimed or resumed, and ``grant_tool`` never
stores a grant for one. Browser and real-graph proof:
``tests/e2e/test_always_ask_one_time_approval.py``.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MISC = _ROOT / "kazma-ui" / "kazma_ui" / "routes_direct" / "misc.py"


def test_one_time_only_names_the_x_writes_a_question_asks_about():
    from kazma_core.safety.hitl_grants import one_time_only

    assert one_time_only([{"name": "x_post"}]) == ["x_post"]
    assert one_time_only([], "x_delete_post") == ["x_delete_post"]
    assert one_time_only([{"name": "file_write"}, {"name": "x_schedule_post"}]) == ["x_schedule_post"]
    # Control: an ordinary danger tool may be allowed for the chat.
    assert one_time_only([{"name": "file_write"}, {"name": "shell_exec"}]) == []
    assert one_time_only([], "2 tools") == []


def test_grant_tool_never_stores_a_grant_for_an_always_ask_tool():
    from kazma_core.safety.hitl_grants import grant_tool, has_tool_grant, list_grants

    thread = "always-ask-grant-test"
    refused = grant_tool(thread, "x_post", actor="test")
    assert refused["active"] is False and refused.get("error") == "one_time_only"
    assert has_tool_grant(thread, "x_post") is False
    assert all(g.get("tool") != "x_post" for g in list_grants(thread))
    # Control: an ordinary danger tool is still granted.
    assert grant_tool(thread, "file_write", actor="test")["active"] is True
    assert has_tool_grant(thread, "file_write") is True


def _refusal_precedes_the_decision(src: str) -> list[str]:
    """In the approve route, the one-time-only refusal must come before the
    decision is recorded and before any grant is applied."""
    route = src.split("async def approve_tool(", 1)[1] if "async def approve_tool(" in src else ""
    at_refusal = route.find("one_time_only(")
    problems: list[str] = []
    if at_refusal == -1:
        return ["the approve route never asks one_time_only"]
    for later in ("await record_gate_decision(", "_apply_scope_grants, thread_id"):
        at = route.find(later)
        if at != -1 and at < at_refusal:
            problems.append(f"{later!r} runs before the one-time-only refusal")
    return problems


def test_the_route_refuses_before_it_decides_anything():
    assert _refusal_precedes_the_decision(_MISC.read_text(encoding="utf-8")) == []


def test_the_gate_fails_on_the_route_without_the_refusal():
    """Negative control: the route as it was (grants applied, no refusal)."""
    old = (
        "    async def approve_tool(thread_id, request):\n"
        "        await record_gate_decision(thread_id, approved)\n"
        "        scope = await asyncio.to_thread(\n"
        "            _apply_scope_grants, thread_id, approved, scope, actor,\n"
        "        )\n"
    )
    assert _refusal_precedes_the_decision(old) == ["the approve route never asks one_time_only"]
    late = old.replace(
        "        scope = await",
        "        _always = one_time_only(pending_tools)\n        scope = await",
    )
    assert len(_refusal_precedes_the_decision(late)) == 1
