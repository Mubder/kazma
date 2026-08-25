# GOAL: Post–industry-stack leftovers (everything except SaaS)

**Status:** Done (2026-08-25) — A/B built, C capped, D recorded wontfix, SaaS park untouched  
**Created:** 2026-08-25  
**Source:** Industry stack audit + quality tranche + leftover ranking after 0.10.0  
**Rule:** Do **not** start/restart the Kazma server.

Kazma’s identity (do not lose this): **one LangGraph brain**, many mouths, HITL + commitment, V2 memory, self-hosted one-operator. Not Cursor. Not a phone-call app. Not multi-tenant SaaS.

---

## Mission

Close **every remaining industry leftover that is not SaaS**, so nothing sits in a “we’ll do it later” pile. Each item is either:

1. **Build** — real value for Kazma as it is today, or  
2. **Wontfix (recorded)** — named, with a reason, so it is not forgotten.

Success is an empty remainder list **except the SaaS park**.

---

## Out of scope — SaaS park (do not start)

These stay parked until you explicitly open a SaaS season:

| Parked | Why it is SaaS, not this goal |
|--------|-------------------------------|
| Default-on tenancy on every DB | Multi-user product |
| Passkeys / WebAuthn | Multi-user MFA |
| S3 / MinIO for blobs | Multi-replica files |
| Postgres **document metadata** (HA docs) | Multi-replica documents |
| E2B for **all** `shell_exec` (host shell gone) | Untrusted multi-tenant jail |
| Temporal **required** (no in-process swarm) | Cloud-agent fleet |
| Nginx HA + sticky / shared turn journal | Multi-replica web |
| k8s Hub as a supported agent deploy | Pretend-HA |

If a work package would only pay off for those rows, it is **wontfix here**, not a silent skip.

---

## What is real value for Kazma

Value = makes the **same operator** more reliable, cheaper, or more capable **without** a second brain, a second permission system, or a SaaS control plane.

| Rank | Value | Meaning |
|------|--------|---------|
| **A — product** | The agent does something it cannot do well today, on the existing mouths | Build first |
| **B — correctness** | Stops silent misroutes, 429 death, spec holes, CI blindness | Build next |
| **C — hygiene** | Makes the next change safer; no user-visible win | Last, bounded |
| **D — not Kazma** | Second product or SaaS physics | Wontfix (recorded) |

**Kazma is not** a LiveKit phone company, not Anthropic’s Computer Use VM farm, not “LiteLLM or nothing.” Those are D unless they plug into the existing graph + HITL.

---

## Complete remainder inventory (nothing omitted except SaaS)

### A — Product (build)

| ID | Item | Real value | How (freeze, don’t invent) |
|----|------|------------|----------------------------|
| A1 | **MCP resources / prompts / sampling / elicitation / roots** | Kazma is already a strong MCP *tools* client. 2026 MCP is more than tools. Skills and servers already ship resources; we ignore them. | Client-side MCP spec surface on the existing manager. Resources → fenced data. Prompts → user-visible. Sampling → HITL (same danger gate). Roots → workspace binding. **Not** an MCP *server* for other IDEs (ACP already covers hosting Kazma). |
| A2 | **Computer-use planner adapters** (Anthropic CUA / Gemini Computer Use) | Today `computer_use` is vision-JSON + Playwright. Native CUA is better at click targeting when that model is active. Same tool, same HITL. | Optional planner behind `computer_use`. Fallback stays current loop. No desktop VM. No second tool family. |
| A3 | **LiveKit: publish TTS as a room track + subscribe path** | Duplex today: LiveKit AEC + barge-in; TTS still plays in the browser. Closing the media loop makes “call” honest when LiveKit is on. | Optional server participant **or** browser-publish of TTS into the room. Brain remains STT → **graph** → TTS. **Not** LiveKit Agents LLM. |
| A4 | **Realtime STT/TTS only** (OpenAI Realtime / Gemini Live as *audio codecs*) | Lower latency listen/speak on web duplex. | Use Realtime/Live **only** to transcribe and speak. Tokens of meaning still go through `invoke_llm_chat` / the supervisor. Kill-switch. If a provider cannot separate audio from tool loop, skip that provider. |

### B — Correctness (build)

| ID | Item | Real value | How |
|----|------|------------|-----|
| B1 | **429 backoff** on the LLM retry layer | Roadmap still lists it. Rate-limit death is a real turn failure (`transient` already exists). | Exponential backoff on 429 inside `_call_llm_with_retry` / provider chat. Do not flatten `transient`. LiteLLM proxy still optional. |
| B2 | **Keyword model router is not the router** | `"code"` in a prompt must not pick the coding model forever. You already have `models.defaults.<kind>`. | `classify_prompt` becomes a hint. Selection: env lock → user `models.defaults.<kind>` → active. Keyword lists cannot override an explicit default. Document honesty. |
| B3 | **Trajectory eval in CI** | Eval pack exists; industry bar is “no merge if the supervisor regresses” on **tool traces**, not only recall. | Extend `scripts/eval_pack.py` with a frozen tool-trace fixture (no live LLM). CI already runs the pack — add cases, don’t add a second harness. |
| B4 | **Playwright e2e one smoke in CI** | Catches “page boots, Live button exists, SSE chat posts.” | One non-flaky spec: health + chat composer visible. Fix boot-wait **first** or it stays out. No full matrix. |

### C — Hygiene (build last, bounded)

| ID | Item | Real value | How (cap the work) |
|----|------|------------|---------------------|
| C1 | **God files** | Distant invariant breaks (crawl import, Telegram desync). | **Do not** rewrite `chat.js` / `sse_chat.py` / `app.py`. Extract **one** concern you must touch anyway (e.g. voice WS already split). Any further split is opportunistic in A/B PRs, not a rewrite season. |
| C2 | **Ruff / Bandit as CI gates** | Lint-as-gate on a 10-year backlog is a stall. | **Do not** remove `--exit-zero` globally. Optional: Ruff on **new/changed files only** in CI if cheap. Bandit stays advisory. |
| C3 | **Scraping providers Bright Data / Oxylabs** | Same `ProxyProvider` interface. Zero value until IP blocks hurt. | One class + `_PROVIDERS` line + Settings dropdown **when** you need it. Skeleton + docs now so the interface stays the add-a-provider story. No account required for tests (NullProvider behavior). |
| C4 | **Session TTL 5 min as HITL/reminder lookup** | Already documented; still a footgun. | Code comment + diagnosis-map + fail-closed message if someone looks up SessionStore for >5 min jobs. No TTL change (cron already uses `delivery_target`). |

### D — Wontfix here (recorded, not leftover)

| ID | Item | Why it is not Kazma now |
|----|------|-------------------------|
| D1 | **LiteLLM as the only LLM egress** | Breaks laptop / native four-branch (Anthropic/Azure/Bedrock/Gemini). Optional gateway **already shipped**. Exclusive = SaaS ops policy, not a kernel. |
| D2 | **OpenAI Realtime / Gemini Live as the conversation brain** | Second loop. Skips `turn_failed`, HITL `interrupt()`, commitment, memory fence. A4 is the only allowed shape (audio codec). |
| D3 | **Duplex voice on Telegram / Discord / Slack** | Those mouths are **files**, not WebRTC calls. Platform bots cannot barge-in. Voice notes stay turn-based. |
| D4 | **Computer-use Firecracker desktop VM** | That’s E2B-for-desktop / SaaS isolation. A2 is adapters on the existing tool. |
| D5 | **Re-add OpenTelemetry** | Purged. Langfuse + console are the freeze. Re-add only if OTLP to Jaeger is a real requirement (it is not). |
| D6 | **TUI as Claude Code** | TUI stays ops console. Coding CLI is `kazma ask` + ACP. |
| D7 | **MCP server** (resources/prompts for *other* IDEs to host our tools) | ACP hosts Kazma. We remain an MCP **client**. |

---

## Non-goals (invariants)

- Do not replace LangGraph, HITL (three gates), commitment, `turn_failed`, `hoist_system_messages`, four-branch provider dispatch.
- Do not start/restart uvicorn.
- Do not import `litellm` as the kernel.
- Do not add a fourth permission system.
- Do not grow Chroma as production memory.
- Windows: no `asyncio.create_subprocess_exec` on the SelectorEventLoop.

---

## Work packages (DAG)

```
B1 429 backoff ─────────────────────────────────┐
B2 router honesty ──────────────────────────────┤
A1 MCP spec (resources/prompts/sampling/roots) ─┼──► B3 eval traces
A2 computer_use native planners ────────────────┤
A3 LiveKit TTS in-room (if LiveKit configured) ─┤
A4 Realtime as STT/TTS codec only ──────────────┘
        │
        ▼
C3 proxy provider stubs (Bright Data / Oxylabs)
C4 session-TTL footgun honesty
C1 opportunistic extracts only (no god-file rewrite)
C2 optional Ruff-on-changed-files (Bandit stays advisory)
B4 Playwright one smoke — only if boot-wait is stable
        │
        ▼
WP-FINAL: remainder list empty except SaaS park + D-wontfix
```

Suggested ship order (one PR / one test gate each):

1. **B1** 429 backoff  
2. **B2** router cannot override `models.defaults`  
3. **A1** MCP resources → prompts → sampling (HITL) → roots  
4. **A2** CUA/Gemini planner behind `computer_use`  
5. **A3** LiveKit TTS track (skip if no LiveKit in env during tests; code + unit tests still ship)  
6. **A4** Realtime audio codec behind kill-switch; skip provider if it cannot stay codec-only  
7. **B3** tool-trace eval cases  
8. **C3–C4** stubs + honesty  
9. **C1/C2/B4** only if still in budget and not a rewrite

---

## Success gates

| # | Gate | Verify |
|---|------|--------|
| G1 | SaaS park untouched | No tenancy/passkeys/S3/PG-doc-meta/E2B-shell/Temporal-required in this goal’s diffs |
| G2 | MCP A1 | Resource read returns fenced data; sampling/elicitation cannot skip HITL; tests without live servers |
| G3 | Router B2 | Explicit `models.defaults.coding` wins over a prompt that says “barcode” / “code” |
| G4 | 429 B1 | Fake 429 then 200: retries with backoff; `transient=True` preserved |
| G5 | CUA A2 | Without Anthropic CUA model, existing vision-JSON loop still works |
| G6 | Voice A3/A4 | Graph still invoked for meaning; kill-switches; Telegram path unchanged |
| G7 | Eval B3 | CI pack includes at least one tool-trace fixture; no live LLM required |
| G8 | D-list | Each D item appears in CHANGELOG/audit as wontfix-here, not “TODO” |
| G9 | Docs | Docusaurus: MCP, computer_use, voice, router, env vars, this GOAL linked from intro/roadmap |
| G10 | Remainder | This file’s A/B/C boxes are Done or explicitly slipped; D stays wontfix; SaaS park stays park |

---

## Key decisions

1. **SaaS is a season, not a leftover** — excluding it is the point of this goal.  
2. **Every non-SaaS leftover is either a WP or a D-wontfix** — no third pile.  
3. **Audio and computer-use plug into the graph** — never a parallel brain.  
4. **MCP client completeness beats MCP server** — ACP already hosts Kazma in editors.  
5. **Hygiene is capped** — no `chat.js` rewrite; Ruff is not a backlog gate.  
6. **LiteLLM stays optional** — exclusive egress is D1.

---

## Operator proceed

Executed after operator **proceed**. Remainder list is empty except the SaaS
park and the D-wontfix table (CHANGELOG + this file).

| ID | Outcome |
|----|---------|
| A1 | Done — `kazma_core/mcp/spec_client.py` + manager methods + native tools |
| A2 | Done — `computer_use_planners.py`; vision-JSON fallback |
| A3 | Done — browser `publishTrack` of TTS; `tts_in_room` on LiveKit status |
| A4 | Done — REST codec + skip Realtime/Live (`KAZMA_REALTIME_CODEC=1`) |
| B1 | Done — Retry-After backoff on generic + Anthropic; `transient` preserved |
| B2 | Done — word-boundary classify; `models.defaults.<kind>` wins |
| B3 | Done — `tool_trace` + `computer_use` HITL in eval pack |
| B4 | Slipped — Playwright e2e stays out of CI (boot-wait still flaky) |
| C1 | Capped — no god-file rewrite |
| C2 | Capped — Ruff `--exit-zero`; Bandit advisory |
| C3 | Done — Bright Data / Oxylabs stubs + Settings dropdown |
| C4 | Done — `sessions.ttl` + diagnosis-map + cron comment |
| D1–D7 | Wontfix recorded in CHANGELOG |
| SaaS park | Untouched |
