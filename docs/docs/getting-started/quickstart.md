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

## 4. Add a skill

```bash
kazma hub search "weather"
kazma hub install author/weather-skill
```

The skill is now available to your agent.

## Architecture overview

```
User Message → Agent Loop → Tool Selection → Skill Execution → Response
      ↓              ↓              ↓               ↓              ↓
  Checkpoint    Token Count    Dialect Router   Sandbox    Context Compaction
```

## Next steps

- [Architecture deep dive](../core-concepts/architecture)
- [Creating skills](../skill-development/creating-skills)
- [Delegation protocol](../core-concepts/delegation-protocol)
