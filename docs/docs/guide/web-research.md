---
id: web-research
title: Web research
sidebar_label: Web research
description: Search, scrape, crawl, and digest public web content with Kazma tools
---

# Web research

Kazma can search the web, fetch pages, page through long documents, crawl a site within bounds, and digest saved extracts — all from **normal chat** (or `/swarm`). There is **no** `/research` slash command.

> **Arabic brand:** product name is **Kazma** / **كاظمه** (or **كاظمة**). Never **كازما**.

## How you use it

| You want | What to do |
|----------|------------|
| Quick research with sources | Chat: *“Research X, use the web, cite URLs”* |
| Deep multi-page research | Chat: *“Crawl `https://docs…` and digest the pages”* |
| Multi-worker parallel research | `/swarm research …` or *“use the swarm to research X”* |
| Force a pipeline | Name tools: *“`web_search`, then `read_url_to_file`, then `digest_research_file`”* |

The supervisor chooses tools; you do not need to name them unless you want control.

## Tool map

| Tool | Role |
|------|------|
| `web_search` | Search (SearXNG → DuckDuckGo → Bing HTML). Prefer `KAZMA_SEARXNG_URL`. |
| `read_url` | One URL, **paged window** (`offset`, `max_chars`). Header shows total length + next offset. |
| `read_url_to_file` | Full extract saved **inside the workspace** (default folder `KAZMA_RESEARCH_DIR`, usually `research/`). |
| `crawl_page` | Native alias of `read_url` (advanced-web-crawler skill). |
| `crawl_site` | **Bounded** same-domain multi-page crawl; saves pages + returns an index. |
| `list_research_chunks` | Chunk index + previews for a saved file. |
| `read_research_chunk` | One chunk by index. |
| `summarize_research_file` | Light extractive outline. |
| `digest_research_file` | Walks **all** chunks **in-tool**; returns one **bounded** digest (context-safe). |

Native skill **`advanced-web-crawler`** also registers `web_search_duckduckgo`, `crawl_page`, `parse_document` (auto-loaded; not an Agent Skills marketplace install).

See [Tools catalog](../reference/tools-catalog).

## Caps (important for long pages)

| Stage | Default | Env |
|-------|---------|-----|
| `read_url` window | 16 000 chars | `KAZMA_READ_URL_MAX_CHARS` |
| Graph truncate (normal tools) | 4 000 | `KAZMA_TOOL_RESULT_MAX_CHARS` |
| Graph truncate (research tools) | 16 000 | `KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS` |
| Digest output | 12 000 | `KAZMA_RESEARCH_DIGEST_MAX` |

**Double-cap history:** older builds used hard 8k scrape + 4k graph truncate. Research tools now use the higher research graph cap so paging is useful.

Paging example (agent or explicit):

```text
read_url(url, offset=0)
read_url(url, offset=16000)   # next window; full text cached in-process ~15 min
```

Full-page research:

```text
read_url_to_file(url) → digest_research_file(path) → read_research_chunk for details
```

## Multi-page crawl (`crawl_site`)

| Control | Default | Hard ceiling |
|---------|---------|--------------|
| `max_pages` | 8 | 50 (`KAZMA_CRAWL_MAX_PAGES`) |
| `max_depth` | 2 | 5 (`KAZMA_CRAWL_MAX_DEPTH`) |
| `same_domain_only` | `true` | recommended |
| `delay_ms` | 300 | politeness |
| SSRF | every URL | private/metadata blocked |

Saves under the workspace (default research subfolder) and returns a markdown index.

## Stronger search (SearXNG)

`web_search` tries **SearXNG first**, then DuckDuckGo → Bing → Wikipedia.

| Setup | Command / env |
|-------|----------------|
| Compose profile | `docker compose --profile search up -d searxng` |
| Host port | `http://127.0.0.1:8088` (maps container `8080`) |
| Env | `KAZMA_SEARXNG_URL=http://127.0.0.1:8088` |
| ConfigStore | key `search.searxng_url` (same purpose) |
| Settings | `deploy/searxng/settings.yml` enables **JSON** format (required) |

Kazma multi-base discovery also probes `localhost:8088`, `host.docker.internal:8088`, and `searxng:8080`. Live bases are cached briefly; dead hosts cool down ~60s.

## Optional harder fetch backends

Not invincible against enterprise bot walls. Improves success rate:

| Env | Purpose |
|-----|---------|
| `KAZMA_FETCH_BACKEND` | `auto` \| `httpx` \| `jina` \| `firecrawl` |
| `KAZMA_FIRECRAWL_API_KEY` | Firecrawl API key (best quality on hard sites) |
| `KAZMA_FIRECRAWL_URL` | Self-hosted Firecrawl base (optional) |
| `KAZMA_JINA_READER` | `1` = always try first; unset = **recovery only**; `0` = never |
| `JINA_API_KEY` / `KAZMA_JINA_API_KEY` | Optional Jina auth (higher rate limits) |

**Fetch order**

1. Optional pre-backends when opted in (Firecrawl key / `KAZMA_JINA_READER=1`).
2. Local httpx + trafilatura.
3. **Hard-page recovery** on bot walls / thin or empty extracts:  
   Firecrawl (if key) → Jina (unless `KAZMA_JINA_READER=0`) → Playwright.

Knowledge ingest (`knowledge_ingest_url` / site) reuses the same `_fetch_full_text` cascade.

Playwright (optional install): `pip install 'kazma[web]'` and `playwright install chromium`.

## Safety & honesty

- **SSRF-safe** on all fetches and redirects.  
- Saves stay **inside the active workspace** (any subpath; default auto-dir `KAZMA_RESEARCH_DIR`).  
- **Not** unlimited internet spidering.  
- **Not** anti-bot invincible.  
- Digests are **extractive** (no nested LLM inside the tool); the chat model synthesizes the final report.  
- HITL still applies to danger tools; research web tools are generally **read/safe** (writes go to workspace files via pathlib).

## Modes: quick vs deep

| Mode | How to trigger | Behavior |
|------|----------------|----------|
| **Quick** | “look up”, short questions | Free-form tools; 1–2 hops OK |
| **Deep / paper** | “research thoroughly”, “comprehensive report”, **`/research deep <topic>`**, or tool `run_research_pipeline` | Multi-query search → full-page acquire → digests → **LLM synthesis** → report under `research/reports/` |

The supervisor also has a **soft depth gate**: deep-worded requests that only ran `web_search` get one system nudge to fetch ≥2 full sources before concluding.

### Synthesis tools

| Tool | Role |
|------|------|
| `digest_research_file` | Extractive map of one file (no nested LLM) |
| `synthesize_from_digests` | Cross-source LLM analysis |
| `run_research_pipeline` | Full paper pipeline (search→acquire→digest→synthesize→save) |

## Recommended playbooks

### Single topic

1. `web_search` (≥2 queries for thorough work)  
2. `read_url_to_file` on top results (≥2 sources)  
3. `digest_research_file` then `synthesize_from_digests`  
4. Answer with citations  

### Docs site

1. `crawl_site(start_url, max_pages=12, max_depth=2)`  
2. `digest_research_file` per saved path (or selective chunks)  
3. Report  

### Comprehensive paper

```text
/research deep <topic>
# or
run_research_pipeline(topic="...", depth="deep", max_sources=8)
```

### Swarm

`/swarm research …` / `dispatch_swarm` auto-researcher includes save/digest/pipeline tools.

## Related

- [Tools catalog](../reference/tools-catalog)  
- [Environment variables](../reference/environment-variables)  
- [Skills, MCP & Tools](skills-mcp-and-tools)  
- [Portability](../ops/portability) (workspace + `kazma-data/`)  
