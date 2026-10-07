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
| `document_import` | Workspace-safe file → durable ingest + parse |
| `document_status` | Job / document state; no ids → tenant platform overview |
| `document_read` | Paged fenced content by opaque id |
| `document_index` | Publish into a Knowledge library (`library_id`) |
| `document_search` | Search library (fenced hits) |
| `document_cancel` | Cancel job by id |
| `document_convert` | Convert format → artifact id |
| `document_redact` | Physical PDF redaction → new artifact |

No special `[document]` extra is required for the coordinator itself; optional
render/redaction engines (pymupdf, WeasyPrint, LibreOffice) affect conversion
and some PDF mutations only.

## Document generation (`document_generator`) — simple / legacy path

Create **simple** documents from structured content **without** the durable
platform. Files land under `kazma-data/documents/`. Prefer `document_platform`
when you need opaque IDs, ACL, jobs, or re-ingest.

Requires the `[document]` extra for PDF/DOCX/XLSX:

```bash
pip install -e ".[document]"   # reportlab, python-docx, openpyxl
```

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
`KAZMA_DOCUMENT_FONT_DIR`. When an Arabic font is active, all styles
(title, headings, body) use it with `TA_RIGHT` alignment.

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
| `email_manager` | email_list/get/send/delete/categorize/analyze | Gmail, MS Graph, IMAP/POP, sandbox |
| `git_github_manager` | git_status/commit/push_pull, github_create_pr/list_issues | |
| `secret_vault` | vault_store/retrieve/list/delete | AES-256-GCM encrypted |
| `advanced_web_crawler` | web_search_duckduckgo, crawl_page, parse_document | CSV/JSON/XLS/PDF parsing |
| `system_health_monitor` | get_system_stats, list_active_processes, read_system_logs | |
| `task_scheduler_cron` | schedule_task, list_scheduled, cancel_scheduled | HITL-gated |
| `x_publisher` | x_status, x_post, x_delete_post, x_schedule_post, x_list_scheduled, x_cancel_scheduled_post | Official X API v2. Chat writes **always HITL**. Web compose/plan is [X Studio](x-publisher) (`/x`) |
| `environment_bootstrapper` | install_python_packages, install_npm_packages, check_environment | HITL-gated |
| `visual_interpreter_generator` | analyze_local_image, generate_ui_mockup | |
| `arabic_bilingual_nlp` | arabic_translate, hijri_convert, insert_diacritics | |
| `chat_platform_dispatcher` | dispatch_notification, send_approval_request, send_message | cross-platform notify |
| `code_analyzer_linter` | lint_code, format_code, run_unit_tests | ruff + pytest |
| `code-review`, `fix-lint`, `refactor-file`, `write-tests` | *(manifest-only, no tools)* | prompt/workflow skills |

---

## Authoring your own

See [Creating Skills](../skill-development/creating-skills) and the
[Skill Manifest Spec](../reference/skill-manifest). A minimal skill is a
directory under `native/<name>/` with `skill_manifest.yaml` (metadata + tool
keys) and `tools.py` (async functions whose names match the manifest keys).
Tools are auto-discovered — no registration call needed.
