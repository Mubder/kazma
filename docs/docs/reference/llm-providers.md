---
id: llm-providers
title: LLM Providers
sidebar_label: LLM Providers
description: All supported LLM providers — OpenAI-compatible presets and native provider classes (Anthropic, Azure, Bedrock, Gemini).
---

Kazma talks to LLMs over plain HTTP (no SDK lock-in). Most providers speak the
**OpenAI Chat Completions wire format** and work through the generic
`LLMProvider` with `Authorization: Bearer`. A few providers need a **dedicated
native class** because their auth or request schema differs — those are
called out below and dispatched in `model_registry.py`.

Configure providers in **Web UI → Settings → Providers**, or in `kazma.yaml`,
or via environment variables. See [Configuration → Provider
presets](../guide/configuration#52-built-in-provider-presets).

---

## Two integration tiers

| Tier | How it works | Providers |
|---|---|---|
| **OpenAI-compatible** (generic `LLMProvider`) | One httpx client, `Bearer` auth, `/v1/chat/completions` | OpenAI, DeepSeek, Groq, xAI, OpenRouter, Mistral, Together, Cohere, Fireworks, Perplexity, AI21, NVIDIA NIM, Ollama, LM Studio, Custom |
| **Native class** (auth/schema differs) | Dedicated provider class + a branch in `model_registry.get_client()` | Anthropic, Azure, Bedrock, Google Gemini |

> The generic `LLMProvider` always sends `Authorization: Bearer` to
> `/chat/completions`. It **cannot** reach Anthropic-native (`/messages`),
> Azure (`api-key` header + `api-version`), or Bedrock (SigV4). Those need
> their native classes — adding one means a new class + a branch in all three
> client-building sites (`get_client`, `get_model`, `get_client_by_provider`),
> not just a preset entry.

---

## OpenAI-compatible providers

All work with a single API key env var (or configured in the UI). No code
changes — just set the base URL + key.

| Provider | `base_url` | Env var |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| DeepSeek | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| Groq | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| xAI / Grok | `https://api.x.ai/v1` | `XAI_API_KEY` |
| OpenRouter | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| Mistral | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| Together AI | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` |
| Cohere | `https://api.cohere.ai/v1` | `COHERE_API_KEY` |
| Fireworks AI | `https://api.fireworks.ai/inference/v1` | `FIREWORKS_API_KEY` |
| Perplexity | `https://api.perplexity.ai` | `PERPLEXITY_API_KEY` |
| AI21 Labs | `https://api.ai21.com/studio/v1` | `AI21_API_KEY` |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `NVIDIA_API_KEY` |
| Ollama (local) | `http://127.0.0.1:11434/v1` | *(none)* |
| LM Studio (local) | `http://localhost:1234/v1` | *(none)* |
| Custom | *(any)* | *(any)* |

Local providers (Ollama/LM Studio) don't need a real key — a dummy key is
injected automatically.

---

## Native providers

### Anthropic (Claude) — `AnthropicProvider`

Talks to the native `/v1/messages` endpoint with `x-api-key` +
`anthropic-version: 2023-06-01` headers. Translates OpenAI-format messages to
the Anthropic schema (system is top-level, content is typed blocks,
`tool_use`/`tool_result` blocks for tool calling).

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Then select a Claude model in the UI or `kazma.yaml`. The registry
auto-dispatches to `AnthropicProvider` when the active provider is
`anthropic`. No proxy needed.

### Azure OpenAI — `AzureProvider`

OpenAI-compatible payload but with Azure-specific routing: `api-key` header
(not Bearer), deployment-scoped URL
(`.../openai/deployments/<deployment>/chat/completions`), and a required
`api-version` query parameter.

```bash
export AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
export AZURE_OPENAI_API_KEY=<key>
export AZURE_OPENAI_DEPLOYMENT=<deployment-name>
export AZURE_OPENAI_API_VERSION=2024-10-21
```

### AWS Bedrock — `BedrockProvider`

Uses the **Converse API** for a uniform interface across Bedrock-hosted models
(Claude, Llama, Mistral, ...). Requests are SigV4-signed via the standard
`boto3` credential chain (env vars, shared-credentials file, or IAM role).
Requires `pip install boto3` — degrades with a clear message when absent.

```bash
export AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
# optional: AWS_SESSION_TOKEN for temporary credentials
```

Model id examples: `anthropic.claude-3-5-sonnet-20241022-v2:0`,
`meta.llama3-1-70b-instruct-v1:0`.

### Google Gemini — `GeminiProvider`

Vertex AI via Application Default Credentials (ADC). `base_url` is computed
per project/location. Hardcoded model list: `gemini-2.5-flash`,
`gemini-2.5-pro`, `gemini-2.0-flash`, `gemini-2.0-flash-lite`. See
[quickstart](../guide/quickstart) for ADC setup.

---

## Switching providers/models at runtime

The registry auto-corrects provider/model mismatches: calling a model owned
by a different provider than the active one switches both. Use **Settings →
Providers** in the UI, `/config model <name>` (chat), or the Model selector in
the sidebar. See [AGENTS.md §1](../../../AGENTS.md) for the model-registry
invariants.
