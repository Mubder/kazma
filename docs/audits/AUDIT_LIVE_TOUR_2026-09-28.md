# Using the live install like a person would — 2026-09-28

Six passes over the live install (`my.kazma.ai`, one operator, Windows,
Postgres shared state, SQLite everything else), in English and then in
Arabic, page by page and feature by feature, fixing what a person would hit.
Each pass shipped as its own commit with tests; `CHANGELOG.md` carries the
full text of every fix. This file is the map: what was found, what was
fixed, what was judged and left, and what the day changed in how the
project is checked.

## How the tour was run

- The dev checkout (`G:\GitHubRepos\kazma`) is where the work happened; the
  live install was reached only through `git pull` and the guard's
  `--reload --when-idle` (AGENTS.md, Server Management), never edited.
- Every page was opened in the app's own browser as the operator, in
  English and in Arabic, with real data on it. For Arabic, the same
  detector `tests/e2e/test_pages_read_in_arabic.py` uses was run on the
  live page: every shown text node and every `placeholder` / `title` /
  `aria-label` outside `translate="no"`, judged word by word (names,
  acronyms and identifiers excluded; `tests/_ui_names.py` is the list).
- A finding became a fix only with a test that fails on the old code, and
  where the finding was one of a class, a gate over the class.
- Deploys: full suite green in two chunk counts (`fast_test.py --chunks 4`
  and `--chunks 7`), metrics regenerated, bandit and ruff as CI runs them,
  push, pull, reload when idle, boot log read (zero warnings each time),
  then the page toured again on the new build.

## Fixed today, by pass

| Pass | What a person hit | Where it is now |
|---|---|---|
| 1 | A file asked for in the web chat went to Telegram; the Swarm page could not use the swarm; GitHub "no open issues" over an open one; `/replay` and `/fork` sent to the model; a frozen clock on finished answers; Providers pills English in Arabic; "No sessions yet" over 128 chats; Workspace recent files 30 s and empty; README's stale test count | CHANGELOG "Found by using the live install like a person would" |
| 2 | The daily digest never sent, backups nine hours old (schedulers counted from boot); `auto` swarm tasks failing on most questions; a skill switch that switched nothing; Agents page "Stopped" over a working agent; the IDE editor squeezed to a sliver | "The live install, second pass" |
| 3 | The IDE fix overshooting at 918 px; every research session called "Deep"; instant replies leaving their turn "thinking" for ten seconds | "The live install, third pass" |
| 4 | The Dashboard's session table naming no chat; chat-store reads on the event loop (150–400 ms per list); a bare `library_id` box on Documents; markdown in tab titles and notifications; the "Monaco" claim | "The live install, fourth pass" |
| 5 | The Memory page almost entirely English in Arabic, and English on nearly every other page (approval buttons, "Completed", Copy/Edit, "web · 2 msgs · just now", the login page); Settings → Proxy "Save failed" after a successful save; Research's "[object Object]" heading | "Every page reads in Arabic" |
| 5b | With real data: the Research list, details and stage names; memory-backend status lines in Settings; MCP add-server errors; "telegram" in lower case; the Archived research tab dating every session 21 January 1970 | "Pages with your data on them read in Arabic too" |
| 6 | The chat's `/` menu English in every language; the Replay picker listing 131 raw uuids; Documents "Storage: degraded" in red over a healthy single-server install; the Swarm page's own English (history, results, details, live lines, template buttons, worker pickers); Scheduled's X activity, MCP categories, IDE command titles, X Studio reasons, Memory chips; two Linux-only CI failures (a `msgpack` import that only a dev-machine extra satisfied; the Memory page's "Needs attention" diagnostics unmarked) | "The live install, sixth pass" |

Every one has a test. The gates that hold the classes:

- `tests/e2e/test_pages_read_in_arabic.py` — every page, every Settings
  tab, a chat turn paused at its approval card and finished, the chat's
  command menu, and the Research, Swarm and Scheduled pages seeded with
  runs, reminders and X calls (12 and 59 English strings with the old code).
- `tests/test_templates_have_no_english.py`, `tests/test_i18n_keys_exist.py`,
  `tests/test_chat_i18n_bridge.py`, `tests/js/test_chat_slash_i18n.js`,
  `tests/js/test_research_i18n.js`, `tests/js/test_settings_saves_report_truth.js`.
- `tests/test_scheduler_cadence.py` (every scheduler due in 30 minutes runs
  within 40), `tests/test_skill_switches.py`, `tests/test_swarm_auto_worker.py`,
  `tests/test_github_list_issues.py`, `tests/test_web_chat_files.py`,
  `tests/test_chat_session_routes_off_loop.py`, `tests/test_dashboard_session_table.py`,
  `tests/test_replay_thread_titles.py`, `tests/test_document_operations_phase9.py`
  (readiness), `tests/test_instant_reply_frames.py`, `tests/e2e/test_layout_widths.py`.

## Judged and left as they are

These were seen on the live install and deliberately not changed; each is
either the owner's decision, data rather than code, or outside this pass.

- **Memory recall's weak-hit trade-off** (a near-miss shown as "possibly
  related"): tuned on the benchmark, documented in AGENTS.md §15G; not
  re-tuned from one live question.
- **The live GitHub token lacks Issues: Read.** The tool now says so instead
  of "no open issues"; granting the permission is the owner's.
- **The filesystem MCP server points at the sandbox** (`kazma-data/workspace`)
  on this install; the binding rebinds on Switch Repo. Configuration, not a
  bug.
- **Marketplace ("hub") skills register no tools** with the agent — not
  built; the page says what a hub skill is.
- **Historical failed X posts** in the audit and a duplicate draft set in X
  Studio — data from earlier runs; drafts are retired, never deleted.
- **Test artifacts on the live install** from earlier live tests (a
  `kazma_live_test.md` document, a test library) — the owner's to remove;
  live tests since keep memory off and archive after themselves.
- **An empty "CITRA Kuwait AI Guide" library** — a crawl that found nothing;
  the page now shows each library's coverage, so an empty one is visible.
- **The owner's Telegram id appears in a code comment, tests and docs** — a
  single-operator install by the owner's rule (`docs/THREAT_MODEL.md`).
- **The skills discovery in tests reads the real home skills folder**
  (`~/.agents/skills`) — noted; the shield pins the data dir, not the home.
- **The first approval row's status chip is laid out differently from the
  others** — pre-existing, in both languages; cosmetic.
- **GitHub Actions minutes** ran out mid-day and came back; the owner
  accepted the gap. Every push of the day was gated by the full suite
  locally, and the two Linux-only failures CI then found are fixed above.

## What the day changed in how Kazma is checked

- An empty test install cannot show what a page does with data. The Arabic
  browser tour now seeds research runs, swarm runs, a reminder and X calls
  before it reads the pages; the harness's pages were clean while the live
  ones were not.
- A page tour on the live build, with the test's own detector, is the last
  step of every deploy, not a substitute for the tests.
- A dependency that is only on the dev machine (`msgpack` via `locust`)
  passes every local run; the regression test hides it. CI is read once per
  fix set, and a red required check on `main` is a failing build, fixed at
  once (AGENTS.md §31).
- "Degraded" means a configured backend is not the one serving — never a
  single-replica store the operator chose (AGENTS.md §19D).
