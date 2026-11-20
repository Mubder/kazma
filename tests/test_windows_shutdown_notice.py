"""Kazma stops cleanly when Windows shuts down (2026-11-20).

The owner's restart and Windows Update's restart that followed cut Kazma off:
Task Scheduler ends the guard at the start of a shutdown, and the server heard
of it only at the very end, after the database service had stopped, and was
ended mid-stop. The ``KazmaEngineShutdown`` service now tells every Kazma
server at Windows' preshutdown notice, through the pipe ``KazmaShutdownNotice``
(``engine-shutdown.cs``), and waits until each has ended before it stops the
engine (``kazma_core.shutdown_notice``).

The real service program is built here with the .NET Framework's compiler and
run in its ``--notice-drill`` mode: it listens, then makes the stop the
preshutdown notice makes, on a pipe of this test's own (never the machine's
real service). Controls: a server that never ends is reported as not stopped
within the budget; with no listener the drill finds nothing to stop.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest
from kazma_core import container_engine_setup as ces
from kazma_core import shutdown_notice

ROOT = Path(__file__).resolve().parents[1]

windows_with_csc = pytest.mark.skipif(
    sys.platform != "win32" or not ces._csc().is_file(),
    reason="the .NET Framework's compiler runs on Windows only")


@pytest.fixture(scope="module")
def service_program(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("shutdown-service")
    source = folder / "kazma-engine-shutdown.cs"
    source.write_text(ces._asset("engine-shutdown.cs"), encoding="utf-8")
    exe = folder / "kazma-engine-shutdown.exe"
    built = subprocess.run([str(ces._csc()), "-nologo", "-optimize+", "-target:exe", f"-out:{exe}", str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stdout + built.stderr
    return exe


def _drill(exe: Path, pipe: str, *, listen_s: int, kazma_budget: int) -> subprocess.Popen:
    return subprocess.Popen([str(exe), "--notice-drill", str(listen_s), "--notice-pipe", pipe,
                             "--kazma-budget", str(kazma_budget)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL)


def _pipe() -> str:
    return f"KazmaShutdownNoticeTest-{uuid.uuid4().hex[:12]}"


@windows_with_csc
def test_every_server_is_told_and_the_service_waits_for_each_to_end(service_program) -> None:
    pipe = _pipe()
    drill = _drill(service_program, pipe, listen_s=3, kazma_budget=20)
    told: list[float] = []
    ended = [threading.Event(), threading.Event()]

    def server(i: int) -> None:
        def stop() -> None:
            told.append(time.monotonic())
            # A server's stop takes a moment; its end of the pipe closes with it.
            threading.Timer(0.8, ended[i].set).start()

        shutdown_notice.listen(pipe, on_stop=stop, stop_event=ended[i], retry_s=0.2, supervised=False)

    threads = [threading.Thread(target=server, args=(i,), daemon=True) for i in range(2)]
    for thread in threads:
        thread.start()
    out, _ = drill.communicate(timeout=60)
    assert drill.returncode == 0, out
    assert "stopping Kazma (2 servers)" in out and "Kazma stopped after" in out, out
    assert len(told) == 2
    for thread in threads:
        thread.join(5)


@windows_with_csc
def test_control_a_server_that_never_ends_is_said(service_program) -> None:
    pipe = _pipe()
    drill = _drill(service_program, pipe, listen_s=2, kazma_budget=2)
    never = threading.Event()
    thread = threading.Thread(
        target=shutdown_notice.listen, args=(pipe,),
        kwargs={"on_stop": lambda: None, "stop_event": never, "retry_s": 0.2, "supervised": False}, daemon=True)
    thread.start()
    try:
        out, _ = drill.communicate(timeout=60)
    finally:
        never.set()
    assert drill.returncode == 2, out
    assert "did not stop within 2 s" in out, out


@windows_with_csc
def test_control_with_no_server_listening_nothing_is_stopped(service_program) -> None:
    out, _ = _drill(service_program, _pipe(), listen_s=0, kazma_budget=5).communicate(timeout=60)
    assert "no Kazma server is listening" in out, out


CHILD = textwrap.dedent("""
    import signal, sys, threading, time
    sys.path[:0] = {paths!r}
    from kazma_core import shutdown_notice

    def handler(signum, frame):
        print("stopping by", signal.Signals(signum).name, flush=True)
        sys.exit(0)

    signal.signal(signal.SIGBREAK, handler)
    threading.Thread(target=shutdown_notice.listen, args=({pipe!r},),
                     kwargs={{"retry_s": 0.2, "supervised": False}}, daemon=True).start()
    print("listening", flush=True)
    while True:
        time.sleep(0.1)
""")


@windows_with_csc
def test_the_word_stops_the_process_the_way_the_guards_ctrl_break_does(service_program) -> None:
    """The listener's own stop (no stand-in): SIGBREAK reaches the main
    thread's handler, the process ends, and the service sees it end."""
    pipe = _pipe()
    paths = [str(ROOT / "kazma-core"), str(ROOT)]
    child = subprocess.Popen([sys.executable, "-c", CHILD.format(paths=paths, pipe=pipe)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             stdin=subprocess.DEVNULL)
    try:
        assert child.stdout.readline().strip() == "listening"
        out, _ = _drill(service_program, pipe, listen_s=3, kazma_budget=20).communicate(timeout=60)
        said, _ = child.communicate(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
    assert "stopping by SIGBREAK" in said, said
    assert child.returncode == 0
    assert "Kazma stopped after" in out, out


def test_with_no_service_kazma_says_so_once(caplog) -> None:
    caplog.set_level(logging.INFO, logger="kazma_core.shutdown_notice")
    done = threading.Event()
    threading.Timer(0.5, done.set).start()
    shutdown_notice.listen(_pipe(), on_stop=lambda: None, stop_event=done, retry_s=0.05, supervised=True)
    said = [r for r in caplog.records if "nothing will stop Kazma cleanly" in r.getMessage()]
    assert len(said) == 1 and said[0].levelno == logging.WARNING, [r.getMessage() for r in caplog.records]
    assert shutdown_notice.FIX in said[0].getMessage()


def test_what_is_said_before_logging_is_set_up_is_said_after(monkeypatch) -> None:
    """The listener starts before the app sets up its logging (a shutdown
    during a boot is heard): its lines are held, then said."""
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(shutdown_notice, "_held", [])
    shutdown_notice._log(logging.INFO, "held %s", "line")
    assert shutdown_notice._held == [(logging.INFO, "held %s", ("line",))]
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    level = logging.getLogger("kazma_core.shutdown_notice").level
    logging.getLogger("kazma_core.shutdown_notice").setLevel(logging.INFO)
    try:
        root.addHandler(Catch())
        for _ in range(40):
            if seen:
                break
            time.sleep(0.1)
    finally:
        logging.getLogger("kazma_core.shutdown_notice").setLevel(level)
    assert seen == ["held line"]
    assert shutdown_notice._held is None


def test_serve_py_listens_and_only_serve_py() -> None:
    """The server's own process listens (serve.py); an app built anywhere
    else -- a test's -- never connects to the machine's real service."""
    serve = (ROOT / "serve.py").read_text(encoding="utf-8")
    assert "listen_for_windows_shutdown()" in serve
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-cli/kazma_cli"):
        for path in (ROOT / package).rglob("*.py"):
            if path.name == "shutdown_notice.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "shutdown_notice import start" not in text and "shutdown_notice.start(" not in text, path


def test_the_guard_starts_nothing_while_windows_shuts_down(monkeypatch) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "service"))
    try:
        import kazma_guard
    finally:
        sys.path.remove(str(ROOT / "scripts" / "service"))
    if sys.platform != "win32":
        assert kazma_guard.windows_shutting_down() is False
        return
    assert kazma_guard.windows_shutting_down() is False  # this machine is not shutting down
    source = (ROOT / "scripts" / "service" / "kazma_guard.py").read_text(encoding="utf-8")
    gate = source.index("if windows_shutting_down():")
    assert gate < source.index("self.proc = spawn(self.cmd"), "asked before every start"
