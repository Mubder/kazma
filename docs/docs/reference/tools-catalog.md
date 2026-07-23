---
id: tools-catalog
title: Tools Catalog
sidebar_label: Tools Catalog
description: Complete catalog of built-in agent tools and native skill tools
---

> Exhaustive tool list extracted from `LocalToolRegistry` and native skill manifests. Regenerate with `python scripts/generate_tools_catalog.py`. Danger classification aligns with `CANONICAL_DANGER_TOOLS` — see [Security & Safety](../guide/security-and-safety).

## How tools run

| Layer | Module | Notes |
|-------|--------|-------|
| Built-in registry | `kazma_core/agent/tool_registry.py` | Supervisor SoT; HITL in `execute()` |
| Unified executor | MCP + local | MCP non-allowlist tools force danger under production |
| IDE path | `IdeService._call_tool` | Same registry — no bypass |
| Native skills | `kazma-skills/kazma_skills/native/*` | Loaded via skill manifests |

## Built-in tools (LocalToolRegistry)

| Tool | Category | Danger (typical) | Description |
|------|----------|------------------|-------------|
| `file_read` | filesystem | safe/read | Read a file from the local filesystem. Returns the file contents as text. |
| `file_write` | filesystem | **danger** | Write content to a local file. Creates parent directories if needed. Overwrites existing content. |
| `file_delete` | filesystem | **danger** |  |
| `file_list` | filesystem | safe/read | List files and directories at a path. Returns names sorted alphabetically. |
| `file_search` | filesystem | safe/read |  |
| `memory_search` | memory | safe/read |  |
| `memory_store` | memory | safe/read |  |
| `current_datetime` | utility | safe/read | Get the current date, time, and timezone in ISO-8601 format. |
| `config_save` | system | **danger** |  |
| `config_read` | system | safe/read |  |
| `shell_exec` | system | **danger** | Execute a shell command and return stdout+stderr. Use with caution. |
| `spawn_agent` | delegation | **danger** |  |
| `spawn_agents` | delegation | **danger** |  |
| `dispatch_swarm` | swarm | safe/read |  |
| `check_swarm_task` | swarm | safe/read |  |
| `python_exec` | code | **danger** |  |
| `context_info` | diagnostics | safe/read |  |

### Related tool modules (`kazma_core/tools/`)

These modules implement or support tools (some registered at startup, some via skills):

- `code_exec.py`
- `context_cmd.py`
- `export_session.py`
- `file_read.py`
- `file_write.py`
- `image_gen.py`
- `personality_cmd.py`
- `read_url.py`
- `registry.py`
- `send_message.py`
- `vision_analyze.py`
- `web_research.py`
- `web_search.py`

## Native skill tools

| Tool | Skill | Category | Danger (typical) | Description |
|------|-------|----------|------------------|-------------|
| `web_search_duckduckgo` | advanced-web-crawler | web | safe/read | Search the public web via core web_search (SearXNG / DuckDuckGo / Bing). Markdown titles, URLs, snippets. May rate-limit without SearXNG.
 |
| `crawl_page` | advanced-web-crawler | web | safe/read | Fetch ONE public URL and extract readable text (alias of read_url). Not multi-page crawl. Playwright fallback for bot walls / thin JS shells when installed.
 |
| `parse_document` | advanced-web-crawler | filesystem | safe/read | Parse structured text from local files including CSV, JSON, XLS, or PDF. |
| `arabic_translate` | arabic-bilingual-nlp | nlp | safe/read | Translate context-preserving between Arabic and English. |
| `hijri_convert` | arabic-bilingual-nlp | nlp | safe/read | Convert dates between Gregorian calendar (YYYY-MM-DD) and Hijri calendar. |
| `insert_diacritics` | arabic-bilingual-nlp | nlp | safe/read | Apply correct vowel diacritics (tashkeel/harakat) to Arabic text based on semantic grammar. |
| `browser_navigate` | browser-automation | browser | safe/read | Open a URL in a headless browser and return the page title plus the visible body text (truncated). Use for JS-rendered pages a plain HTTP fetch cannot read.
 |
| `browser_click` | browser-automation | browser | safe/read | Click an element matched by a CSS selector on the current page and return the updated text.
 |
| `browser_extract_text` | browser-automation | browser | safe/read | Extract text content from elements matching a CSS selector on the current page (or the full body if no selector).
 |
| `browser_screenshot` | browser-automation | browser | safe/read | Capture a screenshot of the current page (full page) and save it to kazma-data/images/. Returns the file path.
 |
| `browser_fill_form` | browser-automation | browser | safe/read | Fill input fields on the current page from a mapping of CSS selectors to values, optionally submitting the form.
 |
| `browser_eval_js` | browser-automation | browser | **danger** | Evaluate a JavaScript expression on the current page and return the result. Use with care — this executes arbitrary page-side code.
 |
| `list_events` | calendar | calendar | safe/read | List upcoming calendar events within a time range (ISO 8601). Defaults to the next 7 days.
 |
| `create_event` | calendar | calendar | safe/read | Create a calendar event with a title, start/end (ISO 8601), optional location and description.
 |
| `update_event` | calendar | calendar | safe/read | Update an existing event by id. Only provided fields are changed.
 |
| `delete_event` | calendar | calendar | safe/read | Delete a calendar event by id.
 |
| `find_free_slots` | calendar | calendar | safe/read | Find free time slots of a given duration within a date range, excluding existing busy events.
 |
| `dispatch_notification` | chat-platform-dispatcher | communication | safe/read | Send a notification message to a specific recipient or channel on Telegram, Discord, or Slack. |
| `send_approval_request` | chat-platform-dispatcher | communication | safe/read | Dispatch an interactive approval card with actions/buttons for human verification (HITL). |
| `send_message` | chat-platform-dispatcher | communication | safe/read | Send a text message to the current conversation thread. Use this to reply to the user. The platform and delivery channel are handled automatically. |
| `lint_code` | code-analyzer-linter | code | safe/read | Execute static checks on Python files using ruff linter to detect errors and unused imports. |
| `format_code` | code-analyzer-linter | code | safe/read | Format source code files using ruff format to maintain styling guidelines. |
| `run_unit_tests` | code-analyzer-linter | code | safe/read | Execute tests in the test path using pytest and return a structured summary of successes or traceback errors. |
| `inspect_db_schema` | database-client | database | safe/read | Extract list of tables, column names, data types, primary/foreign keys, and indexes from SQLite databases. |
| `execute_db_query` | database-client | database | safe/read | Execute a read-only SQL SELECT query against a local SQLite database file. |
| `sqlite_query` | database-client | database | safe/read | Execute a read-only SQL query against the local SQLite database. SELECT queries only. Returns rows as JSON. |
| `generate_pdf` | document-generator | document | safe/read | Generate a PDF document from a title and a list of sections (heading + body text). Returns the saved file path.
 |
| `generate_docx` | document-generator | document | safe/read | Generate a Word .docx document from a title and a list of sections (heading + body text). Returns the saved file path.
 |
| `generate_xlsx` | document-generator | document | safe/read | Generate an Excel .xlsx workbook from a list of sheets, each with a list of row-lists (the first row is the header). Returns the file path.
 |
| `generate_markdown_doc` | document-generator | document | safe/read | Generate a Markdown (.md) document from a title and a list of sections (heading + body). Returns the saved file path.
 |
| `email_list` | email-manager | email | safe/read | List, search, and page emails in a folder (INBOX default). Args: folder, query, limit, offset, unread_only, provider (auto\|sandbox\|gmail\|microsoft\|imap), account (optional multi-account alias).
 |
| `email_get` | email-manager | email | safe/read | Fetch full email by message_id. Args: message_id, include_body, max_body_chars, provider.
 |
| `email_send` | email-manager | email | **danger** | Send, reply, forward, or save draft. HITL required. Args: to, subject, body, action (send\|reply\|forward\|draft), cc, message_id, body_format, provider.
 |
| `email_delete` | email-manager | email | **danger** | Move message to trash or permanently delete. HITL required. Args: message_id, permanent, provider.
 |
| `email_categorize` | email-manager | email | **danger** | Mark read/unread, star/flag, add/remove labels, move folder. HITL required. Args: message_id, mark_read, star, add_labels, remove_labels, move_to_folder, provider.
 |
| `email_analyze` | email-manager | email | safe/read | Summarize email, extract action items/deadlines, sentiment, phishing risk. Args: message_id or raw_text, focus (full\|security\|actions), provider.
 |
| `install_python_packages` | environment-bootstrapper | system | **danger** | Install Python packages safely inside the runtime virtual environment using uv or pip. |
| `install_npm_packages` | environment-bootstrapper | system | **danger** | Install Node/npm packages inside the active workspace. |
| `check_environment` | environment-bootstrapper | system | safe/read | Diagnose system binaries, active Python interpreter, PATH variables, and compile resources. |
| `git_status` | git-github-manager | git | safe/read | Get the current git repository status, branch, and staged/unstaged changes. |
| `git_commit` | git-github-manager | git | **danger** | Commit modified or untracked files with a detailed commit message. |
| `git_push_pull` | git-github-manager | git | **danger** | Synchronize local branch changes by executing git pull or git push. |
| `github_create_pr` | git-github-manager | git | **danger** | Create a new Pull Request on the GitHub repository using GitHub APIs. |
| `github_list_issues` | git-github-manager | git | safe/read | Retrieve and view list of issues currently open on the remote repository. |
| `vault_store` | secret-vault | security | safe/read | Store an API key, token, password, or other secret in the encrypted vault. The secret is encrypted with AES-256-GCM and can be retrieved later by name. Use this when the user shares a credential that  |
| `vault_retrieve` | secret-vault | security | **danger** | Retrieve a stored secret from the vault by name. The value is decrypted and returned. This action requires human approval (HITL) before the secret is released. Use when the user asks for a key/secret, |
| `vault_list` | secret-vault | security | safe/read | List all stored secret names and their categories. Secret values are NOT shown — only names. Use this to discover what credentials are available before retrieving one. |
| `vault_delete` | secret-vault | security | **danger** | Permanently delete a stored secret from the vault by name. This action requires human approval (HITL). Use when the user asks to remove a credential. |
| `get_system_stats` | system-health-monitor | diagnostics | safe/read | Fetches CPU, RAM, and Disk space utilization metrics of the host system. |
| `list_active_processes` | system-health-monitor | diagnostics | safe/read | Lists active subprocesses spawned under the parent Kazma process. |
| `read_system_logs` | system-health-monitor | diagnostics | safe/read | Safely streams recent lines of the Kazma gateway and server logs, with filters to mask API tokens and secrets. |
| `schedule_task` | task-scheduler-cron | automation | **danger** | Schedule a task to run autonomously at a future time. Timing: '5m', '1h', 'daily at 9am'. |
| `list_scheduled` | task-scheduler-cron | automation | safe/read | List all scheduled background tasks and their current status. |
| `cancel_scheduled` | task-scheduler-cron | automation | **danger** | Cancel a scheduled background task using its job ID. |
| `analyze_local_image` | visual-interpreter-generator | media | safe/read | Analyze a local screenshot, diagram, or chart and answer visual/structural questions. |
| `generate_ui_mockup` | visual-interpreter-generator | media | safe/read | Generate a beautiful wireframe UI design or illustration based on text description prompts. |

## Manifest-only coding skills

Some native folders ship manifests without a tools map (prompt/workflow skills): `code-review`, `fix-lint`, `refactor-file`, `write-tests`. They appear in the hub/skills UI but do not register discrete tool functions like the rows above.

## MCP tools

MCP servers configured under `mcp.servers` in `kazma.yaml` contribute tools at runtime. Classification:

- Name patterns containing write/exec/delete → danger
- read/list/get → often safe
- Unknown → danger (fail-closed)
- Production may force HITL for non-allowlisted MCP tools

See [Skills, MCP & Tools](../guide/skills-mcp-and-tools).

## Canonical danger list (HITL)

From `kazma_core/safety/hitl.py` → `CANONICAL_DANGER_TOOLS` (also mirrored in this script):

- `browser_eval_js`
- `cancel_scheduled`
- `code_exec`
- `config_save`
- `email_categorize`
- `email_delete`
- `email_send`
- `file_delete`
- `file_write`
- `git_commit`
- `git_push_pull`
- `github_create_pr`
- `install_agent_skill`
- `install_npm_packages`
- `install_python_packages`
- `python_exec`
- `run_tests`
- `schedule_task`
- `shell_exec`
- `spawn_agent`
- `spawn_agents`
- `uninstall_agent_skill`
- `vault_delete`
- `vault_retrieve`

