# Changelog

All notable changes to Kazma are documented here, grouped by sprint.
Features are listed with their implementation PR/commit where available.

---

## Sprint 4+ — UX, Security & Ecosystem

### Agent UX

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Slash Commands | 12 instant commands: `/help`, `/reset`, `/status`, `/model`, `/memory`, `/cost`, `/undo`, `/edit`, `/replay`, `/personality`, `/context`, `/config` | `7a65bde`, `8e4c735` |
| ✅ | Personality System | 8 built-in profiles with runtime switching via `/personality` | `f46b140` |
| ✅ | Quick Reply Buttons | Telegram inline keyboards for HITL approvals + personality selection | `7394980` |
| ✅ | Proactive Suggestions | Post-task next-step hints + automatic tool-intent detection | `5642f56` |
| ✅ | Rate Feedback | Friendly cooldown messages when user hits rate limits | `56a9a3d` |
| ✅ | Context Indicator | Token usage report with role breakdown via `/context` | `56a9a3d` |
| ✅ | Message Edit/Delete | `/undo` removes last agent response; `/edit` replaces it with corrected text | `bdd2846` |
| ✅ | Time Travel Replay | Snapshot-based replay engine — `/replay list`, replay from iteration, compare runs | `d965c8f`, `c9083c2` |
| ✅ | Config Wizard | `/config` with 7 sub-commands: show, model, personality, memory, tools, export | `968c7b2` |
| ✅ | Project Init | `.kazma/` directory system — `kazma project init`, show, validate | `1263b9a` |
| ✅ | Shell Completions | Bash and zsh tab completion for `kazma` CLI | `1dcf49e` |
| ✅ | CLI Banner | Startup banner with status overview, config checks, and quick help | `8f9ae8d` |

### Gateway & Adapters

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Slack Adapter | Socket Mode adapter with listen(), _parse_event(), 429 retry | `de0e9d7` |
| ✅ | Telegram Voice | Voice message transcription in Telegram adapter | `6697525` |
| ✅ | Emoji Reactions | Telegram message reactions + callback query support | `7394980` |

### Tools & Capabilities

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Image Generation | pollinations.ai-backed image gen, saved to `kazma-data/images/` | `2ec1596` |
| ✅ | Vision Analysis | Analyze images via LLM vision capabilities | `1be0db9` |
| ✅ | Knowledge Graph | NetworkX MultiDiGraph backend for structured memory | `6c69708` |
| ✅ | KG Memory Adapter | Knowledge Graph-backed memory integration layer | `d832f9b` |
| ✅ | MCP Server | IDE integration via MCP protocol (VS Code extensions) | `679530b` |
| ✅ | Kazma Hub | Skill marketplace — search, install, publish, certify | `bdd2846` |

### Security

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Security Linter | Static analysis for security anti-patterns in agent code | `bdd2846` |
| ✅ | Dependency Scanner | Vulnerability scanning for Python dependencies | `bdd2846` |
| ✅ | RBAC Permissions | Role-based access control for tools and commands | `bdd2846` |
| ✅ | Audit Trail | Full disclosure logging and certification chain | `bdd2846` |
| ✅ | Disclosure System | Automatic capability disclosure on first interaction | `bdd2846` |

### Documentation

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Slash Commands Doc | Complete reference for all 12 slash commands | `bdd2846` |
| ✅ | Portability Policy | Platform-agnostic deployment guarantees | `2fa2a1a` |
| ✅ | Docusaurus Site | Full documentation site with security + hub guides | `bdd2846` |

---

## Sprint 3 — Advanced Agent & Safety

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Sub-Agent Spawning | Delegate tasks to child graphs with isolated contexts | `d86d46b` |
| ✅ | Sub-Agent Visualization | Thought panel for sub-agent activity in Web UI | `6ff0f7d` |
| ✅ | Cron Autonomy | Scheduled agent actions with SQLite persistence | `5df188a` |
| ✅ | Model Router | Multi-provider routing (DeepSeek, OpenRouter) with intelligent selection | `6c97ff9` |
| ✅ | Auto-Summarization | Context compaction when token window exceeds 4K threshold | `3c5166f` |
| ✅ | Sandboxed Code Exec | Python subprocess with `-I` isolation, 30s timeout, 512MB limit | `3c5166f` |
| ✅ | HITL Approval Gates | Tiered tool approval: safe/warning/danger, inline keyboard | `2ee1c9a` |
| ✅ | RAG Memory | VectorMemory with ChromaDB + sentence-transformers | `8952d50` |
| ✅ | Prometheus Metrics | `/metrics` endpoint for monitoring | `8952d50` |
| ✅ | HITL Auth | Shared-secret authenticated approval endpoint | `8952d50` |
| ✅ | MCP Bridge | UnifiedToolExecutor — local + MCP tool routing | `bb803e5` |
| ✅ | Hardware Telemetry | Async CPU, RAM, GPU monitoring | `bfe39f1` |
| ✅ | SSE Telemetry | Real-time server-sent events stream | `07205e7` |
| ✅ | Production Hardening | Health indicator + SSE reconnect, graceful shutdown | `536b50c` |
| ✅ | Agent Thought Panel | Tool call visualization in Web UI | `c21c99a` |
| ✅ | Gateway Monitor | Multi-platform metrics panel with health banner | `0eacca8`, `70e8ae5` |
| ✅ | Docker Deploy | Single `docker compose up` with 2 volumes | `b150f21` |
| ✅ | Retry & Backoff | Exponential backoff with configurable attempts | `2424604` |

---

## Sprint 2 — Gateway & Multi-Platform

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Gateway Framework | Unified adapter framework with BaseAdapter, polling architecture | `eb3509c` |
| ✅ | Omnichannel Bus | Platform-agnostic message bus + polling adapter + monitor UI | `68685f6` |
| ✅ | Telegram Adapter | Full bot support with aiogram polling, asyncio.Event shutdown | `cecf51b` |
| ✅ | Discord Adapter | Native Markdown, rate-limited | `70e8ae5` |
| ✅ | Session Store | SQLite-based session isolation with checkpoint locks | `54e0f12` |
| ✅ | Rate Limiting | Per-platform token bucket with jitter + 429 retry | `0911192` |
| ✅ | Gateway Monitor UI | Professional dashboard with auto-refresh | `8f781d5` |
| ✅ | Persistence Indicator | Resume-aware UI with checkpoint status | `82ca0b2` |
| ✅ | Webhook Ingress | FastAPI router for Telegram webhook bridge | `8be5cd0` |
| ✅ | Live Gateway Status | Real `/api/gateway/status` endpoint replacing mock data | `715aa76` |
| ✅ | Brain-API Contract | Reply metadata envelope + gateway consumer | `62d9d45` |
| ✅ | Health Endpoint | `/health` with persistence indicator + resume highlight | `b0a0f52` |
| ✅ | Correlation ID | Request tracing across gateway → agent → reply | `647fcdd` |
| ✅ | Thread ID Standardization | Consistent thread_id across all layers | `647fcdd` |

---

## Sprint 1 — Core Foundation

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | ReAct Supervisor | LangGraph-based agent with tool-calling loop | `b150f21` |
| ✅ | SQLite Checkpointing | Durable execution — agents resume mid-task after crash | `b150f21` |
| ✅ | Multi-Provider LLM | DeepSeek, OpenRouter, Ollama with LiteLLM routing | `12777c3` |
| ✅ | Dynamic Model Discovery | Local provider auto-detection and runtime switching | `56cc5ae` |
| ✅ | Provider Config UI | Settings tab with API config + model test | `aa650f5` |
| ✅ | Web UI Dashboard | FastAPI + Jinja2 with Arabic RTL support | `aa05135` |
| ✅ | Terminal UI | Textual TUI with Arabic/RTL support | `743c2b4` |
| ✅ | CLI Entry Point | `kazma status`, `serve`, `wizard`, `hub`, `docs` | `743c2b4` |
| ✅ | Context Authority | Compaction loop architecture — 80% threshold system | `ec860cc` |
| ✅ | Omnichannel Support | Multi-platform UI with shared model config store | `e8be750` |
| ✅ | File I/O Tools | file_read, file_write, file_list, file_search | `2424604` |
| ✅ | Web Search Tool | DuckDuckGo-powered search | `c47ce06` |
| ✅ | URL Reader Tool | Fetch + extract via trafilatura | `c47ce06` |
| ✅ | Export Session Tool | Save conversation history to file | `c47ce06` |
| ✅ | Token Validation | Optional Telegram token check with graceful fallback | `14e5e12` |
| ✅ | Graph Fallback | Auto-fallback to agent.run() when graph is None | `d664be9` |
| ✅ | URL Sanitization | Force `/v1` suffix on all LLM config paths | `4431e5f` |
| ✅ | LiteLLM Routing | Centralized model config via Alpine.store | `97142cc` |
