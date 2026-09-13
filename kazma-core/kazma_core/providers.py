"""Provider presets with default base URLs and model discovery endpoints."""

__all__ = ["GEMINI_MODELS", "PROVIDER_PRESETS", "get_base_url", "get_preset", "list_providers"]

# Well-known Gemini models available via Vertex AI.
# These are hardcoded because Vertex AI does not expose a static /models
# REST endpoint — the base URL is computed dynamically per project/location.
GEMINI_MODELS: list[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "models_endpoint": "/models",
        "auth_header": "x-api-key",
        "docs": "https://console.anthropic.com/keys",
    },
    "groq": {
        "name": "Groq (Free Tier)",
        "base_url": "https://api.groq.com/openai/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://console.groq.com/keys",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://platform.deepseek.com/api_keys",
    },
    "google": {
        "name": "Google Gemini",
        "base_url": "",  # computed by GeminiProvider from project/location
        "models_endpoint": "",  # models are hardcoded below (Vertex AI has no static /models)
        "auth_header": "Bearer",
        "docs": "https://console.cloud.google.com/vertex-ai",
    },
    "xai": {
        "name": "xAI / Grok",
        "base_url": "https://api.x.ai/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://console.x.ai",
    },
    # Z.AI serves its OpenAI-compatible API at /v4, not /v1. Declared here so
    # nothing appends a version to it -- the mistake that made every GLM call
    # 404 while the Settings page reported the provider healthy.
    "zai": {
        "name": "Z.AI (GLM)",
        "base_url": "https://api.z.ai/api/paas/v4",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://docs.z.ai",
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://openrouter.ai/keys",
    },
    "ollama": {
        "name": "Ollama (Local)",
        "base_url": "http://127.0.0.1:11434/v1",
        "models_endpoint": "/models",
        "auth_header": "",
        "docs": "",
    },
    "lm-studio": {
        "name": "LM Studio (Local)",
        "base_url": "http://localhost:1234/v1",
        "models_endpoint": "/models",
        "auth_header": "",
        "docs": "",
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://build.nvidia.com",
    },
    "mistral": {
        "name": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://console.mistral.ai/api-keys",
    },
    "together": {
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://api.together.ai/settings/api-keys",
    },
    "cohere": {
        "name": "Cohere",
        "base_url": "https://api.cohere.ai/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://dashboard.cohere.com/api-keys",
    },
    "fireworks": {
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://fireworks.ai/account/api-keys",
    },
    "perplexity": {
        "name": "Perplexity",
        "base_url": "https://api.perplexity.ai",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://www.perplexity.ai/settings/api",
    },
    "ai21": {
        "name": "AI21 Labs",
        "base_url": "https://api.ai21.com/studio/v1",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "https://studio.ai21.com/account/api-key",
    },
    "azure": {
        "name": "Azure OpenAI",
        "base_url": "",  # computed by AzureProvider from resource name + deployment
        "models_endpoint": "",
        "auth_header": "api-key",
        "docs": "https://learn.microsoft.com/azure/ai-services/openai",
    },
    "bedrock": {
        "name": "AWS Bedrock",
        "base_url": "",  # computed by BedrockProvider from region
        "models_endpoint": "",
        "auth_header": "Bearer",  # SigV4 signing applied by BedrockProvider
        "docs": "https://console.aws.amazon.com/bedrock",
    },
    "custom": {
        "name": "Custom Endpoint",
        "base_url": "",
        "models_endpoint": "/models",
        "auth_header": "Bearer",
        "docs": "",
    },
}


def get_preset(provider: str) -> dict[str, str] | None:
    """Get the preset dict for a provider key, or None if unknown."""
    return PROVIDER_PRESETS.get(provider)


def list_providers() -> list[dict[str, str]]:
    """Return all known providers as a list of {key, name, base_url}."""
    return [
        {"key": key, "name": val["name"], "base_url": val["base_url"]}
        for key, val in PROVIDER_PRESETS.items()
    ]


def get_base_url(provider: str) -> str:
    """Get the default base URL for a provider, or empty string."""
    preset = PROVIDER_PRESETS.get(provider)
    return preset["base_url"] if preset else ""


# ══════════════════════════════════════════════════════════════════════════
# Capabilities — declared, not inferred
# ══════════════════════════════════════════════════════════════════════════
#
# Phase 2 of docs/plans/PROVIDER_LAYER_PLAN.md.
#
# Every provider difference that has broken Kazma was a difference the preset
# could not express, so it lived as an `if` in the transport instead: ~40
# vendor branches in llm_provider.py and hostname exemptions in url_utils.py.
# Three providers 404'd on every call because the API version was guessed
# rather than declared, and one rejected the `developer` role with nothing
# anywhere saying it would.
#
# `None` means NOT VERIFIED, and it is a first-class value here. Writing
# `"tools": True` for a provider nobody has tested would trade one silent
# assumption for another wearing a schema; the UI shows unknowns as unknown,
# and `scripts/provider_conformance.py --live` is how a None becomes a bool.

#: Applied to every provider unless overridden below.
CAPABILITY_DEFAULTS: dict[str, object] = {
    # Which wire format the client speaks. Not a vendor name: several
    # providers share "openai" and differ only in data.
    "api_style": "openai",
    # The role name used for the system turn. OpenAI now also accepts
    # "developer"; Z.AI rejects it outright with 400.
    "system_role": "system",
    "supports": {"tools": None, "streaming": None, "json_mode": None, "vision": None},
    "max_context": None,
}

#: Only what is known. An entry here is either structural (the adapter that
#: serves it) or measured — never assumed.
CAPABILITY_OVERRIDES: dict[str, dict[str, object]] = {
    "anthropic": {"api_style": "anthropic"},
    "bedrock": {"api_style": "bedrock"},
    "google": {"api_style": "google"},
    "azure": {"api_style": "azure"},
    # Measured 2026-09-13 by scripts/provider_conformance.py against
    # glm-5.3: chat, system turn and tool calling all pass. `developer` is
    # rejected with `400 Incorrect role information`, so system_role is not a
    # default here — it is a finding.
    "zai": {
        "system_role": "system",
        "supports": {"tools": True, "streaming": None, "json_mode": None, "vision": None},
    },
    # Measured 2026-09-13 against a local ollama serving mistral:7b — model
    # list, chat, system turn and tool calling all pass. Tool support here is
    # per-model in practice (a model without a tool template will refuse), so
    # this records what the *endpoint* accepts, which is the question the
    # transport asks.
    "ollama": {
        "system_role": "system",
        "supports": {"tools": True, "streaming": None, "json_mode": None, "vision": None},
    },
    # Measured 2026-09-13 pinned to `openai/gpt-4o-mini`: model list, chat,
    # system turn and tool calling all pass.
    #
    # The first run failed system_turn and tool_call and was NOT recorded. It
    # had auto-picked `inference-net/schematron-v2-turbo`, a niche model that
    # ignores the system turn and whose upstream serves no tool endpoint —
    # OpenRouter itself answered correctly, with
    # `No endpoints found that support tool use`. OpenRouter routes to hundreds
    # of models and tool support is per-model, so what this records is that the
    # *endpoint* speaks tools when the chosen model does, which is the question
    # the transport asks.
    "openrouter": {
        "system_role": "system",
        "supports": {"tools": True, "streaming": None, "json_mode": None, "vision": None},
    },
}


def capabilities(provider: str) -> dict[str, object]:
    """Declared capabilities for *provider*, with unknowns as ``None``.

    Always returns the full shape, so callers never branch on a missing key.
    """
    merged: dict[str, object] = {
        "api_style": CAPABILITY_DEFAULTS["api_style"],
        "system_role": CAPABILITY_DEFAULTS["system_role"],
        "supports": dict(CAPABILITY_DEFAULTS["supports"]),  # type: ignore[arg-type]
        "max_context": CAPABILITY_DEFAULTS["max_context"],
    }
    override = CAPABILITY_OVERRIDES.get(provider.lower(), {})
    for key, value in override.items():
        if key == "supports" and isinstance(value, dict):
            merged["supports"].update(value)  # type: ignore[union-attr]
        else:
            merged[key] = value
    return merged


def system_role_for(provider: str) -> str:
    """The role name to use for the system turn. Read this instead of assuming."""
    return str(capabilities(provider)["system_role"])
