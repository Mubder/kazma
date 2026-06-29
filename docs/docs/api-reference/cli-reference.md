---
sidebar_position: 4
---

# CLI Reference

The `kazma` command-line interface is the unified control plane for Kazma.
It exposes gateway lifecycle, swarm orchestration, the skill marketplace, and
project tooling from a single entry point. Kazma also ships two companion entry
points: `kazma-web` (Web UI) and `kazma-tui` (terminal UI).

```bash
kazma [--no-banner] <command> [subcommand] [options]
```

Run `kazma` with no arguments for a banner, config check, and status overview,
or `kazma help` for a quick command list.

## Exit codes

| Code | Meaning |
|:---:|:---|
| `0` | Success |
| `1` | General failure / invalid input / validation error |
| `2` | Unknown command |

---

## Entry points

| Entry point | Command | Description |
|:---|:---|:---|
| CLI | `kazma` | Banner, status, hub, gateway, and swarm management |
| Web UI | `kazma-web [--port 8000]` | FastAPI dashboard (equivalent to `kazma serve`) |
| Terminal UI | `kazma-tui` | Textual TUI with English-only metrics/chat dashboard |

---

## Core commands

### `kazma` (no arguments)

Print the startup banner, run config checks, and show a status overview with a
quick help hint.

```bash
kazma
```

**Options**

| Flag | Description |
|:---|:---|
| `--no-banner` | Suppress the startup banner |

### `kazma status`

Show real system status, including gateway adapter health, swarm worker health,
and server status.

```bash
kazma status
```

### `kazma serve [port]`

Start the WebUI server. This is the CLI equivalent of `kazma-web`.

```bash
kazma serve            # default port 8000
kazma serve 8080       # custom port
```

**Arguments**

| Argument | Default | Description |
|:---|:---|:---|
| `port` | `8000` | Port to bind the server on |

**Exit codes**

- `0` — server shut down cleanly (Ctrl+C)
- `1` — WebUI dependencies not installed

### `kazma wizard`

Launch the interactive skill installation wizard.

```bash
kazma wizard
```

**Exit codes**

- `0` — wizard completed successfully
- `1` — wizard failed or was cancelled

### `kazma completion <bash|zsh|powershell|install>`

Generate or install shell tab-completion for the `kazma` CLI.

```bash
kazma completion bash           # print bash completion script
kazma completion zsh            # print zsh completion script
kazma completion powershell     # print PowerShell completion script
kazma completion install        # auto-detect shell and install
kazma completion install bash   # install for a specific shell
kazma completion --list-models  # print available model names (used by completion)
```

**Subcommands**

| Subcommand | Description |
|:---|:---|
| `bash` | Print the bash completion script |
| `zsh` | Print the zsh completion script |
| `powershell` | Print the PowerShell completion script (aliases: `pwsh`, `ps`) |
| `install [shell]` | Auto-detect the shell and install completion, or install for a specific shell |
| `--list-models` | Print available model names (consumed by `--model` completion) |

### `kazma update [--check] [--force] [--yes]`

Check if a new version of Kazma is available and install it. For pip-installed
copies, checks PyPI for the latest version and runs `pip install --upgrade
kazma`. For git/editable installs, fetches from origin and pulls if behind,
then reinstalls. Shows the current vs latest version, a changelog summary, and
asks for confirmation before updating.

```bash
kazma update              # Check and prompt to update
kazma update --check      # Just check, don't install
kazma update --force      # Force reinstall even if latest
kazma update --yes        # Update without asking
```

**Options**

| Flag | Description |
|:---|:---|
| `--check`, `-c` | Only check for updates, don't install (dry run) |
| `--force`, `-f` | Force update even if already at the latest version |
| `--yes`, `-y` | Skip the confirmation prompt |

### `kazma help` / `--help` / `-h`

Show the quick help text with the list of available commands.

```bash
kazma help
kazma --help
kazma -h
```

---

## Gateway commands

The `kazma gateway` group manages the headless gateway and its platform adapters
(Telegram, Discord, Slack).

### `kazma gateway status`

Show the current status of all gateway adapters, including whether each platform
is connected, its rate-limit state, and message counters.

```bash
kazma gateway status
```

### `kazma gateway start`

Start the gateway and all enabled adapters.

```bash
kazma gateway start
```

### `kazma gateway stop`

Stop the gateway and gracefully shut down all adapters.

```bash
kazma gateway stop
```

### `kazma gateway restart`

Restart the gateway (equivalent to `stop` followed by `start`).

```bash
kazma gateway restart
```

### `kazma gateway refresh`

Refresh/reload gateway adapters, picking up configuration changes without a full
restart.

```bash
kazma gateway refresh
```

---

## Swarm commands

The `kazma swarm` group exposes the full swarm engine from the terminal:
worker management, orchestration patterns, task history, metrics, lifecycle
control, HITL approvals, and circuit breakers.

### `kazma swarm status`

Show swarm health and a summary of registered workers.

```bash
kazma swarm status
```

### `kazma swarm workers`

List all registered workers with their name, model, provider, type, role, and
health status.

```bash
kazma swarm workers
```

### `kazma swarm worker add <name>`

Register a new worker.

```bash
kazma swarm worker add researcher \
  --model gpt-4o-mini \
  --provider openai \
  --type in-process \
  --role researcher
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `name` | Yes | Unique worker name |

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--model M` | provider default | Model name to use |
| `--provider P` | provider default | LLM provider (e.g. `openai`, `deepseek`, `anthropic`) |
| `--type T` | `in-process` | Worker type (e.g. `in-process`, `telegram`) |
| `--role R` | `leaf` | Worker role (e.g. `orchestrator`, `leaf`, `researcher`, `reviewer`) |

### `kazma swarm worker spawn <name> <role>`

Spawn a dynamic worker at runtime. The spawned worker is immediately available in
the registry and dispatchable by all orchestration patterns.

```bash
kazma swarm worker spawn reviewer reviewer \
  --model claude-sonnet-4 \
  --provider anthropic \
  --type in-process
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `name` | Yes | Unique worker name |
| `role` | Yes | Worker role |

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--model M` | provider default | Model name to use |
| `--provider P` | provider default | LLM provider |
| `--type T` | `in-process` | Worker type |

### `kazma swarm worker remove <name>`

Remove a worker from the registry.

```bash
kazma swarm worker remove reviewer
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `name` | Yes | Name of the worker to remove |

### `kazma swarm dispatch <worker> <prompt>`

Dispatch a single task to one worker and print the result.

```bash
kazma swarm dispatch researcher "Summarize today's news in 3 bullets"
kazma swarm dispatch researcher "Review this file" --context "path=src/app.py"
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `worker` | Yes | Target worker name |
| `prompt` | Yes | Task prompt |

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--context C` | — | Extra context passed to the worker |

### `kazma swarm broadcast <prompt>`

Broadcast the same prompt to all registered workers and collect their results.

```bash
kazma swarm broadcast "What is your current load?"
kazma swarm broadcast "Draft an outline" --context "topic=swarm-orchestration"
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `prompt` | Yes | Task prompt |

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--context C` | — | Extra context passed to every worker |

### `kazma swarm consult <prompt> --workers a,b`

Send the prompt to each selected worker independently with a role-aware system
prompt, then run an orchestrator synthesis step that produces a consolidated
answer referencing every successful worker. Workers never see each other's
opinions. Output includes both `individual_opinions` and `synthesized_output`.

```bash
kazma swarm consult "Best database for time-series data?" \
  --workers dba,architect \
  --context "scale=100TB"
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `prompt` | Yes | Task prompt |

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--workers a,b` | Yes | Comma-separated list of worker names |
| `--context C` | — | Extra context passed to every worker |

### `kazma swarm pipeline --workers a,b,c <prompt>`

Run a sequential pipeline where each worker receives the accumulated output of
the previous workers. Supports HITL checkpoints that pause the pipeline for
approval.

```bash
kazma swarm pipeline --workers researcher,writer,reviewer "Draft a blog post about LangGraph"
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `prompt` | Yes | Task prompt |

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--workers a,b,c` | Yes | Ordered, comma-separated list of worker names |

### `kazma swarm fanout --workers a,b <prompt>`

Run the same prompt concurrently across the selected workers and aggregate the
results using the chosen strategy.

```bash
kazma swarm fanout --workers a,b,c "Rate this PR 1-10" --aggregation vote
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `prompt` | Yes | Task prompt |

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--workers a,b` | Yes | Comma-separated list of worker names |
| `--aggregation strategy` | `merge_all` | Aggregation strategy: `first_valid`, `merge_all`, `vote`, `synthesize`, `collect` |

### `kazma swarm history [--type T] [--status S] [--page N] [--page-size N]`

Show persisted task history, filterable by orchestration type and status, with
pagination. History survives restarts (SQLite-backed).

```bash
kazma swarm history
kazma swarm history --type pipeline --status completed
kazma swarm history --page 2 --page-size 20
```

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--type T` | all | Filter by orchestration type (`dispatch`, `broadcast`, `pipeline`, `fan_out`, `consult`, `conditional`) |
| `--status S` | all | Filter by task status (`completed`, `failed`, `timeout`, `paused`) |
| `--page N` | `1` | Page number (1-indexed) |
| `--page-size N` | `20` | Number of tasks per page |

### `kazma swarm task <id>`

Show the full detail of a single task, including per-worker results, handoff
chains, and metadata.

```bash
kazma swarm task task_42
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `id` | Yes | Task ID |

### `kazma swarm metrics [--worker W]`

Show per-worker metrics (tokens used, cost, average latency, success rate). With
no `--worker` flag, shows metrics for all workers.

```bash
kazma swarm metrics
kazma swarm metrics --worker researcher
```

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--worker W` | all | Show metrics for a single worker |

### `kazma swarm start`

Start all registered workers.

```bash
kazma swarm start
```

### `kazma swarm stop`

Stop all registered workers.

```bash
kazma swarm stop
```

### `kazma swarm approve <task_id>`

Approve a paused Human-in-the-Loop checkpoint and resume the task (typically a
pipeline paused at a checkpoint step).

```bash
kazma swarm approve task_42
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `task_id` | Yes | ID of the paused task to approve |

### `kazma swarm reject <task_id>`

Reject a paused HITL checkpoint and abort the task with `status=failed`.

```bash
kazma swarm reject task_42
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `task_id` | Yes | ID of the paused task to reject |

### `kazma swarm circuit-breaker [worker] [--reset]`

Show circuit-breaker state for all workers, or for a single worker when a name is
given. Use `--reset` to manually close an open breaker.

```bash
kazma swarm circuit-breaker                 # show all breakers
kazma swarm circuit-breaker researcher       # show one breaker
kazma swarm circuit-breaker researcher --reset   # reset a tripped breaker
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `worker` | No | Worker name to inspect or reset |

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--reset` | `false` | Reset the named worker's circuit breaker to closed |

---

## Hub commands

The `kazma hub` group is the skill marketplace interface for searching,
installing, validating, and publishing skills.

### `kazma hub register <name>`

Register a skill locally.

```bash
kazma hub register my-skill
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `name` | Yes | Skill name or path to register |

### `kazma hub search <query>`

Search the skill marketplace.

```bash
kazma hub search "weather"
kazma hub search "data processing"
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `query` | Yes | Search query string |

### `kazma hub install <name>`

Install a skill from the hub.

```bash
kazma hub install author/weather-skill
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `name` | Yes | Skill ID to install |

### `kazma hub list`

List all installed skills.

```bash
kazma hub list
```

### `kazma hub info <name>`

Show detailed information about a skill.

```bash
kazma hub info weather-skill
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `name` | Yes | Skill name |

### `kazma hub validate <path>`

Validate a skill manifest and directory structure before submission.

```bash
kazma hub validate ./my-skill
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `path` | Yes | Path to the skill directory |

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--verbose` | `false` | Show detailed validation output |

### `kazma hub uninstall <name>`

Uninstall a skill.

```bash
kazma hub uninstall weather-skill
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `name` | Yes | Skill name to uninstall |

### `kazma hub submit <path>`

Submit a skill to the hub for publication.

```bash
kazma hub submit ./my-skill
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `path` | Yes | Path to the skill directory |

### `kazma hub status`

Show the hub connection status.

```bash
kazma hub status
```

### `kazma hub badge <name>`

Show the certification badge for a skill.

```bash
kazma hub badge weather-skill
```

**Arguments**

| Argument | Required | Description |
|:---|:---|:---|
| `name` | Yes | Skill name |

### `kazma hub certified`

List all Kazma-certified skills.

```bash
kazma hub certified
```

### `kazma hub stats`

Show hub statistics (total skills, certified count, install counts).

```bash
kazma hub stats
```

---

## Project commands

The `kazma project` group manages the `.kazma/` project directory, which holds
project-level rules, context, personality, and tool configuration.

### `kazma project init`

Initialize a `.kazma/` project directory with default templates
(`rules.yaml`, `context.md`, `personality.yaml`, `tools.yaml`, `history/`).

```bash
kazma project init          # initialize in the current directory
kazma project init ./myapp  # initialize in a specific directory
```

**Arguments**

| Argument | Default | Description |
|:---|:---|:---|
| `path` | `.` | Directory to initialize |

### `kazma project show`

Show the current project configuration.

```bash
kazma project show
kazma project show ./myapp
```

**Arguments**

| Argument | Default | Description |
|:---|:---|:---|
| `path` | `.` | Project directory |

### `kazma project validate`

Validate the project configuration and report any issues.

```bash
kazma project validate
```

**Arguments**

| Argument | Default | Description |
|:---|:---|:---|
| `path` | `.` | Project directory |

**Exit codes**

- `0` — project config is valid
- `1` — project config has issues (listed in output)

---

## Docs commands

The `kazma docs` group builds and serves the Docusaurus documentation site.

### `kazma docs build`

Install dependencies and build the static documentation site.

```bash
kazma docs build
```

**Exit codes**

- `0` — build succeeded
- `1` — `npm install` or build failed, or `docs/` directory not found

### `kazma docs serve [port]`

Serve the documentation site locally for development.

```bash
kazma docs serve            # default port 3000
kazma docs serve 3001       # custom port
```

**Arguments**

| Argument | Default | Description |
|:---|:---|:---|
| `port` | `3000` | Port to serve on |

**Exit codes**

- `1` — `docs/` directory not found

---

## Other entry points

### `kazma-web [--port 8000]`

Start the WebUI server. This is an alternative to `kazma serve` and launches the
same FastAPI dashboard.

```bash
kazma-web
kazma-web --port 8080
```

**Options**

| Flag | Default | Description |
|:---|:---|:---|
| `--port` | `8000` | Port to bind the server on |

### `kazma-tui`

Start the terminal UI (Textual TUI with English-only metrics/chat dashboard).

```bash
kazma-tui
```

---

## Global options

These flags may be passed before any command:

| Flag | Description |
|:---|:---|
| `--no-banner` | Suppress the startup banner |
| `--help`, `-h` | Show help |
