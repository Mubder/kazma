"""Both mouths paint every journal frame (AGENTS.md §31D, 2026-09-26).

The turn broker stamps one frame and fans it to the SSE stream AND the
WebSocket. A tab that only watches a turn -- another window, a phone --
has the socket alone, so every frame type the stream's dispatcher handles
must have a case in the socket's dispatcher too. Live 2026-09-26 the
socket had none for ``hitl``, ``tool_call``, ``tool_result`` and
``turn_heartbeat``: a watching tab never saw the other tab's approval
settle, and its block stayed on "approval" under the finished answer
(Linux CI, tests/e2e/test_chunked_stream_browser.py). The frame types are
read from the two dispatchers themselves.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STREAM_JS = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "streaming.js"
STORE_JS = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "stores" / "agentStore.js"

#: Frame types only the stream needs, with the reason. Empty today.
STREAM_ONLY: dict[str, str] = {}

_CASE = re.compile(r"case '([a-z_]+)':")


def _between(src: str, start: str, end: str) -> str:
    i = src.index(start)
    return src[i:src.index(end, i)]


def stream_frame_types(src: str) -> set[str]:
    body = _between(src, "function dispatch(type, data) {", "default:")
    return set(_CASE.findall(body))


def socket_frame_types(src: str) -> set[str]:
    body = _between(src, "handleSocketMessage(frame) {",
                    "console.debug('[AgentStore] Unhandled telemetry event type:'")
    handled = set(_CASE.findall(body))
    # The resume handshake is handled before the switch.
    handled |= set(re.findall(r"if \(type === '([a-z_]+)'\) \{", body))
    return handled


def missing_on_socket(stream_src: str, store_src: str) -> set[str]:
    return stream_frame_types(stream_src) - socket_frame_types(store_src) - set(STREAM_ONLY)


def test_the_socket_handles_every_frame_the_stream_handles():
    stream_src = STREAM_JS.read_text(encoding="utf-8")
    store_src = STORE_JS.read_text(encoding="utf-8")
    assert "hitl" in stream_frame_types(stream_src)  # the instrument reads cases
    assert missing_on_socket(stream_src, store_src) == set()


def test_negative_control_a_dropped_case_is_caught():
    stream_src = STREAM_JS.read_text(encoding="utf-8")
    store_src = STORE_JS.read_text(encoding="utf-8")
    without_hitl = store_src.replace("case 'hitl':", "case 'hitl_removed':")
    assert without_hitl != store_src
    assert missing_on_socket(stream_src, without_hitl) == {"hitl"}


def test_stream_only_exceptions_are_real_stream_cases():
    stream = stream_frame_types(STREAM_JS.read_text(encoding="utf-8"))
    assert set(STREAM_ONLY) <= stream
