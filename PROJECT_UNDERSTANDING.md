# Kazma Project Understanding - Current State Analysis

## Overview

Kazma is a production-grade, domain-agnostic, open-source autonomous AI agent framework built on Python 3.11+ with asyncio-native architecture. It's designed for maximum reliability, durable execution, and massive extensibility.

**Status**: Production-Ready (Phase 5 Complete)
**Version**: 0.1.0 ALPHA
**Architecture**: Local-first, monorepo structure with 7 packages
**Test Suite**: 1125 tests (100% pass rate)
**Lines of Code**: ~35,800

---

## Project Structure

### Monorepo Packages (7)

```
kazma/
├── kazma-core/          # Core agent engine (domain-agnostic)
│   └── kazma_core/
│       ├── agent.py                 # Main ReAct loop + LangGraph state machine
│       ├── checkpoint.py            # SQLite checkpointing (SIGKILL-safe)
│       ├── compaction.py            # 80% context compaction engine
│       ├── authority.py             # Context authority enforcer
│       ├── tracing.py               # OpenTelemetry + Langfuse tracing
│       ├── cost_breaker.py          # $0.50 cost circuit breaker
│       ├── tone_adapter.py          # Cultural tone (Majlis protocol)
│       ├── dialect_detector.py      # Gulf dialect detection
│       ├── state.py                 # AgentState TypedDict
│       ├── llm_provider.py          # LLM provider abstraction
│       ├── tool_registry.py         # MCP tool registry
│       ├── mcp_client.py            # MCP protocol client
│       ├── tokenizer.py             # Dual-engine tokenizer
│       ├── kuwaiti_tokenizer.py     # Kuwaiti Arabic tokenizer
│       ├── msa_tokenizer.py         # MSA tokenizer
│       ├── router.py                # Dialect-aware routing
│       ├── majlis.py                # Majlis social protocol
│       ├── pacing.py                # Conversation pacing
│       ├── cultural_context.py      # Cultural context engine
│       ├── rbac.py                  # Role-based access control
│       ├── division_sandbox.py      # Division sandboxing
│       ├── authorization_flow.py    # Authorization flows
│       ├── audit_logger.py          # Audit logging
│       ├── recovery.py              # Startup recovery
│       ├── streaming.py             # Streaming responses
│       ├── token_counter.py         # Token counting
│       ├── hub/                     # Kazma Hub skill registry
│       │   ├── manifest_schema.py   # Skill manifest validation
│       │   ├── registry.py          # SQLite-backed skill registry
│       │   ├── versioning.py        # Semver + compatibility checks
│       │   ├── loader.py            # Dynamic skill loader
│       │   ├── validator.py         # Skill validation pipeline
│       │   ├── api.py               # REST API (FastAPI)
│       │   ├── badges.py            # Kazma-Certified badge system
│       │   ├── cli.py               # Hub CLI commands
│       │   └── __main__.py          # Hub main entry
│       ├── delegation/              # Agent-to-agent delegation
│       │   ├── protocol.py          # Delegation protocol
│       │   ├── discovery.py         # Agent discovery (mDNS)
│       │   ├── orchestrator.py      # Multi-agent orchestrator
│       │   ├── security.py          # Delegation security (signing)
│       │   └── swarm.py             # Swarm intelligence
│       ├── security/                # Security & certification
│       │   ├── linter.py            # Security linter (SEC001-SEC031)
│       │   ├── certification.py     # Kazma-Certified process
│       │   ├── audit_trail.py       # Security event audit trail
│       │   ├── dependency_scanner.py# Vulnerability scanner (OSV)
│       │   ├── disclosure.py        # Responsible disclosure
│       │   └── hardening.py         # Production hardening
│       ├── cli/                     # CLI infrastructure
│       │   └── wizard.py            # Skill installation wizard
│       └── docs/                    # Documentation system
│
├── kazma-memory/        # Memory engine (sqlite-vec + SQLite FTS5)
│   └── kazma_memory/
│       ├── search_backend.py        # SQLite-only search with FTS5 + BM25
│       └── arabic_tokenizer.py      # Arabic text processing
│       # REMOVED: tantivy_backend.py, migration.py, benchmark.py, report_store.py
│
├── kazma-skills/        # Skill system (YAML manifests)
│   └── kazma_skills/
│       └── manifest.py              # Skill manifest validation
│
├── kazma-connectors/    # External integrations (currently minimal)
│   └── kazma_connectors/
│       └── __init__.py
│
├── kazma-providers/     # LLM providers (LiteLLM router)
│   └── kazma_providers/
│       ├── base.py                  # Base provider interface
│       ├── router.py                # Multi-provider failover
│       └── __init__.py
│
├── kazma-ui/            # FastAPI + HTMX dashboard (Arabic RTL)
│   └── kazma_ui/
│       ├── __init__.py
│       └── app.py                   # FastAPI application factory
│
├── kazma-tui/           # Textual TUI with Arabic/RTL
│   └── kazma_tui/
│       ├── __init__.py
│       └── tui.py                    # Terminal UI
│
├── kazma-cli/           # CLI entry point
│   └── kazma_cli/
│       └── main.py                  # `kazma` command tree
│
├── examples/            # Reference implementations
│   └── almuhalab_custom_skills/     # ALMuhalab-specific skills
│       ├── drone_inspection/        # FPV drone telemetry + YOLO
│       ├── trading_intel/           # Market data + trading loops
│       ├── branding/                # Division branding
│       ├── asset_generation/        # Image/video generation
│       └── tests/                   # Example skill tests
│
├── tests/               # 1125 tests (pytest + asyncio)
│   ├── test_checkpoint.py
│   ├── test_sqlite_search_backend.py  # New SQLite FTS5 tests
│   └── [many more test files...]
│
├── docs/                # Documentation
│   ├── docs/
│   │   ├── getting-started/
│   │   ├── api-reference/
│   │   ├── skill-development/
│   │   ├── core-concepts/
│   │   ├── kazma-hub/
│   │   ├── security/
│   │   └── contributing/
│   └── skill-manifest-spec.md
│
├── .github/workflows/   # CI/CD pipelines
│   └── ci.yml                     # GitHub Actions CI
│
├── kazma.yaml           # Root configuration
├── serve.py             # Web UI server script
├── setup.sh             # Bootstrap installation script
└── README.md            # Project documentation
```

---

## Core Components

### 1. Agent Loop (kazma-core/kazma_core/agent.py)
- **Architecture**: LangGraph ReAct loop (think → act → observe)
- **Maximum iterations**: 10
- **Checkpointing**: SQLite-based, SIGKILL-safe
- **Cost control**: $0.50 circuit breaker with 5-minute silence window
- **Key features**:
  - LLM integration via LLMProvider
  - MCP tool execution via ToolRegistry
  - Context authority enforcement at 80% threshold
  - Startup recovery from checkpoints

### 2. Memory System (kazma-memory/kazma_memory/)
- **Architecture**: SQLite FTS5 + sqlite-vec vector search
- **Arabic support**: Native Arabic tokenizer (MSA + Kuwaiti dialects)
- **Search**: Hybrid BM25 (keyword) + vector similarity (semantic)
- **Key features**:
  - SQLite FTS5 virtual table with automatic triggers
  - Arabic text normalization (Alef variants, diacritics removal)
  - Stop words filtering (including Kuwaiti dialect terms)
  - Stemming for better matching
  - Zero external dependencies (no Rust/maturin)

### 3. Cultural Intelligence
- **Dialect routing**: MSA vs Gulf (Kuwaiti) dialect detection
- **Majlis protocol**: Cultural conversation pacing and tone
- **Arabic tokenizers**: Dual-engine (Kuwaiti + MSA)
- **RTL support**: Full right-to-left layout for Arabic interfaces

### 4. Security & Access Control
- **RBAC**: Role-based access control
- **Division sandboxing**: Multi-tenant isolation (Oil & Gas, Tourism, Trading)
- **Security linter**: SEC001-SEC031 rules
- **Kazma-Certified**: Skill certification system
- **Audit trail**: Security event logging

### 5. MCP Integration
- **MCP client**: Full Model Context Protocol support
- **Tool registry**: Dynamic tool discovery and execution
- **177,000+ tools**: Access to entire MCP ecosystem
- **Zero vendor lock-in**: Standard protocol compliance

### 6. Kazma Hub
- **Skill registry**: SQLite-backed skill registry
- **Manifest validation**: YAML schema validation
- **Version management**: Semver + compatibility checks
- **REST API**: FastAPI-based hub API
- **Certification**: Kazma-Certified badge system

### 7. Interfaces
- **Web UI**: FastAPI + HTMX (Arabic RTL, Linear design)
- **TUI**: Textual terminal UI with Arabic/RTL support
- **CLI**: Command-line interface (`kazma` command)

---

## Configuration (kazma.yaml)

```yaml
agent:
  name: "kazma"
  version: "0.1.0"
  language: "ar"          # Arabic
  rtl: true               # Right-to-left

models:
  default: "gpt-4o-mini"
  router: "litellm"      # Multi-provider failover
  fallback: "gpt-4o-mini"

llm:
  base_url: "https://api.openai.com/v1"
  api_key: ""             # Set via OPENAI_API_KEY env var
  model: "gpt-4o-mini"
  max_tokens: 4096
  temperature: 0.7

mcp:
  servers:
    - name: "filesystem"
      transport: "stdio"
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

memory:
  enabled: true
  max_context_tokens: 128000
  retrieval_top_k: 5

ui:
  host: "0.0.0.0"
  port: 8000
  rtl: true
```

---

## Dependencies (pyproject.toml)

### Core Dependencies
- **LangGraph**: Agent state machine
- **sqlite-vec**: Vector similarity search
- **aiosqlite**: Async SQLite
- **FastAPI + Uvicorn**: Web UI
- **Textual**: TUI
- **Langfuse**: Observability
- **OpenTelemetry**: Distributed tracing
- **Cryptography**: Security
- **PyYAML**: Configuration

### Optional Dependencies
- **dev**: pytest, pytest-asyncio, ruff, mypy
- **cli**: click, rich
- **tui**: textual, python-bidi

### NOT Included (Architectural Decision)
- **Tantivy**: Removed - replaced with SQLite FTS5 (no Rust dependencies)

---

## Recent Architectural Changes

### Tantivy Removal (January 2025)
- **Decision**: Lead Architect override for edge deployment optimization
- **Removed**: All Tantivy dependencies (tantivy-py, migration, benchmark)
- **Added**: SQLite FTS5 with Arabic tokenization
- **Benefits**: Zero external build dependencies, simpler deployment
- **Documentation**: ARCHITECTURE_CHANGE.md provides full details

---

## Entry Points

### CLI Commands
```bash
kazma serve           # Start Web UI
kazma tui            # Start TUI
kazma wizard         # Skill installation wizard
kazma hub [command]  # Hub commands (search, install, list, etc.)
```

### Python Entry Points
```bash
python -m kazma_core.agent           # Run agent (CLI)
python -m kazma_tui.tui              # Run TUI
python serve.py                      # Run Web UI
python -m kazma_ui.app --port 8080    # Web UI custom port
```

---

## Test Suite

- **Total Tests**: 1125
- **Framework**: pytest + pytest-asyncio
- **Coverage**: Core agent, memory, Arabic tokenization
- **CI**: GitHub Actions (lint + test jobs)
- **Status**: 100% pass rate

---

## Key Files for Modification

When making changes, these are the most important files:

1. **kazma.yaml** - Agent configuration
2. **kazma-core/kazma_core/agent.py** - Main agent loop
3. **kazma-core/kazma_core/state.py** - Agent state structure
4. **kazma-memory/kazma_memory/search_backend.py** - Search implementation
5. **kazma-memory/kazma_memory/arabic_tokenizer.py** - Arabic processing
6. **kazma-ui/kazma_ui/app.py** - Web UI application
7. **kazma-tui/kazma_tui/tui.py** - Terminal UI

---

## Documentation Files

- **README.md** - Project overview and quickstart
- **ARCHITECTURE_CHANGE.md** - Tantivy removal documentation
- **BUG_FIX_TASK.md** - Bug fix task history
- **KAZMA_PROJECT_SUMMARY.md** - Complete project summary
- **docs/** - Comprehensive documentation site

---

## Development Guidelines

### Running the Project
```bash
# Setup
./setup.sh  # or uv sync

# Run agent
python -m kazma_core.agent

# Run Web UI
python serve.py  # or kazma-web

# Run TUI
python -m kazma_tui.tui  # or kazma-tui

# Tests
pytest tests/
pytest tests/test_sqlite_search_backend.py -v

# Lint
ruff check .
ruff format .
```

### Code Style
- **Line length**: 120 characters
- **Python version**: 3.11+
- **Linter**: ruff (E, F, I, N, W, UP)
- **Type checker**: mypy (strict mode)
- **Import ordering**: stdlib → third-party → local

---

## Current State Summary

### ✅ Working
- All 1125 tests passing
- GitHub CI passing (lint + test)
- Web UI functional
- TUI functional
- SQLite FTS5 search working
- Arabic tokenization working
- MCP integration working

### 🎯 Ready for
- New feature development
- Bug fixes
- Skill development
- Connector additions
- Provider additions

### 📝 Notes for Next Development
- Zero external search dependencies (SQLite-only)
- Arabic support is core (MSA + Kuwaiti)
- Edge deployment optimized (no Rust/maturin)
- MCP is primary tool integration method
- Context authority prevents context window exhaustion
