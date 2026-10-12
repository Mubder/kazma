"""Acquisition outcomes; content is never used as an error discriminator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class FetchErrorCode(StrEnum):
    INVALID_URL = "invalid_url"
    POLICY = "policy_error"
    ROUTING = "proxy_routing_error"
    THROTTLED = "origin_throttled"
    ROBOTS = "robots_disallowed"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    SECURITY = "security_blocked"
    SCOPE = "scope_blocked"
    REDIRECT = "redirect_error"
    HTTP = "http_error"
    CONNECT = "connect_error"
    READ = "read_error"
    TIMEOUT = "timeout"
    DEADLINE = "acquisition_deadline"
    DECODING = "decoding_error"
    UNAVAILABLE = "backend_unavailable"
    EMPTY = "empty_extract"
    CHALLENGE = "challenge_page"
    LOGIN = "login_required"
    PROVIDER_RESPONSE = "invalid_provider_response"
    UNSUPPORTED_CONTENT = "unsupported_content"
    LIMIT = "resource_limit"
    STORAGE = "storage_error"
    WORKSPACE = "workspace_path_error"
    EXISTS = "file_exists"
    INTERNAL = "acquisition_error"
    CANCELLED = "cancelled"


class AcquisitionError(Exception):
    """A classified boundary failure, distinct from returned page content."""

    def __init__(self, code: FetchErrorCode, message: str, *, transient: bool = False):
        super().__init__(message)
        self.code, self.transient = code, transient


@dataclass(frozen=True)
class FetchAttempt:
    backend: str
    url: str
    outcome: str
    latency_ms: float
    final_url: str | None = None
    http_status: int | None = None
    error_code: FetchErrorCode | None = None
    error: str | None = None
    transient: bool = False
    remote: bool = False
    wire_bytes: int | None = None
    decoded_bytes: int | None = None
    truncated: bool | None = None
    usage: dict[str, Any] | None = None


@dataclass
class FetchResult:
    """One terminal outcome, with observable attempts and honest unknowns.

    ``url`` is the requested URL. ``final_url=None`` means the backend did not
    establish redirect provenance. Byte counts are observed, never inferred
    from string length. Attempts describe this call; a cache hit makes no new
    acquisition attempts. Provider charges/balances remain unknown until the
    accounting boundary supplies provider evidence.
    """

    ok: bool
    url: str
    text: str = ""
    error: str | None = None
    purpose: str = "generic"
    char_count: int = 0
    latency_ms: float = 0.0
    backend: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    final_url: str | None = None
    http_status: int | None = None
    error_code: FetchErrorCode | None = None
    transient: bool = False
    attempts: tuple[FetchAttempt, ...] = ()
    wire_bytes: int | None = None
    decoded_bytes: int | None = None
    truncated: bool = False
    truncation_reasons: tuple[str, ...] = ()
    quality: str = "unknown"
    links: tuple[str, ...] = ()
    links_complete: bool | None = None
    cache_hit: bool = False
    usage: dict[str, Any] | None = None
    credits: dict[str, Any] | None = None

    @property
    def is_error_stub(self) -> bool:
        # Kept for callers of the old façade. Page text beginning "Error:"
        # can be legitimate content and is not a transport failure.
        return not self.ok

    def legacy_text(self) -> str:
        if not self.ok:
            return f"Error: {self.error or 'Acquisition failed.'}"
        if self.http_status is not None and self.http_status >= 400 and self.quality == "json":
            return f"[HTTP {self.http_status} — JSON response returned as-is]\n{self.text}"
        if self.truncated:
            return f"[Acquisition partial: {', '.join(self.truncation_reasons) or 'resource limit'}]\n{self.text}"
        return self.text

    def evidence(self) -> dict[str, Any]:
        """Small serializable projection; exclude extracted bodies and links."""
        return {
            "url": self.url, "ok": self.ok, "final_url": self.final_url,
            "backend": self.backend, "http_status": self.http_status,
            "error_code": self.error_code, "error": self.error,
            "transient": self.transient, "quality": self.quality,
            "char_count": self.char_count, "latency_ms": self.latency_ms,
            "wire_bytes": self.wire_bytes, "decoded_bytes": self.decoded_bytes,
            "truncated": self.truncated, "truncation_reasons": self.truncation_reasons,
            "links_count": len(self.links), "links_complete": self.links_complete,
            "cache_hit": self.cache_hit, "attempts": [asdict(a) for a in self.attempts],
            "usage": self.usage, "credits": self.credits,
            "robots": self.meta.get("robots", {"mode": "not_requested", "checks": []}),
            "browser_process": self.meta.get("browser_process"),
        }

    def notice(self) -> str:
        label = {"httpx": "local HTTP", "chromium": "local Chromium",
                 "jina": "Jina Reader", "firecrawl": "Firecrawl"}.get(self.backend, "unknown backend")
        parts = [f"Acquisition: {label}"]
        if self.http_status is not None:
            parts.append(f"HTTP {self.http_status}")
        if not self.ok and self.error_code:
            parts.append(f"error {self.error_code}")
        if not self.ok and self.attempts:
            parts.append("attempts: " + ", ".join(
                f"{a.backend} {a.outcome}" +
                (f" (HTTP {a.http_status})" if a.http_status is not None else
                 f" ({a.error_code})" if a.error_code else "")
                for a in self.attempts[-8:]))
        if self.meta.get("robots", {}).get("mode") == "disabled_by_operator":
            parts.append("robots disabled by operator")
        elif self.meta.get("robots", {}).get("mode") == "enforced":
            parts.append("robots enforced")
        if self.cache_hit:
            checks = self.meta.get('robots', {}).get('checks', [])
            parts.append("cached page; robots metadata checked over HTTP" if any(c.get('metadata_requests') for c in checks)
                         else "cached; no new acquisition requests")
        if any(c.get('proxy_used') for c in self.meta.get('robots', {}).get('checks', [])):
            parts.append("robots proxy used; usage/credits unavailable")
        if self.truncated:
            parts.append("partial: " + ", ".join(self.truncation_reasons))
        if self.quality in ("thin", "unverified"):
            parts.append(f"quality {self.quality}")
        remote = {("proxy" if a.backend in ("httpx", "chromium") else a.backend)
                  for a in self.attempts if a.remote}
        if remote:
            parts.append("third-party attempts: " + ", ".join(sorted(remote)))
        if remote or self.backend in ("jina", "firecrawl") or self.meta.get("proxy_used"):
            from kazma_core.web_acquire import provider_usage

            observations = provider_usage._observations(self.usage)
            if not observations:
                parts.append("usage/credits unavailable" if self.credits is None else "usage unavailable; credits recorded")
            else:
                parts.extend(f"Jina reported tokens {item['reported']}" for item in observations)
                if not self.usage['complete']:
                    parts.append("other attempt usage unavailable")
                parts.append("credits unavailable" if self.credits is None else "credits recorded")
        if self.final_url is None:
            parts.append("final URL unavailable")
        return "[" + "; ".join(parts) + "]"


@dataclass(frozen=True)
class ExtractSaveResult:
    acquisition: FetchResult
    path: str | None = None
    absolute_path: str | None = None
    saved_bytes: int | None = None
    error: str | None = None
    error_code: FetchErrorCode | None = None

    @property
    def ok(self) -> bool:
        return self.acquisition.ok and self.path is not None and self.error is None
