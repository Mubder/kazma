# Kazma — Providers & Models Architecture

## Single Source of Truth: ModelRegistry

The `ModelRegistry` (`kazma-core/kazma_core/model_registry.py`) is the **absolute
single source of truth** for all model and provider configuration. Every interface
(Web UI, TUI, Telegram Gateway) MUST fetch available models from the registry and
use `find_provider_for_model()` for execution routing.

```
┌──────────────────────────────────────────────────────────────────┐
│                       ConfigStore (SQLite)                       │
│                    ~/.kazma/config.db                            │
├──────────────────────────────────────────────────────────────────┤
│                        ModelRegistry                             │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐   │
│  │ Providers   │  │ Saved Models │  │ Discovered Models      │   │
│  │ (api key,   │  │ (named       │  │ (fetched from provider │   │
│  │  base_url)  │  │  profiles)   │  │  /models endpoint)     │   │
│  └──────┬──────┘  └──────┬───────┘  └───────────┬────────────┘   │
│         └────────────────┼──────────────────────┘               │
│                   find_provider_for_model(model_id)               │
└──────────────────────────────────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
       Web UI (/api/    TUI (/model     Telegram
        providers,      command)        /model command
        /api/models)
```

## Key Methods

| Method | Purpose |
|:---|:---|
| `list_providers()` | Returns all configured providers with API keys masked |
| `discover_models(provider_name)` | Fetches model list from provider's `/models` endpoint |
| `find_provider_for_model(model_id)` | Searches BOTH manual `models` AND `_discovered_models` to find which provider owns a model |
| `list_model_profiles()` | Returns saved named model profiles |
| `serialize()` | Writes discovered models to ConfigStore SQLite (survives restarts) |
| `get_client(model)` | Returns `LLMProvider` with correct `base_url` + `api_key` for the given model |

## Routing Chain (fixed)

Previously, the system had a **split-brain** where:
1. `chat.py` had a hardcoded `"http://127.0.0.1:1234/v1"` (LM Studio) fallback
2. `sse_chat.py` changed the model name but never resolved `base_url` or `api_key`
3. `/api/models` returned empty because it didn't include discovered models

After Sprint 12 fixes:
1. All routing goes through `find_provider_for_model()` — no hardcoded fallbacks
2. `sse_chat.py` passes resolved `base_url` + `api_key` to `reconfigure()`
3. `/api/providers` merges `discovered_models` + `all_models` per provider

## Interfaces Must Use

| Interface | Endpoint | Purpose |
|:---|:---|:---|
| Web UI dropdowns | `GET /api/providers` | Provider-grouped model lists for chat, swarm workers |
| TUI `/model` command | `get_universal_models()` | Plain text model list |
| Telegram `/model` | `telegram_bus.model_list_text()` | Markdown model list |
| LLM dispatch | `registry.get_client(model)` | Resolved provider with credentials |
