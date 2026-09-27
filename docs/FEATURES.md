# What Kazma does — a plain list

Written from the code on **2026-09-27** for the website: every line was
checked against the source, and each names where to look. It says what exists,
not what is planned.

**Status words.** **Shipped**: works out of the box or one setting away.
**Opt-in**: works once you install or configure the named extra.
**Partial**: works, with the limit stated. **Not built**: people ask; it is
not there.

---

## One assistant, everywhere you talk to it

| Feature | Status | Where |
|---|---|---|
| Web chat with streaming answers, a step-by-step activity view per answer, and approvals inline | Shipped | `/chat` |
| Telegram, Discord and Slack: text, photos and documents, voice notes transcribed, approve/deny buttons, per-platform allowed-user lists | Shipped | `kazma-gateway/` |
| Terminal UI, including a file editor | Shipped | `kazma-tui` |
| Command line: one-shot questions (`kazma ask`), and the Agent Client Protocol for editors (`kazma acp`) | Shipped | `kazma-cli/` |
| An MCP server: other agents can use Kazma's tools, with the same approval gate (`kazma mcp`) | Shipped | `kazma_core/mcp/server.py` |
| Spoken replies (text-to-speech) and speech input; click-to-play in web chat | Shipped | Settings → Voice |
| Live two-way voice in the browser through LiveKit | Opt-in | `LIVEKIT_URL` + keys |
| English and Arabic interface, full right-to-left layout | Shipped | every page |

## Models

| Feature | Status | Where |
|---|---|---|
| 20 provider presets: OpenAI, DeepSeek, Groq, xAI, OpenRouter, Mistral, Together, Fireworks, Perplexity, Cohere, AI21, NVIDIA and Z.AI (OpenAI-compatible); native Anthropic, Google Gemini (incl. Vertex), Azure OpenAI and AWS Bedrock; and any custom OpenAI-compatible endpoint | Shipped | Settings → Providers |
| Local models through Ollama and LM Studio | Shipped | Settings → Providers |
| A provider's Test sends a real completion, not just a ping | Shipped | Settings → Providers |
| Best model per task for multi-agent workers; image questions go to a vision-capable model | Shipped | `models/selection.py`, `vision_capability.py` |
| When a model fails over to another, you are told (page + banner) | Shipped | `observability/model_fallback.py` |
| Token and cost per answer; a cost breaker that halts runaway spending | Shipped | chat footer, dashboard |

## Safety and control

| Feature | Status | Where |
|---|---|---|
| Dangerous actions wait for your approval — on chat, multi-agent and pipeline paths alike; unclassified tools are gated by default | Shipped | [Threat model](THREAT_MODEL.md) |
| Plans checked against memory before they act: a reminder date is anchored to what you said, not what the model guessed; catastrophic shell commands are refused before the approval card | Shipped | [Commitment layer](docs/guide/commitment-layer.md) |
| Web pages, search results, documents and recalled memory are fenced as untrusted data; the effect is measured on a public benchmark | Shipped | [Prompt injection: the numbers](INJECTION.md) |
| Secrets in an encrypted vault (AES-256-GCM); never passed to programs a tool starts; masked on every screen and API | Shipped | `security/vault.py`, `security/child_env.py` |
| Protections against server-side request forgery, cross-site requests, and request floods | Shipped | `security/ssrf.py`, `kazma_ui/csrf.py`, `rate_limit.py` |
| Login by secret, local users or OIDC single sign-on; admin, operator and viewer roles | Shipped | `/login`, [OIDC setup](docs/ops/oidc-setup.md) |
| Several users or teams on one install | Partial — built for one trusted operator; multi-user separation is enforced for memory and chats, not audited end to end | [Multi-user](docs/products/multi-user-saas.md) |
| Code the agent runs in an isolated container | Opt-in — Docker (`KAZMA_CODE_EXEC_DOCKER=force`) or E2B | `tools/code_exec.py`, `sandbox/e2b.py` |

## Memory

| Feature | Status | Where |
|---|---|---|
| Remembers conversations and the facts in them, across chats, with when each fact became true | Shipped | [Memory](docs/guide/memory-and-rag.md) |
| Recall by evidence: a memory is used only when it matches the question well enough — nothing when nothing does | Shipped | `memory/recall.py` |
| Every answer shows which memories it used | Shipped | chat activity: "Memory used" |
| "About me": a short text you write, read at the start of every reply | Shipped | Settings → Memory |
| Weekly topic summaries of long threads | Shipped | Memory page |
| Forget one memory or a whole chat; keep a chat out of memory; export everything | Shipped | chat menu → Memory…, Memory page |
| Works in Arabic and English, Gulf dialect included | Shipped | `memory/query_terms.py` |
| Nothing lost by accident: archiving never erases a memory's text | Shipped | `memory/macro_sleep.py` |
| Shared memory between installs through Postgres | Partial — memories and facts mirror; each install keeps its own vectors and graph | [Postgres](docs/ops/postgres-and-saas.md) |

## Knowledge and documents

| Feature | Status | Where |
|---|---|---|
| Knowledge Library: add files, web pages or whole sites; search by keywords and by meaning; Arabic-aware | Shipped | `/knowledge` |
| Documents: safe upload (quarantine, file-type checks, macro rejection), text and OCR from PDF, Word, Excel and PowerPoint, Arabic included | Shipped | `/documents` |
| Virus scanning of uploads | Opt-in — needs ClamAV installed | `documents/malware.py` |
| Convert, split, merge and redact documents; generate reports as Word, PDF or HTML with correct Arabic typography | Shipped | `/documents` |
| Deep web research with sources: search, read, crawl, summarize | Shipped | `/research` |
| Web search through SearXNG (self-hosted metasearch, in the Docker setup) | Shipped | `tools/web_search.py` |
| Reading hard pages through Firecrawl, Jina or a real browser | Opt-in — keys / Playwright | `tools/read_url.py` |
| Scraping through a rotating residential proxy | Opt-in | Settings → System → Proxy |
| Image generation (Pollinations with no key; DALL-E, Stability, Flux with keys) and image understanding | Shipped | `tools/image_gen.py` |

## Work on code

| Feature | Status | Where |
|---|---|---|
| Web IDE: browse, edit, patch, run, git — every write through the approval gate | Shipped | `/ide` |
| Clone and switch repositories; the agent always works in the active one | Shipped | `/workspace` |
| GitHub: branches, commits, pull requests, through a GitHub App or a token | Shipped | `git_github_manager` skill |
| Code search across the repository (ripgrep plus symbols) | Shipped | `kazma_core/code_index/` |
| Steer a running task (`/steer`), abort it, plan first (`/plan`), long missions (`/long`) | Shipped | chat commands |
| Rewind or branch a conversation from any earlier step | Shipped | `/replay`, `/fork`, [Time travel](docs/guide/time-travel-and-replay.md) |

## Many agents at once

| Feature | Status | Where |
|---|---|---|
| Six ways to run workers: dispatch, broadcast, pipeline (with approval checkpoints), fan-out with voting, consult, conditional | Shipped | `/swarm`, `kazma swarm` |
| Workers created on demand from templates — none need registering first | Shipped | `swarm/autoscaler.py` |
| Circuit breakers, retries, timeouts and output checks per worker | Shipped | `swarm/reliability.py` |
| Durable multi-step runs through Temporal | Opt-in | `swarm/durable_temporal.py` |

## Automation and connected accounts

| Feature | Status | Where |
|---|---|---|
| Reminders and scheduled tasks, delivered to the chat you asked from | Shipped | `/scheduled` |
| X (Twitter): drafts, schedule, post through the official API, reply to mentions | Shipped | `/x` |
| Email (Gmail, Outlook) and calendar (Google, Microsoft) | Shipped | Settings → Email |
| Skills from the open agentskills.io ecosystem: search, install from GitHub, signed and verified | Shipped | `/skills` |
| MCP tools from any MCP server (stdio, SSE, streamable HTTP) | Shipped | `/mcp` |

## Running it

| Feature | Status | Where |
|---|---|---|
| A supervisor that restarts on real failure, reloads gracefully and pages you over Telegram | Shipped | `scripts/service/kazma_guard.py` |
| Backups of everything every 6 hours, snapshotted locally and offsite (restic), checked daily, with a weekly restore rehearsal of the database | Shipped | [Disaster recovery](docs/ops/disaster-recovery.md) |
| Move an install to another machine or OS in one bundle | Shipped | `kazma migrate` |
| Health checks that make a real round trip; alerts to Telegram, Discord or Slack; a daily digest and a weekly resilience report | Shipped | `/health/deep` |
| Old chat step history pruned on a schedule you set | Shipped | Settings → System |
| Postgres for settings, chats, tasks and checkpoints; SQLite with no setup | Shipped | [Postgres](docs/ops/postgres-and-saas.md) |
| Prometheus metrics; OpenTelemetry and Langfuse traces | Shipped / Opt-in | `/metrics`, [OpenTelemetry](docs/ops/opentelemetry.md) |
| Updates in one command | Shipped | [`kazma update`](docs/ops/kazma-update.md) |
| Fault injection to test retries and failover: slow or failing model calls, tool calls and reply saves (other targets have no injection point yet); off unless enabled | Opt-in | `KAZMA_CHAOS_ENABLED`, [Chaos testing](docs/ops/chaos-testing.md) |

## Not built

- A paid bug bounty (responsible disclosure only — [SECURITY.md](../SECURITY.md)).
- Cryptographic trust tiers for skills beyond HMAC verification, and signed delegation between agents.
- A hardening check that runs at startup: `kazma-security.yaml` is read by no code; the hardening report runs on demand.
- A security audit of multi-tenant, internet-facing deployments. Kazma is built for one trusted operator; what that means is written down in [THREAT_MODEL.md](THREAT_MODEL.md).
