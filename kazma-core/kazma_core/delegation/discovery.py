"""Agent Discovery — Discovers and ranks other Kazma agents by capability.

Uses the KazmaHub registry for capability-based agent discovery and
maintains a local reputation cache for ranking discovered agents.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentInfo:
    """Discovered agent information with metadata."""
    agent_id: str
    capabilities: list[str]
    endpoint: str = ""
    reputation: float = 1.0
    last_seen: float = 0.0
    response_latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveryResult:
    """Result of an agent discovery query."""
    agents: list[AgentInfo]
    query_capabilities: list[str]
    total_found: int
    discovery_time_ms: float


class AgentDiscovery:
    """Discovers other Kazma agents on the network.

    Agents are ranked by:
    1. Capability match score (exact matches weighted higher)
    2. Reputation score (updated based on task outcomes)
    3. Response latency (lower is better)

    Args:
        agent_id: This agent's unique identifier.
        hub: KazmaHub instance for registry queries.
    """

    def __init__(self, agent_id: str, hub: Any) -> None:
        self.agent_id = agent_id
        self.hub = hub
        self.known_agents: dict[str, AgentInfo] = {}
        self._local_capabilities: list[str] = []

    async def announce(self, capabilities: list[str]) -> None:
        """Announce this agent's presence and capabilities.

        Publishes to:
        - KazmaHub registry
        - Local known_agents cache

        Args:
            capabilities: List of capabilities this agent offers.
        """
        self._local_capabilities = capabilities
        info = AgentInfo(
            agent_id=self.agent_id,
            capabilities=capabilities,
            last_seen=time.time(),
            metadata={"announced_at": time.time()},
        )

        # Register with hub
        if hasattr(self.hub, "register_agent"):
            from kazma_core.hub import AgentInfo as HubAgentInfo

            hub_info = HubAgentInfo(
                agent_id=self.agent_id,
                capabilities=capabilities,
            )
            await self.hub.register_agent(hub_info)

        # Cache locally
        self.known_agents[self.agent_id] = info
        logger.info(
            "Agent announced: %s (caps=%s)", self.agent_id, capabilities
        )

    async def discover(
        self,
        required_capabilities: list[str],
        max_results: int = 10,
    ) -> list[AgentInfo]:
        """Discover agents with required capabilities.

        Returns agents sorted by:
        1. Capability match score
        2. Reputation score
        3. Response latency (lower is better)

        Args:
            required_capabilities: Capabilities needed.
            max_results: Maximum agents to return.

        Returns:
            Sorted list of matching AgentInfo.
        """
        start = time.time()
        candidates: list[AgentInfo] = []

        # Query hub if available
        if hasattr(self.hub, "find_agents_by_capabilities"):
            hub_agents = await self.hub.find_agents_by_capabilities(required_capabilities)
            for ha in hub_agents:
                info = AgentInfo(
                    agent_id=ha.agent_id,
                    capabilities=ha.capabilities,
                    endpoint=getattr(ha, "endpoint", ""),
                    reputation=getattr(ha, "reputation", 1.0),
                    last_seen=time.time(),
                    metadata=getattr(ha, "metadata", {}),
                )
                candidates.append(info)
                self.known_agents[info.agent_id] = info

        # Also check local cache
        for aid, info in self.known_agents.items():
            if aid == self.agent_id:
                continue  # Skip self
            if aid not in {c.agent_id for c in candidates}:
                if all(c in info.capabilities for c in required_capabilities):
                    candidates.append(info)

        # Filter out self from all candidates
        candidates = [c for c in candidates if c.agent_id != self.agent_id]

        # Score and rank
        scored = []
        for agent in candidates:
            score = self._score_agent(agent, required_capabilities)
            scored.append((score, agent))

        scored.sort(key=lambda x: (-x[0], x[1].response_latency_ms))
        results = [agent for _, agent in scored[:max_results]]

        elapsed_ms = (time.time() - start) * 1000
        logger.info(
            "Discovery complete: %d agents found for caps=%s (%.1fms)",
            len(results),
            required_capabilities,
            elapsed_ms,
        )
        return results

    def _score_agent(
        self, agent: AgentInfo, required: list[str]
    ) -> float:
        """Score an agent based on capability match and reputation."""
        if not required:
            return agent.reputation

        # Capability match: fraction of required capabilities the agent has
        matches = sum(1 for c in required if c in agent.capabilities)
        capability_score = matches / len(required)

        # Weight: 70% capability, 30% reputation
        return 0.7 * capability_score + 0.3 * agent.reputation

    async def get_agent_info(self, agent_id: str) -> Optional[AgentInfo]:
        """Get detailed info about a specific agent.

        Args:
            agent_id: The agent to look up.

        Returns:
            AgentInfo if found, None otherwise.
        """
        # Check local cache first
        if agent_id in self.known_agents:
            return self.known_agents[agent_id]

        # Query hub
        if hasattr(self.hub, "get_agent"):
            ha = await self.hub.get_agent(agent_id)
            if ha is not None:
                info = AgentInfo(
                    agent_id=ha.agent_id,
                    capabilities=ha.capabilities,
                    endpoint=getattr(ha, "endpoint", ""),
                    reputation=getattr(ha, "reputation", 1.0),
                    last_seen=time.time(),
                    metadata=getattr(ha, "metadata", {}),
                )
                self.known_agents[agent_id] = info
                return info

        return None

    async def update_reputation(self, agent_id: str, score: float) -> None:
        """Update agent reputation based on task outcomes.

        Score is clamped to [0.0, 2.0] where:
        - 0.0 = completely unreliable
        - 1.0 = default
        - 2.0 = highly reliable

        Args:
            agent_id: Agent to update.
            score: New reputation score (0.0-2.0).
        """
        clamped = max(0.0, min(2.0, score))

        if agent_id in self.known_agents:
            # Exponential moving average
            old = self.known_agents[agent_id].reputation
            self.known_agents[agent_id].reputation = 0.7 * old + 0.3 * clamped

        # Update hub if available
        if hasattr(self.hub, "update_agent_reputation"):
            await self.hub.update_agent_reputation(agent_id, clamped)

        logger.info(
            "Reputation updated for %s: %.2f", agent_id, clamped
        )

    async def get_available_agents(self) -> list[AgentInfo]:
        """Get all known available agents (excluding self)."""
        agents = [
            info
            for aid, info in self.known_agents.items()
            if aid != self.agent_id
        ]
        # Also include hub-registered agents not in local cache
        if hasattr(self.hub, "list_agents"):
            hub_agents = await self.hub.list_agents()
            known_ids = {a.agent_id for a in agents}
            for ha in hub_agents:
                if ha.agent_id != self.agent_id and ha.agent_id not in known_ids:
                    agents.append(AgentInfo(
                        agent_id=ha.agent_id,
                        capabilities=ha.capabilities,
                        endpoint=getattr(ha, "endpoint", ""),
                        reputation=getattr(ha, "reputation", 1.0),
                    ))
        return agents

    def get_discovery_stats(self) -> dict[str, Any]:
        """Return discovery statistics."""
        agents = [
            info for aid, info in self.known_agents.items()
            if aid != self.agent_id
        ]
        return {
            "agent_id": self.agent_id,
            "known_agents": len(agents),
            "local_capabilities": self._local_capabilities,
            "avg_reputation": (
                sum(a.reputation for a in agents) / len(agents)
                if agents
                else 0.0
            ),
        }
