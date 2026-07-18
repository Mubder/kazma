"""Kazma Delegation — Agent-to-agent delegation protocol.

Enables secure, auditable delegation of sub-tasks between autonomous
Kazma agents without global shared state. Each agent operates
independently while collaborating on shared objectives.

**Status: UNWIRED library (product decision 2026-07-18).**

Implemented and tested, but **not** imported by the production agent runner,
SwarmEngine, gateway, or UI. SwarmEngine is the live multi-agent path.
This package remains import-stable for experiments and tests until product
either wires it into SwarmEngine or archives it under ``archive/``.

See ``docs/audits/UNWIRED_INVENTORY.md`` and ``docs/audits/AUDIT_FULL_2026-07-18.md``.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "kazma_core.delegation is an unwired library (not used by SwarmEngine). "
    "Prefer SwarmEngine for multi-agent work. See docs/audits/UNWIRED_INVENTORY.md.",
    DeprecationWarning,
    stacklevel=2,
)

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
