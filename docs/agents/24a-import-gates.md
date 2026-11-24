<!-- AGENTS.md, piece 24a-import-gates. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 24. Import Integrity + Web Acquisition SoT + CSRF/Rate-Limit (2026-08-14 audit round)

**A. Import-integrity gates — `tests/test_imports.py` (deletion SOP).**
- Three tests: `test_every_product_module_imports` (imports every module
  of every `kazma-*/kazma_*` package), `test_no_dangling_kazma_import_references`
  (AST scan: every `kazma_*` import reference must resolve to a real file)
  and `test_every_imported_kazma_name_exists` (every `from kazma_x.mod
  import name` names something `mod` defines at its top level).
  **No try/except exemption for Kazma's own modules** (removed 2026-09-26):
  the module is in this repo, so the import resolves or it is dead code, and
  the `except` makes it a feature that is silently off. Six were: the
  Knowledge Library's meaning search (`memory.chroma_client`, deleted
  2026-07-31, reported as "chromadb not installed"), the gateway's §25
  Partial handling and its budget counter (one removed name,
  `record_budget_exhausted`, sank the three imported beside it), kazma.yaml's
  `agent.nonstop` (a `load_config` that never existed), the `/memory` count
  and a health helper nothing called. Only a third-party package may be
  optional.
- Born from the crawl.py incident: a module deletion left a dangling import
  in `web_acquire/__init__` — py_compile passed (syntax-only), no test
  imported the package, production research broke at first use
  (`ModuleNotFoundError`). **Rule: deleting a module requires green
  `tests/test_imports.py` in the SAME commit, and importers removed in the
  same change.** Optional pre-commit hook: `.pre-commit-config.yaml`.
- **Every product module also imports ON ITS OWN**, in a fresh interpreter
  (`scripts/check_fresh_imports.py`, CI job "Every module imports on its
  own"). The one-process gate above cannot see an import cycle that works
  only when entered at the right module: each import finds the earlier ones
  loaded. `kazma_core.routing_engine` was one until 2026-09-26 -- the swarm
  package's `__init__` imports it, and it imported `kazma_core.swarm.task`
  back at module level. A module that a package's `__init__` imports must
  not import that package at module level; take annotation-only types under
  `TYPE_CHECKING` and the rest at call time. Both gates walk one list
  (the script's `iter_modules`).
- `tools/read_url.fetch_result` is the public typed ladder entry point used by
  `web_acquire.fetch_text`. `fetch_full_text` remains a public legacy text
  adapter (alias of `_fetch_full_text`); neither starts a second ladder.
  Product consumers use `FetchResult.ok`/`error_code`, never an `Error:`
  prefix in extracted text. `save_url_extract` returns a typed atomic-save
  result to research; it is not a registered agent tool. The chat/chunk tools
  fence remote text at the model boundary; internal typed bodies stay raw.

