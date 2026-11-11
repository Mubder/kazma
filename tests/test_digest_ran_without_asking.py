"""The daily digest says what ran without asking, and the YOLO windows.

Live 2026-10-31: a chat ran with YOLO on for an hour (turned on from an
approval card) and sent a file nobody was asked about. The digest listed the
day's approvals as if every call had been asked. Three gates let a call skip
its card on a standing approval -- the chat graph, the swarm bus, the MCP gate
-- and each said so in its own words or not at all: a grant on the chat graph
left no line. Now each says it in one line (``safety.unasked``), and the
digest reads that line chat by chat.

The call on the card that granted the approval is not one of them: the
approve route grants first and resumes after, so that call runs under the
grant -- it was the 10-31 log's "auto-approving: file_write".
"""

from __future__ import annotations

import ast
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from kazma_core.observability import daily_digest
from kazma_core.safety import unasked
from kazma_core.safety.hitl import (
    approval_need,
    reset_current_thread_id,
    set_current_thread_id,
)
from kazma_core.safety.hitl_grants import clear_grants, grant_tool
from kazma_core.safety.task_grants import clear_task_grant
from kazma_core.safety.unasked import (
    BY_TOOL_GRANT,
    BY_YOLO,
    card_answered,
    note_ran_without_asking,
)
from kazma_core.safety.yolo import disable_yolo, enable_yolo

REPO = Path(__file__).resolve().parents[1]
THREADS = ("thr-yolo-a", "thr-grant-b", "thr-card-c")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.delenv("KAZMA_ALLOW_YOLO", raising=False)
    monkeypatch.setattr(unasked, "_card_calls", {})
    for tid in THREADS:
        disable_yolo(tid)
        clear_grants(tid)
        clear_task_grant(tid)
    yield
    for tid in THREADS:
        disable_yolo(tid)
        clear_grants(tid)
        clear_task_grant(tid)


def _lines(caplog, fn, marker: str = "") -> list[tuple[str, str]]:
    """``(level, message)`` of every line *fn* logs holding *marker*."""
    caplog.clear()
    with caplog.at_level(logging.INFO):
        fn()
    return [(r.levelname, r.getMessage()) for r in caplog.records if marker in r.getMessage()]


def _unasked_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("[HITL] ran without asking:")]


def _app_log(tmp_path, records) -> Path:
    """*records*: ``(seconds ago, level, message)``, as the JSON file holds them."""
    now = datetime.now(UTC)
    path = tmp_path / "kazma.log"
    path.write_text("\n".join(
        json.dumps({"timestamp": (now - timedelta(seconds=ago)).isoformat(),
                    "level": level, "message": msg})
        for ago, level, msg in records), encoding="utf-8")
    return path


def _digest(tmp_path, monkeypatch, records, zone="UTC") -> str:
    guard = tmp_path / "guard.log"
    guard.write_text("", encoding="utf-8")
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(guard))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, records)))
    monkeypatch.setenv("KAZMA_TZ", zone)
    return daily_digest.build_digest(hours=24)


def _at(seconds_ago: float, zone: str) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).astimezone(
        ZoneInfo(zone)).strftime("%H:%M")


# -- the digest reads the lines the code writes ----------------------------


def test_a_yolo_window_and_what_ran_under_it_are_listed_by_chat(tmp_path, monkeypatch, caplog):
    """The lines come from the real writers, so the reader and the writers
    cannot drift apart."""
    on = _lines(caplog, lambda: enable_yolo("thr-yolo-a", actor="web:admin:abc"), "YOLO ENABLED")
    sent = _lines(caplog, lambda: note_ran_without_asking("send_file", "thr-yolo-a", BY_YOLO))
    off = _lines(caplog, lambda: disable_yolo("thr-yolo-a", actor="telegram:42"), "YOLO DISABLED")
    granted = _lines(caplog, lambda: [note_ran_without_asking("file_write", "thr-grant-b", BY_TOOL_GRANT)
                                      for _ in range(2)], "ran without asking")
    assert len(on) == len(sent) == len(off) == 1 and len(granted) == 2
    records = [(7200, *on[-1]), (6000, *sent[-1]), (3600, *off[-1])]
    records += [(1800, *line) for line in granted]
    text = _digest(tmp_path, monkeypatch, records, zone="Asia/Kuwait")
    section = text.split("Ran without asking:", 1)[1].split("\n\n", 1)[0]
    assert section == (
        " 3 calls (times in Asia/Kuwait)\n"
        "  chat thr-yolo:\n"
        f"    YOLO on at {_at(7200, 'Asia/Kuwait')} for 60 min, by web:admin:abc, "
        f"off at {_at(3600, 'Asia/Kuwait')}\n"
        "    under YOLO: send_file 1\n"
        "  chat thr-gran:\n"
        '    under "Allow tool": file_write 2'
    )
    # The owner's choice, never a fault.
    assert "No failures, no restarts, no alerts." in text


def test_yolo_turned_on_with_nothing_run_says_so(tmp_path, monkeypatch, caplog):
    on = _lines(caplog, lambda: enable_yolo("thr-yolo-a", actor="web:admin:abc"), "YOLO ENABLED")
    text = _digest(tmp_path, monkeypatch, [(600, *on[-1])])
    assert "Ran without asking: 0 calls (times in UTC)" in text
    assert "    nothing ran without asking" in text


def test_a_day_without_any_says_nothing(tmp_path, monkeypatch):
    assert "Ran without asking" not in _digest(tmp_path, monkeypatch, [])


def test_turning_yolo_off_alone_is_not_a_window(tmp_path, monkeypatch, caplog):
    """``/yolo off`` logs whether or not YOLO was on."""
    off = _lines(caplog, lambda: disable_yolo("thr-yolo-a", actor="web:admin:abc"), "YOLO DISABLED")
    assert "Ran without asking" not in _digest(tmp_path, monkeypatch, [(60, *off[-1])])


def test_lines_from_before_the_change_are_read(tmp_path, monkeypatch):
    """The swarm bus's own words until 2026-11-01. Its YOLO line also counted
    the card's call, run as the window opened (live: 0.09 s after it); a call
    seconds later is a real one."""
    tid = "96acc835-5d7a-4a40-a105-1224fd060c91"
    records = [
        (3600.0, "WARNING", f"[SECURITY] YOLO ENABLED thread={tid} actor=web:admin:hBLjUeDr ttl=3600s"),
        (3599.91, "WARNING", f"[Safety] YOLO mode active for thread={tid} — auto-approving: file_write"),
        (3205.0, "WARNING", f"[Safety] YOLO mode active for thread={tid} — auto-approving: send_file"),
        (100.0, "INFO", f"[Safety] tool grant covers python_exec on thread={tid} — already approved "
                        "by the operator"),
    ]
    text = _digest(tmp_path, monkeypatch, records)
    assert "Ran without asking: 2 calls (times in UTC)" in text
    assert "    under YOLO: send_file 1\n" in text
    assert '    under "Allow tool": python_exec 1' in text


def test_an_unreadable_line_of_it_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "kazma.log"
    path.write_text("{not json [HITL] ran without asking: tool=x thread=y by=yolo\n", encoding="utf-8")
    guard = tmp_path / "guard.log"
    guard.write_text("", encoding="utf-8")
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(guard))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(path))
    assert "log lines this digest could not read: 1" in daily_digest.build_digest(hours=24)


# -- the card's own calls were asked ---------------------------------------


def test_the_call_on_the_card_that_granted_it_is_not_noted(caplog):
    card_answered("thr-card-c", [{"name": "file_write"}, {"name": "file_write"}, {"name": "send_file"}])
    with caplog.at_level(logging.INFO):
        for tool in ("file_write", "file_write", "send_file", "file_write", "send_file"):
            note_ran_without_asking(tool, "thr-card-c", BY_YOLO)
    assert _unasked_lines(caplog) == [
        "[HITL] ran without asking: tool=file_write thread=thr-card-c by=yolo",
        "[HITL] ran without asking: tool=send_file thread=thr-card-c by=yolo",
    ]


def test_without_the_card_s_mark_its_call_is_noted(caplog):
    """Negative control: what the 10-31 log said of the card's file_write."""
    with caplog.at_level(logging.INFO):
        note_ran_without_asking("file_write", "thr-card-c", BY_YOLO)
    assert len(_unasked_lines(caplog)) == 1


def test_a_card_mark_the_resume_never_used_expires(caplog, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(unasked.time, "monotonic", lambda: clock[0])
    card_answered("thr-card-c", [], "file_write")
    clock[0] += unasked._CARD_MARK_S + 1
    with caplog.at_level(logging.INFO):
        note_ran_without_asking("file_write", "thr-card-c", BY_YOLO)
    assert len(_unasked_lines(caplog)) == 1


def test_a_card_for_several_tools_is_never_read_by_its_label(caplog):
    """A batch gate's ``tool`` is "2 tools", never a tool."""
    card_answered("thr-card-c", [], "2 tools")
    assert unasked._card_calls == {}


# -- each gate says it -----------------------------------------------------


def _state(tid: str, tool: str) -> dict:
    from kazma_core.agent.state import PendingToolCall

    return {
        "thread_id": tid,
        "messages": [{"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": tool, "arguments": "{}"}}]}],
        "tool_calls_pending": [PendingToolCall(id="c1", name=tool, arguments={"path": "a.txt"})],
        "tool_calls_done": [],
        "tool_results": {},
        "consecutive_tool_failures": 0,
    }


class _Exec:
    def __init__(self):
        self.ran: list[str] = []

    async def execute(self, name, args):
        self.ran.append(name)
        return {"content": "ok", "is_error": False}


class _Tracer:
    def trace_tool_execution(self, **kwargs):
        pass


def _no_card(monkeypatch):
    import langgraph.types as lg_types

    def interrupt(payload):
        raise AssertionError(f"a granted call must not ask: {payload}")

    monkeypatch.setattr(lg_types, "interrupt", interrupt)


@pytest.mark.asyncio
async def test_a_tool_grant_on_the_chat_graph_is_noted_once(monkeypatch, caplog):
    """The chat graph logged nothing for a grant until 2026-11-01; the
    registry's bus check is skipped for the call, so the graph says it."""
    from kazma_core.agent.graph_tool_worker import tool_worker_node

    grant_tool("thr-grant-b", "file_write", actor="web:admin:abc")
    _no_card(monkeypatch)
    executor = _Exec()
    with caplog.at_level(logging.INFO):
        await tool_worker_node(_state("thr-grant-b", "file_write"), tool_executor=executor,
                               tracer=_Tracer(), hitl_config={"enabled": True})
    assert executor.ran == ["file_write"]
    assert _unasked_lines(caplog) == [
        "[HITL] ran without asking: tool=file_write thread=thr-grant-b by=tool_grant"]


@pytest.mark.asyncio
async def test_the_card_s_own_call_on_the_chat_graph_is_not_noted(monkeypatch, caplog):
    from kazma_core.agent.graph_tool_worker import tool_worker_node

    grant_tool("thr-grant-b", "file_write", actor="web:admin:abc")
    card_answered("thr-grant-b", [{"name": "file_write"}])
    _no_card(monkeypatch)
    with caplog.at_level(logging.INFO):
        await tool_worker_node(_state("thr-grant-b", "file_write"), tool_executor=_Exec(),
                               tracer=_Tracer(), hitl_config={"enabled": True})
    assert _unasked_lines(caplog) == []


def test_a_call_no_card_would_ask_about_is_not_noted():
    """A read tool under YOLO ran without asking nothing."""
    enable_yolo("thr-yolo-a", actor="web:admin:abc")
    token = set_current_thread_id("thr-yolo-a")
    try:
        assert approval_need("file_read", {"enabled": True}) == (False, None)
        assert approval_need("file_write", {"enabled": True}) == (False, BY_YOLO)
        assert approval_need("x_post", {"enabled": True}) == (True, None)
    finally:
        reset_current_thread_id(token)


@pytest.mark.parametrize(("grant", "by"), [("yolo", "yolo"), ("tool", "tool_grant")])
def test_the_swarm_bus_says_it_in_the_same_line(grant, by, caplog):
    from kazma_core.swarm.safety import SafetyMiddleware

    if grant == "yolo":
        enable_yolo("thr-yolo-a", actor="web:admin:abc")
    else:
        grant_tool("thr-yolo-a", "file_write", actor="web:admin:abc")
    with caplog.at_level(logging.INFO):
        allowed = SafetyMiddleware()._allowed_without_asking(
            "file_write", "{}", "task-1", "thr-yolo-a", False)
    assert allowed is True
    assert _unasked_lines(caplog) == [
        f"[HITL] ran without asking: tool=file_write thread=thr-yolo-a by={by}"]


# -- every gate that skips a card on a standing approval says so -----------

#: The functions that read a standing approval to let a call run unasked.
_READERS = {"is_yolo_active", "has_task_grant", "has_tool_grant", "_grant_task",
            "_grant_tool", "standing_approval", "approval_need"}

#: Functions that read one and are not where a call skips its card, with why.
DECLARED = {
    "safety/hitl.py:get_hitl_config": "turns the graph's gate off under YOLO; "
    "the call goes to the bus or the MCP gate, which say it",
    "safety/hitl.py:approval_need": "returns the standing approval; the chat graph says it",
    "safety/hitl.py:requires_approval": "a predicate over approval_need",
    "safety/hitl.py:standing_approval": "the answer itself",
    "safety/hitl_grants.py:list_grants": "lists the grants for a page",
    "swarm/safety.py:_grant_tool": "a wrapper; _allowed_without_asking says it",
    "swarm/safety.py:_grant_task": "a wrapper; _allowed_without_asking says it",
    "safety/commitment/authorize.py:_effective_mode": "YOLO relaxes the semantic "
    "checks (remind, cancel), never an approval card",
    "agent/capacity_commands.py:_flags": "shows the chat's YOLO state",
    "agent/graph_tool_worker.py:_split": "reads the approvals in a thread for "
    "tool_worker_node, which says it",
}


def _readers(sources: dict[str, str]) -> dict[str, bool]:
    """``file:function`` of each function that reads a standing approval ->
    whether it says what ran without asking."""
    found: dict[str, bool] = {}
    for name, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            called = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    called.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
                elif isinstance(sub, ast.Name) and sub.id in _READERS:
                    called.add(sub.id)  # handed to a thread
            if called & _READERS:
                found[f"{name}:{node.name}"] = "note_ran_without_asking" in called
    return found


def _skippers(sources: dict[str, str]) -> list[str]:
    """The readers that neither say it nor are declared."""
    return [key for key, says in _readers(sources).items() if not says and key not in DECLARED]


def _product_sources() -> dict[str, str]:
    out = {}
    for root in REPO.glob("kazma-*/kazma_*"):
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(r in text for r in _READERS):
                rel = path.relative_to(root).as_posix()
                out[rel] = text
    return out


def test_every_gate_that_skips_a_card_says_so():
    sources = _product_sources()
    readers = _readers(sources)
    for gate in ("agent/graph_tool_worker.py:tool_worker_node",
                 "swarm/safety.py:_allowed_without_asking"):
        assert readers.get(gate) is True, gate
    assert any(k.startswith("mcp/manager.py:") and says for k, says in readers.items())
    assert _skippers(sources) == []
    # A declaration of a function that reads none is stale.
    assert sorted(set(DECLARED) - set(readers)) == []


#: What a card's answer calls to grant a standing approval.
_GRANTERS = {"try_enable_yolo", "grant_tool", "grant_task"}


def _granters(sources: dict[str, str]) -> dict[str, bool]:
    """``file:function`` of each function that grants a standing approval ->
    whether it names the card's calls (``card_answered``)."""
    found: dict[str, bool] = {}
    for name, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in _GRANTERS:
                called = {getattr(s.func, "attr", None) or getattr(s.func, "id", "")
                          for s in ast.walk(node) if isinstance(s, ast.Call)}
                if called & _GRANTERS:
                    found[f"{name}:{node.name}"] = "card_answered" in called
    return found


def _all_sources() -> dict[str, str]:
    out = {}
    for root in REPO.glob("kazma-*/kazma_*"):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "tests" not in path.parts and any(g in text for g in _GRANTERS):
                out[f"{root.name}/{path.relative_to(root).as_posix()}"] = text
    return out


def test_every_card_that_grants_a_standing_approval_names_its_calls():
    """Its calls then run under that approval, and they were asked: the web
    route, the chat apps' "Approve for task" and ``kazma ask``'s "always"."""
    granters = _granters(_all_sources())
    assert {"kazma_ui/routes_direct/misc.py:_apply_scope_grants",
            "kazma_core/cli/ask.py:_grant_asked_tools"} <= set(granters)
    assert any(k.startswith("kazma_gateway/agent_handler/hitl.py:") for k in granters)
    assert [k for k, names in granters.items() if not names] == []


def test_the_card_gate_catches_a_grant_that_names_nothing():
    """Negative control: the route's grant as it was until 2026-11-01."""
    old = "def _apply(t, names):\n    for n in names:\n        grant_tool(t, n, actor='a')\n"
    assert _granters({"misc.py": old}) == {"misc.py:_apply": False}


def test_the_web_route_s_grant_marks_the_card_s_calls(caplog):
    from kazma_ui.routes_direct.misc import _apply_scope_grants

    for scope in ("yolo", "tool"):
        caplog.clear()
        assert _apply_scope_grants("thr-card-c", True, scope, "web:admin:abc",
                                   [{"name": "file_write"}], "file_write", "") == scope
        with caplog.at_level(logging.INFO):
            note_ran_without_asking("file_write", "thr-card-c", BY_YOLO)
            note_ran_without_asking("file_write", "thr-card-c", BY_YOLO)
        assert len(_unasked_lines(caplog)) == 1, scope
        disable_yolo("thr-card-c")
        clear_grants("thr-card-c")


def test_the_gate_catches_a_skip_that_says_nothing():
    """Negative control: the MCP gate as it was until 2026-11-01."""
    old = (
        "async def execute(tool_name, _tid):\n"
        "    if _tid and await asyncio.to_thread(\n"
        "        lambda: is_yolo_active(_tid) or has_task_grant(_tid)\n"
        "        or has_tool_grant(_tid, tool_name)):\n"
        "        return True\n"
    )
    assert _skippers({"mcp/manager.py": old}) == ["mcp/manager.py:execute"]
