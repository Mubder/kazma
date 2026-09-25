"""A tool call is ONE activity row, whichever producer wrote it.

Live 2026-09-24 (turn e99d06a0b33f): the Activity list showed "x status
Running..." and "x post Running..." beside their finished rows. Two
producers dropped the tool call id: the telemetry bridge (tracing/events.py
-> tool_lifecycle) and both activity capturers (ws_chat._record_ws_activity
and sse_chat._record_activity). Without an id the stored part is keyed name
+ state + text, so a call's running and done stamps can never merge.

These drive the REAL bridge, the REAL WebSocket capture and the REAL persist
conversion; test_static_gates.py::test_tool_rows_carry_their_call_id keeps
every other producer honest.
"""

from __future__ import annotations

import asyncio

from kazma_core.tracing.events import EventBridge
from kazma_ui.routes.ws_chat import _record_ws_activity
from kazma_ui.turn_document import activity_of, parts_from_stream


async def _stream(events):
    for ev in events:
        yield ev


def _telemetry(events):
    async def collect():
        return [e async for e in EventBridge.process_stream(_stream(events), thread_id="t1")]

    return [e for e in asyncio.run(collect()) if e.type == "tool_lifecycle"]


CALL = [
    {"event": "on_tool_start", "name": "x_post", "run_id": "call_02_q0GD",
     "data": {"input": {"proposal_id": "prop_003dd7eec788:1"}}},
    {"event": "on_tool_end", "name": "x_post", "run_id": "call_02_q0GD",
     "data": {"output": '{"posted": true}'}},
]


def test_tool_lifecycle_telemetry_carries_the_call_id():
    events = _telemetry(CALL)
    assert [e.data["status"] for e in events] == ["tool_running", "tool_completed"]
    assert all(e.data.get("tool_call_id") == "call_02_q0GD" for e in events), events


def test_the_ws_capture_keys_the_row_by_the_call():
    log: list[dict] = []
    for ev in _telemetry(CALL):
        _record_ws_activity(log, ev, thought_recorded=[False])
    assert [r.get("id") for r in log] == ["tool#call_02_q0GD", "tool#call_02_q0GD"]


def test_one_call_persists_as_one_finished_row():
    log: list[dict] = []
    for ev in _telemetry(CALL):
        _record_ws_activity(log, ev, thought_recorded=[False])
    rows = [r for r in activity_of(parts_from_stream(activity=log)) if r["kind"] == "tool"]
    assert len(rows) == 1, rows
    assert rows[0]["state"] == "done"
    assert rows[0]["id"] == "tool#call_02_q0GD"
