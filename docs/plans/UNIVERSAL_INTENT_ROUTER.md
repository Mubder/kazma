# Universal Intent Router — Industrial Architecture Plan

**Status:** SUPERSEDED by `docs/plans/KAZMA_INTENT_ENGINE.md` — do not implement this plan.
**Superseded by:** [`docs/plans/KAZMA_INTENT_ENGINE.md`](KAZMA_INTENT_ENGINE.md) (2026-08-15).
**Why:** This plan produced a document-generation early-return wearing a
universal-router costume (fake 0.75 confidence, dead Tier 2, HITL-bypassing
handler, global newest-PDF fallback). The replacement is the product-wide
fail-safe engine spec.

**Date:** 2026-08-15
**Scope (historical):** Make the intent system the central routing brain — every task,
every pipeline, everything flows through it.

---

## The Problem

Kazma's supervisor is a single free-form loop. Every task — regardless of
type — enters the same iterate→tool→iterate→tool cycle. The model has to
figure out HOW to accomplish the goal through exploration. For open-ended
research this is correct. For structured tasks ("reproduce this PDF") it
burns 100 iterations on the wrong execution path.

**Current flow:**
```
User message
  → turn_input.py (intent: normal/continue/store/cleanup/multi_part/shift)
  → supervisor free-form loop (EVERYTHING goes here)
  → /research command → research pipeline (explicit only)
  → /swarm command → swarm dispatch (explicit only)
```

**Industry flow (target):**
```
User message
  → Universal Intent Router (classifies task TYPE + confidence)
  → "research"        → research pipeline (auto-routed)
  → "document"        → document pipeline (auto-routed)
  → "code"            → code execution path
  → "file_mgmt"       → file tools path
  → "swarm"           → swarm dispatch (auto-routed)
  → "analysis"        → analysis pipeline
  → "general/open"    → free-form agent loop (for genuinely open-ended tasks)
  → "continue"        → resume previous pipeline
```

The model is the CONTENT ENGINE (what to write, how to phrase it), not the
EXECUTION PLANNER (which tools to call in what order). Structured tasks get
deterministic pipelines; open-ended tasks get the free-form loop.

---

## Architecture

### 1. Intent Router (`kazma_core/agent/intent_router.py` — NEW)

Single entry point. Every user message passes through it before the
supervisor decides what to do.

```python
@dataclass(frozen=True)
class TaskIntent:
    """Classification result for one user turn."""
    category: str          # "document" | "research" | "code" | ...
    confidence: float      # 0.0–1.0
    pipeline: str | None   # suggested pipeline name (None = free-form)
    parameters: dict       # extracted params (format, source_path, deliver_to)
    reason: str            # human-readable why

def classify_task(
    text: str,
    *,
    messages: list[dict] | None = None,
    attachments: list[dict] | None = None,
) -> TaskIntent:
    """Classify a user turn into a task category + pipeline suggestion.

    Two-tier classification:
      1. Heuristic (fast, free): regex + keyword patterns per category
      2. LLM fallback (only when heuristic is ambiguous): one fast call

    Returns the highest-confidence intent. Unknown → general/free-form.
    """
```

**Intent categories** (each pipeline registers its patterns):

| Category | Triggers | Pipeline |
|----------|----------|----------|
| `document` | "reproduce/create/generate/make ... PDF/DOCX/report/document/spreadsheet" | document_pipeline |
| `research` | "research/investigate/find out/analyze topic/deep dive" | research_pipeline |
| `code` | "run/execute/write script/python/code" | code path |
| `file_mgmt` | "organize/move/copy/delete/rename files" | file tools |
| `swarm` | "dispatch/spawn/delegate to workers" | swarm dispatch |
| `analysis` | "analyze data/chart/compare/summarize dataset" | analysis |
| `general` | everything else, open-ended questions | free-form loop |
| `continue` | "continue/proceed/keep going/finish" | resume previous |

### 2. Pipeline Registry (`kazma_core/agent/pipeline_registry.py` — NEW)

Extensible registry. Each pipeline is a structured workflow that registers
its intent patterns, budget, and handler.

```python
@dataclass
class Pipeline:
    """A structured workflow for a class of tasks."""
    name: str                              # "document", "research", ...
    description: str                       # what it handles
    intent_patterns: list[IntentPattern]   # regex + keywords + negations
    handler: Callable[..., Awaitable[str]] # async execution function
    budget: PipelineBudget                 # tokens, iterations, timeout
    fallback_to_agent: bool = True         # escalate to free-form if needed

@dataclass
class PipelineBudget:
    max_tokens: int = 10_000
    max_steps: int = 10
    timeout_seconds: float = 300.0
    max_llm_calls: int = 5

class PipelineRegistry:
    """Central registry — pipelines self-register on import."""
    _pipelines: dict[str, Pipeline] = {}

    def register(self, pipeline: Pipeline) -> None: ...
    def match(self, intent: TaskIntent) -> Pipeline | None: ...
    def get(self, name: str) -> Pipeline | None: ...
    def list(self) -> list[Pipeline]: ...
```

### 3. Supervisor Integration (`graph_builder.py` — MODIFIED)

The supervisor's `_supervisor` node gets a routing branch BEFORE entering
the free-form tool loop:

```python
# In supervisor_node, after intent_mode classification:
task_intent = classify_task(text, messages=messages, attachments=...)

if task_intent.pipeline and task_intent.confidence >= CONFIDENCE_THRESHOLD:
    # Structured task → execute pipeline directly
    result = await execute_pipeline(task_intent, state)
    return {
        "messages": messages + [{"role": "assistant", "content": result}],
        "next_node": NodeName.RESPOND,
        "intent_mode": "normal",
    }
else:
    # Open-ended or ambiguous → free-form agent loop (current behavior)
    # ... existing supervisor logic ...
```

### 4. Document Pipeline (`kazma_core/agent/pipelines/document.py` — NEW)

The first new pipeline — 5 deterministic steps:

```python
async def document_pipeline(intent: TaskIntent, state: SupervisorState) -> str:
    """Deterministic document generation workflow.

    Steps:
    1. READ: read the source (file/document/attachment)
    2. EXTRACT: one LLM call to structure the content into markdown
    3. WRITE: file_write the markdown to disk
    4. GENERATE: generate_pdf/generate_docx with markdown_path
    5. DELIVER: send_file if a delivery target was detected
    """
```

### 5. Existing Pipeline Migration

| Pipeline | Current | After |
|----------|---------|-------|
| Research | `/research` command only | Auto-routed by intent + command still works |
| Swarm | `/swarm` command only | Auto-routed by intent + command still works |
| Document | Doesn't exist | New pipeline, auto-routed |
| Code | Free-form loop | Extracted as a pipeline |
| General | Everything | Free-form loop (unchanged, for open-ended) |

---

## Key Design Decisions

### Confidence threshold
- High confidence (≥0.8): route directly to pipeline, skip free-form
- Medium (0.5–0.8): route to pipeline BUT allow fallback to free-form
- Low (<0.5): free-form agent loop

### Two-tier classification
- **Tier 1 (heuristic, free, <1ms):** regex + keyword matching. Catches
  90% of clear cases ("make me a PDF", "research X", "run this script").
- **Tier 2 (LLM, one call, ~1s):** only when Tier 1 is ambiguous. Uses
  the active model with a short classification prompt.

### Pipeline → free-form escalation
If a pipeline's steps fail or the content requires more creativity than
the pipeline provides, it can escalate: `return await free_form_fallback(state)`.
The model then handles it with the existing loop.

### Free-form → pipeline delegation
The existing system-prompt iteration nudges already hint the model toward
structured tools. With the router in place, the system prompt can also
say: "If you detect a structured subtask (document generation, research),
pause and delegate to the appropriate pipeline."

### Budget enforcement
Each pipeline has its own budget. When exhausted, the pipeline returns
a partial result with a "budget exceeded" note — it doesn't silently
continue or crash. The user sees exactly what happened.

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `kazma_core/agent/intent_router.py` | NEW | TaskIntent + classify_task + two-tier classifier |
| `kazma_core/agent/pipeline_registry.py` | NEW | Pipeline + PipelineBudget + PipelineRegistry |
| `kazma_core/agent/pipelines/__init__.py` | NEW | Package init + pipeline auto-registration |
| `kazma_core/agent/pipelines/document.py` | NEW | Document generation pipeline (5 steps) |
| `kazma_core/agent/pipelines/code.py` | NEW | Code execution pipeline |
| `kazma_core/agent/graph_builder.py` | MODIFY | Add router branch in supervisor_node |
| `kazma_core/agent/turn_input.py` | MODIFY | Export classify_task for reuse |
| `kazma-gateway/agent_handler/graph.py` | MODIFY | Pass attachments to the router |
| `kazma-core/kazma_core/agent/tool_registry.py` | MODIFY | Register pipelines as callable tools |

---

## Effort Estimate

| Component | Estimate |
|-----------|----------|
| Intent router core (TaskIntent + two-tier classifier) | 3 hours |
| Pipeline registry + budget | 2 hours |
| Document pipeline | 3 hours |
| Code pipeline | 1 hour |
| Supervisor integration + routing branch | 2 hours |
| Research/swarm migration (register existing) | 1 hour |
| Tests | 2 hours |
| **Total** | **~14 hours (2 days)** |

---

## What This Gives You

| Before | After |
|--------|-------|
| "Reproduce this PDF" → 100 iterations, python_exec rabbit hole | → 5 deterministic steps, <10 iterations |
| "Research X" → must type `/research` | → auto-routed to research pipeline |
| Every task enters the same free-form loop | Structured tasks get pipelines; open-ended get the loop |
| Model chooses execution path (often wrong) | Router chooses; model fills content |
| One global iteration limit | Per-pipeline budgets |
| System-prompt rules the model ignores | The wrong tools aren't available in the pipeline |
