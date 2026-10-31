---
id: tui
title: Terminal UI (TUI)
sidebar_label: TUI
description: Textual-based Kazma dashboard and editor
---

# Terminal UI (TUI)

> This page is published at [kazma.ai/docs/tui/](https://kazma.ai/docs/tui/) ([Arabic](https://kazma.ai/ar/docs/tui/)).

```bash
kazma-tui
```

Built with **Textual** (`kazma-tui`). Provides a dashboard for chat, status, files/editor, and HITL prompts without a browser.

## Capabilities

| Area | Notes |
|------|-------|
| Chat | Talk to the same agent brain as Web/gateway |
| Editor / files | IDE-style workspace operations via shared `IdeService` / tools |
| Documents | **Documents** tab (`DocumentsPanel`) — list/inspect processed docs through the running server's Documents API (upload still Web/chat) |
| HITL | Approval modal widgets for danger tools |
| Status | Models, gateway, health |
| Language | English or Arabic: **Ctrl+L**, or **Ctrl+P** → *Switch language*; the choice is saved. The tabs and the navigation rail follow it in the web UI's words, with right-to-left styling for Arabic |
| Demo mode | `KAZMA_DEMO_MODE` (never use in real prod) |

### Documents tab

- Nav rail entry **Documents** (keyboard **8**).
- Reads the running server (`kazma serve`) through `GET /api/documents`; with no
  server it says the platform is unavailable. The server applies the same ACL
  as on the Web page to the TUI's credential (the shared secret header, so the
  `operator` principal; `local` when no secret is configured), so it lists the
  documents that principal can read. **Refresh** reloads the list.
- Select a row to preview the first 8,000 characters of its fenced content
  (`GET /api/documents/{document_id}/content`); a document that is not `ready`
  yet shows "Preview failed".
- **Upload / convert / GC** remain on Web `/documents` or agent tools — TUI is inspect-first.

Guide: [Document Intelligence](../guide/document-intelligence).

## Config

Respects the same `kazma.yaml`, ConfigStore, and env vars as the Web UI (`KAZMA_SECRET` when talking to a remote server, vector memory envs, etc.).

## Related

- [IDE](ide) · [CLI](../guide/cli-reference) · [Gateways](../guide/gateways-and-platforms)  
