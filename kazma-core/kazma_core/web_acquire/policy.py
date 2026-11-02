"""Operator-controlled acquisition egress, independent of stored API keys.

Environment controls are an interim operator interface. No target page, tool
argument or model output can opt into a hosted extractor or scraping proxy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar

_job_policy = ContextVar('acquisition_job_policy', default=None)


@contextmanager
def policy_scope(policy):
    """Freeze an operator snapshot during owned work; reset before UI yields."""
    token = _job_policy.set(policy)
    try:
        yield
    finally:
        _job_policy.reset(token)


class AcquisitionPolicyError(ValueError):
    """Invalid policy or forbidden acquisition route."""


@dataclass(frozen=True)
class AcquisitionPolicy:
    mode: str = "local_only"
    providers: frozenset[str] = frozenset()
    browser_network: str = 'required'

    def permits(self, provider: str) -> bool:
        return self.mode == "local_first_with_remote_fallback" and provider in self.providers

    @property
    def cache_namespace(self) -> str:
        return self.mode + ":" + ",".join(sorted(self.providers)) + ":browser=" + self.browser_network


#: What a proxy needs besides its settings: the acquisition policy's two
#: variables (AGENTS.md section 13). Said wherever the proxy is offered
#: (FA-115: the card and its Test did not, and the Test answered "disabled by
#: acquisition policy" with no step).
PROXY_POLICY_STEP = (
    "Kazma routes scraping through the proxy only when KAZMA_ACQUISITION_MODE="
    "local_first_with_remote_fallback and KAZMA_ACQUISITION_REMOTE_PROVIDERS includes "
    "proxy. Set both in .env, then reload Kazma."
)


def proxy_policy_state() -> dict[str, object]:
    """The live acquisition policy as it bears on the proxy, for the Settings
    card: ``{mode, providers, proxy_allowed}``, or ``{error, proxy_allowed:
    False}`` when the variables are invalid. Never raises."""
    try:
        policy = acquisition_policy()
    except AcquisitionPolicyError as exc:
        return {"error": "policy_invalid", "message": str(exc), "proxy_allowed": False}
    return {"mode": policy.mode, "providers": sorted(policy.providers),
            "proxy_allowed": policy.permits("proxy")}


def acquisition_policy() -> AcquisitionPolicy:
    """Fail closed on unknown modes/providers; credentials are never consent."""
    snapshot = _job_policy.get()
    if snapshot is not None:
        return snapshot
    mode = os.environ.get("KAZMA_ACQUISITION_MODE", "local_only").strip().lower()
    if mode not in ("local_only", "local_first_with_remote_fallback"):
        raise AcquisitionPolicyError(
            "Invalid KAZMA_ACQUISITION_MODE; use local_only or "
            "local_first_with_remote_fallback. No acquisition was authorized."
        )
    providers = frozenset(p.strip().lower() for p in
                         os.environ.get("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "").split(",")
                         if p.strip())
    if providers - {"jina", "firecrawl", "proxy"}:
        raise AcquisitionPolicyError(
            "Invalid KAZMA_ACQUISITION_REMOTE_PROVIDERS; allowed names: jina, firecrawl, proxy."
        )
    network = os.environ.get('KAZMA_BROWSER_NETWORK_MODE', 'required').strip().lower()
    if network not in {'required', 'compatibility'}:
        raise AcquisitionPolicyError('Invalid KAZMA_BROWSER_NETWORK_MODE; use required or compatibility. No browser acquisition authorized.')
    return AcquisitionPolicy(mode, providers, network)
