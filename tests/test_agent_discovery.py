"""Tests for AgentDiscovery — capability-based agent discovery."""

from __future__ import annotations

import pytest
from kazma_core.delegation.discovery import AgentDiscovery, AgentInfo
from kazma_core.hub import KazmaHub


@pytest.fixture
def hub(tmp_path):
    return KazmaHub(registry_path=str(tmp_path / "test_registry.db"))


@pytest.fixture
def discovery(hub):
    return AgentDiscovery(agent_id="local-agent", hub=hub)


class TestAnnounce:
    """Test agent announcement."""

    async def test_announce_registers_with_hub(self, discovery, hub):
        await discovery.announce(["summarization", "translation"])
        agents = await hub.list_agents()
        assert len(agents) == 1
        assert agents[0].agent_id == "local-agent"
        assert agents[0].capabilities == ["summarization", "translation"]

    async def test_announce_caches_locally(self, discovery):
        await discovery.announce(["analysis"])
        assert "local-agent" in discovery.known_agents
        info = discovery.known_agents["local-agent"]
        assert info.capabilities == ["analysis"]

    async def test_announce_updates_capabilities(self, discovery):
        await discovery.announce(["cap1"])
        await discovery.announce(["cap1", "cap2"])
        info = discovery.known_agents["local-agent"]
        assert info.capabilities == ["cap1", "cap2"]


class TestDiscover:
    """Test agent discovery."""

    async def test_discover_finds_hub_agents(self, discovery, hub):
        from kazma_core.hub import AgentInfo as HubInfo

        await hub.register_agent(HubInfo(agent_id="remote-1", capabilities=["summarization", "translation"]))
        await hub.register_agent(HubInfo(agent_id="remote-2", capabilities=["analysis"]))

        found = await discovery.discover(["summarization"])
        assert len(found) == 1
        assert found[0].agent_id == "remote-1"

    async def test_discover_excludes_self(self, discovery, hub):
        from kazma_core.hub import AgentInfo as HubInfo

        await hub.register_agent(HubInfo(agent_id="local-agent", capabilities=["summarization"]))
        await hub.register_agent(HubInfo(agent_id="remote-1", capabilities=["summarization"]))

        found = await discovery.discover(["summarization"])
        agent_ids = [a.agent_id for a in found]
        assert "local-agent" not in agent_ids

    async def test_discover_multiple_capabilities(self, discovery, hub):
        from kazma_core.hub import AgentInfo as HubInfo

        await hub.register_agent(HubInfo(agent_id="r1", capabilities=["summarization"]))
        await hub.register_agent(HubInfo(agent_id="r2", capabilities=["summarization", "translation"]))
        await hub.register_agent(HubInfo(agent_id="r3", capabilities=["summarization", "translation", "analysis"]))

        found = await discovery.discover(["summarization", "translation"])
        agent_ids = [a.agent_id for a in found]
        # r3 has more capabilities so should rank higher
        assert "r3" in agent_ids
        assert "r2" in agent_ids
        assert "r1" not in agent_ids  # Doesn't have translation

    async def test_discover_respects_max_results(self, discovery, hub):
        from kazma_core.hub import AgentInfo as HubInfo

        for i in range(10):
            await hub.register_agent(HubInfo(agent_id=f"r{i}", capabilities=["general"]))

        found = await discovery.discover(["general"], max_results=3)
        assert len(found) == 3

    async def test_discover_empty_when_no_match(self, discovery, hub):
        from kazma_core.hub import AgentInfo as HubInfo

        await hub.register_agent(HubInfo(agent_id="r1", capabilities=["summarization"]))
        found = await discovery.discover(["quantum-computing"])
        assert len(found) == 0

    async def test_discover_includes_local_cache(self, discovery, hub):

        # Add to local cache directly
        discovery.known_agents["cached-agent"] = AgentInfo(
            agent_id="cached-agent",
            capabilities=["custom-cap"],
            reputation=1.5,
        )

        found = await discovery.discover(["custom-cap"])
        assert len(found) == 1
        assert found[0].agent_id == "cached-agent"


class TestGetAgentInfo:
    """Test agent info lookup."""

    async def test_get_from_hub(self, discovery, hub):
        from kazma_core.hub import AgentInfo as HubInfo

        await hub.register_agent(HubInfo(agent_id="r1", capabilities=["cap1"]))
        info = await discovery.get_agent_info("r1")
        assert info is not None
        assert info.agent_id == "r1"

    async def test_get_from_local_cache(self, discovery):
        discovery.known_agents["cached"] = AgentInfo(agent_id="cached", capabilities=["cap1"])
        info = await discovery.get_agent_info("cached")
        assert info is not None
        assert info.agent_id == "cached"

    async def test_get_nonexistent_returns_none(self, discovery):
        info = await discovery.get_agent_info("ghost")
        assert info is None


class TestUpdateReputation:
    """Test reputation updates."""

    async def test_update_reputation(self, discovery, hub):
        from kazma_core.hub import AgentInfo as HubInfo

        await hub.register_agent(HubInfo(agent_id="r1", capabilities=["cap1"]))
        # First discover to populate local cache
        await discovery.discover(["cap1"])

        await discovery.update_reputation("r1", 1.8)
        # Check local cache (exponential moving average)
        info = discovery.known_agents.get("r1")
        if info:
            # EMA: 0.7 * 1.0 + 0.3 * 1.8 = 1.24
            assert info.reputation == pytest.approx(1.24, abs=0.01)

    async def test_reputation_clamped(self, discovery):
        discovery.known_agents["r1"] = AgentInfo(agent_id="r1", capabilities=[], reputation=1.0)
        await discovery.update_reputation("r1", 5.0)
        info = discovery.known_agents["r1"]
        assert info.reputation <= 2.0

    async def test_reputation_floor(self, discovery):
        discovery.known_agents["r1"] = AgentInfo(agent_id="r1", capabilities=[], reputation=1.0)
        await discovery.update_reputation("r1", -5.0)
        info = discovery.known_agents["r1"]
        assert info.reputation >= 0.0


class TestGetAvailableAgents:
    """Test listing available agents."""

    async def test_excludes_self(self, discovery, hub):
        from kazma_core.hub import AgentInfo as HubInfo

        await hub.register_agent(HubInfo(agent_id="local-agent", capabilities=["cap"]))
        await hub.register_agent(HubInfo(agent_id="remote-1", capabilities=["cap"]))

        available = await discovery.get_available_agents()
        agent_ids = [a.agent_id for a in available]
        assert "local-agent" not in agent_ids
        assert "remote-1" in agent_ids


class TestDiscoveryStats:
    """Test statistics."""

    async def test_stats(self, discovery, hub):
        from kazma_core.hub import AgentInfo as HubInfo

        await hub.register_agent(HubInfo(agent_id="r1", capabilities=["cap1"]))
        await discovery.discover(["cap1"])

        stats = discovery.get_discovery_stats()
        assert stats["agent_id"] == "local-agent"
        assert stats["known_agents"] >= 1
