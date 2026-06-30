---
sidebar_position: 3
---

# Configuration

Kazma uses YAML configuration files and environment variables.

## Configuration file

Default location: `~/.kazma/config.yaml`

```yaml
# ~/.kazma/config.yaml
name: my-kazma-instance
model: openai/gpt-4o
provider: openai

# Skills directory
skills_dir: ~/.kazma/skills

# Hub registry
hub:
  db_path: ~/.kazma/hub/registry.db
  auto_update: false

# Agent behavior
agent:
  max_tokens: 4096
  temperature: 0.7
  checkpoint_interval: 10

# Delegation
delegation:
  enabled: true
  max_depth: 3
  timeout_seconds: 300

# Security
security:
  sandbox_enabled: true
  audit_trail: true
  allowed_permissions:
    - file_read
    - network_outbound
```

For production deployments the project-root `kazma.yaml` adds gateway and swarm
sections (see below). Copy it to `kazma.local.yaml` (git-ignored) for local
overrides; environment variables always take precedence.

## Gateway configuration

The headless gateway runs the Telegram, Discord, and Slack adapters with
per-platform rate limiting and session isolation.

```yaml
gateway:
  rate_limits:
    telegram: 30     # requests/second
    discord: 5
    slack: 1
  suggestions:
    enabled: true
  voice:
    enabled: false
    provider: openai  # openai | local | groq
```

| Field | Description |
|:---|:---|
| `gateway.rate_limits.<platform>` | Per-platform requests/second cap |
| `gateway.suggestions.enabled` | Enable post-task proactive suggestions |
| `gateway.voice.enabled` | Enable voice message transcription |
| `gateway.voice.provider` | STT provider (`openai`, `local`, `groq`) |

Manage the gateway lifecycle from the CLI:

```bash
kazma gateway status     # show adapter status
kazma gateway start      # start the gateway
kazma gateway stop       # stop the gateway
kazma gateway restart    # restart (stop + start)
kazma gateway refresh    # reload adapters
```

## Swarm configuration

The swarm engine orchestrates multiple workers with dispatch, broadcast,
pipeline, fan-out, consult, and conditional patterns. Workers can be in-process
agents or Telegram bots.

```yaml
swarm:
  enabled: true
  max_concurrent: 3          # max parallel dispatches (fan_out/broadcast/consult)
  # group_chat_id is read from the SWARM_CHAT_ID environment variable.
  group_chat_id: 0
  orchestrator:
    name: Kazma Orchestrator
    profile: default
  workers:
    - name: "builder"
      model: "deepseek-chat"
      provider: "deepseek"
      type: "in-process"
      role: "leaf"
    - name: "reviewer"
      model: "claude-sonnet-4"
      provider: "anthropic"
      type: "telegram"
      bot_token_env: KAZMA_REVIEWER_BOT_TOKEN
      role: "leaf"
    - name: "researcher"
      model: "gpt-4o-mini"
      provider: "openai"
      type: "in-process"
      role: "orchestrator"
  dispatch:
    default_strategy: "round-robin"
    retry_on_failure: true
    max_retries: 2
```

| Field | Description |
|:---|:---|
| `swarm.enabled` | Enable the swarm engine |
| `swarm.max_concurrent` | Bounded concurrency for parallel patterns (default `5`) |
| `swarm.orchestrator` | Orchestrator name and personality profile |
| `swarm.workers[].name` | Unique worker name |
| `swarm.workers[].model` | Model name |
| `swarm.workers[].provider` | LLM provider |
| `swarm.workers[].type` | Worker type (`in-process` or `telegram`) |
| `swarm.workers[].role` | Worker role (e.g. `orchestrator`, `leaf`, `researcher`) |
| `swarm.workers[].bot_token_env` | Env var name holding a Telegram bot token (for `telegram` workers) |
| `swarm.dispatch.default_strategy` | Default worker selection strategy |

Manage the swarm from the CLI:

```bash
kazma swarm status                              # swarm health and worker summary
kazma swarm workers                             # list all workers
kazma swarm worker add researcher \
  --model gpt-4o-mini --provider openai \
  --type in-process --role researcher           # add a worker
kazma swarm dispatch researcher "Summarize X"   # send one task
kazma swarm pipeline --workers a,b "Do X"       # sequential pipeline
kazma swarm start                               # start all workers
kazma swarm stop                                # stop all workers
```

See the [CLI Reference](../api-reference/cli-reference) for the complete swarm
command list, including `broadcast`, `consult`, `fanout`, `history`, `metrics`,
HITL `approve`/`reject`, and `circuit-breaker`.

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `KAZMA_HOME` | Kazma home directory | `~/.kazma` |
| `KAZMA_HUB_DB` | Hub registry database path | `~/.kazma/hub/registry.db` |
| `KAZMA_MODEL` | Default model override | `openai/gpt-4o` |
| `KAZMA_SECRET` | Secret for binding `0.0.0.0` and auth endpoints | — |
| `KAZMA_LOG_LEVEL` | Logging level | `INFO` |
| `KAZMA_PORT` | Web UI port | `8000` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | — |
| `DISCORD_BOT_TOKEN` | Discord bot token | — |
| `SLACK_BOT_TOKEN` | Slack bot token | — |
| `SLACK_APP_TOKEN` | Slack app (Socket Mode) token | — |
| `SWARM_CHAT_ID` | Swarm group chat ID for notifications | `0` |

## CLI commands

```bash
# Build documentation
kazma docs build

# Serve documentation locally
kazma docs serve

# Start the interactive wizard
kazma wizard

# See all commands
kazma help
```

For the full command list see the [CLI Reference](../api-reference/cli-reference).
