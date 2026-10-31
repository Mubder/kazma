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
import socket
from pathlib import Path

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
