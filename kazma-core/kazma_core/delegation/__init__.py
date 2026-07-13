"""Kazma Delegation — Agent-to-agent delegation protocol.

Enables secure, auditable delegation of sub-tasks between autonomous
Kazma agents without global shared state. Each agent operates
independently while collaborating on shared objectives.

**Status: UNWIRED library.** Implemented and tested, but not imported by the
production agent runner, SwarmEngine, gateway, or UI. See
``docs/audits/UNWIRED_INVENTORY.md``. Import paths stay stable until product
decides to wire this into SwarmEngine or retire it.
"""

from kazma_core.delegation.discovery import AgentDiscovery
from kazma_core.delegation.orchestrator import (
    DelegationOrchestrator,
    OrchestrationResult,
    SubTask,
)
from kazma_core.delegation.protocol import (
    DelegationProtocol,
    DelegationRequest,
    DelegationResponse,
    DelegationResult,
    RequestStatus,
)
from kazma_core.delegation.security import DelegationSecurity
from kazma_core.delegation.swarm import (
    CascadeResult,
    ConsensusResult,
    SwarmIntelligence,
)

__all__ = [
    "AgentDiscovery",
    "CascadeResult",
    "ConsensusResult",
    "DelegationOrchestrator",
    "DelegationProtocol",
    "DelegationRequest",
    "DelegationResponse",
    "DelegationResult",
    "DelegationSecurity",
    "OrchestrationResult",
    "RequestStatus",
    "SubTask",
    "SwarmIntelligence",
]
