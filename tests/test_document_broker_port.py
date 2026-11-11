"""The document broker's address can move, and Kazma follows (2026-10-31).

Live that day Docker Desktop restarted and Windows reserved TCP ports
20181-20780; the broker's port 20527 was among them, and every document was
refused for hours while the broker failed to listen every five minutes. The
broker now says so with its own exit code before anything slow, the guard
moves it (``tests/test_guard_document_broker.py``), and Kazma reads the
address the guard has now from its state file.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.documents import broker_supervision, worker_broker, worker_container


def test_a_port_it_cannot_take_is_exit_7_before_the_image_is_attested(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("x" * 48, encoding="utf-8")
    monkeypatch.setattr(worker_broker, "_find_docker_cli", lambda: pytest.fail("attested before listening"))
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        code = worker_broker.main(["--image", "x", "--token-file", str(token), "--listen", f"tcp:127.0.0.1:{port}"])
    assert code == worker_broker.EXIT_PORT_UNAVAILABLE == 7


class _AnswersAndCloses:
    """A broker that answers each connection and closes it first, as the real
    one answers the guard's status probe."""

    limits = SimpleNamespace(concurrency=1)

    def handle(self, sock: socket.socket) -> None:
        sock.sendall(b"S")


def broker_port_in_time_wait() -> int:
    """A broker's port the way a killed broker leaves it: the broker answered a
    probe and closed that connection first, so the connection holds the port
    in TIME_WAIT for a minute after the broker is gone."""
    server = worker_broker._make_server("tcp:127.0.0.1:0", _AnswersAndCloses(), allow_non_loopback=False)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.settimeout(5)
            assert client.recv(1) == b"S"
            assert client.recv(1) == b""  # the broker closed first
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    time.sleep(0.05)  # the last ACK, on loopback
    return port


def test_a_broker_started_again_listens_where_the_killed_one_did():
    """Linux CI, 2026-11-10: a killed broker's restart could not listen on its
    port ("Address already in use") until the guard gave up: the guard's
    status probes leave connections in TIME_WAIT on every broker port."""
    listen = f"tcp:127.0.0.1:{broker_port_in_time_wait()}"
    assert worker_broker._tcp_listen_refused(listen) is None
    server = worker_broker._make_server(listen, _AnswersAndCloses(), allow_non_loopback=False)
    try:
        # A port another socket LISTENS on is still refused.
        assert isinstance(worker_broker._tcp_listen_refused(listen), OSError)
    finally:
        server.server_close()


@pytest.mark.skipif(os.name == "nt", reason="Windows lets a plain bind take a port in TIME_WAIT")
def test_control_the_old_bind_is_refused_where_the_killed_broker_was(monkeypatch):
    """Negative control: the broker's bind before the fix (no SO_REUSEADDR)."""
    monkeypatch.setattr(worker_broker, "_REUSE_ADDRESS", False)
    monkeypatch.setattr(worker_broker._TcpServer, "allow_reuse_address", False)
    listen = f"tcp:127.0.0.1:{broker_port_in_time_wait()}"
    assert isinstance(worker_broker._tcp_listen_refused(listen), OSError)


def test_windows_never_reuses_a_bound_address():
    """SO_REUSEADDR on Windows would let another process take the port."""
    assert worker_broker._TcpServer.allow_reuse_address is (os.name != "nt")


def _handed(monkeypatch, tmp_path, *, state_url, source="guard"):
    token = tmp_path / "token"
    token.write_text("t" * 48, encoding="utf-8")
    state = tmp_path / "guard.state.json"
    state.write_text(json.dumps({"document_broker": {"url": state_url}}), encoding="utf-8")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL", "tcp://127.0.0.1:20527")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_TOKEN_FILE", str(token))
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(state))
    if source:
        monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL_SOURCE", source)
    else:
        monkeypatch.delenv("KAZMA_DOCUMENT_BROKER_URL_SOURCE", raising=False)


def test_kazma_follows_the_guards_move_without_a_restart(tmp_path, monkeypatch):
    _handed(monkeypatch, tmp_path, state_url="tcp://127.0.0.1:20781")
    assert broker_supervision.broker_url() == "tcp://127.0.0.1:20781"
    assert worker_container._endpoint().address == ("127.0.0.1", 20781)
    assert broker_supervision.supervisor_report()["url"] == "tcp://127.0.0.1:20781"


def test_a_broker_kazma_was_not_handed_by_the_guard_is_left_alone(tmp_path, monkeypatch):
    """Negative control: a broker .env names (no source mark) is not followed."""
    _handed(monkeypatch, tmp_path, state_url="tcp://127.0.0.1:20781", source=None)
    assert worker_container._endpoint().address == ("127.0.0.1", 20527)


@pytest.mark.parametrize("state_url", ["tcp://10.0.0.5:20781", "unix:///tmp/x.sock", "", None])
def test_only_a_loopback_address_is_taken_from_the_state_file(tmp_path, monkeypatch, state_url):
    _handed(monkeypatch, tmp_path, state_url=state_url)
    assert broker_supervision.broker_url() == "tcp://127.0.0.1:20527"


def test_the_request_names_the_current_address(tmp_path, monkeypatch):
    _handed(monkeypatch, tmp_path, state_url="tcp://127.0.0.1:20781")
    request = tmp_path / "request.json"
    monkeypatch.setenv(broker_supervision.REQUEST_FILE_ENV, str(request))
    monkeypatch.setattr(worker_container, "isolation_selection",
                        lambda: {"mode": "container", "source": "setting"})
    assert broker_supervision._publish_broker_request() is True
    assert json.loads(Path(request).read_text(encoding="utf-8"))["url"] == "tcp://127.0.0.1:20781"


def test_the_guard_and_the_broker_agree_on_the_exit_codes():
    """The guard reads the broker's exit codes; it cannot import the broker
    (stdlib only, loaded by path), so its copies are held equal here."""
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py"
    spec = importlib.util.spec_from_file_location("_exit_code_guard", path)
    guard = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = guard
    spec.loader.exec_module(guard)
    assert (guard.BROKER_EXIT_NO_IMAGE, guard.BROKER_EXIT_NO_ENGINE, guard.BROKER_EXIT_NEWER_IMAGE,
            guard.BROKER_EXIT_PORT_UNAVAILABLE) == (
        worker_broker.EXIT_NO_IMAGE, worker_broker.EXIT_NO_ENGINE, worker_broker.EXIT_NEWER_IMAGE,
        worker_broker.EXIT_PORT_UNAVAILABLE)
