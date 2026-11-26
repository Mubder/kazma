<!-- AGENTS.md, piece 60-providers-1. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 60. Providers, stage 1: correct what exists (2026-11-08)

The owner's goal: any provider in the world, with ease. A review of the live
install that day found the backup model unusable, every DeepSeek call priced
as gpt-4o-mini, stale model settings and duplicate provider rows; the code
review found more. Stage 1 makes what exists correct. Stages 2-4 are the
provider list as data with per-type forms, per-model details with
`provider:model` references, and several keys per provider.

- **A price is the model's own, or unknown** (`kazma_core/model_prices.py`).
  In order:
  1. the owner's price (`models.prices`, Settings → Providers & Connectors →
     Model prices, under `provider:model` or the model name);
  2. free for a server on this network (`url_is_local`);
  3. the Anthropic and Bedrock tables, matched by `table_price`: a dated id
     or variant only, never a newer version (`claude-opus-4-5` is not
     `claude-opus-4`);
  4. otherwise unknown.

  OpenRouter's own `usage.cost` wins over all of them. `LLMConfig`'s prices
  default to None. `LLMResponse.cost_known` says whether a price was found.
  - The ledger's `priced` column counts the calls with no known price apart
    in every sum (`unpriced`).
  - Rows written before the column were priced at gpt-4o-mini's $0.15/$0.60
    (8,839 live rows, $50.05). They are marked unknown, with their cost
    cleared, once, by `_migrate`.
  - A row names its provider, not the client class
    (`llm_ledger.provider_of`).

  The chat stats line, the cost badge, `/cost`, the Dashboard, the daily
  limit, and the research and replay pages say "unknown" (or "at least") and
  never `$0.0000`. A reply row keeps `cost_known`; a row saved before has
  none, and its made-up cost is not shown (`chat.js` `_savedCost`). A write
  to `models.prices`, by any path, rebuilds the cached clients
  (`ModelRegistry._on_settings_written`). Gate: `tests/test_model_prices.py`.
  The Dashboard's Total cost card reads the in-memory trace store, which now
  counts the calls of unknown price (`TraceEntry.priced`,
  `unpriced_llm_calls`): it said `$0.0000` beside "Spent today"'s "—" (live
  2026-11-15). `dashboard._cost_text` and `dashboard.js` `costText` are one rule
  (`tests/test_dashboard_total_cost.py`).
- **Cached input has its own price** (2026-11-25; its gate reads every call
  of the rule under any name it is imported or assigned as, `partial`, and a
  price computed by hand with `/ 1_000_000` or `* 1e-6`): the owner's price, the
  catalog and OpenRouter's list carry `cached_input`, and
  `llm_provider.cost_from_usage` reads the cached and written tokens through
  the ledger's own reading (`token_usage.token_counts`,
  `cache_write_tokens`). DeepSeek's `prompt_cache_hit_tokens` were billed in
  full; Anthropic's cached tokens, left out of its `input_tokens`, replaced
  the uncached ones; Bedrock's were not read. No cached price: a tenth of the
  input price. Gate: `tests/test_cached_input_price.py` (each usage shape; the
  old rule as the control; every caller passes the cached price).
- **Requests newer models take**: OpenAI's own API gets
  `max_completion_tokens`. A 400 that refuses `max_tokens`, or any
  temperature but the default, is asked once more without it, and the fix is
  remembered per endpoint and model (`llm_provider._PARAM_FIXES`,
  `_retry_refused_parameters`).
- **A call to OpenRouter names Kazma** (2026-11-14, REMAINING 4.6,
  `llm_provider._attribution_headers`): `HTTP-Referer: https://kazma.ai` and
  `X-Title: Kazma`, OpenRouter's app attribution, to openrouter.ai's own API
  only, the same from every install (never the person); a provider row's own
  headers win; `KAZMA_OPENROUTER_ATTRIBUTION=0` turns them off. Gate:
  `tests/test_openrouter_attribution.py`.
- **Vision is one order** (`vision_capability.classify`):
  1. a name that says it sees (`*vision*`, `*-vl*`, `KAZMA_VISION_MODELS`);
  2. the text-only families;
  3. the allow-list (GPT-5, o1/o3/o4-mini, Grok 4, GLM-V, `claude-*`);
  4. the `*-instruct` guess.

  Matched on the id after a vendor prefix. Gate:
  `tests/test_provider_request_fixes.py`.
- **Addresses**: `/v1` is added only to an address with no version anywhere
  in its path (DeepInfra's `/v1/openai` was doubled). A preset that moved
  takes its stored rows along at boot (`providers.RETIRED_PRESET_URLS`;
  Cohere → `/compatibility/v1`). Gate: `tests/test_provider_addresses.py`.
- **A server on the owner's network needs no key**
  (`runtime/live_llm.url_is_local`, one IP rule with discovery:
  `security/ssrf.own_network_ip`). That means loopback, private ranges,
  Tailscale's shared space, a host with no dot or a network suffix. Never
  link-local or a metadata address (`169.254.169.254`, `100.100.100.200`).
  - Discovery asks a host only when every address it resolves to is on that
    network (`resolves_to_own_network`). `allow_private` skips every check,
    metadata included: never pass it for this.
  - The providers list carries `local`; the chat and sidebar pickers share
    one rule.
  - The guide finds such a server by address (§21d).

  Gate: `tests/test_network_model_servers.py`.
- **One check, through the client chat uses, before saving**
  (`kazma_core/provider_check.py`):
  - `candidate_entry` is the stored row with the typed values over it; an
    empty or masked key means the stored one.
  - `list_models` calls `ModelRegistry.discover(candidate=)`, which neither
    caches nor prunes.
  - `check_model` builds the client with `build_client` and sends one message
    with `CHECK_MAX_TOKENS` (160, the old probe's lesson). An empty answer
    fails. A failure is a code plus the HTTP status, never the provider's
    reply text.

  Who uses it:
  - `POST /api/providers/check`: the dialog's Test, which saves nothing.
  - `POST /api/providers/{name}/test`: a saved provider. It records
    `healthy` / `chat_failing` / `down`, and keeps a typed key only when the
    check answered.
  - The setup guide.

  The Bearer probe (`provider_probe.py`) is gone: Anthropic, Azure and
  Bedrock failed its Test while chat worked. The dialog's Save opens on an
  answer; a failure offers Save anyway behind a confirm, and an edit of the
  key or address needs a new test. Gates: `tests/test_provider_check.py`,
  `tests/test_provider_conformance.py`, and in a browser (by the sidebar,
  EN/AR, Model prices too) `tests/e2e/test_provider_settings_browser.py`.
- **The backup model can answer** (`kazma_core/failover_backup.py`):
  - `backup_status` gives each chain entry, the provider the runtime would
    send it to, and whether that provider can be called.
  - `suggest_backups` offers a model of each usable provider of another
    vendor (the preset's, in whatever region: `provider_endpoints.vendor_of`;
    `?beside=` a provider about to be the default), only one the runtime would route there, never a provider
    whose last check failed (`down`, `chat_failing`).
  - Saving a backup nothing serves is a 400. Settings shows the status and
    one-click suggestions (`/api/settings/failover-backup`).
  - The drill says `failover_off` and `backup_unusable` for what they are; it
    said "No backup model is configured" for both.

  Gates: `tests/test_failover_backup.py`, `tests/test_model_failover_drill.py`.
- **No switched-on cloud provider without a key**: a save leaves one off
  (`switched_off: no_key`), and the toggle refuses (409 `no_key`). A second
  row at one address is named, not refused (`same_address_as`). Gate:
  `tests/test_provider_list_hygiene.py`.
- **Dead model settings retired**. These were read by nothing, or only to
  add gpt-4o-mini to the model lists:
  - `llm.input_cost_per_1m` and `llm.output_cost_per_1m`;
  - `models.default`, `models.fallback` and `models.router`.

  They are removed from kazma.yaml with `RetiredDefault(removed=True)`.
  `llm.model` is the legacy `custom` profile's model only: as every
  provider's last resort it sent gpt-4o-mini to DeepSeek. A saved profile's
  **Use** switches to it (Load filled fields nothing showed). Gate:
  `tests/test_dead_model_settings.py`.

