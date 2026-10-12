"""Call-local attempt observation, also carried into the Windows browser loop."""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

import httpx

from kazma_core.errors import redact_secrets
from kazma_core.security.ssrf import SSRFError
from kazma_core.web_acquire.policy import AcquisitionPolicyError
from kazma_core.web_acquire.quality import LoginRequiredError
from kazma_core.web_acquire.result import AcquisitionError, FetchAttempt, FetchErrorCode, FetchResult
from kazma_core.web_acquire.scope import AcquisitionScopeError


def classify_failure(exc: BaseException) -> tuple[FetchErrorCode, bool]:
    if isinstance(exc, AcquisitionError):
        return exc.code, exc.transient
    if isinstance(exc, asyncio.CancelledError):
        return FetchErrorCode.CANCELLED, False
    if isinstance(exc, AcquisitionScopeError):
        return FetchErrorCode.SCOPE, False
    if isinstance(exc, AcquisitionPolicyError):
        return FetchErrorCode.POLICY, False
    if isinstance(exc, LoginRequiredError):
        return FetchErrorCode.LOGIN, False
    if isinstance(exc, SSRFError):
        return FetchErrorCode.SECURITY, False
    if isinstance(exc, httpx.ProxyError):
        return FetchErrorCode.ROUTING, True
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return FetchErrorCode.TIMEOUT, True
    if isinstance(exc, (ConnectionError, httpx.ConnectError)):
        return FetchErrorCode.CONNECT, True
    if isinstance(exc, (httpx.ReadError, httpx.RemoteProtocolError)):
        return FetchErrorCode.READ, True
    if isinstance(exc, httpx.DecodingError):
        return FetchErrorCode.DECODING, False
    if isinstance(exc, httpx.HTTPStatusError):
        return FetchErrorCode.HTTP, exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, ImportError):
        return FetchErrorCode.UNAVAILABLE, False
    if isinstance(exc, ValueError):
        return FetchErrorCode.INVALID_URL, False
    return FetchErrorCode.INTERNAL, False


@dataclass
class FetchTrace:
    url: str
    profile: str = "generic"
    attempts: list[FetchAttempt] = field(default_factory=list)
    robots_checks: dict = field(default_factory=dict)
    http_source: FetchResult | None = None
    recovered_source: FetchResult | None = None
    browser_source: FetchResult | None = None
    extract_truncated: bool = False
    dynamic_tabs: bool = False


active_trace: ContextVar[FetchTrace | None] = ContextVar("acquisition_trace", default=None)


class AttemptSpan:
    """Record an attempted operation even when it raises or is cancelled."""

    def __init__(self, backend: str, url: str, *, remote: bool = False):
        self.trace = active_trace.get()
        self.backend, self.url, self.remote = backend, url, remote
        self.fields: dict = {}
        self.started = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        fields = dict(self.fields)
        if exc is not None:
            code, transient = classify_failure(exc)
            fields.update(outcome="cancelled" if code == FetchErrorCode.CANCELLED else "failed",
                          error_code=code, error=redact_secrets(f"{type(exc).__name__}: {exc}")[:300], transient=transient)
        fields.setdefault("outcome", "response")
        if self.trace is not None:
            self.trace.attempts.append(FetchAttempt(
                backend=self.backend, url=self.url, remote=self.remote,
                latency_ms=round((time.perf_counter() - self.started) * 1000, 1), **fields,
            ))
        return False

    def response(self, status: int, **fields) -> None:
        self.fields.update(http_status=status, **fields)
        if status >= 400:
            self.fields.update(outcome="failed", error_code=FetchErrorCode.HTTP,
                               error=f"HTTP {status}", transient=status in (429, 500, 502, 503, 504))
        elif 300 <= status < 400:
            self.fields["outcome"] = "redirect"

    def fail(self, code: FetchErrorCode, message: str) -> None:
        self.fields.update(outcome="failed", error_code=code,
                           error=redact_secrets(message)[:300], transient=False)
