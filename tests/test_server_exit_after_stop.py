"""The server process ends when its stop is done, within the stop's budget.

Roadmap theme 2 (2026-11), found by the graceful reload drill
(``tests/test_drill_graceful_reload.py``): the app's own shutdown finished in
seconds, the process did not. uvicorn re-raised the stop signal through the
C runtime's default action and the main thread sat in it; behind that,
interpreter exit joins every non-daemon thread (an executor thread waiting
on a worker process); and asyncio's teardown waits for every task to finish
its cancellation. The guard killed a reload the owner had asked for, and
paged it.

``serve.py`` now (``kazma_core.process_exit``): the stop signals come back
to Python as KeyboardInterrupt, the process leaves through
``exit_after_shutdown`` once uvicorn returns, and a deadline armed when the
stop begins ends it at the stop's budget plus ``EXIT_MARGIN_S`` whatever is
stuck. Each is held on a real process stopped the way the guard stops one;
each control is the same process without that piece, and it is still alive.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.timeout(180)

#: The stop's budget the children get (the guard hands one down).
BUDGET_S = 4

_CHILD = r"""
import asyncio, os, signal, sys, threading, time
import uvicorn
from kazma_core import shutdown

flags = set(sys.argv[2].split(","))
import atexit
# What a graceful exit must still run (the procedural recorder's drain, the
# vector store's release): a marker written at exit.
atexit.register(lambda: open(sys.argv[3], "w").write("ran"))
if "stuckexit" in flags:  # an exit handler that never returns: registered last, runs first
    atexit.register(lambda: time.sleep(3600))
if "prepare" in flags:
    from kazma_core.process_exit import prepare_server_exit
    prepare_server_exit()
elif "handlers" in flags:  # the signals only: no deadline
    for name in ("SIGBREAK", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, signal.default_int_handler)

# A thread nothing ends: an executor thread waiting on a worker process.
threading.Thread(target=lambda: time.sleep(3600), name="stray", daemon=False).start()

async def _ignores_cancel():
    while True:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            continue

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                shutdown.install_shutdown_signal_hooks()
                if "stuck" in flags:
                    app.stuck = asyncio.ensure_future(_ignores_cancel())
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

try:
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning",
                timeout_graceful_shutdown=2)
except KeyboardInterrupt:
    pass
print("uvicorn returned", flush=True)
if "rawexit" in flags:  # the first version of the fix: os._exit, no exit handlers
    os._exit(0)
if "exit" in flags:
    from kazma_core.process_exit import exit_after_shutdown
    exit_after_shutdown(0)
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start(tmp_path: Path, flags: str) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    marker = tmp_path / "atexit.marker"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env["KAZMA_SHUTDOWN_BUDGET_S"] = str(BUDGET_S)
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "-c", _CHILD, str(port), flags, str(marker)], cwd=str(tmp_path), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    end = time.monotonic() + 60
    while time.monotonic() < end:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return proc, port
        if proc.poll() is not None:
            raise AssertionError(proc.stderr.read().decode(errors="replace")[-2000:])
        time.sleep(0.2)
    proc.kill()
    raise AssertionError("the child server never listened")


def _stop(proc: subprocess.Popen) -> float:
    """The guard's request (``_request_graceful_stop``); returns when it was sent."""
    if os.name == "nt":
        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
    else:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    return time.monotonic()


def _ended_within(proc: subprocess.Popen, seconds: float) -> int | None:
    try:
        return proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        return None


def _kill(proc: subprocess.Popen) -> str:
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    try:
        _out, err = proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        return ""
    return err.decode(errors="replace")


def test_the_server_leaves_once_its_shutdown_has_run(tmp_path):
    proc, _port = _start(tmp_path, "prepare,exit")
    try:
        _stop(proc)
        code = _ended_within(proc, 8)
        assert code == 0, f"exit code {code}"
    finally:
        _kill(proc)
    assert (tmp_path / "atexit.marker").read_text() == "ran", "the exit handlers did not run"


def test_negative_control_a_bare_os_exit_skips_the_exit_handlers(tmp_path):
    proc, _port = _start(tmp_path, "prepare,rawexit")
    try:
        _stop(proc)
        assert _ended_within(proc, 8) == 0
    finally:
        _kill(proc)
    assert not (tmp_path / "atexit.marker").exists(), "the control ran the exit handlers: it proves nothing"


def test_a_stuck_exit_handler_does_not_hold_the_exit(tmp_path):
    proc, _port = _start(tmp_path, "prepare,exit,stuckexit")
    try:
        _stop(proc)
        assert _ended_within(proc, 20) == 0
    finally:
        _kill(proc)


def test_negative_control_without_the_exit_a_stray_thread_holds_it(tmp_path):
    proc, _port = _start(tmp_path, "prepare")
    try:
        _stop(proc)
        assert _ended_within(proc, 8) is None, "the process ended on its own: the control proves nothing"
    finally:
        _kill(proc)


def test_the_deadline_ends_a_stop_that_is_stuck(tmp_path):
    """A task that ignores its cancellation keeps asyncio's teardown waiting:
    the deadline ends the process at the budget plus the margin, and says so."""
    from kazma_core.process_exit import EXIT_MARGIN_S

    proc, _port = _start(tmp_path, "prepare,exit,stuck")
    try:
        sent = _stop(proc)
        code = _ended_within(proc, BUDGET_S + EXIT_MARGIN_S + 15)
        took = time.monotonic() - sent
        assert code == 1, f"exit code {code}"
        assert BUDGET_S + EXIT_MARGIN_S - 1 <= took <= BUDGET_S + EXIT_MARGIN_S + 8, took
    finally:
        err = _kill(proc)
    assert "passed its budget" in err, err[-1500:]


def test_negative_control_without_the_deadline_the_stuck_stop_never_ends(tmp_path):
    from kazma_core.process_exit import EXIT_MARGIN_S

    proc, _port = _start(tmp_path, "handlers,exit,stuck")
    try:
        _stop(proc)
        assert _ended_within(proc, BUDGET_S + EXIT_MARGIN_S + 8) is None, (
            "the stuck stop ended without the deadline: the control proves nothing")
    finally:
        _kill(proc)
