<!-- AGENTS.md, piece 15l-graph-and-page. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**L. The graph shows what memory holds, and extraction keeps it clean
(2026-10-03, `memory/graph_view.py`, `entity_retire.py`, `graph_hygiene.py`).**
On live, the Memory graph showed many nodes alone; most were made by the
drawing, the rest by extraction.
- **The route cuts by connected group** (`graph_view.keep_connected`): the
  hub's group grown out from the hub by belief count, then the other groups
  by weight. A node kept without the neighbours that join it to its group is
  painted alone, and the old sort-and-slice dropped the one-belief fact
  nodes first. The hub links of every endpoint (`user → related_to → X`,
  importance 1) are loaded past the 800-fact cap: the cap ranked them last
  and dropped them first. A literal value (`true`, `4`) is one node per fact
  (`value:<belief id>`, `isValue`), never dropped and never one shared node.
  `stats.isolated` counts nodes alone in the DATA, before the cut.
- **A vector auto-merge leaves a redirect** (`entity_resolution._auto_merge`):
  the merged name becomes a shell with `metadata.merged_into`, and the
  target gains the name's alias hash. The ledger row named an entity that
  did not exist, so under `ensure_primary_schema`'s foreign keys every
  auto-merge raised (logged at DEBUG) and created a duplicate. Quarantine
  creates the new entity before its ledger row for the same reason. The
  extractor files a fact under the id a name resolved onto.
- **An entity's id keeps its script** (`entity_resolution.slug`, the ONE
  id rule, Memory audit M02, 2026-10-13): NFKC, casefolded, the letters,
  marks and digits of every script, other runs one `_`, 80 characters; an
  id for a label with no letter (an emoji) from its own text; an empty label
  has none (`ValueError`; `mutate_belief` refuses it). The rule kept
  `a-z0-9` and fell back to `"entity"`, in four copies plus the Memory
  API's: every all-Arabic, Persian or CJK subject was `entity`, unrelated
  facts superseded each other, and later non-Latin objects were rewritten to
  `entity`. ASCII ids are unchanged. Spellings that differ by vowel marks or
  letter forms meet in the alias hash (`documents.arabic.fold_for_search`),
  never in the id. An id is a tenant's: the key is `(tenant_id, id)` (M21,
  §16's multi-tenant note), and a redirect (`canonical_entity_id`), a
  protection (`entity_protection`), a count and a retire read the tenant's
  own row. Service names (`current_facts`) and the same-words key
  (`graph_hygiene`) use it too. Rows the old rule wrote: `entity_repair`
  (`scripts/memory_entity_repair.py size`, then `apply` on a copy) quarantines
  each collapsed fact and queues its turn for the deep pass -- nothing is
  guessed; the live run is the owner's. Gate: `tests/test_memory_entity_ids.py`
  (Arabic, Persian, CJK, emoji through the writer and the extractor; no
  `a-z0-9` keep-list or `"entity"` fallback in memory code, the old rule as
  the negative control; the repair on a copy).
- **Tier-2 resolution queries stored name vectors** (2026-10-13, Memory audit
  M18): an entity keeps its name's vector (`entities.embedding`, stored at
  creation, by `_store_name_vector` on an exact match, and by the vector
  repair pass for older rows, `repair_entity_name_vectors`), and
  `resolve_entity` asks `VectorEngine.nearest_entities` for the nearest. Every
  extraction embedded every entity name of the tenant (93 names, seconds of
  CPU a turn on live; past 4,096 names the short-text cache thrashed). An
  extraction now embeds only the names it resolves. Gate:
  `tests/test_memory_entity_name_vectors.py` (the same embed calls at 60 and
  6,000 entities; the old scan as the negative control).
- **Subjects that are work items are not entities**
  (`ego_anchor.subject_should_mint_entity`: phases, tickets, versions,
  hashes, paths, mostly-digit and sentence-length slugs). The fact is still
  written and anchored. An object is minted only beside a subject that is
  an entity. The deep pass is given the subjects in use
  (`entity_resolution.entity_vocabulary`), as it is given the predicates.
  A ticket has three digits at least: slugs are lower case, so "gpt-4",
  "windows-11" or "covid-19" look like tickets by shape, and a product
  losing its node costs more than a ticket keeping one. Measured on live
  (377 entities) the rules matched six, all clutter.
- **An entity goes with its last fact if it holds nothing else**
  (`entity_retire.retire_empty_entities`, from `invalidate_belief` and a
  supersede): a plain concept only -- own name and hash as aliases, no
  metadata, grouping, merge row or redirect onto it; not protected, major
  or high-stakes. Declared in `tests/test_memory_deletes.py`. **A fact's
  entities are asked for by their ids** (2026-11-24,
  `entity_counts.entity_ids_named`: the value, its slug, and the entity of
  that name): a fact keeps the user's words ("Lisbon") and its entity the id
  they make ("lisbon"), so a retire asked by the text took the Arabic entity
  and left every English name with a capital on the map, after a forget, an
  invalidation and a supersede, and the stored counts were not recomputed.
  Every caller that holds a fact's text asks it; the merge, purge and repair
  callers hold ids (declared). A live fact naming the entity in another
  spelling keeps it. Gate: `tests/test_entity_goes_with_its_fact.py` (EN/AR;
  the call by text as the negative control, which loses English only; a
  planted caller).
- **Which entities no delete, merge or purge takes is one answer**
  (`memory/entity_protection.py`): core ids (`user`, `assistant`, `kazma`),
  the hub's own names (`self_hub.collect_self_entity_ids`: an entity the
  hub's aliases name, or one labelled you/me/user) and the operator's flag.
  A self entity may still be merged INTO the hub. Five copies did not agree,
  and four named Kazma's author: every install protected an entity called
  "mubder" and none its own owner's other names. A store that cannot answer
  refuses (the page's check answered "not protected" on a read error). Every
  purge (the page's Hygiene, `memory_purge_empty_entities`) removes exactly
  `entity_retire.plain_empty_shells`, and memory health's `empty` counts
  that list: both purges deleted every entity with no live fact, merge
  redirects included, and a redirect is how a merged name keeps reaching
  its target (`canonical_entity_id`). Gates:
  `tests/test_memory_entity_protection.py` (an install whose owner is
  Layla; the old purge rule as the negative control) and
  `tests/test_no_owner_identity_in_product.py` (no product string literal
  names the author, none outside a docstring the author's projects; the
  repository's address is the product's).
- **The page's counts converge** (`entity_counts.repair_entity_counts`,
  maintenance entry "entity count repair"): an entity whose stored
  `belief_count`/`graph_degree` differ from the live counts is recomputed,
  500 a pass, and a merge recomputes the neighbours too
  (`recompute_entity_counts(neighbours=True)`: one that linked to both
  names has one neighbour fewer). 11 of 377 live entities were wrong, two
  since an August merge. The stats bar's "empty" is what the purge takes,
  and a redirect is listed "merged into X", never "empty" -- the bar
  counted redirects and stayed above zero after a purge.
  `tests/test_entity_count_repair.py` (the source-and-target-only
  recompute as the negative control).
- **The Memory page says what happened** (2026-10-13, Memory audit M32):
  hygiene is `ok` only when every action it ran is (`failed`, `error:
  hygiene_partial`; it said "Hygiene complete" over a failed archive); the
  Episodes chip counts `RECALLABLE_TIERS` only (it counted tombstones); the
  archive of ended facts returns `remaining` and its `cap` (2,000 a run, said
  in the label and the toast); a forget whose remote vector delete is pending
  says so; the probe panel shows weekly summaries and a degraded search;
  server messages the page shows are codes (`not_found`, `protected`,
  `about_too_long` with `length`/`max`) worded by the page in its language.
  Gates: `tests/test_memory_page_truth.py`,
  `tests/e2e/test_about_me_limit_arabic.py`.
- **The Memory page is laid out by what a person comes for** (2026-11-19,
  the owner's request after the 2026-11-18 review): status, the memory map,
  what Kazma knows, conversations; the clean-up and the health numbers are
  folded (`#memory-cleanup`, `#memory-health`, `components/memory_health.html`).
  Its words are the catalog slice `i18n/catalog/memory_page.py` (EN/AR).
  **Restore takes only a backup of the memory database**: the list showed the
  operations database's copies (`memory_ops_<ts>.db`) beside the memory
  ones with the same Restore button, and restoring one replaced every memory
  with the task queue. The list and the route take only
  `backup.is_memory_backup_name` (`memory_state_<ts>.db`, the stem of
  `primary_memory_db`), and `restore_memory_backup` refuses a file without
  the memory database's own tables (`NotAMemoryBackup`, read `immutable=1`)
  before anything is copied (400 `not_a_memory_backup`). **Back up now**
  answers `failed` / `nothing_backed_up` when nothing was copied, and its
  manifest names files, never server paths; **Optimise** says which database
  failed. Joining two things (it moves every fact, for good) and the tidy-up
  ask first, the tidy-up naming each clean-up it will run; a delete names
  the thing. The map: label-sized layout, no idle animation, fit once
  settled, token colours, labels placed by rank without overlap, Arabic
  labels right to left, a legend of the marks in use, zoom and Show all and
  pinch, and a failed load said as one (`#v2g-failed`, never "Nothing here
  yet"). On this page the `hidden` attribute hides whatever display a class
  or inline style gives (`.memory-page [hidden]`): "Show the next part"
  (`.btn`) stayed after the last part, and the map inspector's extras
  (inline flex) never folded. Gates: `tests/test_memory_backups_page.py`
  (the real routes; the old list and the old restore as negative controls),
  `tests/e2e/test_memory_page_browser.py` (by the sidebar, EN/AR, desktop
  and phone; nothing marked hidden is drawn, the page without the rule as
  the negative control).
- **The map is a radial hierarchy** (2026-11-20, the owner: the major nodes
  "hide within the many nodes available"; `memory_console.js`
  `_v2gAssignRanks`, `_v2gStep`). Rank 1 the hub (you), held at the
  centre; rank 2 the things marked major (the entity's `is_major` column), or, when none
  is, the hub's five most connected neighbours, evenly on an inner ring; rank
  3 the rest on an outer band of up to four lanes, pulled to the side of the
  major it shares most lines with. Each rank has its size (hub largest), its
  label (always shown for ranks 1 and 2), its line (the hub's spokes to its
  majors) and its push; faint orbit rings mark the two rings; a dot the
  person placed keeps its place. Majors are the accent, the hub its own
  colour (majors were an amber beside the hub's yellow). A name that cannot
  sit under its dot tries above it, then beside it. Gate:
  `tests/e2e/test_memory_map_radial.py` (by the sidebar, EN/AR: the hub at
  the centre, the majors on their ring and evenly spaced, the rest beyond on
  their major's side, sizes by rank; with nothing marked, the most connected
  stand in).
- **A memory problem says what to do and where** (2026-11-20): every
  memory health check that can warn or fail passes `fix` (one sentence) and
  `link` (the page where it is done) to `health._comp`, and the health answer
  lists `problems` (detail, fix, link) beside `issues`; the Memory page's
  "Needs attention" shows "What to do:" and Open (links of this site only).
  It said what was wrong and never how to fix it ("Neo4j does not answer (the
  sign-in was refused); 17 changes waiting"). Gate:
  `tests/test_problems_say_how_to_fix.py` (a check that can fail with no
  `fix=`, a planted one as the control).
- **Every operator memory change leaves a trail and a real status** (2026-10-13,
  Memory audit M35, `kazma_ui/memory_actions.py`): `MemoryActionRecorder`,
  mounted with the memory API, records every POST/PUT/PATCH/DELETE under
  `/api/memory` -- an INFO line (`[memory] operator action=... actor=...
  tenant=... ids=... status=...`) and a `memory_audit_log` row
  (`operator_action`): route, actor from the session (never the body),
  tenant, the path's ids, the status; never a value. It answers a handler's
  `ok: false` with 404 (not found), 409 (refused), 422 (a wrong request) or
  500 (`safe_error`); most routes answered 200 for every failure. One
  recorder, so a new memory route is recorded the day it is added. The daily
  digest counts the forgets ("Planned work"), and `/metrics` carries
  `kazma_memory_operator_actions_total{action=}` (this process, any
  outcome). `invalidate_belief` returns
  `safe_error`, never the exception's text. Gate: `tests/test_memory_actions.py`
  (every mutating memory route requested and recorded; the routes without the
  recorder as the negative control). The tenant gate takes a handler's own
  4xx (a body with `ok`) as reached.
- **Memory input is bounded and escaped** (2026-10-13, Memory audit M36): one
  set of length limits (`memory/limits.py`: subject 200, predicate 120,
  object 4,000, a name 120) checked first in `mutate_belief` -- refused, never
  cut -- and by the edit, link and group routes (the edit route alone capped,
  `mutate_belief` and group fields nothing); a memory request body is refused
  past 256 KiB (`memory_api._bounded_body`, declared or streamed; bodies were
  read whole); the entity search applies the tenant inside its full-text
  subquery (its 2,000 rows were taken across every tenant first); every
  database value the Memory console puts in an HTML attribute goes through
  `_attr` (`_esc` left quotes, and a migrated entity type wrote its own
  attributes); `/memory off` in a chat app asks for `/memory off confirm`
  before it forgets. Gates: `tests/test_memory_input_bounds.py`,
  `tests/e2e/test_memory_hostile_type.py` (a hostile type renders as text, no
  handler, the filter still toggles).
- **The graph export is well-formed and says when it was cut** (2026-10-13,
  Memory audit M33): `GET /api/memory/v2/graph/export` builds GraphML with
  ElementTree (`saxutils.escape` left `"` in attributes: any fact with a quote
  made the file unreadable), drops characters XML cannot hold, and both
  formats carry `truncated` (and the `X-Kazma-Truncated` header) when the
  graph passed `limit`. Gate: `tests/test_memory_graph_export.py`.
- **The Entities search finds an entity by its id**
  (`memory_api._fts_match_expr`): words split where the index splits them
  (an underscore separates), the id matches even when the index holds no
  word of the query, and an exact id or name comes first. `kazma_ai_admin`
  became the one token `kazmaaiadmin` and found nothing.
- **The page finds an Arabic word in any usual spelling**: the facts, the
  memories and the Entities searches ask `memory/word_search.py` beside their
  index (§15 G (3), "A list finds a word in any spelling"); an alias is read
  out of its JSON (half its writers escape the Arabic). The map filters in
  the browser with the same fold (`static/js/search_fold.js`, held to
  `fold_for_search` over the Arabic blocks, the digits and the Latin letters,
  `tests/test_search_fold_js.py`). A list draws the answer to what was asked
  LAST: a slow search's rows, with their Forget buttons, stood under the next
  one's words (`tests/js/test_memory_search_last_asked.js`).
- **Memory health reports the clutter** (`v2_health` -> `graph`: empty,
  isolated, work-item and same-word duplicate entities, with examples).
  Read-only; the operator merges or deletes with the existing controls.
  "The same words" is the predicates' rule (`predicates._predicate_key`:
  order, filler words and a plural "s" aside), one rule for both.
- Gate: `tests/test_memory_graph_clean.py` (a noisy extraction batch leaves
  no work-item, isolated or empty entity; each rule with the old behaviour
  as its negative control).

