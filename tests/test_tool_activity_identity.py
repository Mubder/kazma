"""A tool call is ONE activity row, whichever producer wrote it.

Live 2026-09-24 (turn e99d06a0b33f): the Activity list showed "x status
Running..." and "x post Running..." beside their finished rows. Two
producers dropped the tool call id: the telemetry bridge (tracing/events.py
-> tool_lifecycle) and both activity capturers (the WebSocket's and the SSE
chat's). Without an id the stored part is keyed name + state + text, so a
call's running and done stamps can never merge.

These drive the REAL bridge, the REAL capture and the REAL persist
conversion. The web chat's one capturer is the SSE stream's
(``sse_chat._helpers._record_frame_activity``, lifted out of the route
closure); the WebSocket's twin left with the socket's graph client
(2026-09-30, AUD-026). test_static_gates.py::test_tool_rows_carry_their_call_id
keeps every frame producer honest.
"""

from __future__ import annotations

import asyncio

from kazma_core.tracing.events import EventBridge
from kazma_ui.sse_chat._helpers import _record_frame_activity
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


# The frames the SSE stream carries for that call.
FRAMES = [
    ("tool_call", {"tool_name": "x_post", "tool_call_id": "call_02_q0GD",
                   "inputs": {"proposal_id": "prop_003dd7eec788:1"}}),
    ("tool_result", {"tool_name": "x_post", "tool_call_id": "call_02_q0GD",
                     "result": '{"posted": true}'}),
]


def test_the_capture_keys_the_row_by_the_call():
    log: list[dict] = []
    for ev_type, data in FRAMES:
        _record_frame_activity(log, ev_type, data)
    assert [r.get("id") for r in log] == ["tool#call_02_q0GD", "tool#call_02_q0GD"]


def test_a_frame_without_the_id_falls_back_to_name_keying():
    """Negative control: the defect's shape -- no id, so no merge key."""
    log: list[dict] = []
    _record_frame_activity(log, "tool_call", {"tool_name": "x_post"})
    assert "id" not in log[0]


def test_one_call_persists_as_one_finished_row():
    log: list[dict] = []
    for ev_type, data in FRAMES:
        _record_frame_activity(log, ev_type, data)
    rows = [r for r in activity_of(parts_from_stream(activity=log)) if r["kind"] == "tool"]
    assert len(rows) == 1, rows
    assert rows[0]["state"] == "done"
    assert rows[0]["id"] == "tool#call_02_q0GD"
