<!-- AGENTS.md, piece ui-3-pages-and-controls. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Every page loads clean — four did not (2026-09-26).** Touring the live
  install page by page found the IDE, workspace, knowledge and settings pages
  throwing on every load; no test had opened them. The rules that broke:
  - **No markup inside a double-quoted directive.** `x-text="a ? '<span
    class="ki">' : b"` ends the attribute at `class=` (a SyntaxError per
    row). Build markup in a component method and bind it with `x-html`.
  - **Icons inserted after load come from `KazmaIcons.span(name)`**, which
    returns them filled. `icons.js` hydrates `[data-icon]` only on DOM ready,
    so a bare `<span data-icon>` inserted later stays empty. A page reached
    by the sidebar is such an insert: soft navigation fills the swapped-in
    body and the header title (`hydrateIcons` in `nav.js`, after the swap
    and after Alpine renders the page); without it 32 of the Workspace's
    icons were blank by the sidebar and none on a load (2026-11-09,
    `tests/e2e/test_soft_nav_icons.py`, the old soft navigation as its
    control).
  - **A `<template x-for>` carries only `x-for` and `:key`.** Any other
    binding on it runs OUTSIDE the loop ("s is not defined").
  - **Declare every field a template reads in the component's initial
    data**, and expose a closure constant the template uses (`kb.js`'s `S`).
    A field set only by a loader throws until the loader runs.
  - **Never `x-init="init()"` beside `x-data`.** Alpine 3 calls the
    component's `init()` itself; eleven templates called it again, the app
    shell in `base.html` among them, so every page ran its init twice —
    every load fetched twice and every `setInterval` poller ran twice.
  - **One poller per endpoint per page.** The system-alerts banner reads
    `$store.notifications` (which polls `/api/alerts/recent`) instead of a
    10 s poller of its own (`tests/js/test_alert_banner_link.js`).
  - **Settings mixins compose by descriptor** (`settings.js`):
    `Object.assign` evaluated every getter once and froze it, so the
    Packages, Skills and Tools filters never worked.
  - **The CodeMirror bundle checks its own load order**
    (`scripts/vendor_codemirror.py`: every `require()` bundled earlier, or
    the build fails). One missing addon stopped nine editor modes loading.
  Gates: `tests/e2e/test_pages_load_clean.py` (every nav page in its own
  tab: no uncaught error, every directive compiles, every bundled mode
  registered), `tests/test_alpine_templates.py` (every directive in every
  template compiles in node; `<template x-for>` scope; no second `init()`),
  `tests/js/test_settings_mixins.js`, `tests/test_vendor_codemirror.py` —
  each with a negative control.
- **A page reads its deep link from the page, not the address** (2026-11-08).
  Soft navigation runs the incoming page's scripts BEFORE it pushes the new
  address, so `location.search` at start is the page before: the bell's link
  to `/settings?tab=agent` opened Settings on its first tab, as did every
  in-app link to a Settings tab, Chat's `?s=`/`?new=1` and Replay's
  `?thread=`. Each page's root element carries what it was asked for
  (`data-*` from the request: `settings-root` `data-tab`, `chat-root`
  `data-s`/`data-new`, `replay-root` `data-thread`, as Research, Knowledge and
  the Dashboard's incident already did), read first; an address rewrite
  (`replaceState`) runs only on the page's own path, or it rewrote the page
  before. Gate: `tests/test_deep_links_survive_soft_navigation.py` (a planted
  bare read as the control); behaviour: `tests/e2e/test_bell_opens_the_page.py`
  (the old Replay script fails it).
- **A notification opens the page it is about** (2026-11-08, the owner). Every
  item `/api/alerts/recent` returns carries `link`
  (`observability.alerts.alert_link`): its own `link:/...` callback, else its
  subsystem's page (`SUBSYSTEM_PAGES`, `SUBSYSTEM_PREFIX_PAGES`), else the
  Dashboard, whose open-problems card lists every alert; always a path of this
  site. The bell's rows are links to it (`$store.notifications.href`, which
  refuses another site), closing the bell. A new subsystem put in the bell
  needs a page or a link: `tests/test_bell_notifications_open_their_page.py`
  (a planted one as the control), `tests/js/test_bell_links.js`. **Opening
  one marks it read** (the owner: "the notification still there with a bell
  badge showing"): `POST /api/alerts/read` records the ids in
  `observability.notification_reads` (`system.notifications.read`, runtime,
  at most 500, the oldest read dropped), the list says `read` per item, the
  badge counts the unread, read news (a success or information) leaves the
  bell and a read problem stays dimmed until it is fixed; "Mark all as read"
  marks the rest. Gates: `tests/test_notification_reads.py`, the store's half
  in `tests/js/test_bell_links.js` (the old count as the control), the
  browser walk in `tests/e2e/test_bell_opens_the_page.py`.
- **A page reached by soft navigation has every script it includes**
  (`modules/nav.js`, 2026-10-03). A sidebar link swaps the page and runs its
  scripts; each is one of three kinds: the base shell's (`GLOBAL_LIB_PATHS`,
  run once by the page load: every classic script base.html loads), a shared
  one (`isSoftNavSharedScript`: a classic script in `static/js/modules/`,
  loaded once per version, `scriptKey`), or the page's own
  (`isSoftNavPageScript`, run on every visit). The shared kind did not
  exist: the folder was skipped as "modules", and Chat keeps its turn
  machinery there (`turn_document.js`, `turn_view.js`,
  `turn_presentation.js`, `delivery_cursor.js`...). On live, a Chat opened
  from the Memory page painted no turn -- no header, no steps, no approval
  card -- until a refresh, and `chat.js` declines quietly without those
  modules, so no error said why. A shared script that binds a document
  listener binds it once (`turn_visibility.js`). Gates:
  `tests/test_soft_nav_page_scripts.py` (every template's scripts, none
  dropped; the old rule as the negative control) and
  `tests/e2e/test_soft_nav_into_chat.py` (Memory, then Chat by the sidebar,
  then a turn that must paint; away and back runs each module once).
  **The page's scripts run in the order the page has them**
  (`runPageScripts`, 2026-11-22), external and inline alike, as a page
  load runs them: an inline script inside the swapped root runs where it
  stands, one elsewhere in the body is added to the body. Pages define their
  words inline before the script that reads them (chat.html's `CHAT_I18N`,
  the Dashboard's `__DASH_I18N`, Knowledge's `__KB_STRINGS`, Memory's
  `__MEM_STRINGS`); every script file used to run first, so whatever
  `chat.js` painted before the inline scripts ran was English on an Arabic
  page reached by the sidebar. No inline script ahead of a page script may
  wait for the page's load (soft navigation would run it at once). Gate:
  `tests/e2e/test_soft_nav_page_data_first.py` (Arabic, a tab opened on
  Memory, Chat by the sidebar with the script after `chat.js` held back:
  every text the turn header shows; the words moved after the page's last
  script, today's order, as the control). **A page's own merge into
  `KAZMA_I18N` runs again** (the second review): soft navigation skipped
  every inline script naming `window.KAZMA_I18N` -- meant for the shell's
  bootstrap -- so research.html's merge never ran again, the refresh wiped
  it and the Research page reached by the sidebar was English. The shell's
  bootstrap is marked `data-kazma-i18n-bootstrap` and skipped by that mark
  alone. Gate: `tests/e2e/test_research_words_on_soft_nav.py` (nav.js's
  text rule put back as the control).
- **Every `KazmaX.member` a page reads exists** (2026-09-28). The Swarm
  Templates tab called `KazmaUtils.esc` (the helper is `escapeHtml`), the
  render threw, and its fetch's `catch` showed "Failed to load templates"
  over three good ones. `tests/js/test_namespace_members.js` loads every
  script that defines a namespace in a sandbox where no page is ready and
  checks every member reference in the static JS and the templates.
- **A control changes something, or is not shown** (2026-09-28). The Agents
  page's Start/Stop flipped `agent.is_running`, which nothing else read: the
  page said "Stopped" over an agent answering chats. It now shows whether the
  server is serving and says how a reply or the server is stopped; the
  `POST /api/agents/{start,stop}` route that answered 409 with that text is
  gone since 2026-10-01 (nothing called it). Built-in skills'
  "Uninstall" and the skill switch were the same shape (§22G), and so was
  Settings → Skills until 2026-10-01. `DELETE /api/settings/{key:path}` is
  gone the same day: nothing called it, and it deleted whatever key the path
  named -- the provider list, the platform users, a secret's vault pointer --
  with no check; the route inventory missed it because a page called other
  `/api/settings/...` paths and the inventory matched paths, not methods.
  So did six more a method-level reading found the same day: a DELETE twin
  of the clear-approvals POST, GET and PATCH of one bookmark, a second MCP
  server list (`GET /api/settings/mcp`), a swarm "metrics" stub and the
  Agents start/stop -- all removed.
- **A button reaches what it names** (2026-09-28). The calendar card
  ("Google / Outlook") had one "Connect Calendar" that only started Google's
  sign-in. `tests/test_button_label_matches_action.py` follows every
  template button's click into the scripts its page loads and fails when
  the label names a provider its routes never reach, or names none inside a
  section for several while it serves one (negative control: the old card).
- **What the owner can change has a control** (2026-09-28): a route that
  changes something the owner decides gets a control on its page, or a
  reason it has none. Found with no control at all: removing a document from
  a Knowledge library, protecting a memory entity, resetting a swarm
  breaker, Outlook Calendar's disconnect, folders outside the workspace.
  Every one is now on its page. **The route inventory is a gate**
  (`tests/test_api_route_callers.py`, 2026-09-30): every `/api` route the
  built app serves has a caller -- a string in a page script, a template, the
  TUI, the CLI or a script (server-side Python is not a caller: an error text
  once made a dead route look called) -- or is declared in
  `NOT_CALLED_BY_A_PAGE` with its reason (OAuth redirects, the documented
  Document API, operator reports...). A declaration fails the day its route
  gains a caller or disappears. The first pass removed three dead routes
  (the typing-telemetry stubs and `/api/system/flush`, which reset live
  registry singletons) and found the Settings backup had no restore control;
  it has one since 2026-10-01 (§47), with runtime state and credentials kept
  out of both the file and the restore. **Since 2026-10-01 it reads methods**:
  each method of a route needs a caller WITH that method (read from the call
  around the literal -- `fetch`'s `method:`, `hx-post`, a Python client's
  `.delete(` / `_request(..., "DELETE", ...)`; a call it cannot read counts
  for every method), or a `"METHOD /path"` declaration. Matching paths only
  had let the delete-any-setting route and six other uncalled methods pass
  because a page called the same path with another method. **A prefix built
  by concatenation** (`'/api/research/' + id`) reaches only a route whose next
  segment is a parameter or a word quoted in its file or in a template that
  loads it (`'/api/x/reply/' + kind`, the page passing `'retry'`): any route
  under the prefix used to count, and `/api/research/eval` passed that way.
  **Every route exists when the app is built**
  (`tests/test_routes_exist_when_built.py`, the real lifespan run, route
  table compared): mount a router in `KazmaAppBuilder._setup_routers`, and
  read what startup makes per request (the replay API's getters). The
  research and replay APIs were mounted in `_on_startup` until 2026-10-01,
  so none of these gates had ever seen their 29 routes. The first look
  found a paper export that read any file the server could (served by
  `/download`), a report scorer that measured any file on disk -- one rule
  now, `research_pipeline.resolve_report_file` (a request names a path inside
  a `research/reports` folder) -- a DAG workflow validator for workflows
  nothing could run (removed), the task-history export and the memory
  graph's move and tier with no control (Swarm → Task History **Export
  CSV**; **Group under→** on a grouped node moves it, **Tier…**), and a
  create route that failed on its primary key for a node with a parent.
- **A panel lays out by its own width** (`@container`), not the window's:
  with the sidebar open a 918px window left the IDE editor ~40px and
  clipped the providers panel. `tests/e2e/test_layout_widths.py` (each with
  the container switched off as its control). A narrower panel moves the
  least important region first: the IDE puts its AI chat below the editor
  at 1000px and stacks the file tree only below 560px, capped in height --
  stacking everything at once put the editor 2,400px down the page.
- **Kazma installs as an app** (2026-11-14, REMAINING 4.1,
  `kazma_ui/web_app.py`): every page (and the login page) links
  `/manifest.webmanifest` (name, `start_url` `/chat`, standalone, the icons
  `scripts/make_app_icons.py` makes, shortcuts, `lang`/`dir` in the page's
  language), served before sign-in. `/sw.js` -- Web Push's worker, registered
  by every page at load -- answers a failed NAVIGATION with the one page it
  caches, `/static/offline.html` (bilingual, retries `/health/live`); it never
  answers or caches anything else. Gates: `tests/test_web_app_manifest.py`,
  `tests/e2e/test_web_app_install_browser.py` (Chrome's own
  `Page.getInstallabilityErrors` empty on a page reached by the sidebar; the
  offline page with the network gone).
- **Dates, times and numbers go through `window.KazmaFormat`**
  (`static/js/locale_format.js`, loaded by `base.html`): `Intl` in the page's
  language (`<html lang>`), Latin digits in Arabic like every other number
  the interface shows. Pages used the browser's own locale and wrote "ago"
  by hand, so Arabic pages showed English dates (`tests/js/test_locale_format.js`).
  **A count is said in its plural form**: `{{ t_plural('key', n) }}` in a
  template, `window.kazmaCount('key', n, vars)` in a script (`base.html`: the
  catalog's `<key>.zero` ... `<key>.other`, six in Arabic), never a number
  beside a word in one form -- "1 أدوات" on the MCP card and in Settings.
  `vars.n` shows the number another way ("12.3K") while the form follows
  the number; the chat's own scripts read their forms through the
  `plural` bridge (`tiCount`). Gates: `tests/test_count_labels.py` (no glued
  count in a template or an Alpine expression; no catalog count label in
  one form -- the last 34 were converted on 2026-10-02 -- whether the noun
  comes right after the count or after a word or two, "(s)" included; and
  the ARABIC of every entry checked on its own, because the English cannot
  show it: "Cleared {n} failed" was "مُسحت {n} مهام فاشلة". An Arabic word
  that does not change with the count, such as a preposition, a stat line's
  adjective or a unit, is declared there; a placeholder that is not a count
  (a name, a path) is skipped by both),
  `tests/test_kazma_count.py` (every `kazmaCount` key has every form).
  The English-only surfaces, which are the TUI, the CLI and the chat-app
  replies, say a count through `kazma_core.english_count.count_noun(n,
  'message')`: they printed "(1 msgs)" and "0/1 requests available".
  `tests/test_english_counts.py` reads every f-string in those packages,
  and a value that is not a count is declared there with why.
  A page-level helper for catalog strings must not be named after an
  element tag: the Dashboard's `tr()` was shadowed by `var tr =
  createElement('tr')` in its row loop and rendered no rows (caught by
  `tests/e2e/test_dashboard_session_table.py`). **No script declares the
  name of a translation helper it calls**, as a parameter or a variable
  (`tests/test_scripts_keep_their_translator.py`, 2026-10-02): the Swarm
  page's active-task loop `function(t)` hid `t()`, and the first translated
  word threw inside an empty `catch` -- a running task the page had not
  started never appeared.
- **The Dashboard's session table reads the chat store** for a thread's
  title, platform and message count, and the checkpoints for its saved steps
  and last activity (`list_checkpoints`: newest per thread, time from the
  uuid6 id via `checkpoint_retention.checkpoint_time`). It read a
  five-minute gateway cache and the Postgres checkpoint row (which holds no
  messages), so every live row said "unknown / anonymous / 0". Delete removes
  the chat; "Clear saved steps" removes every chat's checkpoints and keeps
  the chats (they carry on from their messages, `fallback_history`).
- **A control is wired once** (2026-09-28): never an inline `on<event>` in a
  template and a script listener for the same element and event -- Start
  All and Stop All sent every click twice. A handler that only prevents the
  default (`onsubmit="return false;"`) is not an action.
  `tests/test_ui_double_wiring.py`.
- **A list shows its empty state only after it has loaded.** The chat
  sidebar painted "No sessions yet" for a second on every visit (an early
  re-render replaced the "Loading…" placeholder).
  `tests/e2e/test_session_list_loading.py`. Every template empty state
  (length vs zero, `!list.length`, `!list.some(...)`) names a flag its loader
  sets on SUCCESS (`xLoaded`), or loading AND error flags -- `!loading` alone
  says "none" over a failed read -- or is declared with why in
  `tests/test_empty_states_after_load.py` (2026-10-15: Documents said "No
  documents yet." for seconds over five, and 39 other empty states had the shape;
  `tests/e2e/test_documents_list_loading.py`). A failed read says it failed:
  no `catch` in a page script paints an empty list, an empty-state text or
  made-up rows (the same gate; the Workspace showed a fake `kazma-core/`
  listing when its file read failed).
- **A poller reads through `window.kazmaGetJson`** (`auth-guard.js`): the
  parsed body of a 2xx JSON response, else null (an error status, a proxy's
  HTML page, no network); it never throws or logs. While the server restarts
  Cloudflare answers 502 HTML, and the Memory and Swarm pollers parsed it as
  JSON, logging "Unexpected token '<'" on every restart (2026-09-27). Gate:
  `test_every_page_survives_a_restart` (every nav page and Settings tab, every
  API GET answered 502 HTML, the page's clock run 65 s; negative control).
- **Every translation key the chat page's scripts use is bridged.** chat.js
  and agentStore.js read `window.CHAT_I18N`, which `chat.html` fills line by
  line; 15 keys had no line and showed English in every language. Gate:
  `tests/test_chat_i18n_bridge.py` (each `ti`/`tiFmt`/`tiCount` key has a
  line, each line's catalog key exists in English and Arabic, all six plural
  forms for a count).
- **A polled route never calls a rate-limited API per poll.**
  `/api/github/status` spent ~2,000 GitHub calls an hour per open Workspace
  tab (10 s poll, fetched twice per tick, three calls each). It now keeps one
  status per (owner, repo, token fingerprint) for 30 s, fetched once at a time
  (`_status_once`: single-flight), reads its stores in a thread, and the page
  refreshes on a new connection only. `tests/test_github_status_cache.py`.

