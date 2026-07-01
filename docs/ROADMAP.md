# Kazma Roadmap

## v1.0 — Core Engine ✅
- Autonomous AI agent framework with ModelRegistry singleton
- Swarm orchestration (InProcess + Telegram workers)
- 4-layer memory (ChromaDB, Graph, FTS5, sqlite-vec)
- Self-improvement engine with LLM Meta-Refiner
- HITL approval queue for Soul Evolution
- Dialect-aware router (Kuwaiti + MSA pipelines)
- Tool registry with permission gating
- SSRF + AST-based security validation
- Web UI, TUI, and Telegram interfaces
- **Universal Model Registry** — global model routing, durable discovery, cross-UI sync
- **design-b-modern** branding — grid background, cyan accent, reactive font persistence

---

## v2.0 — Global Localization Architecture *(planned — UP NEXT)*

### Framework-Wide Persona Toggle

**Objective:** Hot-swap the entire agent persona between cultural contexts
without restarting the engine.

**1. Global Context Manager**
A singleton `CulturalContextManager` broadcasts the active persona state:

```python
class CulturalContext(Enum):
    ENGLISH_PROFESSIONAL = "en-pro"
    ARABIC_MAJLIS       = "ar-majlis"
    KUWAITI_DIWANIYA    = "kw-diwaniya"
```

Every subsystem listens to `ctx.active_culture` and adapts automatically.

**2. Tri-Layer Propagation**

| Layer | Mechanism |
|:---|:---|
| **UI Layer** | Swaps locale JSONs, applies RTL/LTR CSS via `dir` attribute |
| **Cognitive Layer** | Hot-swaps worker `SOUL.md` system prompts to match cultural tone and dialect |
| **Memory Layer** | Injects `culture` metadata filters into ChromaDB/FTS5 queries for language-matched RAG |

**3. State Persistence**
- `cultural_context` saved to ConfigStore (`cultural.context = "ar-majlis"`)
- Survives restarts via `get_config_store()`

**4. Trigger Points**
- UI toggle button (Web + TUI)
- Telegram `/culture ar-majlis` command
- API: `POST /api/culture { context: "ar-majlis" }`
- Worker dispatch auto-inherits active culture from context manager

### v2.0 Milestones
- [ ] `CulturalContextManager` singleton in `kazma-core/culture/`
- [ ] `SOUL.md` hot-swap in `WorkerRegistry`
- [ ] RTL/LTR CSS toggle + locale JSON swap in Web UI
- [ ] FTS5/ChromaDB culture filter injection
- [ ] Telegram `/culture` command
- [ ] TUI culture indicator + toggle (`Ctrl+K`)
