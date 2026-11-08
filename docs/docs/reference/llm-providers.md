---
id: llm-providers
title: LLM Providers
sidebar_label: LLM Providers
description: All supported LLM providers — OpenAI-compatible presets and native provider classes (Anthropic, Azure, Bedrock, Gemini).
---

> This page is published at [kazma.ai/docs/llm-providers/](https://kazma.ai/docs/llm-providers/) ([Arabic](https://kazma.ai/ar/docs/llm-providers/)).

Kazma talks to LLMs over plain HTTP (no SDK lock-in). Most providers speak the
**OpenAI Chat Completions wire format** and work through the generic
`LLMProvider` with `Authorization: Bearer`. A few providers need a **dedicated
native class** because their auth or request schema differs — those are
called out below and dispatched in `model_registry.py`.

Configure providers in **Web UI → Settings → Providers**, or in `kazma.yaml`,
or via environment variables. See [Configuration → Provider
presets](../guide/configuration#52-built-in-provider-presets).

**Voice is not chat.** The same OpenAI/Groq/Cohere **key** is used for
Whisper STT, but Whisper models are hidden from chat pickers and Provider
Test (`/chat/completions`). Pick STT/TTS under [Settings →
Voice](../guide/voice-and-media).

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

### Optional LiteLLM proxy

Set `KAZMA_LITELLM_URL` (or `llm.gateway.url`) to send **OpenAI-compatible**
calls through a LiteLLM proxy (spend, keys, fallbacks live in the proxy).
This is not exclusive: Anthropic / Azure / Bedrock / Gemini stay native.
Loopback Ollama / LM Studio stay direct unless `KAZMA_LITELLM_LOCAL=1`.
`KAZMA_LITELLM=0` disables the proxy. Kazma does not `import litellm`.

## OpenAI-compatible providers

All work with a single API key env var (or configured in the UI). No code
changes — just set the base URL + key. Settings → Providers & Connectors →
Add provider offers every one below, from the same list the server and the
setup guide read (`GET /api/providers/catalog`).

| Provider | `base_url` | Env var |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| DeepSeek | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| Groq | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| xAI / Grok | `https://api.x.ai/v1` | `XAI_API_KEY` |
| OpenRouter | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| Mistral | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| Together AI | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` |
| Cohere | `https://api.cohere.ai/compatibility/v1` | `COHERE_API_KEY` |
| Fireworks AI | `https://api.fireworks.ai/inference/v1` | `FIREWORKS_API_KEY` |
| Perplexity | `https://api.perplexity.ai` | `PERPLEXITY_API_KEY` |
| AI21 Labs | `https://api.ai21.com/studio/v1` | `AI21_API_KEY` |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `NVIDIA_API_KEY` |
| Z.AI (GLM) | `https://api.z.ai/api/paas/v4` | `ZAI_API_KEY` |
| Moonshot AI (Kimi) | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` |
| MiniMax | `https://api.minimax.io/v1` | `MINIMAX_API_KEY` |
| Qwen (Alibaba Cloud Model Studio) | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| Cerebras | `https://api.cerebras.ai/v1` | `CEREBRAS_API_KEY` |
| DeepInfra | `https://api.deepinfra.com/v1/openai` | `DEEPINFRA_API_KEY` |
| Hugging Face Inference Providers | `https://router.huggingface.co/v1` | `HUGGINGFACE_API_KEY` |
| SambaNova Cloud | `https://api.sambanova.ai/v1` | `SAMBANOVA_API_KEY` |
| SiliconFlow | `https://api.siliconflow.com/v1` | `SILICONFLOW_API_KEY` |
| Novita AI | `https://api.novita.ai/openai/v1` | `NOVITA_API_KEY` |
| Hyperbolic | `https://api.hyperbolic.xyz/v1` | `HYPERBOLIC_API_KEY` |
| Cloudflare Workers AI | `https://api.cloudflare.com/client/v4/accounts/ACCOUNT_ID/ai/v1` (your account id in place of `ACCOUNT_ID`) | `CLOUDFLARE_API_KEY` |
| Ollama (local) | `http://127.0.0.1:11434/v1` | *(none)* |
| LM Studio (local) | `http://localhost:1234/v1` | *(none)* |
| vLLM (local) | `http://localhost:8000/v1` | *(none)* |
| llama.cpp server (local) | `http://localhost:8080/v1` | *(none)* |
| LocalAI (local) | `http://localhost:8080/v1` | *(none)* |
| Jan (local) | `http://localhost:1337/v1` | *(none)* |
| Custom | *(any)* | *(any)* |

A fresh install lists the providers it always listed; the newer ones are
added from the dialog. A provider added under a name of your own ("Work
Azure") keeps the type it was made from, and speaks that type's format.
A provider you delete stays deleted, one Kazma lists by itself included:
it is not listed or added back at the next start until you add it again.

### Headers, and an address as typed

Under **Advanced** in the dialog:

- **Headers** are sent with every request to the provider: a gateway's own
  sign-in, an organisation id, OpenRouter's app name. Their values are kept
  as secrets (encrypted, shown masked).
- **Use this address exactly as typed** stops Kazma adding `/v1` to an
  address that names no version, for a server whose path has none.
- **This provider serves speech, not chat** keeps a speech-to-text service
  out of every chat model list. Such providers are listed under **Speech
  services**; Settings → Voice chooses which one Kazma uses. A provider
  counts as one without the switch when its address is a speech endpoint
  (`/audio/transcriptions`, `/audio/speech`, any path with `audio`, Deepgram,
  ElevenLabs or AssemblyAI) or when every model it lists is a speech model.
  Kazma sends a speech service no chat message, so **Test** has nothing to
  check and **Save** opens without it.

### Model servers on this computer or your network

A model server on this computer or on your own network needs no key, and its
calls cost nothing. That means Ollama, LM Studio, vLLM, llama.cpp, LocalAI,
Jan or any other OpenAI-compatible server, at any of these addresses:

- this computer (`localhost`, `127.0.0.1`);
- a private address (`192.168.x.x`, `10.x.x.x`, `172.16–31.x.x`, IPv6 `fd00::/8`);
- a Tailscale address (`100.64.0.0/10`);
- a host name with no dot (`gpu-box`), or one ending in `.local`, `.lan`,
  `.home.arpa` or `.internal` (`host.docker.internal`).

Such a server can be tested without a key, its models listed and chosen in
the chat's picker. The setup guide finds one on another computer by its
address ("A server on another computer on your network"). Cloud metadata
addresses (`169.254.169.254`, Alibaba Cloud's `100.100.100.200`) never count
as yours.

**An address is used as you give it** when it already names a version
anywhere in its path (DeepInfra's `/v1/openai`, Z.AI's `/api/paas/v4`);
`/v1` is added only to an address with none (`https://openrouter.ai/api`).

### Test, before saving

**Test Connection** sends the model one short message through the same client
chat uses for the provider (Anthropic's own messages API for Anthropic,
Bedrock's for Bedrock, and so on). In the Add / Edit dialog it checks the
values as typed, without saving them; **Save** opens once the test answered,
and a failed test offers **Save anyway** behind a confirmation. A failure says
what failed (the key was refused, no such model, rate limited, unreachable,
an empty answer) and the HTTP status, never the provider's reply text. A
cloud provider with no key is saved switched off, and cannot be switched on
until it has one. Two providers at one address are named on the list.

### Prices

A call's cost is the model's own price, or it is shown as **unknown**:

1. your price for the model (**Settings → Providers & Connectors → Models →
   Model prices**, in US dollars per million tokens). That card also lists the
   models used in the last 30 days that a working provider serves with no
   known price;
2. free, for a server on this computer or your network;
3. the price table of the Anthropic and Bedrock clients, for the models they
   name (a dated id such as `claude-sonnet-4-20250514` matches
   `claude-sonnet-4`; a newer version never does);
4. otherwise unknown: shown as "price unknown" in the chat, `$ —` on the
   chat's cost badge, a count of unpriced calls on the Dashboard and in
   `/cost`, and left out of the daily cost limit.

OpenRouter reports what each call cost (`usage.cost`), and that wins.

### Newer OpenAI models

OpenAI's own API is sent `max_completion_tokens` (its reasoning models refuse
`max_tokens`). Any other endpoint that refuses `max_tokens`, or any
temperature but the default, is asked once more without it, and the fix is
remembered for that endpoint and model.

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

Set up in Settings → Providers & Connectors → Add provider → Azure OpenAI:
the endpoint, the key, the API version and the deployment names (Azure routes
by deployment, so a deployment name is the model name). The environment still
works for an install configured by it; Settings' values win.

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

Set up in Settings → Providers & Connectors → Add provider → AWS Bedrock:
the region and an access key id and secret (a session token for temporary
credentials). Left empty, the AWS credentials of this computer are used
(environment, shared credentials file, or an IAM role):

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

## Model details

Settings → Providers & Connectors → **Models** holds what you set by hand:
the default model, Model details, Model prices and saved profiles.
**Model details** shows every model you can choose with what Kazma knows
about it, and where each fact came from. It lists the default model, the
models used in the last 30 days and the ones whose details you changed;
search finds any other, and **Show all** lists every one:

1. **your value** (Edit, on the model's row): its context window, output
   limit, whether it sees images, whether it uses tools;
2. **the provider's own model list**: OpenRouter states each model's
   context length, output limit, input kinds, tool support and price; Groq,
   Mistral, vLLM and Gemini state context windows and limits;
3. **Kazma's catalog**: whether well-known models see images and use tools,
   and the vendor's own list price, on its own API only (dated).

A context window is the provider's own or yours, never a guess: the catalog
holds no sizes. When a window is known, Kazma sends no more than it, and no
more than a known output limit.

## Choosing a model

Models are named with their provider, `provider:model` (`openrouter:google/gemini-2.5-flash`,
`groq:llama-3.3-70b-versatile`), so the same model name from two providers is
two choices. A name alone still works: it goes to the provider in use when
that one lists it.

- **The default model** is the one new chats and chat apps with no model of
  their own use: Settings → Providers & Connectors → Models, the
  sidebar's picker, or `/model default` in a chat app (admin).
- **The chat's picker** sets the model of the chat it is in, and no other.
  A chat opened again shows the model it runs with.
- **In a chat app**, `/model` sets that chat's model; `/model reset` makes it
  follow the default again ([slash commands](./slash-commands#model)).

The registry auto-corrects provider/model mismatches: calling a model owned
by a different provider than the active one switches both. A saved
profile's **Use** switches to its model, and to the provider that serves it.

## More than one key

A provider can hold several keys (Advanced → **More keys** in the dialog),
each with a label of your own. When a call fails for its key -- the key is
refused, out of credit, or out of requests after Kazma's own waits -- that
key rests and Kazma sends the call again with the next key, and tells you
once. A refused key rests a day, one out of credit six hours, one out of
requests fifteen minutes or as long as the provider asked.

## Checked in the background

Once an hour Kazma asks every switched-on chat provider that has what its
sign-in needs for its model list: no model call, no tokens. A provider that
does not answer twice in a row is shown as Unreachable, and when it is the
provider in use you are told once, and again when it answers. A key the
provider refused gives way to its next key, if it has one. Providers with no
model list (Azure, Bedrock, Vertex) are not checked this way.

**The backup model** (Settings → System → Non-stop → failover) must be one a
provider can answer for: switched on, with a key or on your network, listing
the model. Settings shows which provider would serve each backup, refuses to
save one nothing serves, and offers a model of each other working provider.
A backup from the same vendor as the model in use is flagged: one outage
stops both. See [AGENTS.md §1](https://github.com/Mubder/kazma/blob/main/AGENTS.md) for the model-registry
invariants.
