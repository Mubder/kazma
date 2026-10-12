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
