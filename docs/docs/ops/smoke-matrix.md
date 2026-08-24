---
id: smoke-matrix
title: Smoke test matrix
sidebar_label: Smoke matrix
description: Manual verification matrix for research, KB, proxy, memory, chat explain, and documents
---

# Smoke test matrix

Run after pulls that touch research, Knowledge Library, proxy, V2 memory, or
**Document Intelligence**. Check off each row; note environment (local / Docker /
SearXNG / proxy).

**Automated (offline) subset — industry CI:**

```powershell
pwsh -File scripts/industry_smoke.ps1
```

Runs readiness, re-index skip, session cancel, proxy helpers, golden memory
eval, and docs presence. **Live network / LLM rows below still need a human.**

**Prerequisites (manual live rows)**

- App up (`uvicorn` / usual serve)
- At least one LLM configured
- Optional: SearXNG, Proxy Provider (anyIP), Neo4j
- Preflight: `GET /api/research/ready` (add `?live=1` for a tiny live search)

---

## 1. Research

| # | Action | Expect | Pass |
|---|--------|--------|------|
| R1 | Open `/research` → Start deep research (short topic, brief, 3 sources) | Live panel stage log updates; session card appears | ☐ |
| R2 | Let run finish | Status `done`; report path; MD open works | ☐ |
| R3 | Start another run → **Cancel** mid-flight | Status `cancelled`; no hang; list refresh | ☐ |
| R4 | Chat or `/research deep <topic>` (gateway) | Progress messages or summary with report | ☐ |
| R5 | Settings: force thin network if needed | Fail closed deep shows clear error (not silent empty) | ☐ |

---

## 2. Knowledge Library

| # | Action | Expect | Pass |
|---|--------|--------|------|
| K1 | Create lib + crawl a small public docs root | Job shows discovered/fetched/ingested | ☐ |
| K2 | **Refresh** same lib immediately | `unchanged` / skipped rise; few or zero new embeds | ☐ |
| K3 | Remove a page from scope (or prune via re-discover) | `pruned` count or gone URL absent from search | ☐ |
| K4 | Enable **auto_inject** on lib; ask in chat about a doc fact | Answer cites KB / footer | ☐ |
| K5 | Settings → Memory → **Smart Knowledge search** on; technical Q without auto_inject | Still injects from active libs with chunks | ☐ |

---

## 3. Proxy Provider

| # | Action | Expect | Pass |
|---|--------|--------|------|
| P1 | Settings → System → Proxy = none → Test | Direct mode OK | ☐ |
| P2 | Configure anyIP (or stub) → Test | Exit IP shown when valid | ☐ |
| P3 | With proxy on: `read_url` hard page | httpx path may use proxy; Playwright recovery still works | ☐ |
| P4 | KB crawl with proxy on | Discover + fetch succeed (or clear failures) | ☐ |

---

## 4. Memory + chat explain

| # | Action | Expect | Pass |
|---|--------|--------|------|
| M1 | Chat: “Remember my favorite color is teal.” then “What color do I like?” | Recalls teal | ☐ |
| M2 | Settings → Memory → **Explain recall** ON | Next chat turn workbench shows **Memory context** panel | ☐ |
| M3 | Panel chips | Beliefs/episodes show `fts5` / `dense` / `belief_ppr` / `session_boost` as applicable | ☐ |
| M4 | With KB inject: technical doc question | Panel also lists **KB** rows with `kb_rrf` | ☐ |
| M5 | Dashboard probe | Same channel chips; **Run golden eval** returns pass rate | ☐ |
| M6 | Multi-hop beliefs (works_at → located_in) | Probe/chat can surface multi-hop object | ☐ |
| M7 | Open `/memory` after restart | Previously payload-object concepts (literal objects) sit on a hub `related_to` edge, not a disconnected island | ☐ |
| M8 | Inspect a grouped node → **Ungroup** | Grouping gone; beliefs unchanged | ☐ |
| M9 | Truncation banner (graph > 200 nodes) | Banner names nodes **and** connections hidden by slicing | ☐ |

---

## 5. Document Intelligence

| # | Action | Expect | Pass |
|---|--------|--------|------|
| D1 | Open `/documents` → upload a small `.txt` / PDF | Document appears; state progresses to `ready` (or clear fail code) | ☐ |
| D2 | Open detail → content preview | Fenced/readable text; BiDi OK for Arabic if used (`dir=auto`) | ☐ |
| D3 | Index into a library_id → search | Hits return with citations inside untrusted fence | ☐ |
| D4 | Ops panel: capacity + readiness | Snapshot loads; multi-replica honesty if applicable | ☐ |
| D5 | Gateway: `/documents list` (or `/docs list`) | Opaque ids + titles | ☐ |
| D6 | Agent tool `document_import` on workspace file | Returns document_id; not a path escape | ☐ |
| D7 | `python scripts/certify_documents.py` | `overall_status` not `FAIL`; canary_ready when core parsers ready | ☐ |
| D8 | Settings → Documents → change a limit → save → re-read | Live ConfigStore update without restart | ☐ |
| D9 | (Optional) ClamAV installed → malware_scan `on` → upload clean file | Accept; readiness `malware.available` true | ☐ |
| D10 | (Optional) PG + `KAZMA_DOCUMENTS_METADATA_BACKEND=postgres` | `ops/readiness` `metadata_multi_replica` true when pool up | ☐ |

Guide: [Document Intelligence](../guide/document-intelligence) · [Phase map](../guide/document-phases) · [Ops](./document-processing).

---

## 6. Regression quick hits

| # | Action | Expect | Pass |
|---|--------|--------|------|
| X1 | HITL danger tool still prompts | Approve/deny works | ☐ |
| X2 | Model switch in UI | Next turn uses selected model | ☐ |
| X3 | Long turn | Heartbeats / synthesizing; no false “Done 0s” empty bubble | ☐ |

---

## Sign-off

| Field | Value |
|-------|--------|
| Date | |
| Build / commit | |
| Tester | |
| Notes | |

Related: [Recent features guide](../guide/recent-features) · [Production checklist](./production-checklist) · [Web research](../guide/web-research) · [Knowledge library](../guide/knowledge-library) · [Document Intelligence](../guide/document-intelligence) · [Diagnosis map](./diagnosis-map).
