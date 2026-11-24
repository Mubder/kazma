<!-- AGENTS.md, piece 37-store-registry. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 37. Kazma's own stores: declared once, read back by a tool, refused raw, carried whole (`kazma_core/store_registry.py`)

Live 2026-09-25: asked "list me all remaining posts", the agent spent 67 tool
calls and an operator-approved `python_exec` byte-dumping
`agent_artifacts.db`. It could SAVE drafts (`save_proposal`) but had no tool
to READ them; the SQL tool refused with "pass a workspace SQLite file",
`file_read` returned raw SQLite bytes, and `python_exec`'s refusal list had
never heard of the file. Underneath: posting ONE draft stamped its whole set
`proposal_posted`, so 7 of 11 drafts vanished from X Studio and sat on the
14-day age-out meant for spent sets. And "is this one of Kazma's stores?"
had five hand-kept answers (SQL tool, path-policy names, path-policy
location rule, migration exporter, importer) — the bundle silently left
`agent_artifacts.db`, `x_scheduled.db`, `x_posts.db`, `hitl_gates.db`,
`task_ledgers.db`, `rbac.db`, `audit.db` and `llm_calls.db` behind.

- **`STORES` is the one declaration** of every database Kazma keeps: what it
  holds and how it crosses machines (`bundle` / `settings` / `documents` /
  `rebuilt` / `machine` / `legacy`, with a reason when not `bundle`).
  Runtime-named files are `STORE_FAMILIES` + `DYNAMIC_NAME_SITES`. The
  migration exporter and importer derive their lists from it.
- **`DATA_DIR_ENTRIES` declares everything else in the data dir** (2026-10-01):
  the registry and templates JSON, the asset folders, the install id, logs,
  caches, the migration's own folders, with the same dispositions; anything
  undeclared is the operator's own and travels. Gate 10 in
  `tests/test_store_registry.py` reads every `<data dir> / "name"` product
  code builds (aliases, wrappers, module constants, f-strings) and fails on
  an undeclared one; a round trip proves what arrives and what stays.
- **`TOOL_WRITES`: every tool that can change anything declares where the
  write lands and which tool reads it back** — per WRITER, not per store
  (`agent_artifacts.db` always had a reader: the scratchpad context feed; the
  drafts half had none). A write to a Kazma store must name a read-tier tool
  or a `CONTEXT_FEEDERS` entry; no note excuses it. Adding a write-tier or
  danger tool without an entry fails CI.
- **One predicate, every door, and the refusal names the reader.**
  `is_kazma_store()` (any SQLite file under the data dir except the
  `workspace/` sandbox; `vault.db` / `hitl_gates.db` by name anywhere) is
  what the SQL tools, the file tools (reads AND writes — reads were open
  until this), `python_exec`, `shell_exec`, the commitment exec resolver
  (before the approval card) and the card disclosure all ask;
  `store_refusal()` says what the store holds and which tool reads it. A
  path grant cannot open a store, so the file refusal never offers one.
- **Kazma's secret files and store copies are stores too** (2026-11
  functional audit, FA-001 and siblings). On live the workspace is the
  install folder: `file_read(".env")` and `file_search("restic")` (read
  tier, no card) returned the vault key and restic's password,
  `file_read` printed `memory_state.db.pre_relabel` (a copy of the memory
  database), and an approved `python_exec` read `/workspace/.env` in its
  container. `is_kazma_store` also answers for the install's `.env`
  family, `restic.pass`, the broker token, any `.env` under the data dir
  (`is_kazma_secret_file`), a name that continues a database suffix
  (`.db.bak-...`) and any file under the data dir that starts with the
  SQLite header. Every tool that resolves a path asks it
  (`tests/test_kazma_secret_files_refused.py`, a class gate over the
  tools). The python_exec container shows Kazma's folders (data dir,
  user home) and secret files inside the workspace empty
  (`code_exec._workspace_mounts` through
  `store_registry.kazma_paths_inside`; a list that cannot be worked out
  mounts no workspace), and the text check before the card reads
  `/workspace/...` as the workspace (`path_policy.sandbox_workspace_target`).
  Only `_workspace_mounts` spells a container bind mount, besides the move
  from Docker Desktop re-creating a container's own binds (`engine_move._mount_args`,
  section 63) (`tests/test_sandbox_hides_kazma_files.py`, each with its
  negative control, a real Docker run where the image is present).
- **Drafts carry per-item state.** `used_at` / `used_via` / `used_ref` on the
  item; the row's `kind` is derived (`proposal_posted` only when every item
  is used). Marking requires the posting tool's own `"ok": true`. A startup
  heal re-derives sets the old whole-set rule stamped, from Kazma's X
  records (post ledger, schedule) — and leaves a set with no evidence alone.
  `list_proposals` is the reader; `stored_text_for` answers only a ref that
  names ONE draft (the chat gate's rule, now X Studio's too).
- **`{"ok": false}` is a failed tool call** (`tool_registry._json_reports_failure`),
  not only `Error:` / `⚠️` / `Safety:` prefixes.
- **Gates:** `tests/test_store_registry.py` (every DB name in the code
  declared; every writer declares its readback; store writes have a
  no-approval reader; context feeders wired; every store's migration
  disposition, both directions and a real export→import round trip; every
  door refuses every store and names the reader, the user's sandbox DB passes
  every door; no store path built from the process CWD; no
  `with sqlite3.connect()` — it commits and never closes, which is why
  `kazma migrate import` failed on Windows at its first swap until
  2026-09-25). Behaviour: `tests/test_saved_drafts_readback.py`.
- **Not covered:** a store named only by a variable (a command that builds
  the path at runtime passes the text checks — the tools' own path rules and
  the operator's card remain the control); per-tenant isolation of a store
  inside one SQLite file is the store's own job, not this registry's.

