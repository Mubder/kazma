---
id: smoke-matrix
title: Smoke test matrix
sidebar_label: Smoke matrix
description: Manual verification matrix for research, KB, proxy, memory, and chat explain
---

# Smoke test matrix

Run after pulls that touch research, Knowledge Library, proxy, or V2 memory.
Check off each row; note environment (local / Docker / SearXNG / proxy).

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

---

## 5. Regression quick hits

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

Related: [Recent features guide](../guide/recent-features) · [Production checklist](./production-checklist) · [Web research](../guide/web-research) · [Knowledge library](../guide/knowledge-library) · [Diagnosis map](./diagnosis-map).
