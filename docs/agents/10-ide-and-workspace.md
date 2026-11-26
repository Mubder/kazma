<!-- AGENTS.md, piece 10-ide-and-workspace. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 10. IDE Subsystem (`kazma-core/kazma_core/ide/`)

The IDE is a transport-agnostic coding backend (Web, TUI, all chat platforms).
It is the **single source of truth** for file/exec/git/swarm operations on a
workspace. Three new modules; understanding their interaction is essential.

**A. Workspace root resolution — ONE ladder (binding SoT)**
- Public API: `kazma_core.workspace.binding.resolve_active_root()` (also
  `file_write._get_workspace` / `configure_workspace` for compat).
- Used by: all `file_*` tools, IdeService, workspace UI API, env_context.
- **Resolution precedence:** per-task `workspace_scope` ContextVar →
  **active WorkspaceStore row** → `configure_workspace()` pin →
  `KAZMA_WORKSPACE` env → `{data_dir}/workspace` default sandbox. A fresh
  store seeds its first active row from `KAZMA_WORKSPACE`, else the working
  folder when it is a project, else the default sandbox: the row then
  outranks the variable, so a guess seeded over it kept the guess (2026-11,
  §56).
- **Binding bus:** `WorkspaceStore.set_active_workspace` →
  `notify_root_changed(root)` → pin tools + MCP rebind for
  `workspace_bound` servers (`${KAZMA_ACTIVE_WORKSPACE}` in command).
- MCP filesystem must NOT stay on a static `kazma-data/workspace` fossil
  after Switch Repo / clone. **Nor in storage** (2026-10-02): every read and
  write of the MCP store passes `mcp_servers_store._canonical_server` (a
  workspace-bound server is marked `workspace_bound: true`, and an old
  sandbox path in its folder argument goes back to the placeholder;
  `mcp_rebind.is_legacy_sandbox_arg` knows each spelling, the bare
  `kazma-data/workspace` included). The live settings held a pre-placeholder
  copy that won over kazma.yaml's by name, the MCP page showed it, and the
  next MCP edit wrote it over kazma.yaml. The page shows the command as it
  runs (`KazmaAgent.get_mcp_servers`). `tests/test_mcp_servers_store.py`
  (identity canonicalizer as the negative control). Storage holds
  configuration only: `_canonical_server` drops the runtime fields
  (`_RUNTIME_FIELDS`: status, connected, tool counts...), which `upsert`
  wrote into kazma.yaml for every server.
- **Every MCP server Kazma ships runs an exact version** (2026-11, supply
  chain). kazma.yaml ran `@modelcontextprotocol/server-filesystem` through
  `npx -y` with no version: each start ran whatever npm served. kazma.yaml,
  the catalog and `mcp_servers_store.SHIPPED_PACKAGE_VERSIONS` name one
  version (held equal, the catalog held to the registry record). The first
  boot copied kazma.yaml's list into the settings store, which wins by name,
  so installs follow two ways: a store still holding that copy takes today's
  at boot (`config_defaults` retires `mcp.servers`), and any stored command
  naming a shipped package bare is pinned by `_canonical_server`
  (`_pinned_command`), which every read and write passes; a version the
  owner wrote stays. Gate: `tests/test_mcp_shipped_servers_pinned.py` (every
  npx/uvx command in a shipped config pinned; the old kazma.yaml line, the
  canonicalizer without the pins and the boot without the declaration as
  negative controls).
- **The running server never writes kazma.yaml's MCP servers**
  (2026-10-02): it is a tracked file, and every page's MCP change left the
  live checkout modified, so a `git pull` touching kazma.yaml refused to
  merge (the swarm templates' lesson, §14). Writes go to the settings store
  (`mcp.servers`) and config.raw; a kazma.yaml server removed from a page is
  named in `mcp.removed_servers` (written in the same `batch_set`) and left
  out of the merge, and adding it again clears it. The one write left is
  `move_plaintext_secrets` swapping typed secrets for pointers
  (`_write_everywhere(rewrite_yaml=...)`, the file's own list kept). Gate:
  `test_only_the_secrets_mover_rewrites_kazma_yaml`
  (`tests/test_mcp_servers_store.py`).
- **The manager's connect pins, and keeps failures per server**
  (`AsyncMCPManager.connect_from_config`, 2026-10-02). A workspace-bound
  config no caller pinned (no `_resolved_workspace`) starts on the active
  workspace, read off the loop; the reconnect sweeper and Settings' Test
  passed the stored config and started the filesystem server on the literal
  placeholder. A call changes `connection_errors` only for its own servers:
  boot connects one server per call, each call cleared them all, and the
  sweeper -- which retries what is named there -- never retried an early
  failure. A stop drops the server's entry; the text is `redact_secrets`'d.
  One Start/Stop: `KazmaAgent.start_mcp_server` / `stop_mcp_server`, for
  the MCP page, Settings' switch (saved AND applied now) and `/config tools
  toggle` (run on the server loop from the resolver's thread). One "is it
  on": `mcp_servers_store.server_enabled`. `tests/test_mcp_servers_live.py`.
- **Per-task scope guard (deep-audit 2026-08-19):** MCP rebind is
  PROCESS-GLOBAL — a per-task `workspace_scope` does NOT rebind servers.
  `mcp/manager.py:execute_mcp_tool` fail-closes with an actionable error
  when a per-task scope targets a different root than the bound MCP root
  (kill-switch `KAZMA_MCP_SCOPE_GUARD=0`). Per-workspace MCP instances
  remain future work.
- Path-traversal protection: `IdeService.resolve()` does a string-level
  `normpath` `..` check + containment backstop (symlink/junction-aware).
- **Containment is not the store rule: every IDE read asks
  `is_kazma_store` too, inside the workspace** (2026-11-26, the review of
  that day's code-scanning triage). `resolve` asked the path policy only for
  a path outside the workspace, and the LSP request read a path inside it
  whole: on live the workspace is the install folder, and code completion
  offered the words of `.env`. Code scanning had reported that read (#53);
  it was dismissed because `resolve` "contains" it. `resolve` now refuses
  Kazma's stores and secret files wherever they are; the IDE MCP server's
  `read_file` / `search_code` and the TUI's file preview ask the same
  predicate. Gate: `tests/test_ide_reads_refuse_kazma_files.py` (a census of
  every file read in `kazma_core/ide/` and every module that drives
  `get_ide_service()`, each declared with a probe that plants Kazma's
  secret files in the workspace; every IdeService method that takes a path
  refuses one before a tool runs; the control blinds the predicate and
  every probe finds the secret).
- **A relative path an agent tool is given means the active workspace**
  (`binding.resolve_tool_path`), the directory `shell_exec` runs in — never
  the server process's CWD. Every file tool, `check_path_access` and the
  skill tools resolved against the CWD until 2026-09-26; it only looked
  right because the live workspace IS the install folder. After a Switch
  Repo, `file_read("README.md")` read the install's README. Gate:
  `tests/test_tool_paths.py` (every tool that builds a path from its own
  parameter, behavioural runs from a CWD holding a same-named decoy).
  `file_delete` refuses the workspace root and its ancestors. The SQL tools
  (`sqlite_query`, `execute_db_query`, `inspect_db_schema`) joined them on
  2026-10-02 (`database_client._checked_sqlite_file`): their path parameter
  is `db_uri`, which the gate's name pattern did not match, and they opened
  it with `sqlite3.connect`, which was not on its list of openers; both are
  now.
- **File tools keep a file's own line endings**
  (`tools/text_newlines.py`): read exact, write `newline=""` in the file's
  existing style; a new file takes the platform default. Text mode turned
  every patched LF file into CRLF on Windows, and the checkpoint rollback
  wrote CRLF files back as `\r\r\n`. Tests: `tests/test_text_newlines.py`
  (bytes, both styles).
- **A checkpoint restore writes only a file the checkpoint holds**
  (`FileCheckpointStore._held_file`, 2026-10-01): the whole-file and the
  per-hunk reject (`restore_one`, `restore_hunk`) both take the file through
  the checkpoint's own list and `check_path_access(..., "write")`, and keep
  its line endings. `POST /api/ide/checkpoints/{id}/restore-hunk` wrote any
  path the request named, itself, in text mode. The review diffs exact text
  (`hunks.file_diff`, the one diff for review and restore, so a hunk index
  means the same hunk in both) and lists a file created after the checkpoint.
  `tests/test_ide_checkpoint_hunks.py`.

**B. HITL routing — no parallel write/exec path**
- All mutating/exec IDE operations (`write_file`, `apply_patch`, `delete_file`,
  `run`, `run_file`, `git`) delegate to `LocalToolRegistry.execute()` via
  `IdeService._call_tool()`. The HITL gate lives in `tool_registry.py:execute()`
  (§7B). Never call the underlying tool functions directly from the IDE layer.
  Prefer `file_apply_patch` for edits to existing files (not whole-file write).

**C. Awareness injection — `ide/env_context.py`**
- `build_env_context()` resolves workspace root, repo slug (from WorkspaceStore
  cache or `git remote`), branch, GitHub auth, and available tools into a
  markdown block.
- It is the turn's `environment` note, current, ONE copy a call: the web
  transport adds it (`sse_chat/` package, voice), and the supervisor builds it
  at iteration 0 for a turn that arrives without one -- the chat apps, a
  scheduled task, a child graph (`graph_supervisor`,
  `graph_helpers._ensure_environment_note`). Workspace switches take effect at
  the next turn (a turn note since 2026-09-29, §43). It is never part of the
  base prompt: `agent_runner.py` baked the block it saw at server start into
  the base prompt until 2026-11-15 -- a second copy on the web, and the chat
  apps' only copy, stale after Switch Repo. Every dispatched worker prompt
  gets its own (`worker.py`). The IDE chat's open file is its own turn note
  (`source="ide_context"`), never glued to the question.
- `IdeService.send_to_swarm()` attaches the env block to the task `context` —
  never drop this or workers lose workspace awareness. It awaits the
  threaded `build_env_context` (2026-10-02): it ran the blocking builder (two
  git probes of up to 4 s, a store read) on the event loop "on purpose" from
  2026-08-27. `_build_env_context_sync` is a loop-stall helper now, so no
  async code calls it again (`tests/test_ide_send_to_swarm.py`).

**D. Per-task workspace targeting — `ide/workspace_scope.py`**
- `workspace_scope(workspace_id)` is an async context manager backed by a
  `ContextVar`. `worker_dispatch.py` wraps `worker.dispatch()` in it when a
  `SwarmTask` carries `workspace_id`.
- `_get_workspace()` consults the scope FIRST, so concurrent tasks can target
  different repos. `SwarmTask.workspace_id` (None = global active workspace)
  propagates through `SwarmDispatchContext.metadata`.
- `ContextVar` propagates across `await` points within one asyncio task;
  `asyncio.create_task` copies the context (var travels with it).

- **Approval resumes keep the turn's workspace** (2026-10-07):
  `agent.turn_workspace.turn_workspace` captures the resolved root at turn
  entry into the declared `SupervisorState.workspace_root`. Both UI entry
  points (`turn_runtime.invoke_turn` / `astream_events`) and core `_ainvoke`
  use it. A `Command` reads that checkpointed root and restores a path scope
  for execution; the approval request's context and global active row cannot
  redirect a paused turn. A new turn captures the current scope afresh;
  `/compact` maintenance keeps the checkpoint's root instead.
  Legacy checkpoints without the root, invalid roots and missing directories
  fail closed with a public instruction to start a fresh turn. Never infer a
  legacy root from a pending tool's arguments. Gate:
  `tests/test_turn_workspace_resume.py` (reopened SQLite saver, both entries,
  concurrent scopes, exactly one effect, old unscoped resume as negative control).

**E. Repo identity — `WorkspaceStore` persistence**
- `stores/workspaces.py` has repo-identity columns (`repo_url`, `owner`,
  `repo`, `default_branch`, `is_github`) added via idempotent `ALTER TABLE`.
- `repo_for(root_path)` returns cached identity (avoids `git remote` per turn);
  `set_repo_identity()` persists it. `env_context` prefers the cache.
- Native GitHub tools (`git_github_manager/tools.py`) use the shared
  `GitHubClient` (OAuth→PAT→env token) via lazy import, with env-var fallback
  for headless mode. Don't revert to `os.getenv("GITHUB_TOKEN")`-only.
- **An empty list from GitHub is not "none".** GitHub filters an endpoint
  to what the token may read (a fine-grained token without "Issues" gets
  `[]`, not a 403), so `github_list_issues` checks an empty answer against
  the repository's `open_issues_count` and says the token may lack the
  permission; on the live install the agent reported "no open issues" over
  #20 (2026-09-28). Pull requests are dropped BEFORE the list is cut.
  `tests/test_github_list_issues.py`.

**E1b. Git history has read-only tools** (2026-10-19): `git_log` (count up
to 100, path, oneline) and `git_diff` (uncommitted, a commit or a range,
path, stat) beside `git_status`, read tier, git run with the tool child
environment and `core.fsmonitor` off, output fenced (commit messages are
whoever committed them). With host shell off and the Python sandbox refusing
subprocess, "git log -3 --oneline" had no way to run on live (Telegram).
`tests/test_git_read_tools.py`.

**E2. A project's files are what its repository keeps**
(`workspace/project_files.git_project_files`, 2026-10-02): tracked files
plus untracked ones `.gitignore` does not exclude, for the agent's
`file_search`, the code index and the Workspace page's recent files. Git
runs with `tool_child_env()` and `core.fsmonitor` off. `None` = walk: no
work tree, git missing, or the folder itself ignored and named as the path
(the default sandbox inside the install's checkout). The search and the
index walked every folder off a skip list: on the live install (workspace =
install folder) a 4.7 GB clone, ignored by `.gitignore`, took one search
13.9 s and could take the code index's whole 4,000-file budget. The three
walks also share ONE skip list (`GENERATED_DIRS`; they kept three), and the
repository ignores `AppData/`. `tests/test_project_files.py` (the old walk
as the negative control; the lists compared by identity). The code index and
the security report (§49) read `project_files.iter_project_files` (git's list,
else a walk; never a generated or dot folder); the search keeps its own walk
because it searches `.github`.

**F. Transports**
- Web: `/ide` page + `/api/ide/*` router (`ide_api.py`); file-aware AI chat
  reuses `/api/chat/stream` (no parallel path).
- TUI: `editor.py` `EditorScreen` (pushed from `files.py`).
- Chat: `/ide` slash commands in `commands.py:_try_ide_command`, wired in
  `graph.py` after the swarm intercept.

