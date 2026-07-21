---
sidebar_position: 2
---

# Quickstart

This guide walks you through creating a minimal Kazma agent and interacting with it.

## 1. Create an agent config

Create a file `my_agent.yaml`:

```yaml
name: my-first-agent
model: openai/gpt-4o
system_prompt: |
  You are a helpful assistant. Answer questions concisely.
temperature: 0.7
```

## 2. Run the agent

Kazma provides three entry points. The quickest way to get started is the CLI:

```bash
# Start the WebUI server (default port 8000)
kazma serve
kazma serve 8080          # custom port

# Or use the dedicated WebUI entry point
kazma-web --port 8080

# Terminal UI
kazma-tui

# CLI — banner, config check, and status overview
kazma
```

You can also point the agent at a custom config:

```bash
kazma serve --config my_agent.yaml
```

## 3. Interact

The agent listens on `http://localhost:8000`. Send a message:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is Kazma?"}'
```

## 4. Check system status

Use the CLI to inspect the gateway, swarm, and server health:

```bash
kazma status               # overall system health (gateway, swarm, server)
kazma gateway status       # gateway adapter status
kazma swarm status         # swarm workers and health
```

## 5. Add a skill

```bash
kazma hub search "weather"
kazma hub install author/weather-skill
```

The skill is now available to your agent.

## 6. Orchestrate a swarm (optional)

If the swarm engine is enabled, dispatch tasks to workers from the CLI:

```bash
kazma swarm workers                                   # list registered workers
kazma swarm dispatch researcher "Summarize the news"  # send one task
kazma swarm status                                    # check swarm health
```

See the [CLI Reference](../api-reference/cli-reference) for the full command
list, including `broadcast`, `pipeline`, `fanout`, and `consult` patterns.

## Architecture overview

```
User Message → Agent Loop → Tool Selection → Skill Execution → Response
      ↓              ↓              ↓               ↓              ↓
  Checkpoint    Token Count    Dialect Router   Sandbox    Context Compaction
```

## Next steps

- [Configuration](../getting-started/configuration)
- [CLI Reference](../api-reference/cli-reference)
- [Architecture deep dive](../core-concepts/architecture)
- [Creating skills](../skill-development/creating-skills)
- [Delegation protocol](../core-concepts/delegation-protocol)
