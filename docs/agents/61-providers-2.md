<!-- AGENTS.md, piece 61-providers-2. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 61. Providers, stages 2-4: one list, model choice, several keys (2026-11-08)

The rest of the plan in §60, done the same day.

**Stage 2: one provider list, kept as data** (`kazma_core/providers.py`).
- **The catalog is the one list.** `PROVIDER_PRESETS` is it: 35 providers,
  each with `api_style`, `auth`, `form`, `kind`, `local`, `default_model`,
  `models_endpoint` and `url_hint`. `capabilities()`, `DEFAULT_MODEL_FOR`,
  the setup guide (`_guided`, `_LOCAL`) and `provider_check.LOCAL_NAMES` read
  it. The Settings dialog reads `GET /api/providers/catalog`; the page's own
  ten presets (`providers.js` `PRESETS`) are gone.
- **Seeding.** Only the presets an install had before are seeded as rows
  (`model_registry_store.SEEDED_PRESETS`). The new ones are offered by the
  dialog and the guide.
- **A row says which preset it was made from** (`preset`). `build_client`,
  discovery and the credentials rule read the preset, so a row named
  "Work Azure" speaks Azure.
- **A row keeps the dialog's other fields**
  (`model_registry_store.OPTIONAL_FIELDS`):
  - `kind` (`chat` or `speech`);
  - `url_as_typed`;
  - `secret_headers`, sent on every request (`LLMConfig.extra_headers`);
  - Azure's `api_version`;
  - Bedrock's `region` and `aws_*` keys (`provider_adapters._bedrock_kwargs`);
  - extra keys (`api_keys`, stage 4).

  Every secret among them is vaulted by its name: a field called `headers`
  would not have been, so it is `secret_headers`. The API masks them all
  (`_mask_provider_entry`), and a masked value posted back keeps the stored
  one (`provider_check.merge_typed_secrets`).
- **An address kept as typed** is one set every normalizer asks
  (`url_utils.is_as_typed`). The registry fills it from the rows, and a
  check adds the address it is checking.
- **One rule for whether a provider has what its sign-in needs**
  (`provider_check.credentials_problem`): a key, Bedrock's AWS keys or the
  AWS chain, a Vertex project, or nothing for this network. The check, the
  save, the toggle (`_can_be_called`), the backup (`failover_backup._usable`),
  the pickers and the background check all use it.
- **Speech is not chat** (`models/modality.row_is_speech`). A row whose
  address is a speech endpoint (`_is_speech_address`: a path with an `audio`
  part or ending at a speech endpoint, `SPEECH_PATH_ENDS`, or a speech-only
  host, `SPEECH_HOSTS`; it wins over `kind: chat`), whose `kind` is speech,
  or whose every model is a speech model, is kept out of model lookup, the
  fallback choice, the pickers and backups, and is listed under Speech
  services. `is_speech_model` also matches a vendor-prefixed id
  (`nvidia/parakeet-...`). Test sends a speech row no chat message (code
  `speech_provider`, health untouched) and the dialog's Save opens without
  it. Live's "Cohere Transcriptions" (`/v2/audio/transcriptions`, no models,
  no kind) passed the model rule as chat (2026-11-08). Gate:
  `tests/test_speech_address_and_deleted_presets.py` (the model-only rule as
  the control).
- **A deleted catalog provider stays deleted**
  (`model_registry_store.REMOVED_PRESETS_KEY`, a list of the deleted names).
  `list_providers` merges every missing seeded preset in and every start
  seeds them, so Delete did nothing for `zai`, `cohere` and the rest. Deleting
  a `SEEDED_PRESETS` row records it in the same `batch_set` as the list; the
  list and `seed_missing_presets` skip it; adding it again takes it off.
  Same gate (no record as the control).
- **The providers page stays short** (2026-11-08, the owner: "why the
  providers page is too long"; live's ran to ~2,000 lines). The default
  model, Model details, Model prices and saved profiles are the Models
  sub-tab (`hubSubtab` `models`, deep link `?tab=models`), styled as the
  provider control plane (`.mc-*` in kazma.css, 21c): four counts, the
  default model's panel, and Model details as one table that scrolls under
  its own header and becomes labelled cards on a narrow panel. The table
  lists `shownModelDetails()`: In use is the default, models with calls in
  the last 30 days (`llm_ledger.used_models`, the route's `used`), the ones
  you changed and the one being edited; All models, a provider or words
  for the rest; a header sorts (`sortModelDetails`); unsaved edits are
  said and Save opens only then (`modelDetailsDirty`).
  A provider that is off and cannot be called (`callable`, from
  `provider_check.credentials_problem`) folds away (`providerFoldedAway`)
  unless it is the one open. Model prices offers a model only when a working
  provider serves it with no price known (the route's `_still_to_price`:
  live listed the 1 November audit's `fault-*` drill models). Gates:
  `tests/test_providers_page_short.py` (the ledger's raw list as the
  control), the browser case in `tests/e2e/test_provider_settings_browser.py`.
- **Voice is one setting, `voice.*`.** `gateway.voice.*` is moved by
  `config_store._MOVED_KEYS`. `reconcile_from_yaml` now moves keys BEFORE it
  seeds: kazma.yaml's `voice.enabled: false` would otherwise win over an
  owner's stored `gateway.voice.enabled: true`.

Gates: `tests/test_provider_catalog.py`, with the old behaviours as
controls.

**Stage 3: per-model details and model choice.**
- **References** (`kazma_core/model_refs.py`): `provider:model`. The part
  before the first `:` is a provider only when a row has that name, so
  Ollama's `llama3:8b` stays bare. The registry's `find_provider_for_model`,
  `get_client`, `get_model` and `set_active_model` take one. A bare name
  that two providers list goes to the provider in use.
- **Details** (`kazma_core/model_details.py`), the first that says wins:
  1. the owner's (`models.details`, validated whole);
  2. what the provider's model list stated: OpenRouter, Groq, Mistral, vLLM
     and Gemini fields, `from_listing`, kept by discovery in
     `registry.model_details.<name>` (runtime);
  3. `CATALOG`: vision, tools and the vendor's own list price, on its own
     API only, dated `CATALOG_AS_OF`.

  The catalog holds no window or output limit: the owner's 2026-10-18 rule
  (§29D). The details are used for:
  - the context window, capped at a known size (`token_counter`);
  - the request's output limit;
  - vision, which a known value decides before the name rules;
  - the price, after the owner's price and before unknown.
- **One list for every picker** (`kazma_core/models/options.py`,
  `GET /api/models/options`): every model a provider that can answer a chat
  offers, by reference, with its details and the default.
  - The web chat's picker sets that chat's model: localStorage
    `kazma.chatModel.<session>`, else the chat's `run_model`, which is now a
    client row field, else the default. It never changes the install's model.
  - The sidebar and Settings → Model details set the default
    (`PUT /api/settings/active_model`).
  - The terminal app's picker lists references (it merged duplicates).
- **The chat apps' `/model` is the chat's**
  (`kazma_core/chat_model_choice.py`: `chat.models`, runtime, at most 2,000
  chats).
  - `/model <n|ref|name>` sets the chat's model, and `/model reset` clears
    it. Any allowed user may do either.
  - `/model default ...` changes the install's default. It is admin-only:
    `slash_commands.changes_global_config` lists it, and `/_models_select`
    is no longer listed.
  - The gateway pins the chat's model around `run_agent_turn`.
  - Telegram's buttons carry references; elsewhere the list shows every
    model, numbered (it showed five per provider).

Gates: `tests/test_model_refs_and_details.py`.

**Stage 4: several keys, checked in the background.**
- **Extra keys** (`kazma_core/provider_keys.py`): a row's `api_keys` are
  tried after its own key. `current_key` gives the first key not resting;
  `key_failure` reads why a call failed (refused 401/403, no credit 402 or a
  "no balance" answer, or out of requests after the client's waits).
  `next_key` rests the failed key (`REST_SECONDS`) and returns the next. The
  state is `registry.key_state.<name>` (runtime) and names keys by
  fingerprint only.
- **The switch happens inside the call.** `LLMProvider._with_key_switches`
  wraps `chat`, and `chat_stream` switches before any text has streamed;
  Anthropic's `chat` does the same. The registry's `switch_provider_key`
  drops the cached client and raises `llm.key_switched.<name>`.
- **The background check** (`kazma_core/provider_health.py`, maintenance
  entry "provider health") runs hourly. It lists each callable chat
  provider's models: no tokens, results in `registry.provider_check.<name>`.
  - Unreachable twice in a row: `health: down`, and when it is the provider
    in use, one `llm.provider_unreachable.<name>`.
  - An answer again: Not tested (`unknown`), and for the provider in use one
    recovery note.
  - A refused key rests when the provider has another.

Gates: `tests/test_provider_keys.py`, and in a browser (by the sidebar)
`tests/e2e/test_provider_settings_browser.py`. The browser test found that
the setup wall stayed up over a network server in use: readiness now
counts the active provider's address (`setup_api.compute_setup_status`).

