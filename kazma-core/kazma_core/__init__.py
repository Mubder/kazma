"""Public core exports, loaded on demand without importing runtime services."""
from __future__ import annotations

import importlib
import os
from typing import Any

# Disable interactive terminal credential prompts across all Git operations
os.environ["GIT_TERMINAL_PROMPT"] = "0"
os.environ["GIT_ASKPASS"] = "echo"

_EXPORT_GROUPS = {
    "audit_logger": ("AuditEntry", "AuditLogger"),
    "authorization_flow": ("ApprovalResult", "AuthorizationFlow", "AuthorizationRequest", "DenialResult"),
    "cost_breaker": ("CostCircuitBreaker", "create_cost_breaker"),
    "cultural_context": ("CulturalContext", "CulturalEvent"),
    "dialect_detector": ("DialectDetector", "DialectResult"),
    "division_sandbox": ("CrossDivisionRequest", "DivisionSandbox", "SandboxResult"),
    "llm_provider": ("LLMConfig", "LLMError", "LLMProvider", "LLMResponse"),
    "majlis": ("ConversationPhase", "MajlisProtocol", "MajlisResponse"),
    "pacing": ("ConversationPacing", "Intent", "TransitionDecision"),
    "rbac": ("DIVISIONS", "PermissionResult", "RBACEngine"),
    "router": ("AgentRequest", "AgentResponse", "DialectRouter"),
    "state": ("AgentState", "initial_state"),
    "tokenizer": ("DualEngineTokenizer", "TokenResult"),
    "tone_adapter": ("FormalityLevel", "ToneAdapter", "ToneProfile"),
    "service_container": ("ServiceContainer", "get_container", "reset_container"),
    "tracing": ("KazmaTracer", "create_tracer"),
}
_EXPORTS = {name: module for module, names in _EXPORT_GROUPS.items() for name in names}


def __getattr__(name: str) -> Any:
    """Preserve the public API while leaf imports keep their own dependencies."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"kazma_core.{module}"), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "ServiceContainer",
    "get_container",
    "reset_container",
    "AgentState",
    "ApprovalResult",
    "AuditEntry",
    "AuditLogger",
    "AuthorizationFlow",
    "AuthorizationRequest",
    "CostCircuitBreaker",
    "CulturalContext",
    "CulturalEvent",
    "ConversationPhase",
    "ConversationPacing",
    "CrossDivisionRequest",
    "DenialResult",
    "DialectDetector",
    "DialectResult",
    "DialectRouter",
    "DIVISIONS",
    "DivisionSandbox",
    "DualEngineTokenizer",
    "FormalityLevel",
    "Intent",
    "KazmaTracer",
    "LLMConfig",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "MajlisProtocol",
    "MajlisResponse",
    "AgentRequest",
    "AgentResponse",
    "PermissionResult",
    "RBACEngine",
    "SandboxResult",
    "ToneAdapter",
    "ToneProfile",
    "TokenResult",
    "TransitionDecision",
    "create_cost_breaker",
    "create_tracer",
    "initial_state",
]
