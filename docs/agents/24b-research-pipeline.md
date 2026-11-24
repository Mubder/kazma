<!-- AGENTS.md, piece 24b-research-pipeline. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **A deep research covers what its plan asked** (2026-10-31). Each search's
  results are ranked against that search's own query and taken in turns
  (`web_acquire.rank_by_coverage`): every sub-question gets a source before
  any gets a second. Pooled and ranked against the topic, the address score
  decided alone (`.gov` +4): a Kuwait history report's first source was a
  COVID-19 cost study. The gap critic runs whatever the source count, with
  its own budget (`_gap_search_due`; half `max_sources`, at least 2); gated
  on a full quota it never ran in a deep run. Fan and meme wikis, copies of
  Wikipedia and pirate document sites are demoted (`rank._DEMOTE_HOST_SUBSTR`:
  the next run took polandball's "Kuwaitball" as its search's best address).
  **A link is judged by its search result too** (the fourth run): a query
  word in the result's own title and snippet (`SearchResult.snippets`, kept
  per URL in the search effect) counts like one in its address
  (`rank_by_coverage(texts=)`), and links with no sign of relevance come
  after every link that has one, from any search; a gap fill takes none
  (`require_signal=True`), so a follow-up search that found only games
  sites adds nothing. Result links are the `**URL:**` lines
  (`serp.extract_urls_from_search`): any URL in the text took the search
  engine's own address from the "_Source: ... searxng@http://..._" line as
  a source, and an empty search ("No web results for:") as one result.
  Gate: `tests/test_research_source_coverage.py` (pooled ranking, the old
  gate, the old demotions, the old URL extraction and the rule without the
  signal tier as negative controls). **A PDF answer is read, through the
  document worker** (`web_acquire/pdf_source.py`, the fifth step): the
  ladder refused every non-text answer, so the research read no thesis or
  journal article. A PDF type, or a nameless binary type whose bytes start
  `%PDF-`, goes to `DocumentService.parse_transient(allow_ocr=False)` from a
  private temporary copy (deleted after): the PDF policy and the sandbox or
  per-job container, never a PDF library in the server (§19 E3), no remote
  parse. Its body limit is decided from the headers
  (`read_capped(max_bytes=callable)`): 20 MB, read whole or refused. No OCR:
  no text on any page is refused as a scan, every page needing OCR is read
  and marked thin. Knowledge crawls keep PDFs out (`reads_pdfs`: profile
  generic only; the crawl corpus locks their outcomes). Gate:
  `tests/test_web_pdf_sources.py` (real PDFs, the real worker; the old gate
  as the negative control). **The planner, the gap
  critic and the report answer without hidden reasoning**
  (`reasoning="off"`): deepseek-flash spent all 8,000 tokens of a report on
  it, then 11,015 of the 16,000 retry, and the run failed at its last step.
  `resilient_chat` passes the switch only to a client whose
  `can_turn_off_reasoning()` is True (`_kwargs_for`): a failover can reach a
  provider whose `chat` takes no such argument
  (`tests/test_research_reasoning_off.py`, passing it through as the
  negative control).
- **Tavily is the search service after SearXNG** (resilience plan F1,
  `web_search._tavily_search`): a remote provider, asked only when the
  acquisition policy names `tavily` and `KAZMA_TAVILY_API_KEY` is set. Each
  query reserves one request of the remote allowance before it leaves and
  its HTTP status is recorded on the reservation (`accounting.record_response`);
  a spent allowance passes the query on. The search worker posts it with the
  key in a header (stdin to the child, never a URL), never through the
  scraping proxy (`search_http._proxy_for`), and never follows a redirect.
  SearXNG answering nothing for an hour pages once (alert "search.searxng_silent"),
  and an answer resets it. Research readiness says whether Tavily is on
  (`second_search`). Gate: `tests/test_search_tavily.py` (no policy, no key,
  the proxy and the chain without Tavily as negative controls).
- **A search answer about something else is no answer** (2026-10-31,
  `web_search._answers_the_query`): a backend's results of which fewer than
  half hold a content word of the query (`memory.query_terms`) in title,
  snippet or address count as empty (`<backend>:unrelated`, a WARNING) and the
  chain goes on. With the local SearXNG's engines rate-limited after a day of
  runs, Bing answered Kuwait history queries with Telegram, a perfume shop and
  a browser game, and the chain stopped there: nine of a run's ten sources.
  Bing result links are decoded as URL-safe base64 and must decode to a plain
  http(s) address, or the row is dropped (`search_http._bing`: the standard
  alphabet left control characters, or kept the Bing redirect itself). The
  Wikipedia fallback is a full-text search (`list=search`, `_wikipedia`), sent
  with `search_http.USER_AGENT`, which names a contact: `opensearch` matched
  title starts only, and Wikimedia answered "KazmaSearch/0.6" with 403, so it
  had never answered a research query. Gate: `tests/test_web_search_unrelated.py`
  (the chain without the check, and the old decoder, as negative controls).
- **The Research page lists research, then the chat's web searches apart**
  (2026-10-31). `list_sessions(kind=)` / `GET /api/research/sessions?kind=`:
  `research` (deep and brief runs) or `search` (`_CHAT_SEARCH_SQL`: depth
  `chat`, or an older row with no known depth and an `rs_chat_` id -- the
  page's `sessionTag` rule). The page reads each kind on its own (26 of the
  newest 30 rows were chat searches), lists papers and runs newest first, and
  the searches in a "Web searches from chat" group after them, each "web search
  from chat · N results" (`meta.results`, written by `record_chat_research(
  result_count=)` with the snapshot it counts). They were listed first, as
  "[Chat] <query> · session · done · 1 source", and read as Kazma's research.
  Gates: `tests/test_research_web_searches.py`,
  `tests/js/test_research_web_searches.js` (the old mixed list as control).
- **A research run's finished paper is kept with it** (2026-10-31,
  `tools/research_final_paper.py`): a run registered in a chat turn names it
  (`turn`, `proposals.current_turn()`), and a `generate_pdf`/`generate_docx`
  that succeeds in the SAME turn copies the document and the Markdown it was
  made from into the run's folder (`final/paper.{pdf,docx,md}`, replaced by a
  later call) and names them on the run's entry (`final`). The Research page
  lists the run by that paper (its title, a PDF button) and opens its text,
  the working report a link. Never matched by anything but the turn: a
  document of another turn, or of a chat that ran no research, is no research
  paper. A failure to keep it is a WARNING; the document is made either way.
  Gates: `tests/test_research_final_paper.py` (the run and the PDF through
  the real pipeline and tool; a later turn and an unstamped run as controls),
  `tests/js/test_research_final_paper.js`.
- Research pipeline execution uses `web_acquire.research_runtime`: four active
  pipelines and four background sessions in this Python process, with no waiting
  queue. Admit before planning/session creation, freeze workspace/operator and
  robots policy, and retain a background lease until its actual task finishes.
  Rejected Web starts return typed 503 capacity errors without creating a row or
  task. Pipeline execution has a cooperative 30-minute deadline; this is not a
  hard native filesystem deadline or durable pipeline recovery. Session/store/
  scoring workers, pipeline preflight/path/registration workers and extract
  publication join cancellation before releasing ownership. Unique per-run
  directories prevent same-topic/same-second collisions. Standalone extract
  saves pin their starting workspace before acquisition. Gate:
  `tests/test_research_runtime.py` (original admission controls, real API/SQLite,
  cross-thread dispatch, preflight/creation/publication cancellation and scope).
  Preflight requires an explicit boolean readiness receipt and stops before
  planning/output on failure. Source batches reserve remaining publication
  slots before dispatch; gap fills keep monotonic filenames. Persist bounded
  `sources.json` atomically before the first fetch and after each batch,
  including gaps; storage failure stops further acquisition. This manifest
  does not replace the unfinished durable pipeline effect/resume journal.
  Cached readiness honors `probe_search=False` even under legacy live-probe
  environment variables, opens a read-only diagnostic scope and omits search
  URL credentials. `web_acquire.operations` is the shared cached CLI/TUI status
  source; never add implicit balance refreshes to status reads. CLI supports
  `kazma acquisition status`; TUI Settings reads explicitly and keeps one
  worker until joined cleanup completes. Live capacities are process-local.
  Gate: `tests/test_acquisition_operations.py` (real API, SQLite, CLI and TUI).
- Deliberate direct-API exceptions (never route through the scraping proxy):
  Jina Reader, Firecrawl, loopback SearXNG. The gateway attachment URL fetch
  DOES route through `get_scraping_client_sync` (SSRF-per-redirect kept).

