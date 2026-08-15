# Kazma Intent Engine — Implementation Spec

**Status:** Ready to implement. This document is the **single source of truth**.
**Date:** 2026-08-15
**Supersedes:** `docs/plans/UNIVERSAL_INTENT_ROUTER.md` (that plan produced the current costume router; do not implement it).
**Audience:** A coding agent that has not seen the design conversation. Follow this file only.

---

## 0. Mission

Build the **product-wide intent engine** for Kazma: one classification + one policy + one dispatch decision on **every** user turn, for every act type (document, research, swarm, code, files, analysis, memory focus, reminders) — **fail-safe**.

The engine is always on. It does **not** replace LangGraph, SwarmEngine, `run_research_pipeline`, Document Intelligence, HITL, or the commitment layer. It **classifies** the turn and either:

| Route | Meaning |
|---|---|
| `execute` | Closed task. Run a registered handler. Handler mutates **only** via `LocalToolRegistry.execute` / `tool_executor.execute`. |
| `constrain` | Stay in the supervisor loop. Inject a binding plan note so the existing brain uses the right tools. |
| `loop` | Default. Free-form supervisor. Used whenever anything is missing, ambiguous, multi-act without a composer, or unsafe. |

“Main engine” = **one SoT every surface consumes**. It does **not** mean “every utterance skips the tool loop.”

### Success (the whole product)

1. Every graph turn writes one `TurnDecision` onto `SupervisorState` (even general chat: `route=loop`).
2. Closed structured tasks (e.g. “reproduce this attached PDF”) finish without a 100-iteration wander **and** still hit HITL + commitment.
3. Open / multi-step / ambiguous tasks stay in the supervisor with a binding plan.
4. False-positive corpus is CI-gated. “build a PDF parser” must never `execute`.
5. One research path, one document-generate path, one swarm path — the engine **points at** existing machinery.
6. Classifier/handler errors fail-open to `loop`. Kill-switches work.

---

## 1. Do not build (explicit)

If you find yourself doing any of these, stop and re-read §0.

- LLM-first classification on every turn (Tier 2 is gray-zone only).
- A new Pipeline DAG / workflow orchestrator. LangGraph is the open orchestrator. `pipeline_schema.PipelineDAG` is the **visual swarm sandbox** — different type, do not reuse the name for intent.
- Auto `SwarmEngine.dispatch` from natural language.
- Auto `python_exec` / `shell_exec` / `schedule_task` / `file_delete` from natural language.
- Entity resolution via `SessionStore` (that store is platform IDs + 5 min TTL; Agents.md §2).
- Registering research/code/analysis **execute** handlers against the current 1-hit regex.
- Using Document Intelligence ingest (`DocumentIngestionService`) as a PDF factory.
- Calling `file_write()`, `generate_pdf()`, `send_file_message()`, `python_exec()` as plain functions from a handler.
- Global “newest PDF in `kazma-data/attachments`” fallback.
- A second `continue` regex. Focus continue lives in `classify_turn_intent` only.
- Putting `chat_id` / `user_id` / `message_id` on the decision or graph state.
- Folding `/hitl` `/steer` `/abort` `/replay` `/fork` `/undo` `/yolo` `/long` into act kinds.
- Merging `ModelRouter.classify` (model profile: vision/coding/reasoning) into user intent.
- Unifying TUI onto the supervisor in Phase 0–2 (known hole; Phase 4).
- Implementing Gemini’s “Phase 1 LLM JSON + Phase 2 DAG” blueprint.

---

## 2. What exists today (read before editing)

### 2.1 Two classifiers that disagree

| Layer | File | Returns | Effect |
|---|---|---|---|
| **Focus** | `kazma-core/kazma_core/agent/turn_input.py` → `classify_turn_intent` | `continue\|store\|cleanup\|multi_part\|shift\|normal` | Recall policy, tool stubbing, task lifecycle. **Keep. Tests must stay green.** |
| **Task type** | `kazma-core/kazma_core/agent/intent_router.py` → `classify_task` | single category + fake confidence | Hard bypass if a pipeline is registered |

Supervisor hook: `graph_builder.py` ~807–862. Calls **sync** `classify_task` (Tier 2 is a `pass`). On hit, runs `document_pipeline` and returns `RESPOND` **without** merging `intent_patch`.

### 2.2 Bugs the first PR must stop

These are measured, not theoretical. A probe on 2026-08-15 produced:

| Utterance | Today | Required |
|---|---|---|
| `reproduce this PDF with better templates` | document 0.95 execute | document_generate, execute **only if** source resolved |
| `build a PDF parser in Python` | document 0.95 execute | **not** document execute (code or loop) |
| `research this topic and make a PDF` | document 0.95 (research dropped) | **multi-act** research + document_generate → constrain |
| `dispatch a worker to create a PDF` | document 0.95 | multi-act swarm + document → constrain |
| `rebuild the document index` | document 0.95 | **not** document (`docx?` matches “document”) |
| `format the documents folder` | document 0.95 | file_mgmt or loop |
| `write me a PDF of the notes` | general | document_generate (add `write`/`draft` verbs) |
| `create a report about climate` | general | loop or constrain research — English `report` is **not** a format |
| `أكمل هذا المستند PDF` / `أنجز تقرير PDF` | continue (second regex) | document_generate act, **not** focus-continue |
| `I have a python question` | code 0.75 should_route | loop |
| `please run the tests` | code 0.75 | loop (HITL `run_tests` if the model asks) |
| `dont just read this PDF, create a new one` | general (whole-string negation) | document_generate |
| `read this PDF` / `what is this PDF about?` | general | loop (not generate) |

Root causes in `intent_router.py`:

- `_score`: 1 hit = 0.75 = `CONFIDENCE_THRESHOLD`. Every keyword “routes.”
- `_DOC_FORMAT_RE` uses `docx?` so `\bdoc` matches **document**.
- Categories are exclusive first-match; document wins.
- Gray zone `0.4 < c < 0.75` is empty (`no match` = 0.40; empty input = 0.50 then `len>20` fails).
- `_CONTINUE_RE` Arabic alts are a second continue detector that swallows generate intents.
- `classify_task` accepts `llm=` and `messages=` and ignores both.

Document pipeline (`pipelines/document.py`):

- Calls `file_write` / `generate_*` / `send_file_message` **directly** (HITL bypass; `file_write` is in `CANONICAL_DANGER_TOOLS`).
- Absolute path that `is_file()` is read with no workspace check.
- `_find_source_in_history` last step: newest `kazma-data/attachments/*.pdf` by mtime (cross-session leak).
- Fitz fallback applies `bidi.get_display` (logical→visual) on already-visual text. Wrong. Render already shapes in `documents/rich_render.py`.
- LLM extract prompt says “condense, do NOT reproduce every word.”
- `powerpoint`/`slide` silently become `generate_pdf`.
- XLSX dumps `structured_md[:500]` into one cell.
- `"Error" in write_result` is a substring trap.
- `PipelineBudget` is never read.

Research already exists and is better than a stub pipeline:

- `research_policy.py`: `is_deep_research_intent`, `deep_research_route_hint`, `should_prefer_pipeline`
- Tool: `kazma_core.tools.research_pipeline.run_research_pipeline`
- Session: `kazma_core.tools.research_session.start_deep_research`
- Slash: gateway `_try_research_command` → `start_deep_research`; SSE/WS `/research` → inline `run_research_pipeline` (two implementations)

Swarm SoT: `SwarmEngine.dispatch`. Do not call from NL.

### 2.3 Safety invariants (Agents.md — do not break)

- §2 Platform isolation: no `chat_id` in graph state.
- §7 HITL: graph `interrupt()` (chat), `LocalToolRegistry.execute` → `safety.check()` (IDE/swarm), pipeline checkpoints. All three stay wired.
- §10 IDE: mutating ops go through `_call_tool` / `execute`. Workspace via `resolve_active_root()`.
- §20 Commitment: `authorize_effect` before HITL on the graph path; `execute()` already audits. Handlers must use `execute()` so this stays on.
- §3 `turn_failed` / `LLMError.transient`: do not synthesize over a broken turn.
- §16 cron: `delivery_target` at schedule time — remind is **never** auto-executed from the intent engine in Phases 0–3.
- Windows selector loop: no `asyncio.create_subprocess_*`; use `to_thread` + `subprocess`.

### 2.4 Four things named “pipeline” (do not merge)

1. Intent handlers (`agent/pipelines/*` / new `intent/handlers/`) — **this project**.
2. Research `run_research_pipeline` — existing tool. Delegate.
3. Swarm `TaskType.PIPELINE` + visual `pipeline_schema.PipelineDAG` — worker DAG sandbox.
4. Document Intelligence job state machine — durable ingest. Not generate.

---

## 3. Target architecture

```
Transport (SSE / WS / gateway / later TUI)
    │  pins working memory (attachments, goal, constraints)
    ▼
classify_turn(...)                    # ALWAYS, iteration 0
    │  1. focus = classify_turn_intent(...)     # existing, unchanged semantics
    │  2. acts  = heuristics (multi-label) [+ Tier 2 if gray]
    │  3. entities = resolve(attachments, workspace)  # never global mtime
    │  4. route = policy(focus, acts, entities, registry, kill-switches)
    ▼
TurnDecision { focus, acts, entities, route, handler, reason, plan_note }
    │  written onto SupervisorState
    │
    ├─ execute  → handler.run(ctx) via tool_executor
    │               ok → RESPOND (merge intent_patch)
    │               fail → fall through to loop
    ├─ constrain → append plan_note system message, then existing supervisor
    └─ loop      → existing supervisor (default)
```

Existing consumers keep working, but read **this** decision instead of re-detecting:

- Focus lifecycle / stub / recall (`intent_mode` = `decision.focus`)
- `deep_research_route_hint` (Phase 2: replace with `plan_note` when `research_deep` in acts)
- Tool constraint filter (unchanged; still from `hard_constraints`)

---

## 4. Package layout

Create `kazma-core/kazma_core/agent/intent/`. Keep `intent_router.py` as a **compat façade** (see §11).

```
kazma_core/agent/intent/
    __init__.py          # public API only (list in §4.1)
    types.py             # enums + dataclasses
    classify.py          # classify_turn / classify_turn_sync
    heuristics.py        # multi-label act detectors (high precision)
    llm.py               # Tier 2 structured JSON, gray zone only
    entities.py          # file/path resolution
    policy.py            # ONLY place that may set route=execute
    registry.py          # IntentHandler registry (replaces pipeline_registry guts)
    config.py            # kill-switches + thresholds (live ConfigStore, never raises)
    handlers/
        __init__.py
        document.py      # move/adapt pipelines/document.py
        research.py      # Phase 2
        compose.py       # Phase 3 research_then_document
```

`pipeline_registry.py`: become a thin delegate to `intent.registry` **or** keep `get_registry()` as an alias that returns the new registry. Do not leave two singletons.

`pipelines/document.py`: re-export `document_pipeline` / `register` from `intent.handlers.document` for one release, then delete the body.

### 4.1 Public API (`intent/__init__.py`)

```python
__all__ = [
    "classify_turn",
    "classify_turn_sync",
    "TurnDecision",
    "IntentAct",
    "EntitySet",
    "ResolvedFile",
    "RouteKind",
    "ActKind",
    "HandlerResult",
    "IntentHandler",
    "get_registry",
    "EXECUTE_MIN",
]
```

No other module should import `heuristics` / `policy` except tests and `classify.py`.

---

## 5. Types (`intent/types.py`)

Use `from __future__ import annotations`. Frozen dataclasses. `StrEnum` for kinds.

```python
from enum import StrEnum
from dataclasses import dataclass, field
from typing import Any


class RouteKind(StrEnum):
    EXECUTE = "execute"
    CONSTRAIN = "constrain"
    LOOP = "loop"


class ActKind(StrEnum):
    DOCUMENT_GENERATE = "document_generate"
    DOCUMENT_INTEL = "document_intel"
    RESEARCH = "research"
    RESEARCH_DEEP = "research_deep"
    SWARM = "swarm"
    CODE_EXEC = "code_exec"
    FILE_MGMT = "file_mgmt"
    ANALYSIS = "analysis"
    REMIND = "remind"
    GENERAL = "general"


# Focus is NOT an ActKind. It stays the turn_input string:
# continue | store | cleanup | multi_part | shift | normal


EXECUTE_MIN = 0.86          # policy: below this, never execute
TIER2_LOW = 0.35
TIER2_HIGH = 0.80
WEAK_KEYWORD = 0.45
HIGH_PRECISION = 0.86
NO_MATCH = 0.35


@dataclass(frozen=True)
class IntentAct:
    kind: str
    confidence: float
    slots: dict[str, Any] = field(default_factory=dict)
    source: str = "heuristic"   # heuristic | llm | command


@dataclass(frozen=True)
class ResolvedFile:
    path: str                   # absolute, access-checked
    filename: str
    mime: str = ""
    source: str = "attachment"  # attachment | workspace | explicit


@dataclass(frozen=True)
class EntitySet:
    files: tuple[ResolvedFile, ...] = ()
    unresolved: tuple[str, ...] = ()     # required slot names missing
    ambiguous: tuple[str, ...] = ()      # slot names with N>1 candidates


@dataclass(frozen=True)
class TurnDecision:
    focus: str
    acts: tuple[IntentAct, ...]
    entities: EntitySet
    route: RouteKind
    handler: str | None
    reason: str
    plan_note: str = ""
    source: str = "heuristic"            # heuristic | llm | mixed | command

    @property
    def primary(self) -> IntentAct | None:
        non_gen = [a for a in self.acts if a.kind != ActKind.GENERAL]
        if not non_gen:
            return None
        return max(non_gen, key=lambda a: a.confidence)


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    message: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    escalate: bool = False               # True → supervisor must fall through
```

---

## 6. Config and kill-switches (`intent/config.py`)

Mirror `get_hitl_config` / `get_proxy_provider`: **live read, never raise**.

| Env | ConfigStore | Default | Effect |
|---|---|---|---|
| `KAZMA_INTENT_ENGINE=0` | `agent.intent.enabled` | on | Classify still may run for tests; supervisor skips constrain injection **and** execute |
| `KAZMA_INTENT_EXECUTE=0` | `agent.intent.execute_enabled` | on | Never `execute`; still classify + constrain |
| `KAZMA_INTENT_TIER2=0` | `agent.intent.tier2_enabled` | on | Skip LLM gray-zone call |

```python
def intent_engine_enabled() -> bool: ...
def intent_execute_enabled() -> bool: ...
def intent_tier2_enabled() -> bool: ...
```

Import `get_config_store` **inside** the functions. On any error, return the safe default (engine on, execute on, tier2 on) except when env is explicitly `0`.

---

## 7. Heuristics (`intent/heuristics.py`)

Return `tuple[IntentAct, ...]`. **Multi-label. No first-match-wins.**

### 7.1 `document_generate`

**Format tokens** (whole words / extensions only — **never** `docx?`):

```
pdf, docx, xlsx, pptx, powerpoint
# Arabic format tokens: مستند, وثيقة, ملف pdf, تقرير
# "report" / "document" / "slide" / "spreadsheet" / "word" / "excel"
# are NOT format tokens by themselves.
```

`word` matches only as `\bword\s+doc` or `\bms\s*word\b`.
`excel` / `spreadsheet` match as format (they do not collide with “document”).
`slide` / `deck` / `presentation` set slot `format=pptx` only with a generate verb — handler must fail closed if `generate_pptx` is missing (do **not** silently PDF).

**Generate verbs:**

```
reproduce, recreat, create, generate, make, convert, export,
write, draft, produce
# Arabic: أنشئ, اصنع, أعيد, حول, صدر, أنجز  (when a format/attachment is also present)
```

**Do not** treat as generate verbs: `build`, `format`, `rebuild`, `run`, `finish` (unless also a generate verb is present).

**Attachment boost:** if `attachments` contains a file whose mime is `application/pdf` or `application/vnd.*` **or** filename ends with `.pdf/.docx/.xlsx/.pptx`, a generate verb alone is enough (`format` from mime/extension).

**Negation:** if a read/explain verb is present **and** no generate verb → do **not** emit `document_generate`. If **both** read and generate (“don’t just read this PDF, create a new one”) → emit `document_generate`.

**Confidence:** verb + (format token or document attachment) → `HIGH_PRECISION` (0.86). Weak “make a document” with no format and no attachment → **do not emit** (the word document is not a format).

**Slots:** `format` (pdf|docx|xlsx|pptx), `deliver_to` (telegram|discord|slack|email|""), `title` optional.

Delivery extract: keep a tight `send/deliver ... to telegram|discord|slack` regex (existing `_SEND_TO_RE` is fine). Do not infer delivery from a bare platform mention.

### 7.2 `research` / `research_deep`

**Do not copy regexes.** Import and call:

```python
from kazma_core.agent.research_policy import (
    is_deep_research_intent,
    is_research_intent,
    extract_topic_hint,
)
```

- `is_deep_research_intent` → `ActKind.RESEARCH_DEEP` @ 0.86, slot `topic=extract_topic_hint(text)`
- else `is_research_intent` → `ActKind.RESEARCH` @ 0.70 (constrain only; **never** execute on 0.70)

### 7.3 `swarm`

Tight pattern: `/\bswarm\b/`, `multi-agent`, `delegate to (workers|agents)`, `fan.?out`, `/swarm`, Arabic `جواق` / `عدة وكلاء`.

Do **not** fire on bare `dispatch` or `spawn` or `workers` alone.

Confidence 0.80. Never execute in Phases 0–3.

### 7.4 `code_exec`

Require (run|execute|eval) **and** (script|code|file|`.py`|`.sh`|attached source).  
Bare `python`, `run this`, `run the tests` → **do not emit**.  
`explain/how does/what is` + code → do not emit.

Never execute in Phases 0–3.

### 7.5 `file_mgmt`

`organize|move|copy|delete|rename|archive` + `files?`. Never execute in Phases 0–3.

### 7.6 `analysis`

Only if (analyze|chart|visuali[sz]e|statistics) + (dataset|csv|xlsx|numbers|results). Never execute.

### 7.7 `document_intel`

`ingest|index this (document|pdf)|redact|document search|document library|/documents|/docs`.  
Must **not** fire on generate-verb + pdf. Never execute in 0–3.

### 7.8 `remind`

`remind me|schedule a (reminder|task)|set a reminder`. Never execute in 0–3.

### 7.9 Empty / nothing matched

Return `(IntentAct(GENERAL, NO_MATCH, source="heuristic"),)`.

---

## 8. Entity resolution (`intent/entities.py`)

```python
def resolve_entities(
    *,
    text: str,
    attachments: list[dict] | None,
    acts: tuple[IntentAct, ...],
) -> EntitySet:
```

### Algorithm

1. Build candidate list from `attachments` items with a usable `path` or `filename`.
2. For each candidate:
   - If `path` is absolute: require `Path.is_file()` **and** `check_path_access(path, "read").allowed`.
   - If relative: resolve against `resolve_active_root()`, then the same access check.
   - Filename-only: look under workspace root and **not** a process-global attachments dump unless that file is already in `attachments` (the transport pinned it).
3. Explicit path in text (quoted or `\S+\.(pdf|docx|xlsx|pptx|md|txt|csv)`): same access check; add if allowed.
4. If `document_generate` is in acts and it needs a source (reproduce/convert/this-file language **or** no inline content):
   - 0 files → `unresolved += ("source_file",)`
   - 1 file → use it
   - N>1 → if text names one uniquely (filename stem, case-insensitive), use that; else `ambiguous += ("source_file",)`
5. **Never** scan `kazma-data/attachments/*.pdf` by mtime.
6. **Never** return a path that failed `check_path_access`.

`document_generate` from-scratch (“write me a PDF of these notes” with notes **in the message** and no attachment) does **not** require `source_file`. Slot `inline_content=True`. Handler uses the user text as content, not a file.

---

## 9. Tier 2 LLM (`intent/llm.py`)

Call **only** when `intent_tier2_enabled()` and gray zone:

```
gray = (
    max(a.confidence for a in acts) < TIER2_HIGH
    or sum(1 for a in acts if a.kind != GENERAL) != 1
    or entities.unresolved
    or entities.ambiguous
)
and len(text.strip()) > 20
and llm is not None
```

Prompt: classify into `ActKind` values only. Reply **JSON only**:

```json
{"acts": [{"kind": "research", "confidence": 0.8, "slots": {"topic": "..."}}]}
```

Validate with a small Pydantic model. Drop unknown kinds. Timeout 4s (`asyncio.wait_for`). On any error: return the heuristic acts unchanged. `source="llm"` on replaced acts.

Do **not** let Tier 2 invent `execute`. Policy still decides the route.

Supervisor must **await** this. Do not add a `pass` stub.

---

## 10. Policy (`intent/policy.py`) — only place that sets `execute`

```python
def decide(
    *,
    focus: str,
    acts: tuple[IntentAct, ...],
    entities: EntitySet,
    registry: IntentRegistry,
) -> tuple[RouteKind, str | None, str, str]:
    """Returns (route, handler_name, reason, plan_note)."""
```

### Ordered rules (first match)

1. If not `intent_engine_enabled()` → `LOOP`, reason=`engine_disabled`.
2. If `focus` in `{"continue", "cleanup", "shift"}` → `LOOP` (or constrain-only plan for cleanup/store — **never execute**). Reason=`focus_<focus>`.
3. Drop `GENERAL` acts. If none left → `LOOP`, reason=`no_act`.
4. If `len(non_general) > 1` and no registered composer for that exact frozenset of kinds → `CONSTRAIN` with an ordered `plan_note` (see §10.1). Reason=`multi_act`.
5. If `entities.unresolved` or `entities.ambiguous` → `CONSTRAIN` (ask which file / need source). Reason=`unresolved` / `ambiguous`. **Never pick a guess.**
6. Primary = highest confidence remaining act.
7. If not `intent_execute_enabled()` → `CONSTRAIN` if we have a plan for that kind, else `LOOP`.
8. If `primary.confidence < EXECUTE_MIN` → `CONSTRAIN` if kind in `SOFT_KINDS` else `LOOP`.
9. Handler = `registry.get_for_act(primary.kind)`. If none → `CONSTRAIN` if soft else `LOOP`. Reason=`no_handler`. **Do not advertise execute.**
10. If handler.required_slots not satisfied by `primary.slots` + `entities` → `CONSTRAIN`.
11. If handler.mutating and not handler.uses_execute → **do not register** (boot assert). If somehow present → `LOOP`, reason=`handler_unsafe`.
12. Else if handler is on the **execute allowlist for this phase** → `EXECUTE`.

### Phase execute allowlist (hard-code + comment “Phase N”)

| Phase | Execute allowlist |
|---|---|
| 0 | **empty** (engine classifies + constrains only; old document early-return is **removed**) |
| 1 | `document_generate` only |
| 2 | + `research_deep` (via `start_deep_research`) |
| 3 | + composer `research_then_document` |
| never in 0–3 | swarm, code_exec, file_mgmt, analysis, remind, document_intel |

`SOFT_KINDS` (constrain even without execute handler):  
`research`, `research_deep`, `swarm`, `document_generate`, `document_intel`, `file_mgmt`, `code_exec`, `remind`, `analysis`.

### 10.1 Constrain plan notes

Keep them short, imperative, English+match user language if Arabic-dominant (`documents.profile.is_arabic_dominant` or existing `language_lock`).

Examples:

- `research_deep`: same text as today’s `deep_research_route_hint` (prefer `run_research_pipeline` once).
- `research`: “Use ≥2 `web_search` + `read_url_to_file`. Do not claim thorough research from snippets.”
- `document_generate` without source: “Ask which file to reproduce, or write from the user’s text if they provided the content.”
- `document_generate` with source but execute off / Phase 0: “Call `file_read` on {filename}, `file_write` markdown, then `generate_pdf` with `markdown_path`. Do not write a Python PDF script.”
- `swarm`: “If parallel workers are needed, tell the user to use `/swarm` or the swarm panel. Do not invent a dispatch.”
- multi-act research+document: “1) Run research (`run_research_pipeline` if deep). 2) Generate a PDF from the report via `generate_pdf(markdown_path=...)`.”

Dedup in supervisor: do not append if a system message already contains `INTENT ENGINE`.

Prefix every plan_note with `INTENT ENGINE:`.

---

## 11. `classify_turn` (`intent/classify.py`)

```python
async def classify_turn(
    text: str,
    *,
    messages: list[dict] | None = None,
    attachments: list[dict] | None = None,
    task_status: str = "",
    task_goal_summary: str = "",
    llm: Any = None,
    command: str | None = None,
    use_embedding_drift: bool = True,
) -> TurnDecision:
```

Steps:

1. `focus = classify_turn_intent(text, messages=..., task_status=..., task_goal_summary=..., use_embedding_drift=...)`
2. If `command` in `{research, research_deep, swarm, documents}`: seed acts from command (`source="command"`, confidence 0.95). Still run heuristics and merge unique kinds.
3. `acts = detect_acts(text, attachments)`
4. `entities = resolve_entities(...)`
5. If gray and tier2 enabled: `acts = await refine_acts_llm(...)`
6. `route, handler, reason, plan_note = decide(...)`
7. Return `TurnDecision`

`classify_turn_sync`: steps 1–4 and 6 only (no await). Used by unit tests that do not need Tier 2.

### Compat façade (`intent_router.py`)

Keep `classify_task`, `TaskIntent`, `IntentCategory`, `CONFIDENCE_THRESHOLD` so old tests compile while you migrate them.

```python
def classify_task(text, *, messages=None, attachments=None, llm=None) -> TaskIntent:
    d = classify_turn_sync(text, messages=messages, attachments=attachments)
    primary = d.primary
    # Map ActKind → old category strings for one release
    ...
    # should_route must be False unless d.route == EXECUTE
```

**Critical:** `TaskIntent.should_route` must follow `d.route == EXECUTE`, not “confidence ≥ 0.75 and pipeline name set.” That was the hijack.

`classify_task_async` → `classify_turn`.

---

## 12. Registry (`intent/registry.py`)

```python
@dataclass
class IntentHandler:
    name: str
    act: str
    required_slots: tuple[str, ...]
    uses_execute: bool
    mutating: bool
    timeout_seconds: float
    run: Callable[..., Awaitable[HandlerResult]]  # (decision, state, **ctx)


class IntentRegistry:
    def register(self, handler: IntentHandler) -> None:
        if handler.mutating and not handler.uses_execute:
            raise RuntimeError(
                f"Refusing to register mutating handler '{handler.name}' "
                "that does not use execute()"
            )
        ...
    def get(self, name: str) -> IntentHandler | None: ...
    def get_for_act(self, act: str) -> IntentHandler | None: ...
    def get_composer(self, kinds: frozenset[str]) -> IntentHandler | None: ...
```

Singleton `get_registry()` with `_auto_register()` importing handlers. Swallow per-handler import errors at **warning** (not debug) so a broken handler is visible.

Phase 0: register **no** execute handlers (or register document with `mutating=True` but policy allowlist empty — either is fine; prefer register document in Phase 1 only).

`pipeline_registry.get_registry` should call this singleton.

---

## 13. Document handler (Phase 1) — `intent/handlers/document.py`

### Contract

```python
async def run_document(decision: TurnDecision, state: dict, **ctx) -> HandlerResult:
    tool_executor = ctx.get("tool_executor")
    llm = ctx.get("llm")
    if tool_executor is None or not hasattr(tool_executor, "execute"):
        return HandlerResult(ok=False, escalate=True, message="no tool_executor")
```

**Every mutate:**

```python
out = await tool_executor.execute("file_write", {"path": md_name, "content": md})
# out is {"content": str, "is_error": bool}
```

Same for `generate_pdf` / `generate_docx` / `generate_xlsx` / send tool if registered.

If the graph HITL interrupt cannot fire from inside a handler (because we are still in the supervisor node, not tool_worker), you **must not** silently write. Two acceptable implementations (pick one, document in the handler docstring):

**Preferred:** If `tool_executor.execute` will bus-HITL (IDE/swarm path) or honor ContextVars, call it. For the **graph** path, the supervisor should treat document execute as: enqueue the same tool calls the handler would make **or** call execute knowing graph HITL ContextVars may not be set.

**Required fail-safe if graph interrupt cannot run from supervisor:**

- Do **not** call raw `file_write()`.
- If `get_hitl_config()["enabled"]` and `file_write` is in require_approval_for and YOLO is off: return `HandlerResult(ok=False, escalate=True, message="hitl_required")` so the **loop** performs the write (HITL card fires in tool_worker). Optionally set `plan_note` already injected so the model does the 5 steps.
- Only auto-execute write/generate when HITL is off, YOLO is on, or approval ContextVar is already true.

This is the load-bearing safety rule. A green “reproduce PDF” that skipped HITL is a **failed** Phase 1.

### Steps (when execute is allowed)

1. **READ** — resolved file from `decision.entities.files[0]` or inline user text. `DocumentService.read_transient_sync(path, approved_path=path)` only for that access-checked path. Fitz fallback: **raw** `page.get_text()`, **no** `get_display`. Empty read → `ok=False, escalate=True`.
2. **STRUCTURE** — `_looks_like_prose` may stay as a hint. Calendar heuristic (`_basic_structure`) only when it actually looks like dated entries. LLM prompt must say **preserve content and language**, do not condense. Cap input. Timeout 180s. Failure → basic structure or escalate.
3. **WRITE** — `execute("file_write", ...)`. Detect errors via `is_error` or content startswith `Error:` — **not** `"Error" in result`.
4. **GENERATE** — format from slots. `pdf` → `generate_pdf`; `docx` → `generate_docx`; `xlsx` → `generate_xlsx` only if you can build real rows, else `ok=False`; `pptx` → `generate_pptx` if registered, else `ok=False` (no silent PDF).
5. **QUALITY** — output path exists, size ≥ 200 bytes; if PDF, page count ≥ 1 (`fitz` page count is enough). Fail → `ok=False`, **do not deliver**.
6. **DELIVER** — only if `deliver_to` set **and** `get_current_delivery_target()` is non-empty or ConfigStore `connectors.{platform}.swarm_chat_id` resolves to a non-empty id. `execute` the send tool if one exists; else `send_file_message` is only allowed if you cannot go through execute — prefer escalate. Never send a failed/empty file.

Timeouts: wrap `run_document` with `asyncio.wait_for(..., timeout=handler.timeout_seconds)` in the supervisor (default 180s).

### Delete from the old handler

- `_find_source_in_history` mtime glob (entire step 3).
- `get_display` on extract.
- XLSX single-cell dump.
- Silent powerpoint→pdf.

---

## 14. Supervisor integration (`graph_builder.py`)

Replace the block at ~807–862 with:

```python
# After classify_turn_intent has set _intent_mode (or fold it into classify_turn
# and set _intent_mode = decision.focus — prefer ONE call).

_decision = None
try:
    from kazma_core.agent.intent.classify import classify_turn
    from kazma_core.agent.intent.config import intent_engine_enabled

    if iteration == 0 and intent_engine_enabled():
        _atts = list(state.get("active_attachments") or [])
        if not _atts:
            from kazma_core.agent.turn_input import extract_active_attachments
            _atts = extract_active_attachments(messages, user_text=last_user_content)
        _decision = await classify_turn(
            last_user_content,
            messages=messages,
            attachments=_atts,
            task_status=_prev_status,
            task_goal_summary=_prev_goal,
            llm=llm,
            use_embedding_drift=(iteration == 0),
        )
        _intent_mode = _decision.focus
        intent_patch["intent_mode"] = _decision.focus
        intent_patch["intent_route"] = str(_decision.route)
        intent_patch["intent_acts"] = [
            {"kind": a.kind, "confidence": a.confidence, "slots": a.slots, "source": a.source}
            for a in _decision.acts
        ]
        intent_patch["intent_reason"] = _decision.reason
except Exception:
    logger.debug("[Supervisor] Intent engine failed (non-fatal)", exc_info=True)
    _decision = None

# Execute (checklist already applied inside policy)
if _decision is not None and _decision.route == RouteKind.EXECUTE and _decision.handler:
    try:
        from kazma_core.agent.intent.registry import get_registry
        _h = get_registry().get(_decision.handler)
        if _h is not None:
            _res = await asyncio.wait_for(
                _h.run(_decision, {**state, "messages": messages}, llm=llm, tool_executor=tool_executor),
                timeout=_h.timeout_seconds,
            )
        else:
            _res = None
    except Exception as exc:
        logger.warning("[Supervisor] handler %s failed: %s — loop", _decision.handler, exc)
        _res = None
    if _res is not None and _res.ok and not _res.escalate:
        return {
            **intent_patch,          # MUST include focus + route + acts
            "messages": messages + [{"role": "assistant", "content": _res.message}],
            "next_node": NodeName.RESPOND,
            "iteration": iteration + 1,
            "task_status": TaskStatus.COMPLETED,
        }
    # else fall through

# Constrain: inject plan_note once
if (
    _decision is not None
    and _decision.route == RouteKind.CONSTRAIN
    and _decision.plan_note
    and not any(
        m.get("role") == "system" and "INTENT ENGINE" in str(m.get("content") or "")
        for m in messages
    )
):
    messages.append({"role": "system", "content": _decision.plan_note})
```

**Preferred:** delete the duplicate `classify_turn_intent` call and let `classify_turn` own focus. Then set `_graph_cleanup` / `_store_intent` / `_is_continue` / `_is_shift` from `decision.focus` exactly as today.

If you keep both calls, they **must** use the same `classify_turn_intent` — never the old `_CONTINUE_RE`.

Phase 2: skip `deep_research_route_hint` when the plan_note already covers `research_deep` (avoid double nudge).

On execute success, **merge `intent_patch`**. Today’s early return drops it — that is a bug; do not repeat it.

---

## 15. SupervisorState (`state.py`)

Add declared fields (LangGraph drops undeclared keys):

```python
intent_route: str          # execute | constrain | loop | ""
intent_acts: list[dict]    # serialized IntentAct dicts
intent_reason: str
```

`initial_supervisor_state`: `intent_route=""`, `intent_acts=[]`, `intent_reason=""`.

Keep `intent_mode` as the focus string.

---

## 16. `turn_input.py` (small)

Add Arabic short-continuations to `_CONTINUATION_PHRASES` **only** as exact phrases: `أكمل`, `استمر`, `تابع` — **not** `أنجز` (that is a generate verb).

Bare `أكمل` → focus continue (existing `is_short_continuation` + length cap).  
`أكمل هذا المستند PDF` is **not** a short continuation (too long / has substance) → focus `normal`, acts include `document_generate`.

Do **not** add a second continue detector.

Existing tests in `tests/test_topic_shift_focus.py` and `kazma-core/tests/test_steer_routing.py` must stay green. Add cases for the Arabic split above.

---

## 17. Gateway working-memory pin (Phase 1)

SSE (`sse_chat.py`) and WS already call `build_turn_working_memory` before invoke.

Gateway `kazma-gateway/kazma_gateway/agent_handler/graph.py` does **not**. After `build_turn_messages`, pin:

```python
from kazma_core.agent.turn_input import build_turn_working_memory
_wm = build_turn_working_memory(user_text, messages=messages, client_attachments=raw_attachments or [])
input_state.update(_wm)
```

Do not put platform IDs into `_wm`.

---

## 18. Research (Phase 2)

- `heuristics` already delegates to `research_policy`.
- Constrain note replaces `deep_research_route_hint` injection when acts contain `research_deep`.
- Execute handler (allowlist Phase 2): call `start_deep_research(...)` (session wrapper) — **not** a new crawler, **not** Swarm. Keep `suppress_chat_recording` for sub-queries.
- Unify SSE/WS `/research` to the same function gateway uses. Leave slash as `command="research_deep"` into `classify_turn` **or** keep slash as an explicit override that calls the same handler. Do not leave three implementations.
- Casual `research` (0.70) never executes.

---

## 19. Composition (Phase 3)

Register composer for `frozenset({RESEARCH_DEEP, DOCUMENT_GENERATE})` or `{RESEARCH, DOCUMENT_GENERATE}`:

1. Run research handler; require a report path in `HandlerResult.artifacts`.
2. Build a new `TurnDecision` with `document_generate` + that file as `ResolvedFile`.
3. Run document handler.

If step 1 fails → escalate to loop (do not generate a PDF of the error).

Swarm / code / files / remind stay constrain-only.

---

## 20. Tests (required — costume dies here)

Put new files under `tests/` (repo root, where `test_intent_router.py` lives) unless the package suite is the local convention; follow whichever directory already holds `test_intent_router.py`.

### 20.1 `tests/test_intent_engine_heuristics.py`

Parametrize. Assert `classify_turn_sync(...).route != RouteKind.EXECUTE` unless noted. Also assert act kinds.

**Must NOT execute (false positives):**

```
build a PDF parser in Python
rebuild the document index
format the documents folder
I have a python question
please run the tests
run this
explain how this code works
what is this PDF about?
read this PDF
create a report about climate
the document says hello
update the document
document this API
```

**Must detect document_generate (route may be constrain if no attachment):**

```
reproduce this PDF with better templates
create a Word document with the meeting notes     # format=docx
generate an Excel spreadsheet of the data         # format=xlsx
أنشئ مستند PDF من هذه البيانات
write me a PDF of the notes                       # inline_content
dont just read this PDF, create a new one
make me a PDF report
convert this to Word
```

**Multi-act (both kinds present, route != execute in Phase 0–1):**

```
research this topic and make a PDF
research the impact of AI on Kuwait and generate a PDF
dispatch a worker to create a PDF                 # if swarm heuristic fires; else document only + not execute without source
```

**Research (not execute in Phase 0–1):**

```
research the impact of AI on Kuwait's economy     # RESEARCH or RESEARCH_DEEP
do a deep dive on cloud security best practices   # RESEARCH_DEEP if policy regex says so
```

**Arabic generate vs continue:**

```
أكمل هذا المستند PDF     # focus != continue; document_generate present
أنجز تقرير PDF           # document_generate; focus != continue
أكمل                     # focus == continue; route != execute
```

**Attachments:**

```
classify_turn_sync("reproduce this", attachments=[{kind:file, mime:application/pdf, path:cal.pdf, filename:cal.pdf}])
# document_generate; route still != execute in Phase 0; Phase 1 execute only if path access-checked
```

### 20.2 `tests/test_intent_engine_policy.py`

- Missing `source_file` + reproduce language → not execute, unresolved or constrain.
- Two attachments, text “this PDF” with no name → ambiguous, not execute.
- `KAZMA_INTENT_EXECUTE=0` → never execute even with perfect slots (monkeypatch env).
- `KAZMA_INTENT_ENGINE=0` → loop, no plan_note required.
- `focus=continue` → never execute even if acts would qualify.
- No handler registered → not execute.
- Mutating handler with `uses_execute=False` → `register()` raises.

### 20.3 `tests/test_intent_engine_entities.py`

- Pinned attachment path inside a temp workspace → resolved.
- Absolute path **outside** workspace without grant → not in `files`, `unresolved` or omitted.
- Do **not** create `kazma-data/attachments/other.pdf` and expect it to be picked when attachments=[].
- Unique filename in text among two pinned files → that file.

### 20.4 `tests/test_intent_engine_tier2.py`

- Fake LLM: gray-zone utterance (long general question **or** multi-act) must call `llm.chat`.
- High-precision “reproduce this PDF” with attachment must **not** require LLM (optional: assert not called).
- LLM raises / returns garbage → heuristic acts kept, no exception, route != execute unless checklist still passes.

### 20.5 `tests/test_document_handler_execute.py` (Phase 1)

- Fake `tool_executor.execute` records names. Handler must call `file_write` then `generate_pdf` (or escalate if HITL requires loop).
- Handler must **not** import-and-call `file_write` as a function (inspect with monkeypatch: if the raw function is called, fail).
- Quality fail (generate returns empty/missing file) → `ok=False`, send not called.
- `"Error" in successful write message` must not be treated as failure if `is_error=False` and the tool uses the real return shape.

### 20.6 Existing tests

- `tests/test_topic_shift_focus.py`, `tests/test_topic_drift_embed_and_stub.py`, `kazma-core/tests/test_steer_routing.py` — unchanged behavior.
- `tests/test_intent_router.py` — migrate to façade: `should_route` is False unless execute. **Delete** `TestSupervisorRouting.test_document_intent_bypasses_free_form` (it accepts RESPOND **or** TOOL_WORKER and swallows exceptions — it cannot fail). Replace with a supervisor unit test that:
  - Phase 0: document-like text without source → `next_node` is **not** an early RESPOND from the pipeline (loop or constrain).
  - Phase 1: with fake executor + resolved file + HITL off → RESPOND and `intent_route=execute` on the patch.

### 20.7 Compile

PowerShell:

```powershell
& '.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'kazma-core\kazma_core\agent\intent\classify.py', doraise=True); print('OK')"
& '.venv\Scripts\python.exe' -m pytest tests/test_intent_engine_heuristics.py tests/test_intent_engine_policy.py tests/test_intent_engine_entities.py tests/test_topic_shift_focus.py tests/test_intent_router.py -q --timeout=60
```

Add files as they land. Never `|| true`.

---

## 21. Implementation phases (PRs)

Ship in order. Do not start Phase N+1 if Phase N exit is red.

### Phase 0 — Engine + stop the bleeding  (first PR, required)

**Do:**

- Add `intent/` package: types, config, heuristics, entities, policy, classify, registry (empty execute allowlist).
- Façade `intent_router.py`.
- Supervisor: `await classify_turn`, write state fields, **remove** the old `classify_task` + `document_pipeline` early-return.
- Constrain plan_note injection.
- Kill mtime glob (even before handler rewrite: if any leftover document path remains, delete that branch).
- `turn_input` Arabic continue split.
- Heuristic + policy + entity tests + migrate façade tests.
- Kill-switches.
- State fields + `initial_supervisor_state`.

**Do not:** wrap document execute yet; do not register an execute handler; do not touch SSE `/research`.

**Exit:**

- False-positive corpus: `route != execute` for every row in §20.1 “Must NOT execute”.
- `build a PDF parser` no longer hits `document_pipeline`.
- Focus tests green.
- `classify_task(...).should_route` is False for those utterances.
- No references to `kazma-data/attachments` + `mtime` / `stat().st_mtime` in agent intent/pipeline code.

### Phase 1 — Safe document execute

**Do:**

- Move/adapt document handler to `intent/handlers/document.py` with `uses_execute=True`.
- Policy allowlist += `document_generate`.
- HITL rule in §13 implemented (escalate if graph cannot card).
- Entity resolver used by handler (no history mtime).
- Quality gate.
- Fitz: no `get_display`.
- Preserve-not-condense prompt.
- Fail closed on pptx/xlsx-if-unstructured.
- Gateway WM pin.
- Execute success merges `intent_patch`.
- `test_document_handler_execute.py`.

**Exit:**

- “reproduce this” + pinned in-workspace PDF + HITL disabled in test: `file_write` and `generate_pdf` go through `execute`.
- Raw `file_write()` not called.
- HITL enabled + no YOLO + no ContextVar: handler escalates (loop), does not write.
- No absolute extra-workspace read.

### Phase 2 — Research SoT

**Do:**

- Constrain notes replace duplicate `deep_research_route_hint` when acts already set.
- Optional execute `research_deep` → `start_deep_research`.
- SSE/WS `/research` call that same function.
- Multi-act research+PDF stays constrain (no composer yet).

**Exit:** one research entry function used by gateway + web slash; casual research not executed; “research X and make a PDF” has both acts.

### Phase 3 — Composer + remaining constrain

**Do:** `research_then_document`; constrain notes for swarm/code/files/remind/analysis/document_intel; diagnosis-map + changelog; mark this spec “implemented through Phase 3”.

**Exit:** composer only runs if research produced a real report path; swarm still not auto-dispatched.

### Phase 4 — Hardening (optional follow-up)

Arabic isolated-form scan on **generated** PDF text; one retry; TUI classify; metrics `kazma_intent_decisions_total{route,act}`.

---

## 22. Docs the implementer must update (same PR as the phase)

| File | Change |
|---|---|
| `docs/plans/UNIVERSAL_INTENT_ROUTER.md` | Status: **superseded** by this file (do this in Phase 0) |
| `docs/docs/ops/diagnosis-map.md` | New element: intent engine (classify_turn → execute/constrain/loop). Symptom: wrong pipeline / hijack / skipped HITL |
| `CHANGELOG.md` | Phase N notes |
| `Agents.md` | Short § if you add a new choke point (handlers must use `execute`) — only if the implementer is allowed to touch it; otherwise diagnosis-map is enough |

Do not resurrect `docs-v2`. Live docs only under `docs/docs/` plus this plan.

---

## 23. Coding standards (Kazma)

- `from __future__ import annotations`
- `logger = logging.getLogger(__name__)`
- Type hints on public functions
- No `ConfigStore()` constructor — `get_config_store()`
- PowerShell: `;` and `$LASTEXITCODE`, never `&&` / `||`
- `py_compile` changed Python files
- Do not add comments that narrate the change; comments only for non-obvious constraints
- Keep modules focused

---

## 24. Suggested implementer order (Phase 0, hour by hour)

1. Write `intent/types.py` + `intent/config.py` + empty `registry.py`.
2. Write `heuristics.py` + `test_intent_engine_heuristics.py` until the corpus is green (no supervisor yet).
3. Write `entities.py` + entity tests.
4. Write `policy.py` + policy tests (execute allowlist empty).
5. Write `classify.py` + façade.
6. Add state fields.
7. Wire supervisor; **delete** old early-return; inject constrain notes.
8. `turn_input` Arabic continue.
9. Kill mtime glob in `pipelines/document.py` (even if handler still exists unused).
10. Migrate `test_intent_router.py`; delete the swallow-all supervisor test.
11. Run the pytest command in §20.7.
12. Changelog + superseded pointer.

Phase 1 starts only when step 11 is green.

---

## 25. Follow-up review checklist (for the reviewer after the agent lands)

Do not accept the PR if any of these fail:

- [ ] `build a PDF parser in Python` → `route != execute` and no document handler ran
- [ ] `rebuild the document index` → not document execute
- [ ] `research X and make a PDF` → both acts; not document-only execute
- [ ] `أكمل هذا المستند PDF` → not focus `continue`
- [ ] No `st_mtime` / newest-PDF glob in `agent/intent` or `agent/pipelines`
- [ ] No `file_write(` direct call from document handler (only `tool_executor.execute`)
- [ ] HITL-on path escalates instead of writing
- [ ] `intent_patch` merged on execute success (`intent_route` present)
- [ ] `classify_turn_intent` tests still pass
- [ ] Gray zone can call a fake LLM; no-match score is not stuck at 0.40 excluded from Tier 2
- [ ] `KAZMA_INTENT_EXECUTE=0` disables execute
- [ ] Supervisor hook is `await classify_turn`, not sync `classify_task` + `pass`
- [ ] No new `PipelineDAG` class
- [ ] No `SessionStore` in entity code
- [ ] `py_compile` + listed pytest green

---

## 26. Glossary

| Term | Meaning |
|---|---|
| Focus | `classify_turn_intent` output: continue/store/cleanup/multi_part/shift/normal |
| Act | What the user wants done: document_generate, research, … |
| Route | execute / constrain / loop |
| Handler | Thin adapter that calls **existing** tools via `execute()` |
| Composer | Handler for a frozenset of acts (Phase 3) |
| Constrain | Stay in LangGraph; inject `INTENT ENGINE:` plan |
| Costume router | Current `intent_router.py` + unregistered categories + HITL-bypassing document script |
