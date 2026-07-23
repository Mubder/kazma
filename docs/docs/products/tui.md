---
id: tui
title: Terminal UI (TUI)
sidebar_label: TUI
description: Textual-based Kazma dashboard and editor
---

# Terminal UI (TUI)

```bash
kazma-tui
```

Built with **Textual** (`kazma-tui`). Provides a dashboard for chat, status, files/editor, and HITL prompts without a browser.

## Capabilities

| Area | Notes |
|------|-------|
| Chat | Talk to the same agent brain as Web/gateway |
| Editor / files | IDE-style workspace operations via shared `IdeService` / tools |
| HITL | Approval modal widgets for danger tools |
| Status | Models, gateway, health |
| Demo mode | `KAZMA_DEMO_MODE` (never use in real prod) |

## Config

Respects the same `kazma.yaml`, ConfigStore, and env vars as the Web UI (`KAZMA_SECRET` when talking to a remote server, vector memory envs, etc.).

## Related

- [IDE](ide) · [CLI](../guide/cli-reference) · [Gateways](../guide/gateways-and-platforms)  
