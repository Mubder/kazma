"""A model switch forgets the cached failover clients.

``KazmaAgent.sync_active_model`` drops the supervisor's cached failover
clients and cooldowns, so a reconfigured or removed provider stops being used
for failover at once (an audit finding: the stale client, with its old base
URL and credentials, was reused until a restart). When the graph was split on
2026-08-25 the caches moved to ``graph_supervisor``, but the clear still
reached for them through ``graph_builder`` inside ``try/except: pass``: an
AttributeError, swallowed on every switch, until 2026-09-30. The switch-service
tests mock ``sync_active_model`` itself, so its body never ran in a test.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from kazma_core.agent import graph_supervisor
from kazma_core.agent_runner import KazmaAgent


@pytest.fixture
def cached(monkeypatch):
    clients = {"deepseek/deepseek-chat": object()}
    cooldowns = {"zai/glm-5": 1e12}
    monkeypatch.setattr(graph_supervisor, "_failover_clients", clients)
    monkeypatch.setattr(graph_supervisor, "_failover_cooldowns", cooldowns)
    return clients, cooldowns


def _fake_registry(monkeypatch):
    client = SimpleNamespace(config=SimpleNamespace(model="new-model"))
    registry = SimpleNamespace(get_client=lambda: client)
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: registry)
    return client


def test_a_model_switch_clears_the_failover_cache(monkeypatch, cached) -> None:
    clients, cooldowns = cached
    client = _fake_registry(monkeypatch)
    agent = SimpleNamespace(_graph=object(), _streaming_graph=object())

    KazmaAgent.sync_active_model(agent)

    assert agent.llm is client and agent._graph is None and agent._streaming_graph is None
    assert clients == {} and cooldowns == {}


def test_negative_control_the_old_reach_through_graph_builder_fails(cached) -> None:
    """The old clear raised AttributeError -- which its except swallowed."""
    from kazma_core.agent import graph_builder

    with pytest.raises(AttributeError):
        graph_builder._failover_clients.clear()
    clients, cooldowns = cached
    assert clients and cooldowns  # nothing was cleared
