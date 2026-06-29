---
sidebar_position: 1
---

# Architecture

Kazma is built as a modular Python framework with clear separation of concerns.

## Module structure

```
kazma-core/         Core agent framework
  agent.py        Agent loop and lifecycle
  router.py       Dialect routing
  compaction.py   Context compaction
  checkpoint.py   State persistence
  state.py        State management
  hub/            Skill registry and management
  delegation/     Multi-agent delegation
  security/       Security auditing

kazma-skills/       Built-in skills
kazma-connectors/   External service connectors
kazma-providers/    LLM provider integrations
kazma-ui/           Arabic RTL dashboard
kazma-tui/          English-only Textual TUI
kazma-cli/          Command-line interface
kazma-memory/       Persistent memory subsystem
```

## Data flow

```
+--------------------------------------------------+
|                    User Input                     |
+------------------+-------------------------------+
                   v
+--------------------------------------------------+
|              Agent Loop (agent.py)               |
|  +---------+  +----------+  +---------------+   |
|  | Token    |  | Dialect  |  | Checkpoint    |   |
|  | Counter  |  | Router   |  | Manager       |   |
|  +---------+  +----------+  +---------------+   |
+------------------+-------------------------------+
                   v
+--------------------------------------------------+
|           Tool / Skill Execution                 |
|  +----------+  +-----------+  +-------------+   |
|  | Sandbox  |  | Skill     |  | Delegation  |   |
|  |          |  | Loader    |  | Protocol    |   |
|  +----------+  +-----------+  +-------------+   |
+------------------+-------------------------------+
                   v
+--------------------------------------------------+
|            Context Compaction                    |
|  Summarize long conversations to fit in window   |
+------------------+-------------------------------+
                   v
+--------------------------------------------------+
|               Response Output                    |
+--------------------------------------------------+
```

## Key design principles

1. **Modularity** — Each module is independent and testable
2. **Async-first** — All I/O is async (aiosqlite, httpx, asyncio)
3. **SQLite-backed** — State, checkpoints, and hub registry use SQLite
4. **Security by default** — Sandboxing, permission checks, audit trails
5. **Arabic RTL support** — First-class Arabic dialect detection and rendering
