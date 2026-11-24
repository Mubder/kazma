<!-- AGENTS.md, piece 13-proxy. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 13. Proxy Provider Addon (`kazma-core/kazma_core/proxy/`)

An opt-in, pluggable scraping proxy so `read_url` / `crawl_site` / `web_search`
can route through a configured proxy (anyip.io first). Acquisition is
**local-only by default** (2026-10-10): `web_acquire.policy` requires
`KAZMA_ACQUISITION_MODE=local_first_with_remote_fallback` AND `proxy` in
`KAZMA_ACQUISITION_REMOTE_PROVIDERS`. Stored proxy settings alone cannot
authorize that route. HTTP clients always disable ambient environment proxies.

- **Proxy resolution is fail-closed (2026-10-11).** `proxy/config.py` reads one
  live category snapshot per route; nested provider reads share it. Only an
  absent/default `none` selects direct routing. Unknown providers, failed config
  or initialization, missing credentials, unresolved vault pointers and invalid
  routes raise typed `proxy_routing_error`; never substitute `NullProvider`.
  An account change takes effect on the next resolution. Settings Test resolves
  once off-loop and probes that exact URI under policy, admission, a 15-second
  deadline and a 128-byte decoded response cap. No raw credentials in errors.
- **The DDGS SDK runs in `web_acquire/search_sdk.py`'s isolated child**, with
  proxy/account environment stripped, explicit route via stdin, a 30-second
  process-tree deadline and bounded memory/output. DDGS_PROXY otherwise overrides
  an explicit proxy. Never mutate process environment around SDK calls. Search
  admission and cancellation retain ownership until child cleanup joins; no next
  backend after cancellation. Other synchronous search backends still require
  hard whole-request containment. Gates: `tests/test_proxy_routing_safety.py`,
  `tests/test_search_sdk_worker.py` and real Chromium proxy-refusal coverage.
- **`get_scraping_client()` (`client.py`) is the single injection point.** The
  scraper builds its `httpx.AsyncClient` via this factory, not `httpx.AsyncClient`
  directly. It injects `proxy=` when configured + rotates UA from
  `USER_AGENT_POOL`. Adding a new fetch path = use this factory.
- **Scraping-scoped ONLY.** The proxy is never applied to LLM API calls — those
  use the separate `http_pool.py`. Never wire `get_scraping_client` into the LLM
  provider path, or provider API keys would route through a third party.
- **Adding a provider** (anyip.io, BrightData and Oxylabs are registered) =
  one class under `proxy/` + one line in `registry.py::_PROVIDERS` + one
  Settings dropdown option. The scraper
  talks to the `ProxyProvider` interface, not to any provider directly. Don't
  hard-code a provider into `read_url.py`.
- **`proxy.password` auto-vault-encrypts** via the existing
  `is_sensitive_config_key` rule.

