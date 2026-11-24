<!-- AGENTS.md, piece 01-provider-model-resolution. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
## Critical Subsystems (READ BEFORE MODIFYING)

### 1. Provider/Model Resolution (`kazma-core/kazma_core/model_registry.py`)
- `get_client(model)` auto-corrects provider/model mismatches at runtime
- `set_active_model()` switches BOTH model AND provider via `find_provider_for_model()`
- Never change one without the other or the LLM call goes to the wrong API endpoint
- **The client class comes from the provider's declared `api_style`**
  (`kazma_core/provider_adapters.py`, `build_client`, the one constructor
  every `ModelRegistry` path calls): `google`→`GeminiProvider`, `anthropic`→
  `AnthropicProvider`, `azure`→`AzureProvider`, `bedrock`→`BedrockProvider`,
  else (`openai`) the generic `LLMProvider`; a provider's style is in
  `providers.capabilities`. The generic `LLMProvider` always sends
  `Authorization: Bearer` to `/chat/completions` — it CANNOT reach
  Anthropic-native (`/messages`), Azure (`api-key` header + `api-version`),
  or Bedrock (SigV4). Adding a non-Bearer provider means a new class + a row
  in `provider_adapters._ADAPTERS` + its `api_style` in the capability table,
  not just a preset.
- A provider with no usable key is replaced by one that has a key in
  `get_client()` — and that is announced, never silent; provider keys are
  install-scoped so it does not happen for a key that is there. Both in §38.
- **Where Spaces is enforced, a model is also the person's** (S2-03, §70):
  `get_client(None)` and `resolve_live_client` give work for a person their
  own model (`spaces.person_models.model_for_work`), and every provider
  class's `chat`/`chat_stream` asks `_person_call` first: it refuses a model
  the person may not use (`_refuse_a_model_not_allowed`) and returns the
  client carrying the person's own key, when they keep one (S2-04), to
  which the method hands the whole call. A new provider class does the
  same (or hands the call to `super()`).

