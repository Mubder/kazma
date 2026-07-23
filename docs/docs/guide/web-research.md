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

## Optional harder fetch backends

Not invincible against enterprise bot walls. Improves success rate:

| Env | Purpose |
|-----|---------|
| `KAZMA_FETCH_BACKEND` | `auto` \| `httpx` \| `jina` \| `firecrawl` |
| `KAZMA_FIRECRAWL_API_KEY` | Firecrawl API key |
| `KAZMA_FIRECRAWL_URL` | Self-hosted Firecrawl base (optional) |
| `KAZMA_JINA_READER` | `1` / `true` to try `r.jina.ai` |

`auto`: Firecrawl (if key) → Jina (if enabled) → local httpx + Playwright fallback.

Playwright (optional install): `pip install 'kazma[web]'` and `playwright install chromium`.

## Safety & honesty

- **SSRF-safe** on all fetches and redirects.  
- Saves stay **inside the active workspace** (any subpath; default auto-dir `KAZMA_RESEARCH_DIR`).  
- **Not** unlimited internet spidering.  
- **Not** anti-bot invincible.  
- Digests are **extractive** (no nested LLM inside the tool); the chat model synthesizes the final report.  
- HITL still applies to danger tools; research web tools are generally **read/safe** (writes go to workspace files via pathlib).

## Recommended playbooks

### Single topic

1. `web_search`  
2. `read_url` or `read_url_to_file` on top results  
3. `digest_research_file` on saved paths  
4. Answer with citations  

### Docs site

1. `crawl_site(start_url, max_pages=12, max_depth=2)`  
2. `digest_research_file` per saved path (or selective chunks)  
3. Report  

### Swarm

`/swarm research …` uses workers; still the same underlying tools when workers have them registered.

## Related

- [Tools catalog](../reference/tools-catalog)  
- [Environment variables](../reference/environment-variables)  
- [Skills, MCP & Tools](skills-mcp-and-tools)  
- [Portability](../ops/portability) (workspace + `kazma-data/`)  
