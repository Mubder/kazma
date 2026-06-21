# Kazma - 🇰🇼 - كاظمة

**Status: ALPHA — Stable Architecture, Experimental API**

Autonomous AI agent framework — Python 3.11+, asyncio-native, sqlite-vec only.

![Tests](https://img.shields.io/badge/tests-1082_passing-brightgreen)
![Version](https://img.shields.io/badge/version-0.1.0--alpha-orange)
![License](https://img.shields.io/badge/license-MIT-blue)

---

## 🌍 Overview

Kazma is a production-grade, domain-agnostic, open-source framework for building reliable AI agents. We built Kazma because the gap between "cool demo" and "reliable agent" is enormous.

**Core Pillars:**

*   **Durable Execution**: Built on LangGraph/SQLite, Kazma supports checkpointing that survives SIGKILL. Your agents resume mid-task, never losing state.
*   **Context Authority**: Implements a strict 80% compaction loop, preventing context window exhaustion and hallucination spirals.
*   **Cultural Moat**: Native support for Arabic (MSA/Gulf dialects) with a "Majlis Mode" protocol for culturally appropriate conversational pacing.
*   **MCP Interoperability**: Native Model Context Protocol (MCP) support — access 177,000+ ecosystem tools with zero vendor lock-in.

---

## 🇰🇼 نظرة عامة (Arabic Overview)

كاظمة هو إطار عمل مفتوح المصدر ومستقل لبناء وكلاء ذكاء اصطناعي (AI Agents) موثوقين وقابلين للتطوير. تم تصميم كاظمة للبيئات التي تتطلب دقة عالية وتوافقاً ثقافياً.

**لماذا كاظمة؟**

*   **التنفيذ المتين**: حفظ تلقائي للحالة؛ إذا تعطل النظام، يكمل الوكيل عمله من نفس النقطة.
*   **سلطة السياق**: آلية ذكية لتلخيص المحادثات والحفاظ على المعلومات المهمة.
*   **هوية ثقافية**: دعم أصيل للغة العربية (الفصحى واللهجة الكويتية) مع بروتوكول "المجلس".
*   **تكامل MCP**: توافق كامل مع بروتوكول سياق النموذج (MCP).

---

## 🏗 Architecture

```
kazma-core/          Agent loop (ReAct), checkpointing, compaction, authority
kazma-memory/        sqlite-vec vector store, retrieval, provenance tagging
kazma-skills/        YAML manifests wrapping MCP tools, certified servers
kazma-connectors/    Telegram, Discord, Slack adapters
kazma-providers/     LiteLLM router, model switching, provider abstraction
kazma-ui/            FastAPI + HTMX dashboard (Arabic RTL, Linear design)
kazma-cli/           CLI entry point: install, diagnostics, hub commands
tests/               1082 tests — pytest + asyncio
```

---

## 📦 Installation & Setup

### Prerequisites

- **Python 3.11+** (3.11 or 3.12 recommended)
- **uv** (preferred) or **pip**
- **SQLite 3.35+** (included with Python)
- **Git**

### Quick Install

```bash
# Clone the repository
git clone https://github.com/Mubder/kazma.git
cd kazma

# Run the certified bootstrap script
chmod +x setup.sh && ./setup.sh
```

The `setup.sh` script performs a deterministic, fail-fast initialization:
1. Verifies Python 3.11+ and uv are installed
2. Syncs all dependencies from `pyproject.toml`
3. Validates sqlite-vec, aiosqlite, and LangGraph are loadable
4. Reports the total test count

If any step fails, the script exits immediately with an actionable error message.

#### Manual Install (alternative)

```bash
# Install with uv (recommended)
uv sync

# Or with pip
pip install -e ".[dev,cli]"
```

### Optional Dependencies

```bash
# Tantivy high-performance search (Rust-backed FTS)
pip install -e ".[tantivy]"

# Langfuse observability dashboard
pip install langfuse

# Arabic dialect detection (fastText)
pip install fasttext
```

### Configuration

Kazma is configured via `kazma.yaml` at the project root:

```yaml
agent:
  name: "kazma"
  version: "0.1.0"
  language: "ar"          # default interface language
  rtl: true

models:
  default: "gpt-4o-mini"  # default LLM
  router: "litellm"        # model router

storage:
  engine: "sqlite-vec"
  path: "kazma-data/checkpoints.db"

memory:
  enabled: true
  max_context_tokens: 128000
  retrieval_top_k: 5

logging:
  langfuse:
    enabled: false
    public_key: ""         # or set LANGFUSE_PUBLIC_KEY env var
    secret_key: ""         # or set LANGFUSE_SECRET_KEY env var
```

For local overrides, copy to `kazma.local.yaml` (git-ignored). Environment variables take precedence over YAML.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `KAZMA_TRACING_BACKEND` | Tracing backend (`langfuse`, `opentelemetry`, `console`) | `console` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key | — |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key | — |
| `LANGFUSE_HOST` | Langfuse server URL | `http://localhost:3000` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry collector endpoint | `localhost:4317` |
| `KAZMA_HUB_URL` | Kazma Hub registry URL | `https://hub.kazma.dev` |

### Running Tests

```bash
# Full test suite (1082 tests, 14 skipped — tantivy, 2 deselected — bug fixes)
pytest tests/

# With coverage
pytest --cov=kazma_core --cov-report=html tests/

# Specific test file
pytest tests/test_checkpoint.py -v

# Run only regression tests (bug fixes)
pytest tests/test_bug_regression.py -v

# Run only our new tests (TraceStore, ConfigStore, Integration, E2E, Error coverage)
pytest tests/test_tracestore.py tests/test_config_store.py tests/test_integration.py tests/test_e2e.py tests/test_error_coverage.py -v
```

### Development

```bash
# Lint
ruff check .
ruff format .

# Type check
mypy kazma-core/kazma_core/

# Run the agent (interactive)
python -m kazma_core.agent
```

### Project Structure

```
kazma-core/              Agent loop (ReAct), checkpointing, compaction, authority
│   └── kazma_core/
│       ├── agent.py         # Main ReAct loop + LangGraph state machine
│       ├── checkpoint.py    # SQLite checkpointing (SIGKILL-safe)
│       ├── compaction.py    # Context compaction engine
│       ├── authority.py     # 80% context authority enforcer
│       ├── tracing.py       # Langfuse + OpenTelemetry + TraceStore
│       ├── cost_breaker.py  # Cost circuit breaker ($0.50 threshold)
│       ├── tone_adapter.py  # Cultural tone (Majlis protocol)
│       ├── dialect_detector.py  # Gulf dialect detection
│       ├── hub/             # Kazma Hub skill registry
│       ├── delegation/      # Agent-to-agent delegation protocol
│       └── security/        # Security linter + certification
├── kazma-memory/            # sqlite-vec vector memory + Arabic tokenizer
├── kazma-skills/            # Skill manifests + certified MCP servers
├── kazma-connectors/        # Platform adapters (Telegram, Discord, Slack)
├── kazma-providers/         # LiteLLM router (multi-provider failover)
├── kazma-ui/                # FastAPI + HTMX dashboard (RTL, Linear design)
├── kazma-cli/               # CLI (`kazma` command)
├── tests/                   # 1082 tests (pytest + asyncio)
├── docs/                    # Documentation
├── kazma.yaml               # Root configuration
└── KAZMA_PROJECT_SUMMARY.md # Full project summary
```
└── KAZMA_PROJECT_SUMMARY.md # Full project summary

## 🆕 Latest Features (Hermes_API_2 Merge)

| Feature | Description |
|---------|-------------|
| **LiteLLM Router** | Multi-provider failover (OpenAI, Anthropic, local) |
| **Skills Framework** | YAML manifests + certified MCP servers |
| **Memory System** | sqlite-vec + Tantivy full-text search + Arabic tokenizer |
| **Real-time Dashboard** | WebSocket live traces, metrics, cost tracking |
| **Multi-Agent Monitoring** | Hub agent discovery, network visualization |
| **Notification System** | Toast notifications with WebSocket feed |
| **Light/Dark Theme** | Linear design system with theme toggle |
| **Arabic RTL** | Full RTL support with dynamic language switching |
| **Circuit Breaker** | Auto-halt on $0.50 cost threshold |
| **Error Handling** | Global 404/500 handlers with friendly error pages |

## 🧪 Latest Test Coverage

```
1082 passed  ✅
  14 skipped (tantivy — optional Rust dependency)
   2 deselected (pre-existing bug fixes)
  ─────────────────────────────────
1098 total tests
```

New test files added:
- `test_tracestore.py` — 15 tests for in-memory TraceStore
- `test_config_store.py` — 9 tests for ConfigStore
- `test_integration.py` — 21 tests for FastAPI routes
- `test_e2e.py` — 14 end-to-end agent lifecycle tests
- `test_error_coverage.py` — 27 edge case tests
```

---

## 📜 License

MIT
