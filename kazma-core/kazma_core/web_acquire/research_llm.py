"""Job-local model/failover snapshots without changing the active profile."""
from __future__ import annotations

import copy
from contextvars import ContextVar
from dataclasses import dataclass

from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

active_route = ContextVar('research_llm_route', default=None)


@dataclass(frozen=True)
class FrozenFailover:
    clients: tuple
    cooldown_seconds: float


class ResearchRoute:
    def __init__(self, unchanged):
        self.unchanged, self.bound = unchanged, None

    def resolve(self):
        if self.bound is not None:
            return self.bound
        from kazma_core.model_registry import get_model_registry
        from kazma_core.agent.nonstop import get_nonstop_config

        if not self.unchanged():
            raise AcquisitionError(FetchErrorCode.POLICY, 'Research model configuration changed before dispatch.')
        registry = get_model_registry()
        primary = self._snapshot(registry.get_client())
        model = getattr(getattr(primary, 'config', None), 'model', None)
        nonstop = get_nonstop_config()
        chain = list(nonstop.failover.chain) if nonstop.failover.enabled else []
        if len(chain) > 8:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Research failover chain exceeds eight models; dispatch refused.')
        fallbacks = tuple((name, self._snapshot(registry.get_client(name))) for name in dict.fromkeys(chain) if name and name != model)
        if not self.unchanged():
            raise AcquisitionError(FetchErrorCode.POLICY, 'Research model configuration changed during route capture.')
        self.bound = (primary, model, FrozenFailover(fallbacks, nonstop.failover.cooldown_seconds))
        return self.bound

    @staticmethod
    def _snapshot(client):
        # Preserve native adapter type and its resolved deployment coordinates.
        # Never share a mutable config or generic HTTP client with live settings.
        from kazma_core.bedrock_llm import BedrockProvider
        from kazma_core.google_llm import GeminiProvider
        if isinstance(client, BedrockProvider) or (isinstance(client, GeminiProvider) and not client._use_ai_studio):
            raise AcquisitionError(FetchErrorCode.ROUTING, 'Durable research requires a frozen credential route; AWS credential-chain and Google ADC routes are not supported. Select a local or explicit API-key provider.')
        frozen = copy.copy(client)
        if hasattr(client, 'config'):
            frozen.config = copy.deepcopy(client.config)
        if hasattr(frozen, '_http'):
            frozen._http = None
        if hasattr(frozen, '_pending_closes'):
            frozen._pending_closes = set()
        frozen._egress_frozen = True
        return frozen

    async def close(self):
        if self.bound is not None:
            clients = [self.bound[0], *(client for _, client in self.bound[2].clients)]
            for client in clients:
                # Only close the private HTTP connection; SDK objects may be shared.
                http = getattr(client, '_http', None)
                if http is not None:
                    await http.aclose()
                    client._http = None


def resolved_route():
    route = active_route.get()
    return route.resolve() if route is not None else None
