---
id: native-skills
title: Native Skills
sidebar_label: Native Skills
description: Built-in tool skills that ship with Kazma — browser automation, calendar, document generation, image generation, database, and more.
---

Native skills are **in-process Python tools** that ship with Kazma and are
auto-registered on the `LocalToolRegistry` at startup (no subprocess, no
network round-trip — unlike [MCP servers](../guide/skills-mcp-and-tools)).
They live in `kazma-skills/kazma_skills/native/` and are discovered by the
`NativeSkillLoader`.

> Skills always **load** even when an optional dependency is missing — calling
> a tool whose backend isn't installed returns a friendly install-hint string
> instead of failing. Install the extra to activate that backend:
> `pip install -e ".[document,database,web]"`.

For the full per-tool list (names, args, danger classification), see the
[Tools Catalog](../reference/tools-catalog).

---

## Browser automation (`browser_automation`)

Headless browser control via **Playwright** for JS-rendered pages a plain HTTP
fetch can't read. Requires the `[web]` extra + a one-time browser install:

```bash
pip install -e ".[web]"
playwright install chromium
```

| Tool | What it does |
|---|---|
| `browser_navigate` | Open a URL, return title + visible body text |
| `browser_click` | Click an element by CSS selector |
| `browser_extract_text` | Extract text from elements (or full body) |
| `browser_screenshot` | Full-page screenshot → `kazma-data/images/` |
| `browser_fill_form` | Fill inputs from a `{selector: value}` mapping |
| `browser_eval_js` | Evaluate page-side JavaScript — **HITL-gated as danger** |

A shared headless Chromium context persists across calls for efficiency.

---

## Calendar (`calendar`)

Read and manage events. Three backends, selected by **vault-backed**
credentials (the same store Gmail uses). Env vars are an override, not
the source of truth.

- **Google Calendar** — Settings → Email → **Connect with Google**
  (Calendar scope is included) or **Connect Google Calendar** on the
  calendar card. Tokens live in the vault as `calendar.google.*`. Enable
  the **Google Calendar API** in the Cloud project. A Gmail-only token is
  never sent to Calendar.
- **Microsoft Outlook** (MS Graph) — Settings → Email → **Connect with
  Microsoft** (requests `Calendars.ReadWrite` with mail), or **Connect
  Outlook Calendar** on the calendar card for the calendar alone.
- Each calendar's **Disconnect** on the card turns it off until it is
  connected there again, even while its mail stays signed in.
- **Sandbox** — in-memory local calendar when **no** account is
  connected (`provider=auto`). Explicit `provider="google"` / `"outlook"`
  **fails closed** with a connect hint — it does not pretend to be an
  empty real calendar.

```bash
# Optional override only — prefer Settings → Email → the calendar card
export GOOGLE_CALENDAR_TOKEN=ya29...
```

| Tool | What it does |
|---|---|
| `list_events` | List events in a time range (ISO 8601; defaults next 7 days) |
| `create_event` | Create an event (summary, start, end, location, description) |
| `update_event` | Update an event by id |
| `delete_event` | Delete an event by id |
| `find_free_slots` | Find free slots of a given duration on a date |

Use `provider="auto"` (default) to pick the first credentialed backend, or
`provider="google"` / `"outlook"` to pin one.

---

## Document platform (`document_platform`) — preferred

Durable, tenant-scoped document intelligence via the shared
`DocumentIngestionService`. Opaque IDs, restart-safe jobs, fenced reads.
See [Document Intelligence](./document-intelligence.md).

| Tool | Purpose |
|---|---|
| `document_import` | Workspace-safe file → durable ingest + parse; waits up to 30 s for a final state and reports the state it reached |
| `document_status` | A `job_id`'s state, or a `document_id`'s newest job; no ids → tenant platform overview |
| `document_read` | Paged fenced content by opaque id (`page`, `offset`, `max_chars`, default 20,000) |
| `document_index` | Publish into a Knowledge library (`library_id`) |
| `document_search` | Search library (`library_id`, `query`, `top_k`; fenced hits) |
| `document_cancel` | Cancel job by id (**danger**: asks for approval) |
| `document_convert` | Convert format → a saved file (its path, for `send_file`) and its artifact id |
| `document_redact` | Physical PDF redaction → new artifact and a saved file (**danger**: asks for approval) |

The tools act as the install's operator, like the `/documents` chat command
and the Web **Documents** page, so they read what was uploaded there and the
page lists what they import (since 2026-10-13). With platform users
(multi-user mode) they refuse. A job or document they cannot read answers
like an unknown one.

The coordinator runs on the base install (pypdf, pdfplumber, python-docx,
openpyxl and python-pptx are base dependencies). The `[document-platform]`
extra adds PyMuPDF (the first PDF parser, and redaction), pypdfium2, OCR
(pytesseract, pdf2image, Pillow) and WeasyPrint (HTML/Markdown → PDF);
Tesseract, LibreOffice and ClamAV are system packages. What is ready is live:
`/documents health` or `GET /api/documents/health` (in the container worker
profile, the worker image's own report).

## Document generation (`document_generator`) — simple / legacy path

Create **simple** documents from structured content **without** the durable
platform. Files land under `kazma-data/documents/`. Prefer `document_platform`
when you need opaque IDs, ACL, jobs, or re-ingest. Generation still runs in the
isolated renderer worker (`DocumentService.generate`), and a generated PDF
passes the same active-content check as an uploaded one before it leaves the
worker.

reportlab, python-docx and openpyxl are base dependencies, so PDF/DOCX/XLSX
need no extra; `[document]` remains only as a compatibility alias.

| Tool | Output | Library |
|---|---|---|
| `generate_pdf` | `.pdf` in `kazma-data/documents/` | reportlab |
| `generate_docx` | `.docx` | python-docx |
| `generate_xlsx` | `.xlsx` (multi-sheet) | openpyxl |
| `generate_markdown_doc` | `.md` | *(no dependency)* |

Each tool takes a title + sections (`[{heading, body}]`), or sheets for XLSX.

**Arabic PDF fonts**: `generate_pdf` uses the same IBM Plex Sans Arabic
pair as the web UI and the Docusaurus docs (vendored under
`kazma_core/documents/assets/fonts/`). Coverage is verified against Arabic
presentation forms before the face is accepted; Amiri remains a naskh
fallback if Plex is absent. Override the directory with
`KAZMA_DOCUMENT_FONT_DIR`. Direction is per paragraph, not per document: an
Arabic paragraph is right-aligned and an English one left-aligned in the same
file. A PDF that holds Arabic is rendered through the DOCX engine and
LibreOffice (`soffice`) when it is installed, for real shaping and bidi;
without LibreOffice it falls back to ReportLab and says so in a warning.

---

## Image generation

A **built-in core tool** (`generate_image` in `kazma_core/tools/image_gen.py`),
not a native skill — multi-backend via `image_backends/router.py`:

| Backend | Provider key | Needs |
|---|---|---|
| Pollinations | `pollinations` | nothing (keyless, **default**) |
| DALL-E (OpenAI) | `dall-e` | `OPENAI_API_KEY` |
| Stability (SDXL) | `stability` | `STABILITY_API_KEY` |
| Flux (FAL.ai) | `flux` | `FAL_KEY` |

`provider="auto"` picks the first credentialed backend. Override with
`KAZMA_IMAGE_PROVIDER`. Output → `kazma-data/images/`.

---

## Database client (`database_client`)

Query databases read-only. SQLite is built-in; Postgres/MySQL/Mongo need the
`[database]` extra:

```bash
pip install -e ".[database]"   # psycopg, pymysql, pymongo
```

| Tool | What it does |
|---|---|
| `inspect_db_schema` | Tables, columns, types, PKs, indexes |
| `execute_db_query` | Local SQLite SELECT, or a named remote read capability |
| `sqlite_query` | Convenience alias for local SQLite |

SQLite paths remain workspace-scoped; Kazma's own stores are refused. Remote
reads use `db_uri="connection:warehouse"`. Raw remote URIs, including localhost
URIs, are refused. The operator defines the connection in
`security.database_client.connections`, a protected setting the agent cannot
write. Each entry needs `enabled: true`, a vault-backed `dsn`, the expected
login `role`, an explicit `tenants` list, and `tables` containing qualified
`schema.table` names (MongoDB: collection names). Wildcards are refused.

For example, an operator can register a PostgreSQL reader from the install's
Python environment. Run this with the usual install configuration loaded and
an encrypted vault configured; never put the password in a prompt or Git:

```python
import os
from kazma_core.config_store import get_config_store
from kazma_core.security.vault import get_vault

vault = get_vault()
assert vault is not None, "Configure the encrypted vault first"
vault.store_install_scoped("warehouse_read_dsn", os.environ["WAREHOUSE_READ_DSN"], category="database")
get_config_store().set("security.database_client.connections", {
    "warehouse": {
        "enabled": True,
        "dsn": "vault://warehouse_read_dsn",
        "role": "kazma_reader",
        "tenants": ["default"],
        "tables": ["reporting.items"],
    }
})
```

Remote SQL uses a parsed SELECT grammar with ordinary projections, comparisons,
joins, nonrecursive CTEs and a small set of built-in aggregates/string functions.
Unknown functions, casts, custom operators, locks, INTO, multiple statements and
tables outside the capability are refused before connecting. PostgreSQL reads
pin `search_path` to `pg_catalog`; tables must be qualified. Positional `%s`
parameters work with both SQL drivers. Views, foreign tables, inherited tables,
RLS, custom PostgreSQL types and generated columns are refused because a read
of them can invoke code outside the declared capability.

Use a dedicated database login, never the application's owner or administrator:

- PostgreSQL: no elevated role flags or memberships, no database CREATE/TEMP,
  no schema CREATE, no table or column write grants, and no EXECUTE on
  application/extension routines. Grant CONNECT, schema USAGE and SELECT on the
  declared ordinary tables. Revoke the default PUBLIC TEMP privilege on the
  **dedicated reporting database**, and revoke PUBLIC EXECUTE on its noncatalog
  routines; an administrator must assess those changes before applying them
  to a shared application database. The tool verifies these conditions on
  every connection and refuses privilege drift.
- MySQL 8: only SELECT/USAGE grants, without grant option or role memberships;
  declared tables must be InnoDB base tables. MariaDB is not qualified: a server
  that lacks MySQL's statement timeout setting refuses the read.
- MongoDB: a custom role with collection-specific `find` privileges only,
  matching the declared database and collections. Standard broader `read`
  roles are refused. Filters support data comparisons and Boolean predicates;
  `$where`, `$expr`, `$function`, regular expressions and other operators are
  refused. `params[0]` selects an allowed collection.

Queries have a two-second server/SQLite execution limit, at most 1,000 rows,
16,384 query characters and a one-million-character returned result limit.
Remote connect/socket waits are bounded; connections close after each call.
Output limits are checked after fetching, so operators should expose tables
with bounded column sizes and database resource quotas. Nonloopback connections
require verified TLS (PostgreSQL uses its configured root certificate; MySQL
and MongoDB use their driver trust configuration). DSN query options are
refused. Install the `[database]` extra for SQLGlot and the remote drivers.

---

## Other native skills

| Skill | Tools | Notes |
|---|---|---|
| `email_manager` | email_accounts/list/get/send/delete/categorize/analyze | Gmail, MS Graph, IMAP/POP, sandbox |
| `git_github_manager` | git_status/log/diff/commit/push/pull/checkout/merge, github_create_pr/merge_pr/create_issue/comment_issue/list_issues | `git_log` and `git_diff` are read-only (no approval) |
| `secret_vault` | vault_store/retrieve/list/delete | AES-256-GCM encrypted |
| `advanced_web_crawler` | web_search_duckduckgo, crawl_page, parse_document | CSV/JSON/XLS/PDF parsing |
| `system_health_monitor` | get_system_stats, list_active_processes, read_system_logs | |
| `task_scheduler_cron` | schedule_task, list_scheduled, edit_scheduled, cancel_scheduled | Booking, editing and cancelling are HITL-gated. See [Scheduled reminders and tasks](#scheduled-reminders-and-tasks) |
| `x_publisher` | x_status, x_post, x_delete_post, x_schedule_post, x_list_scheduled, x_cancel_scheduled_post | Official X API v2. Chat writes **always HITL**. Web compose/plan is [X Studio](x-publisher) (`/x`) |
| `environment_bootstrapper` | install_python_packages, install_npm_packages, check_environment | HITL-gated |
| `visual_interpreter_generator` | analyze_local_image, generate_ui_mockup | |
| `arabic_bilingual_nlp` | arabic_translate, hijri_convert, insert_diacritics | |
| `chat_platform_dispatcher` | dispatch_notification, send_approval_request, send_message | cross-platform notify |
| `code_analyzer_linter` | lint_code, format_code, run_unit_tests | ruff + pytest |
| `code-review`, `fix-lint`, `refactor-file`, `write-tests` | *(manifest-only, no tools)* | prompt/workflow skills |

---

## Scheduled reminders and tasks {#scheduled-reminders-and-tasks}

`schedule_task` books a job; `list_scheduled`, `edit_scheduled` and
`cancel_scheduled` read and change the jobs of the conversation's install. The
Scheduled page (`/scheduled`) shows the same jobs, with the X posts booked in
[X Studio](x-publisher). Booking, editing and cancelling each ask for approval.

### What a scheduled job can do when it fires

A job is one of two kinds, chosen when it is booked:

| Kind | When it fires | Tools |
|---|---|---|
| `reminder` (the default) | Sends its stored text, as written, to the chat it was booked from. No model call. | None |
| `task` | Runs one agent turn on its stored instruction, then sends the answer to that chat. | Only the set declared at booking: `read` (read-only tools, the default) or `read_write` (write tools too). Never MCP tools, and never the tools that always need approval. |

- The approval card of the booking says which kind it is and which tools the
  task will have. A call to any other tool is refused when the job fires.
- The instruction reaches the model labelled as an instruction stored
  earlier, not as something the user just said.
- A task does not fire while approvals (HITL) are switched off, unless the
  operator turned on `safety.cron_fire_without_hitl`. The agent cannot turn
  it on itself. A reminder still fires: it runs no model.
- A task's turn has a time budget (`cron.task_budget_seconds`, default 300).
  A turn that fails -- the model provider is down, the turn errors or runs
  out of time -- is a failed fire: it is counted on the job and raises an
  alert. The error text is never sent as if it were the reminder. After 3
  failures in a row a recurring job stops, with an alert.

### Delivery, late and missed jobs

- A message counts as sent only when the chat app accepted it. A refused
  message is tried again with a growing wait, for up to two hours. Then the
  job ends **undeliverable**, with one alert. If Kazma stopped in the middle
  of sending, the job ends **delivery unknown**, with one alert, and the
  message is not sent a second time.
- A job that fell due while Kazma was down still fires if it is at most
  `cron.catchup_minutes` late (default 60), and says how late it is. A job
  later than that is **missed**: it does not run, and its chat gets one
  notice. A recurring job then moves on to its next time. A window given
  when the job is booked wins over the setting.
- An interrupted fire runs again after a restart. A reload with
  `--when-idle` waits for a fire that is running.
- No restart loses one of these alerts. Each is recorded together with the
  failure it reports, and raised after that. If Kazma stops after raising an
  alert but before recording that it did, the alert is raised once more when
  Kazma starts.

### Times

Times are read in the cron time zone (Settings, `cron.timezone`; else
`KAZMA_TZ`; else UTC). Kazma accepts these forms:

- a delay: `30m`, `2h`, `3d`;
- an ISO time: `2026-12-01T09:00`, read in that zone; `Z` and offsets are
  honoured;
- a recurrence: `daily at 9am`, `weekdays at 08:00`, `every mon,wed at 21:00`,
  or an RRULE such as `FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0;COUNT=10`.

Kazma refuses a time in the past or more than two years ahead. It also
refuses a one-off time that does not exist on a clock-change day, or that
occurs twice; give an offset instead. A recurring job uses the earlier of
two repeated times, and moves a time that does not exist forward by the
change. X posts take one-off times only.

### Changing a job

- An edit or a cancel names the version of the job it read. A job that
  changed meanwhile answers with a conflict; refresh and try again.
- A job running right now takes an edit or a cancel when the run ends.
- A finished job comes back only through **Run again**.
- A timing-only edit never changes the stored prompt.
- Every change records who made it and from where.

On an install with platform users (multi-user mode), scheduling is off: the
page shows why, and the tools and routes refuse. Scheduled jobs are not
separated per user yet.

---

## Authoring your own

See [Creating Skills](../skill-development/creating-skills) and the
[Skill Manifest Spec](../reference/skill-manifest). A minimal skill is a
directory under `native/<name>/` with `skill_manifest.yaml` (metadata + tool
keys) and `tools.py` (async functions whose names match the manifest keys).
Tools are auto-discovered — no registration call needed.
