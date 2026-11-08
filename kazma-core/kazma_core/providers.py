"""The provider catalog: every provider Kazma knows, as data (providers stage 2).

One list, read by every screen and every code path: the server's presets,
the Settings dialog (``GET /api/providers/catalog``), the setup guide, the
client builder and model discovery. Until 2026-11-08 the Settings page kept
its own ten presets, already different from these twenty, and a provider's
wire format, sign-in and default model lived in three other tables.

Each entry says:

* ``name`` -- what the pages show; ``docs`` -- where the provider hands out
  keys (or its documentation);
* ``base_url`` -- its address (empty when the client computes it: Azure,
  Bedrock, Vertex); ``models_endpoint`` -- where its model list is;
* ``api_style`` -- the wire format the client speaks (``openai``,
  ``anthropic``, ``azure``, ``bedrock``, ``google``);
* ``auth`` -- how it signs in: ``bearer``, ``x-api-key``, ``api-key``,
  ``aws`` (SigV4), ``google`` (an AI Studio key or Vertex credentials) or
  ``none``; ``auth_header`` is the older spelling, kept for readers of it;
* ``form`` -- which fields the Settings dialog asks for: ``key`` (an address
  and a key), ``azure`` (endpoint, key, API version, deployment),
  ``bedrock`` (region and AWS keys), ``google`` (AI Studio key, or Vertex
  project and region), ``local`` (an address, no key);
* ``kind`` -- ``chat``, or ``speech`` for a speech-to-text service (kept out
  of every chat model list);
* ``local`` -- a server on this computer or network (no key, no cost);
* ``default_model`` -- the model to try when nothing else names one;
* ``url_hint`` -- what to change in the address, when it has a placeholder.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "GEMINI_MODELS",
    "PROVIDER_PRESETS",
    "RETIRED_PRESET_URLS",
    "catalog",
    "default_model_for",
    "env_key_for",
    "get_base_url",
    "get_preset",
    "is_local_preset",
    "is_speech_provider",
    "list_providers",
]

# Well-known Gemini models available via Vertex AI.
# These are hardcoded because Vertex AI does not expose a static /models
# REST endpoint -- the base URL is computed dynamically per project/location.
GEMINI_MODELS: list[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]


#: A preset's earlier address, by preset: a stored row still on it is moved
#: to the preset's address at boot (``seed_missing_presets``). An address the
#: owner typed is never touched -- only one the preset itself once gave.
RETIRED_PRESET_URLS: dict[str, tuple[str, ...]] = {
    "cohere": ("https://api.cohere.ai/v1",),
}


def _p(name: str, base_url: str, docs: str = "", *, api_style: str = "openai", auth: str = "bearer",
       form: str = "key", kind: str = "chat", local: bool = False, default_model: str = "",
       models_endpoint: str = "/models", url_hint: str = "") -> dict[str, Any]:
    header = {"bearer": "Bearer", "x-api-key": "x-api-key", "api-key": "api-key", "aws": "Bearer",
              "google": "Bearer", "none": ""}[auth]
    return {
        "name": name, "base_url": base_url, "docs": docs, "api_style": api_style, "auth": auth,
        "auth_header": header, "form": form, "kind": kind, "local": local,
        "default_model": default_model, "models_endpoint": models_endpoint, "url_hint": url_hint,
    }


PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    # ── Model makers ────────────────────────────────────────────────────
    "openai": _p("OpenAI", "https://api.openai.com/v1", "https://platform.openai.com/api-keys",
                 default_model="gpt-5-mini"),
    "anthropic": _p("Anthropic", "https://api.anthropic.com/v1", "https://console.anthropic.com/keys",
                    api_style="anthropic", auth="x-api-key", default_model="claude-sonnet-5-5"),
    "google": _p("Google Gemini", "", "https://aistudio.google.com/apikey", api_style="google",
                 auth="google", form="google", default_model="gemini-2.5-flash", models_endpoint=""),
    "deepseek": _p("DeepSeek", "https://api.deepseek.com/v1", "https://platform.deepseek.com/api_keys",
                   default_model="deepseek-chat"),
    "xai": _p("xAI / Grok", "https://api.x.ai/v1", "https://console.x.ai", default_model="grok-4"),
    "mistral": _p("Mistral AI", "https://api.mistral.ai/v1", "https://console.mistral.ai/api-keys",
                  default_model="mistral-large-latest"),
    "cohere": _p("Cohere", "https://api.cohere.ai/compatibility/v1", "https://dashboard.cohere.com/api-keys",
                 default_model="command-a-03-2025"),
    # Z.AI serves its OpenAI-compatible API at /v4, not /v1: declared here so
    # nothing appends a version to it -- the mistake that made every GLM call
    # 404 while the Settings page reported the provider healthy.
    "zai": _p("Z.AI (GLM)", "https://api.z.ai/api/paas/v4", "https://z.ai/manage-apikey/apikey-list",
              default_model="glm-4.6"),
    "moonshot": _p("Moonshot AI (Kimi)", "https://api.moonshot.ai/v1", "https://platform.moonshot.ai/console/api-keys",
                   default_model="kimi-k2-0905-preview"),
    "minimax": _p("MiniMax", "https://api.minimax.io/v1", "https://www.minimax.io/platform/user-center/basic-information/interface-key",
                  default_model="MiniMax-M2"),
    "dashscope": _p("Qwen (Alibaba Cloud Model Studio)", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "https://modelstudio.console.alibabacloud.com/?tab=playground#/api-key",
                    default_model="qwen-plus"),
    "perplexity": _p("Perplexity", "https://api.perplexity.ai", "https://www.perplexity.ai/settings/api",
                     default_model="sonar"),
    "ai21": _p("AI21 Labs", "https://api.ai21.com/studio/v1", "https://studio.ai21.com/account/api-key",
               default_model="jamba-large"),
    # ── Model hosts and routers ────────────────────────────────────────
    "openrouter": _p("OpenRouter", "https://openrouter.ai/api/v1", "https://openrouter.ai/keys",
                     default_model="openai/gpt-5-mini"),
    "groq": _p("Groq", "https://api.groq.com/openai/v1", "https://console.groq.com/keys",
               default_model="llama-3.3-70b-versatile"),
    "cerebras": _p("Cerebras", "https://api.cerebras.ai/v1", "https://cloud.cerebras.ai/platform",
                   default_model="llama-3.3-70b"),
    "together": _p("Together AI", "https://api.together.xyz/v1", "https://api.together.ai/settings/api-keys",
                   default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "fireworks": _p("Fireworks AI", "https://api.fireworks.ai/inference/v1", "https://fireworks.ai/account/api-keys"),
    "deepinfra": _p("DeepInfra", "https://api.deepinfra.com/v1/openai", "https://deepinfra.com/dash/api_keys",
                    default_model="meta-llama/Llama-3.3-70B-Instruct"),
    "huggingface": _p("Hugging Face Inference Providers", "https://router.huggingface.co/v1",
                      "https://huggingface.co/settings/tokens"),
    "nvidia": _p("NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "https://build.nvidia.com",
                 default_model="meta/llama-3.3-70b-instruct"),
    "sambanova": _p("SambaNova Cloud", "https://api.sambanova.ai/v1", "https://cloud.sambanova.ai/apis",
                    default_model="Meta-Llama-3.3-70B-Instruct"),
    "siliconflow": _p("SiliconFlow", "https://api.siliconflow.com/v1", "https://cloud.siliconflow.com/account/ak"),
    "novita": _p("Novita AI", "https://api.novita.ai/openai/v1", "https://novita.ai/settings/key-management"),
    "hyperbolic": _p("Hyperbolic", "https://api.hyperbolic.xyz/v1", "https://app.hyperbolic.xyz/settings"),
    "cloudflare": _p("Cloudflare Workers AI", "https://api.cloudflare.com/client/v4/accounts/ACCOUNT_ID/ai/v1",
                     "https://dash.cloudflare.com/profile/api-tokens",
                     default_model="@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                     url_hint="Replace ACCOUNT_ID with your Cloudflare account id."),
    # ── Cloud platforms with their own sign-in ────────────────────────
    "azure": _p("Azure OpenAI", "", "https://learn.microsoft.com/azure/ai-services/openai",
                api_style="azure", auth="api-key", form="azure", models_endpoint=""),
    "bedrock": _p("AWS Bedrock", "", "https://console.aws.amazon.com/bedrock",
                  api_style="bedrock", auth="aws", form="bedrock", models_endpoint=""),
    # ── On this computer or network ───────────────────────────────────
    "ollama": _p("Ollama (Local)", "http://127.0.0.1:11434/v1", "https://ollama.com/download",
                 auth="none", form="local", local=True),
    "lm-studio": _p("LM Studio (Local)", "http://localhost:1234/v1", "https://lmstudio.ai",
                    auth="none", form="local", local=True),
    "vllm": _p("vLLM", "http://localhost:8000/v1", "https://docs.vllm.ai", auth="none", form="local", local=True),
    "llamacpp": _p("llama.cpp server", "http://localhost:8080/v1", "https://github.com/ggml-org/llama.cpp",
                   auth="none", form="local", local=True),
    "localai": _p("LocalAI", "http://localhost:8080/v1", "https://localai.io", auth="none", form="local", local=True),
    "jan": _p("Jan", "http://localhost:1337/v1", "https://jan.ai", auth="none", form="local", local=True),
    # ── Your own ──────────────────────────────────────────────────────
    "custom": _p("Custom Endpoint", "", ""),
}


def catalog() -> list[dict[str, Any]]:
    """Every preset as the pages read it (``GET /api/providers/catalog``):
    ``custom`` last, as the dialog's "any other" choice."""
    rows = [{"id": pid, **meta} for pid, meta in PROVIDER_PRESETS.items() if pid != "custom"]
    rows.append({"id": "custom", **PROVIDER_PRESETS["custom"]})
    return rows


def is_local_preset(provider: str) -> bool:
    """True for a preset that runs on this computer or network."""
    return bool((PROVIDER_PRESETS.get((provider or "").strip().lower()) or {}).get("local"))


def is_speech_provider(entry: Any) -> bool:
    """True for a provider row (or preset id) that serves speech, not chat.

    A row is speech by its address, its ``kind`` or its models
    (``models.modality.row_is_speech``)."""
    if isinstance(entry, str):
        return str((PROVIDER_PRESETS.get(entry.strip().lower()) or {}).get("kind") or "chat") == "speech"
    if isinstance(entry, dict):
        from kazma_core.models.modality import row_is_speech

        return row_is_speech(entry)
    return False


def env_key_for(provider: str) -> str:
    """The environment variable Kazma reads a key from. ``Z.AI`` -> ``Z_AI_API_KEY``.

    Every non-alphanumeric character becomes ``_``. Replacing only hyphens left
    the dot in place and produced ``Z.AI_API_KEY`` -- a name no shell can
    export -- so the environment fallback was silently unreachable for any
    provider whose name is not plain alphanumerics. The live-probe script had
    already hit this and fixed it locally; the runtime that actually resolves
    keys for chat and for the Test button kept the broken spelling.

    One definition, so a provider that can be configured by env can be
    configured by env everywhere.
    """
    import re

    return f"{re.sub(r'[^A-Z0-9]', '_', provider.upper())}_API_KEY"


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
#: The wire format (``api_style``) is the catalog's (``PROVIDER_PRESETS``).
CAPABILITY_OVERRIDES: dict[str, dict[str, object]] = {
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
    preset = PROVIDER_PRESETS.get(provider.lower()) or {}
    if preset.get("api_style"):
        merged["api_style"] = preset["api_style"]
    override = CAPABILITY_OVERRIDES.get(provider.lower(), {})
    for key, value in override.items():
        if key == "supports" and isinstance(value, dict):
            merged["supports"].update(value)  # type: ignore[union-attr]
        else:
            merged[key] = value
    return merged


#: The model to use for a provider when NOTHING else has said which.
#:
#: This is data about a vendor -- "openai's small chat model is called
#: gpt-5-mini" -- and belongs in one place: the catalog's ``default_model``. It lived in two: an inline dict
#: in `model_registry` (5 providers) and `_FALLBACK_CHAT_MODEL` in
#: `runtime/model_switch` (8). They had already drifted; the registry copy
#: was missing xai, openrouter and mistral, so the same question got
#: different answers depending on which code path asked it.
#:
#: What does NOT belong anywhere is a bare `or "gpt-4o-mini"` as a universal
#: last resort. That is not a default, it is a guess that an unconfigured
#: install should dial OpenAI -- and on an install with no OpenAI key it
#: produced a 401 that looked like a broken provider rather than an empty
#: setting. Ask this map for the provider IN HAND; if the provider is not
#: known, the answer is "" and the caller must say so out loud.
DEFAULT_MODEL_FOR: dict[str, str] = {
    pid: str(meta["default_model"]) for pid, meta in PROVIDER_PRESETS.items() if meta.get("default_model")
}


def default_model_for(provider: str) -> str:
    """The vendor default for *provider*, or "" when we do not know.

    Empty is a real answer and callers must handle it. Substituting some
    other vendor's model because this one is unknown is how an install ends
    up reporting a model it is not running.
    """
    return DEFAULT_MODEL_FOR.get((provider or "").strip().lower(), "")
