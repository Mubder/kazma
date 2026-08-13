# Handoff — Easy, Low-Risk Cosmetic Fixes

**For:** a junior/automated agent (any model tier).
**Purpose:** knock out the remaining purely-cosmetic / dead-code / DRY items from
the security audit. These are intentionally the **only** kind of change in this
file — each one is isolated, mechanical, and cannot affect runtime behavior or
project quality if applied exactly as written.

---

## ⛔ READ THESE RULES FIRST (do not skip)

1. **Do exactly what each task says.** Do not "improve", refactor, rename, or
   touch any other line. No drive-by edits.
2. **One task = one file (except Task 4, which is one file, 3 spots).**
3. **If the `old` string does not match exactly**, STOP and report — do not
   guess. The code may have already been changed.
4. **Never touch** anything involving: security, auth, HITL, concurrency/locks,
   SQLite/DB writes, the LangGraph state, the gateway message loop, LLM
   providers, memory recall/write, document ingestion, or config stores.
   Those are out of scope and quality-critical.
5. After every task, run the **Check** step for that file. Do not commit a file
   that fails its check.
6. Commit each task separately (see "How to commit" at the end).

---

## Setup

You are on branch `main`, working tree clean, in sync with `origin/main`.
Work on a new branch:
```bash
git checkout -b chore/cosmetic-cleanup
```

Validation tools (use the exact commands):
- Python file: `'.venv/Scripts/python.exe' -c "import py_compile; py_compile.compile(r'<FILE>', doraise=True); print('OK')"`
- JS file:     `node --check "<FILE>"`

---

## Task 1 — Replace inline `display:none` with `x-cloak` (3 spots)

**Why:** AGENTS.md UI convention — `[x-cloak]{display:none!important;}` is the
single global rule. An `x-show` element that uses inline `style="display:none;"`
works today only because a CSS class backstops it; the inline style is the
documented anti-pattern (inline `display` wins over Alpine's toggle). Pure
cosmetic — no behavior change.

**File A:** `kazma-ui/kazma_ui/templates/chat.html` (around line 18)

`old` (match exactly, includes leading spaces):
```
       @click="closeMobileChatSidebar()" style="display: none;"></div>
```
`new`:
```
       @click="closeMobileChatSidebar()" x-cloak></div>
```

**File B:** `kazma-ui/kazma_ui/templates/base.html` (around line 49)

`old`:
```
             @click="closeMobileNav()" style="display: none;">
```
`new`:
```
             @click="closeMobileNav()" x-cloak>
```

**File C:** `kazma-ui/kazma_ui/templates/base.html` (around line 124)

`old`:
```
         @click.self="$store.search.close()" style="display: none;"
```
`new`:
```
         @click.self="$store.search.close()" x-cloak
```

**Check:** open the 3 files and confirm the `style="display: none;"` is gone from
those exact lines and `x-cloak` is present. (HTML templates — no compile step.)
**Commit:** `fix(ui): use x-cloak instead of inline display:none on x-show panels`

---

## Task 2 — IDE system message: render through the markdown escaper

**Why:** `templates/ide.html` renders `role === 'system'` content as raw HTML
(`x-html="m.content"`), while user/assistant content correctly goes through the
escaping `_renderMd`. Today it's inert (the only system message is a hardcoded
literal), but it's an asymmetric trust assumption — any future system message
with attacker-influenced text would render as live HTML.

**File:** `kazma-ui/kazma_ui/templates/ide.html` (line ~261)

`old`:
```
                <div x-html="m.role === 'system' ? m.content : _renderMd(m.content)"></div>
```
`new`:
```
                <div x-html="_renderMd(m.content)"></div>
```

**Check:** confirm the line no longer references `m.role === 'system'`.
**Commit:** `fix(ui): escape IDE system messages through _renderMd`

---

## Task 3 — TUI: fix the dead Arabic-header localization block

**Why:** `kazma_tui/app.py` does `self.query_one(Header)` to apply an Arabic
CSS class, but `Header` is **never imported** (only `KazmaHeader` is). So the
call always raises `NameError`, swallowed by the `except` at debug — the
localization is a silent no-op. `KazmaHeader` is the widget actually rendered,
so targeting it is the one-token fix; the surrounding try/except keeps it safe
(worst case it logs at debug and does nothing).

**File:** `kazma-tui/kazma_tui/app.py` (line ~658)

`old`:
```
            header = self.query_one(Header)
```
`new`:
```
            header = self.query_one(KazmaHeader)
```

**Check:**
```bash
'.venv/Scripts/python.exe' -c "import py_compile; py_compile.compile(r'kazma-tui/kazma_tui/app.py', doraise=True); print('OK')"
grep -n "query_one(KazmaHeader)" kazma-tui/kazma_tui/app.py   # should print 1 line
```
(If `KazmaHeader` turns out not to be imported at the top of the file, do NOT
add an import — instead just delete the whole `# 3. Update Header title ...`
try/except block. It does nothing today either way.)
**Commit:** `fix(tui): target KazmaHeader for Arabic header-title localization`

---

## Task 4 — Centralize the GitHub repo slug (DRY)

**Why:** the repo slug `Mubder/kazma` is hardcoded in 3 separate strings in
`update.py`. If the repo is ever renamed/transferred, all version lookups
silently 404. Centralize to one constant. Pure string refactor — values are
byte-identical before/after.

**File:** `kazma-cli/kazma_cli/update.py`

**Step 4a — add the constant.** Find this line near the top of the file:
```
_UPDATE_REMOTE_REF = "origin/main"
```
Immediately **after** it, add:
```
_GITHUB_REPO = "Mubder/kazma"
```

**Step 4b — use it in the 3 strings.**

Spot 1 (line ~293):
`old`:
```
                "https://api.github.com/repos/Mubder/kazma/releases/latest",
```
`new`:
```
                f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
```

Spot 2 (line ~294):
`old`:
```
                "https://api.github.com/repos/Mubder/kazma/tags?per_page=1",
```
`new`:
```
                f"https://api.github.com/repos/{_GITHUB_REPO}/tags?per_page=1",
```

Spot 3 (line ~1556):
`old`:
```
        console.print("Or check network / GitHub releases: https://github.com/Mubder/kazma")
```
`new`:
```
        console.print(f"Or check network / GitHub releases: https://github.com/{_GITHUB_REPO}")
```

**Check:**
```bash
'.venv/Scripts/python.exe' -c "import py_compile; py_compile.compile(r'kazma-cli/kazma_cli/update.py', doraise=True); print('OK')"
grep -c "Mubder/kazma" kazma-cli/kazma_cli/update.py   # should print: 1  (only the _GITHUB_REPO definition)
```
**Commit:** `chore(cli): centralize GitHub repo slug in update.py`

---

## How to commit & finish

Each task above says its own commit message. Make one commit per task (4
commits total). Suggested flow per task:
```bash
git add <the-file>
git commit -m "<the message from the task>"
```

When all 4 are done:
```bash
# sanity: nothing else changed
git status
git diff --stat main..HEAD      # expect exactly these files, tiny diffs

# optional quick test sweep (should be all green; any failure = stop and report)
'.venv/Scripts/python.exe' -m pytest tests/test_cli_commands.py -q

git push -u origin chore/cosmetic-cleanup
```
Then open a PR (or hand the branch back for review).

---

## ✅ Done criteria
- Exactly 4 commits, on `chore/cosmetic-cleanup`.
- Changed files (no others):
  - `kazma-ui/kazma_ui/templates/chat.html`
  - `kazma-ui/kazma_ui/templates/base.html`
  - `kazma-ui/kazma_ui/templates/ide.html`
  - `kazma-tui/kazma_tui/app.py`
  - `kazma-cli/kazma_cli/update.py`
- Every Python file passes `py_compile`; `grep -c "Mubder/kazma"` on update.py == 1.

## ❌ Out of scope (do NOT attempt — these need a senior agent / special env)
Everything else from the audit's "still open" list: the Postgres document fixes,
document principal-ACL threading, the serial gateway-consumer change, the WS
token redesign, the memory lock/cache perf tweaks, `KAZMA_API_KEY` (intentional),
`discover_models allow_private`, the legacy `ShellTool`, `nav.js` new Function
(2-file i18n change), `create_hitl_approval_router` (it IS used by tests — not
dead), and the swarm-task IDOR. If unsure whether something is in scope, it
isn't — stop and ask.
