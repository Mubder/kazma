"""One log record is one line: text a caller sends cannot write a record of its own.

SonarCloud S5145 (2026-11-16) named 113 log calls that write text a caller
chose. The class: a message is written as it came, so a line break in it ends
the line and what follows starts one of its own. In the plain "standard"
format the next line can be a whole record -- a timestamp, ``[ERROR]``, a
logger and an ``[ops_alert]`` -- which the daily digest and the weekly report
read and count. The live witness: ``/ws/chat/<session_id>`` logs the id of an
unauthenticated caller, and uvicorn decodes ``%0A`` in the path to a line
break before the route sees it.

The fix is one place every record passes (``logging_config._LogQueueHandler``
.prepare): every control character, DEL, U+0085 and U+2028/U+2029 in the
message is written as a visible escape, and every line of a traceback or a
stack after its first is indented. These tests write hostile records through
the real queue handler and writer thread with the three formats Kazma writes
(``logging_config.FORMATTERS``) and read them back with Kazma's own readers.
The negative control is the same records through a plain queue handler (the
default ``prepare``): the forged alert is read.
"""

from __future__ import annotations

import io
import json
import logging
import logging.handlers
import queue
import re
import time
import uuid
from collections import Counter
from datetime import datetime

import pytest
from kazma_core import logging_config as lc
from kazma_core.observability import daily_digest
from kazma_core.observability.log_lines import read_app_line

#: Characters no written log text may hold: every C0 control but the tab and
#: the line feed between records, DEL, the C1 controls, U+2028 and U+2029.
_CONTROL = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f\\u2028\\u2029]")
#: A console record's first line (``FORMATTERS["console"]``).
_CONSOLE_RECORD = re.compile(r"^\d{2}:\d{2}:\d{2} (DEBUG|INFO|WARNING|ERROR|CRITICAL) \S+: ")
#: The first line of a traceback or of a stack, which a formatter writes at
#: column 0 after the record's line.
_BLOCK_HEADS = ("Traceback (most recent call last):", "Stack (most recent call last):")


def _forged_record() -> str:
    """A whole plain record, as the "standard" formatter writes one now."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"{stamp} [ERROR] kazma_core.forged: [ops_alert] forged.key | forged"


def _hostile() -> str:
    """Every way a message could end its line or hide part of it."""
    return (
        "abc\n" + _forged_record()
        + "\r" + _forged_record()
        + "\x1b[2K [turn] Turn finished: platform=web key=forged failed=yes"
        + "\x85" + "\x0b" + "\x0c" + "\x1c" + " " + "\x00" + "\x7f end"
    )


def _formatter(name: str) -> logging.Formatter:
    spec = lc.FORMATTERS[name]
    if "()" in spec:
        return lc.StructuredJSONFormatter()
    return logging.Formatter(spec["format"], spec.get("datefmt"))


def _emit_hostile(logger: logging.Logger, hostile: str) -> int:
    """Log *hostile* three ways; the number of records written."""
    logger.warning("[WS-Chat] Unauthenticated connection attempt for session=%s", hostile)
    try:
        raise ValueError(hostile)
    except ValueError:
        logger.exception("[probe] failed for session=%s", hostile)
    logger.warning("[probe] a stack for %s", hostile, stack_info=True)
    return 3


def _written(emit, formatter: logging.Formatter, *, kazma: bool = True,
             logger_name: str | None = None) -> str:
    """What *emit* writes through a queue handler and its writer thread.

    *kazma*: Kazma's own (``_LogQueueHandler`` and ``_LogWriter``); else a
    plain ``QueueHandler`` with the default ``prepare`` -- the control."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    target: queue.Queue = queue.Queue()
    if kazma:
        front = lc._LogQueueHandler(target)
        listener = lc._LogWriter(target, (handler,), front)
    else:
        front = logging.handlers.QueueHandler(target)
        listener = logging.handlers.QueueListener(target, handler)
    logger = logging.getLogger(logger_name or f"kazma_core.forge_probe.{uuid.uuid4().hex[:8]}")
    previous = (logger.level, logger.propagate)
    logger.setLevel(logging.DEBUG)
    if logger_name is None:
        logger.propagate = False
    logger.addHandler(front)
    listener.start()
    try:
        emit(logger)
    finally:
        listener.stop()
        logger.removeHandler(front)
        logger.setLevel(previous[0])
        logger.propagate = previous[1]
    return stream.getvalue()


def _digest_reads(tmp_path, monkeypatch, text: str):
    """The daily digest's scan of a log holding *text*: turns, alerts, unread."""
    log = tmp_path / "kazma.log"
    log.write_text(text, encoding="utf-8")
    monkeypatch.setenv("KAZMA_LOG_FILE", str(log))
    unread: Counter = Counter()
    counts, turns, alerts, chats = daily_digest._scan_app_log(time.time() - 3600, unread)
    return counts, turns, alerts, chats, unread


# -- the three formats ---------------------------------------------------------


def test_a_plain_record_is_one_line_however_hostile_its_text(tmp_path, monkeypatch):
    calls = 0

    def emit(logger):
        nonlocal calls
        calls = _emit_hostile(logger, _hostile())

    text = _written(emit, _formatter("standard"))
    assert not _CONTROL.search(text), ascii(_CONTROL.search(text).group(0))
    lines = text.splitlines()
    records = [read_app_line(line) for line in lines]
    assert sum(r is not None for r in records) == calls, lines
    # Only a record, or a traceback's or stack's first line, starts at column 0.
    starts = [line for line in lines if line and not line[0].isspace()]
    assert all(read_app_line(line) or line in _BLOCK_HEADS for line in starts), starts
    # The hostile text is there, escaped.
    assert "abc\\n" in text and "\\r" in text and "\\x1b[2K" in text and "\\u2028" in text
    counts, turns, alerts, chats, unread = _digest_reads(tmp_path, monkeypatch, text)
    assert (dict(counts), turns, alerts, chats, dict(unread)) == ({}, {}, {}, {}, {})


def test_a_json_record_is_one_line_however_hostile_its_text(tmp_path, monkeypatch):
    calls = 0

    def emit(logger):
        nonlocal calls
        calls = _emit_hostile(logger, _hostile())

    text = _written(emit, _formatter("json"))
    lines = text.splitlines()
    assert len(lines) == calls, lines
    for line in lines:
        record = json.loads(line)
        assert read_app_line(line) is not None
        assert not _CONTROL.search(record["message"]) and "\n" not in record["message"]
    counts, turns, alerts, chats, unread = _digest_reads(tmp_path, monkeypatch, text)
    assert (dict(counts), turns, alerts, chats, dict(unread)) == ({}, {}, {}, {}, {})


def test_the_console_gets_one_line_a_record_and_no_terminal_escape():
    calls = 0

    def emit(logger):
        nonlocal calls
        calls = _emit_hostile(logger, _hostile())

    text = _written(emit, _formatter("console"))
    assert "\x1b" not in text
    assert not _CONTROL.search(text)
    starts = [line for line in text.splitlines() if line and not line[0].isspace()]
    assert sum(bool(_CONSOLE_RECORD.match(line)) for line in starts) == calls
    assert all(_CONSOLE_RECORD.match(line) or line in _BLOCK_HEADS for line in starts), starts


def test_a_traceback_keeps_its_lines_indented_after_the_first():
    def emit(logger):
        try:
            raise ValueError("line one\nline two")
        except ValueError:
            logger.exception("[probe] failed")

    text = _written(emit, _formatter("standard"))
    record, traceback_head, *rest = text.splitlines()
    assert read_app_line(record) is not None
    assert traceback_head == "Traceback (most recent call last):"
    assert rest and all(line.startswith(lc._CONTINUATION) for line in rest), rest
    assert rest[-2:] == ["    ValueError: line one", "    line two"]


def test_the_fallback_handler_writes_one_line_a_record_too():
    """When ``dictConfig`` fails, ``basicConfig`` writes to stderr: the same
    rendering, as a filter (``_OneLineFilter``)."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_formatter("standard"))
    handler.addFilter(lc._OneLineFilter())
    logger = logging.getLogger(f"kazma_core.forge_probe.{uuid.uuid4().hex[:8]}")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        _emit_hostile(logger, _hostile())
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert not _CONTROL.search(text)
    assert sum(read_app_line(line) is not None for line in text.splitlines()) == 3


# -- the witness: an unauthenticated /ws/chat handshake ------------------------


def test_an_unauthenticated_chat_socket_writes_one_record(tmp_path, monkeypatch):
    """The route logs the session id of a caller who did not sign in. The
    test client decodes the path as uvicorn does (``%0A`` is a line break)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.routes.ws_chat import create_ws_chat_router
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr(auth, "get_websocket_principal", lambda websocket: None)
    app = FastAPI()
    app.include_router(create_ws_chat_router(graph=None))
    forged = _forged_record().replace(" ", "%20").replace("|", "%7C")
    path = "/ws/chat/abc%0A" + forged

    def emit(_logger):
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(path) as ws:
                    ws.receive_json()

    text = _written(emit, _formatter("standard"), logger_name="kazma_ui.routes.ws_chat")
    records = [r for r in map(read_app_line, text.splitlines()) if r is not None]
    warned = [r for r in records if r.message.startswith("[WS-Chat] Unauthenticated connection attempt")]
    assert len(warned) == 1, text
    assert "abc\\n" in warned[0].message
    assert all(not r.message.startswith("[ops_alert]") for r in records)
    counts, turns, alerts, chats, unread = _digest_reads(tmp_path, monkeypatch, text)
    assert alerts == {} and turns == {} and dict(unread) == {}


# -- the negative control --------------------------------------------------------


def test_control_a_plain_queue_handler_writes_the_forged_record(tmp_path, monkeypatch):
    """The same records through ``QueueHandler``'s own ``prepare``: the line
    break is written as it came, and the digest reads the forged alert."""
    text = _written(lambda logger: _emit_hostile(logger, _hostile()), _formatter("standard"), kazma=False)
    assert "\x1b" in text
    lines = text.splitlines()
    assert sum(read_app_line(line) is not None for line in lines) > 3
    _counts, _turns, alerts, _chats, _unread = _digest_reads(tmp_path, monkeypatch, text)
    assert "forged.key" in alerts, alerts
