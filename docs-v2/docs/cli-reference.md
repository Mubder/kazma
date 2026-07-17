# CLI Reference

> The complete `kazma`, `kazma-tui`, and `kazma-web` command surface, every flag, and working examples — all verified against `kazma-cli/` source.

---

## 1. Entry points

Defined in `pyproject.toml:73-76`:

| Command | Module | What it is |
|---|---|---|
| `kazma` | `kazma_cli.main:main` | The primary CLI (hub, gateway, swarm, project, docs, completion, update). |
| `kazma-tui` | `kazma_tui.app:main` | The Textual TUI dashboard. |
| `kazma-web` | `kazma_ui.app:main` | The FastAPI Web UI server (alias of `kazma serve`). |

> **Framework note:** The top-level `kazma` command is **hand-rolled argv parsing** (an `if/elif` chain over `sys.argv[1:]` in `main.py:39-94`) — not Click. Only the `kazma hub` subtree uses Click (`kazma_core/hub/cli.py:104`). Global early option `--no-banner` is stripped before dispatch (`main.py:28-32`).

---

## 2. `kazma` — top-level tree

```
kazma
├── (no args)              # banner + status + help hint
├── status                 # probe running server, print versions
├── serve [port]           # launch Web UI (uvicorn)
├── wizard                 # interactive skill-install wizard
├── hub ...                # skill hub (Click group)
├── docs <build|serve>     # build/serve the Docusaurus docs site
├── completion ...         # shell tab-completion
├── project ...            # .kazma/ project config
├── gateway ...            # gateway control via REST
├── swarm ...              # swarm orchestration via REST
├── update                 # check/install CLI updates
└── --help | -h | help     # print command list
```

### 2.1 `kazma` (no args)

Prints the startup banner, runs config checks, prints status, and a help hint (`main.py:35-37`). Honors `--no-banner`.

### 2.2 `kazma status`

Probes `/api/gateway/status` and `/api/swarm/status` on the running server, prints Python/Kazma versions, config path, and key package versions (`main.py:352-439`).

```bash
kazma status
```

### 2.3 `kazma serve [port]`

Launches the Web UI via `uvicorn kazma_ui.app:create_app --factory`. Default port `8000`. Binds `127.0.0.1` normally; switches to `0.0.0.0` **only** when `KAZMA_SECRET` is set (`main.py:97-114, 110`).

```bash
kazma serve            # 127.0.0.1:8000
kazma serve 9090       # 127.0.0.1:9090
KAZMA_SECRET=xxx kazma serve   # 0.0.0.0:8000 (approval endpoints protected)
```

> **Security:** Never run `kazma serve` on `0.0.0.0` without `KAZMA_SECRET`. The HITL approval endpoint would otherwise be unauthenticated.

### 2.4 `kazma wizard`

Runs the interactive skill-installation wizard (`kazma_core.cli.wizard.SkillInstallationWizard`, `main.py:117-123`).

### 2.5 `kazma docs`

Builds or serves the Docusaurus docs site under `docs/` (`main.py:133-202`).

```bash
kazma docs build        # npm install && npm run build
kazma docs serve        # npm run start (default port 3000)
kazma docs serve 4000   # custom port
```

---

## 3. `kazma completion`

Shell tab-completion generation and install (`main.py:224-286`, `completions.py`).

| Subcommand | Description |
|---|---|
| `kazma completion bash` | Print bash completion script. |
| `kazma completion zsh` | Print zsh completion script. |
| `kazma completion powershell` (aliases `pwsh`, `ps`) | Print PowerShell `Register-ArgumentCompleter` script. |
| `kazma completion install [shell]` | Auto-detect shell (PowerShell on win32; zsh/bash on POSIX) and install. |
| `--list-models` | Print available model names. |
| `--list-providers` | Print provider names. |

Global flags (for completion data): `--model`, `--provider`, `--yolo`, `--verbose`, `--no-banner`, `--help`, `-h`.

---

## 4. `kazma project`

`.kazma/` per-project config (`main.py:289-328`, `project.py`).

| Subcommand | Args | Description |
|---|---|---|
| `kazma project init [path]` | default `.` | Create `.kazma/` with `rules.yaml`, `context.md`, `personality.yaml`, `tools.yaml`, `history/`. Idempotent. |
| `kazma project show [path]` | default `.` | Display parsed project config. |
| `kazma project validate [path]` | default `.` | Verify required files exist, YAML parses, `rules.yaml` has `language` and `git_branch`. |

---

## 5. `kazma gateway`

Omnichannel gateway control via REST. All subcommands accept `--port N` and talk to `http://localhost:{port}` (`gateway.py`).

### 5.0 The `--port` flag (swarm / gateway)

Swarm and gateway commands target the server at `http://localhost:8000` by default. The port resolves with this precedence:

| Source | Priority |
|---|---|
| `--port N` (per command) | highest |
| `KAZMA_PORT` env var | middle |
| `8000` (default) | lowest |

If the server is unreachable, the CLI prints `Server not running. Start with: kazma serve` and exits `1` (see [exit codes](#exit-codes)).

| Subcommand | Endpoint | Description |
|---|---|---|
| `kazma gateway status` | `GET /api/gateway/status` | Adapter table. |
| `kazma gateway start` | `POST /api/gateway/start` | Start gateway. |
| `kazma gateway stop` | `POST /api/gateway/stop` | Stop gateway. |
| `kazma gateway restart` | stop + 0.5 s + start | Restart gateway. |
| `kazma gateway refresh` | `POST /api/gateway/refresh-adapters` | Re-scan adapters. |

```bash
kazma gateway status
kazma gateway restart --port 9090
```

---

## 6. `kazma swarm`

Swarm orchestration via REST (`swarm.py`). Value flags: `--model`, `--provider`, `--type`, `--role`, `--context`, `--workers`, `--aggregation`, `--page`, `--page-size`, `--status`, `--port`, `--worker`. Bool: `--reset`.

### 6.1 Status & workers

| Subcommand | Description |
|---|---|
| `kazma swarm status` | `GET /api/swarm/status`. |
| `kazma swarm workers` | List workers. |
| `kazma swarm worker add <name> [--model --provider --type --role]` | `POST /api/swarm/workers`. |
| `kazma swarm worker spawn <name> <role> [--model --provider --type]` | `POST /api/swarm/workers/spawn`. |
| `kazma swarm worker remove <name>` | `DELETE /api/swarm/workers/{name}`. |

### 6.2 Dispatch patterns

| Subcommand | `type` | Description |
|---|---|---|
| `kazma swarm dispatch <worker> <prompt> [--context C]` | `dispatch` | Single worker. |
| `kazma swarm broadcast <prompt> [--context C]` | `broadcast` | All workers. |
| `kazma swarm consult <prompt> --workers a,b [--context C]` | `consult` | Parallel opinions + synthesis. |
| `kazma swarm pipeline --workers a,b,c <prompt>` | `pipeline` | Ordered stages + shared blackboard. |
| `kazma swarm fanout --workers a,b <prompt> [--aggregation strategy]` | `fan_out` | Parallel + aggregate. |

```bash
# Dispatch one task to the "researcher" worker
kazma swarm dispatch researcher "Summarize today's oil prices"

# Fan out to three workers and vote on the result
kazma swarm fanout --workers a,b,c --aggregation vote "Best API design?"

# Run the standard pipeline
kazma swarm pipeline --workers researcher,refiner,builder,validator "Build a CLI tool"
```

### 6.3 Tasks, metrics, reliability

| Subcommand | Description |
|---|---|
| `kazma swarm history [--type --status --page --page-size]` | `GET /api/swarm/tasks` (paginated). |
| `kazma swarm task <id>` | `GET /api/swarm/tasks/{id}`. |
| `kazma swarm metrics [--worker W]` | `GET /api/swarm/workers/{W}/metrics` (or `/metrics/all`). |
| `kazma swarm start` / `kazma swarm stop` | `POST /api/swarm/start` / `/stop`. |
| `kazma swarm approve <task_id>` | `POST /api/swarm/tasks/{id}/approve` (HITL checkpoint). |
| `kazma swarm reject <task_id>` | `POST /api/swarm/tasks/{id}/reject`. |
| `kazma swarm circuit-breaker [worker] [--reset]` | `GET /api/swarm/circuit-breakers`, per-worker, or `POST .../reset`. |

---

## 7. `kazma update`

Self-update (`update.py`). Flags: `--check`/`-c` (dry run), `--force`/`-f`, `--yes`/`-y`, `--help`/`-h`. Auto-detects install type via `pip show` ("Editable project location"):

- **pip install** → queries PyPI (`https://pypi.org/pypi/kazma/json`) and runs `pip install --upgrade kazma`.
- **git install** → `git fetch`, checks `git log HEAD..origin/main`, then `git pull origin main` + `pip install -e .`.

```bash
kazma update --check     # dry-run: show available update
kazma update --yes       # apply without prompting
```

---

## 8. `kazma hub` — skill hub (Click group)

The only Click-based subtree (`kazma_core/hub/cli.py:104`). Group options: `--registry-path` (env `KAZMA_HUB_DB`, default `~/.kazma/hub/registry.db`), `--hub-url` (env `KAZMA_HUB_URL`, default `https://hub.kazma.ai`).

| Subcommand | Description |
|---|---|
| `kazma hub register <path>` | Register a local skill. |
| `kazma hub search [query] [--capabilities --tags --author]` | Search the registry. |
| `kazma hub install <skill_id>` | Install a skill. |
| `kazma hub list` | List installed skills. |
| `kazma hub info <skill_id>` | Show skill details. |
| `kazma hub validate <path> [--json]` | Validate a skill manifest. |
| `kazma hub uninstall <skill_id>` | Remove a skill. |
| `kazma hub submit <path> [--json --source-url]` | POST to `{hub_url}/api/v1/skills/submit`. |
| `kazma hub status <submission_id> [--json]` | Submission status. |
| `kazma hub badge <skill_ref> [--json]` | Show certification badge. |
| `kazma hub certified [--json]` | List certified skills. |
| `kazma hub stats [--json]` | Registry stats. |
| `kazma hub check-certification <path> [--json]` | Check a skill against certification criteria. |
| `kazma hub sign <path> [--secret]` | **HMAC-SHA256 sign** the entry-point `.py` into the manifest. Requires `KAZMA_SECRET`. See [Skills, MCP & Tools](skills-mcp-and-tools.md#cryptographic-signing). |

```bash
# Sign a skill (writes checksum + signature into skill_manifest.yaml)
KAZMA_SECRET=... kazma hub sign ./my-skill

# Search and install
kazma hub search "oil pricing" --capabilities mcp
kazma hub install oil-pricing-pro@1.2.0
```

---

## 9. `kazma-tui`

Launches the Textual TUI (`kazma_tui.app:main` → `KazmaTUI().run()`). Tabs: Dashboard, Chat, Files, Traces, Swarm, Settings. Initializes `ModelRegistry`/`SwarmEngine` singletons if launched standalone. Includes a HITL approval modal (`widgets/hitl_modal.py`).

```bash
kazma-tui
# or: python -m kazma_tui
```

---

## 10. `kazma-web`

Alias for launching the FastAPI Web UI server directly (`kazma_ui.app:main`). Defaults `127.0.0.1:8000`; warns if binding `0.0.0.0` without `KAZMA_SECRET` (`app.py:900-906`).

```bash
kazma-web
```

---

## 11. Slash commands (in-chat)

These are typed inside a connected chat (Telegram/Discord/Slack/Web). Defined in `kazma-gateway/.../slash_commands.py`.

| Command | Description |
|---|---|
| `/help` | List commands by category. |
| `/reset` | Clear conversation (handled in `graph.py:196`). |
| `/status` | Gateway health overview. |
| `/model` or `/models` | Interactive model selector (Telegram inline keyboard). |
| `/memory` | Memory subsystem stats. |
| `/cost` | Token spend for the session. |
| `/replay list \| <iter> \| compare <a> <b> \| clear` | Time-travel. |
| `/config show \| model <n> \| personality <n> \| memory on\|off \| tools list \| tools toggle <n> \| export` | Config wizard. |
| `/personality` | Delegate to `/config personality`. |
| `/context` | Context-window token usage. |
| `/undo` | *Stub — not implemented.* |
| `/edit` | *Stub — not implemented.* |
| `hitl approve|deny <thread_id>` | HITL resume (note: no `/` prefix on Slack, which blocks slash commands). |
| `/swarm` | Swarm orchestration (Telegram-registered; interactive handler). |

> **Parity note:** `/help` text omits `/hitl` and `/swarm` even though both are functional. Telegram's `setMyCommands` registers `/swarm` but not `/hitl`. Discord reserves `/`-prefixed commands for itself, so Kazma receives them as plain text. See [Gateways & Platforms](gateways-and-platforms.md).

---

## 12. Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | General failure / server unreachable (swarm & gateway commands print `Server not running. Start with: kazma serve` and exit `1` when the WebUI server is not running — start it first with `kazma serve`). |
| `2` | Argument/usage error (Click, `kazma hub` subtree only). |

> Swarm and gateway commands are REST clients: they exit non-zero whenever the WebUI server is not running. Always start the server (`kazma serve`) before invoking them.

---

## Documentation Audit Notes

- **`--help` text prints "Kazma CLI v0.2.0"** (`main.py:75`) while `pyproject.toml` is `0.3.0`. Known drift.
- **`kazma hub` is the only Click subtree.** All other commands are hand-rolled argv parsing — flag ordering and `--help` behavior may be less uniform than a pure-Click CLI.
- **`/undo` and `/edit` are stubs** (`slash_commands.py:257, 267`) — present in the menu but explicitly "not yet implemented."
