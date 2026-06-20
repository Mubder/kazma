# Kazma - 🇰🇼 - كاظمة

**Status: ALPHA — Stable Architecture, Experimental API**

Autonomous AI agent framework — Python 3.11+, asyncio-native, sqlite-vec only.

![Tests](https://img.shields.io/badge/tests-979_passing-green)
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
kazma-ui/            FastAPI + HTMX dashboard (Arabic RTL)
kazma-cli/           CLI entry point: install, diagnostics, hub commands
tests/               979 tests — pytest + asyncio
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

# Install with uv (recommended — fast, deterministic)
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
# Full test suite (979 tests)
pytest tests/

# With coverage
pytest --cov=kazma_core --cov-report=html tests/

# Specific test file
pytest tests/test_checkpoint.py -v

# Run only regression tests (bug fixes)
pytest tests/test_bug_regression.py -v
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
kazma/
├── kazma-core/              # Core engine (domain-agnostic)
│   └── kazma_core/
│       ├── agent.py         # Main ReAct loop + LangGraph state machine
│       ├── checkpoint.py    # SQLite checkpointing (SIGKILL-safe)
│       ├── compaction.py    # Context compaction engine
│       ├── authority.py     # 80% context authority enforcer
│       ├── tracing.py       # Langfuse + OpenTelemetry tracing
│       ├── cost_breaker.py  # Cost circuit breaker ($0.50 threshold)
│       ├── hub/             # Kazma Hub skill registry
│       ├── delegation/      # Agent-to-agent delegation protocol
│       ├── security/        # Security linter + certification
│       └── search/          # Tantivy high-performance search
├── kazma-memory/            # sqlite-vec vector memory store
├── kazma-skills/            # Skill manifests + certified MCP servers
├── kazma-connectors/        # Platform adapters (Telegram, Discord, Slack)
├── kazma-providers/         # LLM provider abstraction
├── kazma-ui/                # Arabic RTL dashboard
├── kazma-cli/               # CLI (`kazma` command)
├── examples/                # Reference implementations
│   └── almuhalab_custom_skills/
├── tests/                   # 979 tests
├── docs/                    # Documentation site
├── kazma.yaml               # Root configuration
├── SECURITY.md              # Security policy
├── CONTRIBUTING.md          # Contribution guidelines
└── KAZMA_PROJECT_SUMMARY.md # Full project summary
```

---

## 📜 License

MIT
