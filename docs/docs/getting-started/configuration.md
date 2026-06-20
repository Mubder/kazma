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

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `KAZMA_HOME` | Kazma home directory | `~/.kazma` |
| `KAZMA_HUB_DB` | Hub registry database path | `~/.kazma/hub/registry.db` |
| `KAZMA_MODEL` | Default model override | `openai/gpt-4o` |
| `KAZMA_LOG_LEVEL` | Logging level | `INFO` |

## CLI commands

```bash
# Build documentation
kazma docs build

# Serve documentation locally
kazma docs serve

# Start the interactive wizard
kazma wizard
```
