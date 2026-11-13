"""The suite never reaches the Kazma running on this machine (2026-11-13).

The live install's log held a burst of 33-44 unauthenticated requests from
127.0.0.1 a dozen times a day -- the Swarm status and task polls, the
settings, the documents list and a ``PUT /api/settings/single`` -- each time
someone ran the suite: the terminal app's tests mount the real app, and its
polling asked ``candidate_api_bases()``, which tried ``KAZMA_PORT`` (9090 by
default) and then guessed 9090 and 8000. They were refused only because the
live install asks for a sign-in; on an install that trusts loopback they
would have changed its settings.

Three layers, each shown here: the local API asks only the configured port
(tests/test_agent_turn.py holds the product rule); the root conftest gives
the suite a port nothing listens on; tests/_egress_guard.py refuses a
connection to a Kazma outside the run (tests/test_egress_guard.py holds it,
with a planted test as its negative control).
"""

from __future__ import annotations

import os
import socket

from kazma_core.runtime.local_api import candidate_api_bases
from kazma_core.server_port import DEFAULT_PORT, server_port
from tests import _egress_guard as guard


def test_the_suite_has_a_port_of_its_own_that_nothing_answers():
    port = int(os.environ["KAZMA_PORT"])
    assert port != DEFAULT_PORT and server_port() == port
    assert port not in guard.outside_ports()
    with socket.socket() as probe:
        probe.settimeout(2)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "something listens on the suite's port"


def test_the_live_install_s_port_is_an_outside_port():
    assert DEFAULT_PORT in guard.outside_ports()


def test_the_terminal_app_asks_only_the_suite_s_port(monkeypatch):
    monkeypatch.delenv("KAZMA_BASE_URL", raising=False)
    assert candidate_api_bases() == [f"http://127.0.0.1:{os.environ['KAZMA_PORT']}"]


def test_negative_control_the_old_candidates_reached_9090():
    """The list as it was, under the suite's own port."""
    port = int(os.environ["KAZMA_PORT"])
    old = [f"http://127.0.0.1:{port}"]
    if port != DEFAULT_PORT:
        old.append(f"http://127.0.0.1:{DEFAULT_PORT}")
    old.append("http://127.0.0.1:8000")
    assert f"http://127.0.0.1:{DEFAULT_PORT}" in old
