"""URL normalization utilities for OpenAI-compatible API endpoints.

Ensures base_url values from kazma.yaml or discovery are always
correctly formatted before being used by httpx or LiteLLM.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

__all__ = ["get_dummy_api_key", "normalize_model_name", "normalize_provider_url"]

# Ports that already include /v1 in their path
_OLLAMA_PORTS = {11434}
_LITELLM_PORTS = {4000}


def _declared_preset_urls() -> frozenset[str]:
    """Base URLs Kazma ships, lowercased and without a trailing slash.

    `providers.py` is a pure data module with no imports of its own, so this
    cannot introduce a cycle. Imported lazily anyway, so that a broken preset
    table can never stop URL handling working.
    """
    global _PRESET_URLS
    if _PRESET_URLS is None:
        try:
            from kazma_core.providers import PROVIDER_PRESETS

            _PRESET_URLS = frozenset(
                str(entry.get("base_url", "")).strip().rstrip("/").lower()
                for entry in PROVIDER_PRESETS.values()
                if str(entry.get("base_url", "")).strip()
            )
        except Exception:  # pragma: no cover - defensive
            _PRESET_URLS = frozenset()
    return _PRESET_URLS


_PRESET_URLS: frozenset[str] | None = None


def _is_declared_preset_url(url: str) -> bool:
    return url.strip().rstrip("/").lower() in _declared_preset_urls()


def normalize_provider_url(
    base_url: str,
    *,
    ensure_v1: bool = True,
    default_scheme: str = "http",
) -> str:
    """Normalize a provider base_url for OpenAI-compatible APIs.

    Rules:
      1. Add scheme (http://) if missing.
      2. Strip trailing slashes.
      3. If ensure_v1=True and path doesn't end with /v1:
         - Append /v1 EXCEPT for Ollama (port 11434) which already uses /v1
           or has its own API path.
      4. Handle edge cases: empty string, localhost, IPs.

    Args:
        base_url: The raw URL string (e.g. "localhost:1234", "http://localhost:1234/v1").
        ensure_v1: Whether to append /v1 if missing (default True).
        default_scheme: Scheme to add if missing (default "http").

    Returns:
        Normalized URL string.

    Examples:
        >>> normalize_provider_url("localhost:1234")
        'http://localhost:1234/v1'
        >>> normalize_provider_url("http://localhost:1234/v1/")
        'http://localhost:1234/v1'
        >>> normalize_provider_url("http://localhost:11434")
        'http://localhost:11434'
        >>> normalize_provider_url("https://api.openai.com/v1")
        'https://api.openai.com/v1'
    """
    if not base_url or not base_url.strip():
        return ""

    url = base_url.strip()

    # A URL we shipped ourselves is not a guess to be improved.
    #
    # This function exists to rescue a URL a *human typed* — "localhost:1234"
    # should become "http://localhost:1234/v1". Applying that guesswork to a
    # preset is not a rescue, it is an override, and the preset is the most
    # authoritative statement we have about where a provider's API lives.
    #
    # Three providers have been broken by the override: Google's
    # /v1beta/openai, Z.AI's /api/paas/v4 (84 of 84 calls 404'd), and
    # Perplexity, whose OpenAI-compatible path carries no version segment at
    # all. Each 404'd on every request while the models endpoint kept answering,
    # so the UI reported the provider as healthy.
    if _is_declared_preset_url(url):
        return url.rstrip("/")

    # Step 1: Add scheme if missing
    if not re.match(r"^https?://", url):
        url = f"{default_scheme}://{url}"

    # Step 2: Parse
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    # Step 2b: Deduplicate repeated /v1 suffixes (e.g. /v1/v1 -> /v1, /v1/v1/v1 -> /v1).
    # Some code paths append /v1 to a base_url that already includes it.
    path = re.sub(r"(/v1)+$", "/v1", path)

    # Step 3: Determine port
    port = parsed.port
    hostname = parsed.hostname or ""

    # Step 4: Append /v1 if needed.
    #
    # "Already versioned" means any /v<N> segment, not literally /v1. Z.AI
    # serves its OpenAI-compatible API at /api/paas/v4, and appending /v1 gave
    # /api/paas/v4/v1/chat/completions — a 404 on every call, which is exactly
    # what the Google exemption below was added for. Patching this vendor by
    # vendor has now happened twice; the rule is the fix.
    already_versioned = re.search(r"/v\d+[a-z0-9]*$", path) is not None
    if ensure_v1 and not already_versioned:
        # Don't append /v1 for Ollama (it uses /api/* endpoints)
        # or LiteLLM proxy (it handles routing itself)
        is_ollama = port in _OLLAMA_PORTS or "ollama" in hostname.lower()
        is_litellm = port in _LITELLM_PORTS or "litellm" in hostname.lower()
        # Google's OpenAI-compat endpoints version their own paths
        # (generativelanguage.googleapis.com/v1beta/openai and
        # {region}-aiplatform.googleapis.com/endpoints/openapi) — appending
        # /v1 produced .../openai/v1/chat/completions, a 404 on every call.
        is_google = hostname.lower().endswith("googleapis.com")

        if not is_ollama and not is_litellm and not is_google:
            path = f"{path}/v1"

    # Step 5: Reconstruct
    normalized = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.params,
            parsed.query,
            "",  # drop fragment
        )
    )

    return normalized


def normalize_model_name(model: str, base_url: str = "") -> str:
    """Normalize a model name for LiteLLM / OpenAI-compatible APIs.

    For LM Studio and local providers, prefix with 'openai/' so LiteLLM
    routes correctly. For Ollama, prefix with 'ollama/'.

    Args:
        model: The raw model name (e.g. "gpt-4o-mini", "llama3.2", "local-model").
        base_url: The provider base_url (used to detect provider type).

    Returns:
        Normalized model string.
    """
    if not model:
        return model

    # Already prefixed (e.g. "openai/gpt-4o-mini", "ollama/llama3.2")
    if "/" in model:
        return model

    # Detect provider from URL
    if base_url:
        parsed = urlparse(base_url)
        port = parsed.port
        hostname = (parsed.hostname or "").lower()

        # Ollama
        if port in _OLLAMA_PORTS or "ollama" in hostname:
            return f"ollama/{model}"

        # LM Studio or other local OpenAI-compatible
        if port == 1234 or "lm-studio" in hostname or "lmstudio" in hostname:
            return f"openai/{model}"

        # Localhost with non-standard port → assume OpenAI-compatible
        if hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
            return f"openai/{model}"

    return model


def get_dummy_api_key(base_url: str, configured_key: str = "") -> str:
    """Return an appropriate API key for the provider.

    LM Studio and Ollama don't need real keys, but LiteLLM/OpenAI SDK
    require a non-empty string. Returns a dummy key for local providers.

    Args:
        base_url: The provider base_url.
        configured_key: The key from config (may be empty).

    Returns:
        An API key string (real or dummy).
    """
    if configured_key and configured_key.strip():
        return configured_key.strip()

    if not base_url:
        return "not-needed"

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    port = parsed.port

    # Local providers get dummy keys
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
        if port == 1234:  # LM Studio
            return "sk-lm-studio-dummy-key"
        if port in _OLLAMA_PORTS:  # Ollama
            return "ollama"
        if port in _LITELLM_PORTS:  # LiteLLM proxy
            return "sk-litellm-dummy-key"
        return "not-needed"

    # Remote providers need a real key. Do NOT invent ``not-needed`` here —
    # that string was sent as ``Authorization: Bearer not-needed`` and
    # DeepSeek 401'd while Settings > Test (which reads the registry) passed.
    return (configured_key or "").strip()
