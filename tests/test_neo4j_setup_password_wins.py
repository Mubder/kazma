"""The Neo4j container setup made answers to setup's password (2026-11-20).

Live after the PC reset: the settings restored from the old install's bundle
held that machine's Neo4j password, and this machine's container was made by
``kazma setup install`` with the password it wrote into ``.env``. The stored
one won (``.env`` only filled an empty field), so every graph sync was
refused: "Neo4j does not answer (the sign-in was refused); 17 changes
waiting for it". For the container setup made -- ``KAZMA_NEO4J_DEFAULT`` on,
the same address on this machine -- the password in ``.env`` now wins; a
Neo4j the owner chose elsewhere keeps the password stored for it.
"""

from __future__ import annotations

import pytest
from kazma_core.memory import backends


@pytest.fixture
def setup_env(monkeypatch):
    monkeypatch.setenv("KAZMA_NEO4J_DEFAULT", "1")
    monkeypatch.setenv("KAZMA_NEO4J_URL", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("KAZMA_NEO4J_PASSWORD", "setup-made-this-one")
    for name in ("KAZMA_GRAPH_PROVIDER", "NEO4J_URI", "NEO4J_URL", "NEO4J_PASSWORD",
                 "KAZMA_NEO4J_USER", "NEO4J_USER"):
        monkeypatch.delenv(name, raising=False)


def _effective(graph: dict) -> dict:
    out = {"graph": dict(graph)}
    backends._apply_neo4j_env_defaults(out)
    return out["graph"]


def test_setups_container_takes_setups_password(setup_env):
    stored = {"provider": "neo4j", "url": "bolt://localhost:7687", "user": "neo4j",
              "password": "the-old-machines-one"}
    assert _effective(stored)["password"] == "setup-made-this-one"


def test_a_neo4j_the_owner_chose_keeps_its_password(setup_env, monkeypatch):
    stored = {"provider": "neo4j", "url": "bolt://graph.example.lan:7687", "user": "neo4j",
              "password": "the-owners-server"}
    assert _effective(stored)["password"] == "the-owners-server"
    other_port = {**stored, "url": "bolt://localhost:7688"}
    assert _effective(other_port)["password"] == "the-owners-server"


def test_without_setups_container_the_stored_password_stays(setup_env, monkeypatch):
    monkeypatch.delenv("KAZMA_NEO4J_DEFAULT")
    stored = {"provider": "neo4j", "url": "bolt://localhost:7687", "password": "typed-in-settings"}
    assert _effective(stored)["password"] == "typed-in-settings"


def test_an_empty_stored_password_is_still_filled(setup_env):
    stored = {"provider": "neo4j", "url": "bolt://graph.example.lan:7687", "password": ""}
    assert _effective(stored)["password"] == "setup-made-this-one"


def test_control_the_old_rule_kept_the_old_machines_password(setup_env, monkeypatch):
    """What live ran: .env filled an empty field only."""
    monkeypatch.setattr(backends, "_same_local_server", lambda *_a: False)
    stored = {"provider": "neo4j", "url": "bolt://localhost:7687", "password": "the-old-machines-one"}
    assert _effective(stored)["password"] == "the-old-machines-one"


@pytest.mark.parametrize(("a", "b", "same"), [
    ("bolt://localhost:7687", "bolt://127.0.0.1:7687", True),
    ("neo4j://127.0.0.1", "bolt://localhost:7687", True),
    ("bolt://[::1]:7687", "bolt://127.0.0.1:7687", True),
    ("bolt://localhost:7688", "bolt://127.0.0.1:7687", False),
    ("bolt://10.0.0.5:7687", "bolt://127.0.0.1:7687", False),
    ("", "bolt://127.0.0.1:7687", False),
])
def test_the_same_server_on_this_machine(a, b, same):
    assert backends._same_local_server(a, b) is same
