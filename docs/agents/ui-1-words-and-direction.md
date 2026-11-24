<!-- AGENTS.md, piece ui-1-words-and-direction. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
## UI Conventions (Web)

- **Dialogs:** use the unified Promise-based helpers, never native browser
  dialogs. `window.kazmaConfirm(opts)` (→ `Promise<boolean>`; with a
  `checkbox` label it shows that box unticked and answers `{ok, checked}`),
  `window.kazmaAlert(opts)` (→ `Promise<void>`), `window.kazmaPrompt(opts)`
  (→ `Promise<string|null>`). All backed by `$store.modal`
  (`static/js/modules/stores.js`) + `components/modal.html`. Each has a
  native fallback if Alpine hasn't booted. The modal is single-instance:
  `close()` clears its content 200 ms later (after the leave transition)
  only if no dialog opened meanwhile -- it cleared regardless, and an alert
  shown right after a confirm sat open with no text and no buttons
  (`tests/js/test_modal_store.js`, 2026-09-27).
- **Every interface word comes from the catalog; content is marked
  `translate="no"`** (2026-09-28). Templates use `{{ t('key') }}`; scripts
  use `window.kazmaT(key, english, vars)` (or the page's own helper that
  calls it; chat.js and agentStore.js use `ti`/`tiFmt` through
  `CHAT_I18N`). Never read `window.KAZMA_I18N[key]` yourself: an entry is
  `{en, ar}`, and research.js printed "[object Object]" that way. Dates and
  numbers go through `window.KazmaFormat` (Latin digits in Arabic). What
  stays as written -- the user's words and chat titles, the model's replies,
  names, a tool's or skill's own documentation, server diagnostics -- is
  marked `translate="no"`. Gates: `tests/test_i18n_keys_exist.py` (every key
  a page names exists in both languages), `tests/test_templates_have_no_english.py`
  (no English literal in a template), `tests/e2e/test_pages_read_in_arabic.py`
  (every page, and a chat turn, rendered in Arabic). Names that are the same
  in every language live in `tests/_ui_names.py`.
  **Scripts are gated too** (`tests/test_scripts_have_no_english.py`,
  2026-10-01): a toast, a dialog, a modal or a title a script sets after a
  click is on no page the browser tour reads, and 57 were English in every
  language (the chat's steer messages, "Read aloud", pinned/unpinned,
  "Rebuild started", the Workspace's pull-request viewer...). The gate lexes
  every script and every template's inline script and fails on English words
  at those sinks outside a translation helper (found from the source: a
  function whose first parameter is the key and whose body reads the
  catalog). HTML a script builds (`innerHTML`, `outerHTML`,
  `insertAdjacentHTML`) is read too: the expression's literals are joined in
  order and only what the browser shows is checked -- text between tags and
  the `title`/`aria-label`/`placeholder`/`alt` values, never a tag, a style,
  a class or a data attribute -- so markup split across literals is not
  English; on the old scripts it found 19 strings and no markup. In the same file: every option a page passes to
  `kazmaConfirm`/`kazmaPrompt`/`kazmaAlert` is one `stores.js` reads (the
  Workspace passed `confirmLabel`, so "Delete files too" read "Confirm"); no
  template quotes `{{ ... }}` inside a script -- write `{{ value | tojson }}`
  (Jinja's HTML escaping is not JavaScript escaping: a newline in a value
  ended the script, `&` arrived as `&amp;`); and the strings tables
  `__KB_STRINGS`/`__MEM_STRINGS` hold every `S.<key>` their scripts read.
- **A modal's body is HTML; what came from outside goes in escaped.**
  `$store.modal.body` is rendered with `x-html`. The Workspace's pull-request
  viewer put the PR's title, the reviewers' names and GitHub's merge state in
  as they came, so a PR titled `<img src=x onerror=...>` ran script in the
  operator's page when it was opened (fixed 2026-10-01,
  `tests/js/test_workspace_pr_modal.js`). `kazmaConfirm`/`kazmaPrompt`/
  `kazmaAlert` escape their message themselves; `showModal({body})` does not.
- **A script's table of labels is catalog keys, not words** (2026-09-29):
  chat.js named every step's tool in English ("Read file") on Arabic pages.
  A table read as `ti(pair[0], pair[1])` (`_HEADER_PHASE_LABELS`,
  `_TOOL_FRIENDLY`) is read by `tests/test_chat_i18n_bridge.py` like any
  `ti()` key. **Mixed-direction content sits in a `<bdi>`** -- a tool call, an
  id, a date inside Arabic text is reordered by the bidi algorithm otherwise
  (`dir="ltr"` for code-like content, automatic for a date).
- **Text follows its own language, whatever the UI's** (the owner's rule,
  2026-10-02): the UI language lays out the page and its own labels; English
  text runs and aligns left-to-right and Arabic right-to-left in BOTH UIs.
  Alignment is logical (`text-align: start`); a `left`/`right` keyed to the
  page's `dir` is declared with its reason (`DECLARED` in
  `tests/test_text_follows_its_language.py`) -- the Arabic UI right-aligned
  English replies, tool output, tables and cards. Markdown blocks get their
  direction from their own words (`KazmaBidi.blockDir`: the language most of
  the words are in, a URL/path/identifier one word; a word with an Arabic
  letter or Arabic punctuation is Arabic ("وDOCX"); a name-like Latin word
  -- acronym, CamelCase, a digit, a capital mid-sentence -- counts a
  quarter, or Arabic technical prose reads as English; a tie to the first
  letter; no letters -> the container's, so a number column stays put),
  never from `dir="auto"`'s first letter; the bidi helper isolates a run
  against ITS paragraph, never the message (an English-first reply's Arabic
  paragraph ran left-to-right in both UIs). Data a page shows is content
  (`translate="no"`; the global `unicode-bidi: plaintext` rule gives it and
  its blocks their text's direction, except an element whose `dir` a script
  set -- the renderer's paragraph, a `<bdi dir="ltr">` tool call -- and an
  interface word inside content (`translate="yes"`: an isolate in the page's
  direction); never mark a number, which then reads left-to-right). Content
  shown in a row of interface text is its own block, or it sits on the
  row's start in the page's direction. A line mixing a page label and content is split (the
  memory row). No page-`dir` rule aligns a chat bubble: a reply looks the
  same in either UI. Python asks `i18n.current_language()`; only the
  middleware reads the `kazma-lang` cookie (four routes defaulted to English
  on an Arabic install). Gates: `tests/test_text_follows_its_language.py`,
  `tests/e2e/test_text_follows_its_language.py` (real turns in both UIs,
  live and reloaded, the two compared line for line; old rules as the
  negative controls), `tests/js/test_block_direction.js`.
- **The TUI names its pages from one table, in the web UI's words**
  (`kazma_tui/nav_rail.py` `TAB_LABELS`, 2026-10-01): the tabs and the
  navigation rail read it. The tabs' Arabic labels were never applied --
  `update_localization` called `TabbedContent.tabs`, which does not exist, and
  a catch-all logged it at DEBUG -- and the rail stayed English. A tab the web
  UI also has takes its `nav.<tab>` word, or is declared with its reason
  (`tests/test_tui_labels.py`, through the real app in both languages).
  **Ctrl+L** (`action_toggle_language`, also in the command palette) switches
  and saves the language; nothing called `set_language` before.
  `update_localization` works on the MAIN screen (`screen_stack[0]`), never
  `self.screen`: a switch from the palette runs with a modal on top.
  **A handler reads only what its event carries** (`tests/test_tui_event_attributes.py`,
  from Textual's own source): the Traces tab read `event.coordinate` from
  `DataTable.RowHighlighted` and crashed the TUI whenever a trace existed.
  Text in a RichLog or a DataTable cell is Rich markup: escape what comes
  from data (`rich.markup.escape`) and never put a Textual `$variable` in it
  (`App.get_css_variables()` gives the colour).
