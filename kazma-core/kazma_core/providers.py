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
