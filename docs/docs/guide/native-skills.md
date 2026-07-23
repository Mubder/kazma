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

Read and manage events. Three backends, selected by credential availability:

- **Google Calendar** — OAuth token via `GOOGLE_CALENDAR_TOKEN` (or
  `GOOGLE_OAUTH_TOKEN`).
- **Microsoft Outlook** (MS Graph) — token via `MS_CALENDAR_TOKEN`
  (or `MS_GRAPH_TOKEN`).
- **Sandbox** — in-memory local calendar (always available; events persist
  within the process for testing).

```bash
export GOOGLE_CALENDAR_TOKEN=ya29...   # or MS_CALENDAR_TOKEN
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

## Document generation (`document_generator`)

Create documents from structured content. Requires the `[document]` extra:

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
| `execute_db_query` | Read-only SELECT (dialect auto-detected from URI scheme) |
| `sqlite_query` | Convenience alias for local SQLite |

Dialect is detected from the `db_uri` scheme: `postgresql://` → Postgres
(psycopg3), `mysql://` → MySQL (pymysql), `mongodb://` → Mongo (JSON filter),
else SQLite. All SQL dialects enforce read-only (SELECT/WITH only; write
keywords blocked).

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
