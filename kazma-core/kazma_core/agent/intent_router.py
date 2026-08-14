"""Universal Intent Router — single entry point for task classification.

Every user message passes through this module BEFORE the supervisor decides
what to do. Structured tasks (document generation, research, code execution)
route to deterministic pipelines; open-ended tasks fall through to the
free-form agent loop.

Two-tier classification:
  Tier 1 (heuristic, free, <1ms): regex + keyword patterns per category.
  Tier 2 (LLM fallback): one fast call, only when Tier 1 is ambiguous.

The model is the CONTENT ENGINE (what to write), not the EXECUTION PLANNER
(which tools to call in what order).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TaskIntent",
    "IntentCategory",
    "classify_task",
    "CONFIDENCE_THRESHOLD",
]

logger = logging.getLogger(__name__)

#: Minimum confidence to route directly to a pipeline (bypass free-form).
CONFIDENCE_THRESHOLD = 0.75


# ─── Intent model ────────────────────────────────────────────────────────


class IntentCategory:
    """Task categories — each maps to a pipeline or the free-form loop."""

    DOCUMENT = "document"
    RESEARCH = "research"
    CODE = "code"
    FILE_MGMT = "file_mgmt"
    SWARM = "swarm"
    ANALYSIS = "analysis"
    GENERAL = "general"
    CONTINUE = "continue"


@dataclass(frozen=True)
class TaskIntent:
    """Classification result for one user turn."""

    category: str
    confidence: float
    pipeline: str | None
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    source: str = "heuristic"  # "heuristic" | "llm" | "command"

    @property
    def should_route(self) -> bool:
        """True when this intent should bypass the free-form loop."""
        return (
            self.confidence >= CONFIDENCE_THRESHOLD
            and self.pipeline is not None
            and self.category != IntentCategory.GENERAL
        )


# ─── Tier 1: Heuristic patterns (fast, free) ────────────────────────────

_DOC_FORMAT_RE = re.compile(
    r"\b(pdf|docx?|word|excel|xlsx|spreadsheet|powerpoint|pptx|slide)"
    r"|مستند|ملف\s+pdf|تقرير|وثيقة",
    re.IGNORECASE,
)
_DOC_ACTION_RE = re.compile(
    r"\b(reproduce|recreat|create|generate|make|build|produce|convert|"
    r"export|render|format|redesign|rebuild)"
    r"|أعيد|أنشئ|اصنع|حو[لّ]|صد[رّ]|أنجز",
    re.IGNORECASE,
)
_DOC_NEGATION_RE = re.compile(
    r"\b(read|open|view|check|what is|tell me about|look at|show me)\b"
    r"|اقرا|افتح|شاهد|اعرض",
    re.IGNORECASE,
)

_RESEARCH_RE = re.compile(
    r"\b(research|investigat|deep\s*dive|find\s*out|look\s*up|analyz"
    r".*\s+topic|comprehensive\s+(?:report|analysis|study)|"
    r"search\s+(?:the\s+)?web|web\s+search)"
    r"|ابحث|حقق|استقص|دراسة\s+شاملة|بحث\s+معمق",
    re.IGNORECASE,
)

_CODE_RUN_RE = re.compile(
    r"\b(run|execute|script|python|bash|shell|code)"
    r"\s*(this|that|the|it|file|script|code)?"
    r"|شغل|نفذ|اكتب\s+كود|سكربت",
    re.IGNORECASE,
)
_CODE_NEGATION_RE = re.compile(
    r"\b(what is|explain|how does|tell me|describe)\b"
    r"|اشرح|ما هو|كيف",
    re.IGNORECASE,
)

_SWARM_RE = re.compile(
    r"\b(dispatch|spawn|delegate|swarm|workers|parallel\s+tasks|"
    r"fan\s*out|multi[-\s]?agent)"
    r"|افرز|فوض|جواق|عدة\s+وكلاء",
    re.IGNORECASE,
)

_FILE_MGMT_RE = re.compile(
    r"\b(organize|move|copy|delete|rename|sort|clean\s+up|tidy|"
    r"archive|compress|extract)\s+(?:the\s+|my\s+|these\s+)?files?"
    r"|نظم|انقل|احذف|أعد\s+تسمية|رت[بّ]",
    re.IGNORECASE,
)

_ANALYSIS_RE = re.compile(
    r"\b(analyz|compar|summariz|chart|graph|visualiz|stats?|"
    r"statistics|trend|insight)"
    r"\s+(this|that|the|data|dataset|numbers|results?)"
    r"|حلل|قارن|لخص|رسم\s+بياني|إحصائيات",
    re.IGNORECASE,
)

_CONTINUE_RE = re.compile(
    r"^(proceed|continue|keep going|finish|finish it|resume|go ahead|"
    r"do it|try again|retry|next step|ok continue|yes continue)"
    r"$"
    r"|أكمل|استمر|تابع|أنجز",
    re.IGNORECASE,
)


def _score(text: str, pattern: re.Pattern, negation: re.Pattern | None = None) -> float:
    """Heuristic score: pattern hit ratio with negation penalty."""
    t = text.strip()
    if not t:
        return 0.0
    hits = len(pattern.findall(t))
    if hits == 0:
        return 0.0
    if negation and negation.search(t):
        return 0.0
    # 1 hit = 0.75, 2+ hits = 0.85 (diminishing returns)
    return min(0.85, 0.65 + 0.10 * hits)


def _tier1_classify(text: str, attachments: list[dict] | None) -> TaskIntent:
    """Heuristic classification — fast, free, catches ~90% of clear cases."""
    t = (text or "").strip()
    if not t:
        return TaskIntent(IntentCategory.GENERAL, 0.5, None, reason="empty input")

    # Continue (checked first — overrides everything)
    if _CONTINUE_RE.match(t):
        return TaskIntent(
            IntentCategory.CONTINUE, 0.95, None,
            reason="explicit continuation", source="heuristic",
        )

    # Document generation (needs BOTH format + action, no negation)
    fmt_s = _score(t, _DOC_FORMAT_RE, _DOC_NEGATION_RE)
    act_s = _score(t, _DOC_ACTION_RE)
    if fmt_s > 0 and act_s > 0:
        fmt = _DOC_FORMAT_RE.search(t)
        fmt_name = fmt.group(0).lower() if fmt else "document"
        return TaskIntent(
            IntentCategory.DOCUMENT, min(0.95, fmt_s + act_s * 0.3),
            "document",
            parameters={
                "format": fmt_name,
                "source_hint": _extract_source_path(t, attachments),
                "deliver_to": _extract_delivery_target(t),
            },
            reason=f"format={fmt_name} + action verb", source="heuristic",
        )

    # Attachment + document-like intent (e.g., "reproduce this" with a PDF attached)
    if attachments and act_s > 0 and any(
        (a.get("kind") == "file" and (a.get("mime") or "").startswith(("application/pdf", "application/vnd")))
        or (a.get("filename") or "").lower().endswith((".pdf", ".docx", ".xlsx", ".pptx"))
        for a in attachments
    ):
        return TaskIntent(
            IntentCategory.DOCUMENT, 0.85, "document",
            parameters={
                "source_path": attachments[0].get("path") or attachments[0].get("filename", ""),
                "deliver_to": _extract_delivery_target(t),
            },
            reason="document attachment + action verb", source="heuristic",
        )

    # Research
    r_s = _score(t, _RESEARCH_RE)
    if r_s >= 0.75:
        return TaskIntent(
            IntentCategory.RESEARCH, r_s, "research",
            parameters={"query": t},
            reason="research keywords", source="heuristic",
        )

    # Swarm delegation
    s_s = _score(t, _SWARM_RE)
    if s_s >= 0.75:
        return TaskIntent(
            IntentCategory.SWARM, s_s, "swarm",
            parameters={"prompt": t},
            reason="swarm/delegation keywords", source="heuristic",
        )

    # Code execution (needs action, no negation)
    c_s = _score(t, _CODE_RUN_RE, _CODE_NEGATION_RE)
    if c_s >= 0.75:
        return TaskIntent(
            IntentCategory.CODE, c_s, "code",
            parameters={"source": t},
            reason="code execution keywords", source="heuristic",
        )

    # File management
    f_s = _score(t, _FILE_MGMT_RE)
    if f_s >= 0.75:
        return TaskIntent(
            IntentCategory.FILE_MGMT, f_s, "file_mgmt",
            reason="file management keywords", source="heuristic",
        )

    # Data analysis
    a_s = _score(t, _ANALYSIS_RE)
    if a_s >= 0.75:
        return TaskIntent(
            IntentCategory.ANALYSIS, a_s, "analysis",
            reason="analysis keywords", source="heuristic",
        )

    # No strong match → general (free-form agent loop)
    return TaskIntent(
        IntentCategory.GENERAL, 0.4, None,
        reason="no heuristic match", source="heuristic",
    )


# ─── Helper extractors ───────────────────────────────────────────────────


_PATH_RE = re.compile(
    r"(?:from|of|source|file|attach|read|open|using|based\s+on)\s+"
    r"[\"']?([\w\-./\\]+\.\w{2,5})[\"']?"
    r"|(?:the\s+file\s+|this\s+file\s+)[\"']?([\w\-./\\]+\.\w{2,5})[\"']?"
    r"|[\"']([\w\-./\\]+\.(?:pdf|docx?|xlsx?|pptx?|csv|txt|md))[\"']"
    r"|(?<!\w)([\w\-]+\.(?:pdf|docx?|xlsx?|pptx?))(?!\w)",
    re.IGNORECASE,
)
_SEND_TO_RE = re.compile(
    r"\b(?:send|deliver|share|share\s+it)\s+(?:it\s+)?(?:to|on|via|through)\s+"
    r"(?:my\s+|the\s+|our\s+|your\s+)?(telegram|discord|slack|whatsapp|email)",
    re.IGNORECASE,
)


def _extract_source_path(text: str, attachments: list[dict] | None) -> str:
    """Try to extract a source file path from the text or attachments."""
    m = _PATH_RE.search(text or "")
    if m:
        # Return the first non-None group (the regex has 4 capture groups)
        for g in m.groups():
            if g:
                return g
    if attachments:
        for a in attachments:
            p = a.get("path") or a.get("filename") or ""
            if p:
                return p
    return ""


def _extract_delivery_target(text: str) -> str:
    """Extract delivery platform hint (telegram, discord, slack, etc.)."""
    m = _SEND_TO_RE.search(text or "")
    if m:
        platform = m.group(1).lower()
        if platform in ("telegram", "tg"):
            return "telegram"
        if platform == "discord":
            return "discord"
        if platform == "slack":
            return "slack"
        if platform == "email":
            return "email"
    # Fallback: bare platform mention near "send"
    t = (text or "").lower()
    if "send" in t and ("telegram" in t or "تيليجرام" in t or "تليجرام" in t):
        return "telegram"
    if "send" in t and "discord" in t:
        return "discord"
    if "send" in t and "slack" in t:
        return "slack"
    return ""


# ─── Tier 2: LLM fallback (only when heuristic is ambiguous) ────────────

_LLM_CLASSIFY_PROMPT = """Classify this user request into exactly one category. Reply with ONLY the category name.

Categories:
- document (create/reproduce a PDF, DOCX, spreadsheet, report, presentation)
- research (investigate a topic, web search, deep dive, comprehensive analysis)
- code (run/execute/write code or a script)
- swarm (delegate to multiple agents/workers in parallel)
- file_mgmt (organize/move/rename/delete files)
- analysis (analyze data, charts, comparison, statistics)
- general (chat, question, open-ended task)

Request: {text}

Category:"""

_LLM_VALID = {"document", "research", "code", "swarm", "file_mgmt", "analysis", "general"}


async def _tier2_classify(
    text: str, llm: Any, tier1: TaskIntent
) -> TaskIntent:
    """LLM fallback — one fast call when Tier 1 is ambiguous.

    Only invoked when tier1 confidence is in the 'gray zone' (0.4–0.75)
    and the text is substantive enough to warrant a call.
    """
    try:
        prompt = _LLM_CLASSIFY_PROMPT.format(text=text[:500])
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            tools=None,
        )
        answer = (getattr(response, "content", "") or "").strip().lower()
        # Extract the first valid category word
        for word in answer.split():
            if word in _LLM_VALID:
                pipeline = word if word != "general" else None
                return TaskIntent(
                    word, 0.85, pipeline,
                    parameters=tier1.parameters,
                    reason=f"LLM classified as {word}",
                    source="llm",
                )
    except Exception as exc:
        logger.debug("[intent_router] LLM fallback failed: %s", exc)
    return tier1


# ─── Public API ──────────────────────────────────────────────────────────


def classify_task(
    text: str,
    *,
    messages: list[dict] | None = None,
    attachments: list[dict] | None = None,
    llm: Any = None,
) -> TaskIntent:
    """Classify a user turn into a task category + pipeline suggestion.

    Args:
        text: The user's message text.
        messages: Recent conversation history (for context).
        attachments: Active attachments (for document detection).
        llm: LLMProvider for the Tier 2 fallback (optional — Tier 1 only if None).

    Returns:
        TaskIntent with the best classification.
    """
    intent = _tier1_classify(text, attachments)

    # Tier 2 (LLM) only when heuristic is ambiguous AND we have an LLM
    # AND the text is substantive (>20 chars)
    if (
        0.4 < intent.confidence < CONFIDENCE_THRESHOLD
        and llm is not None
        and len((text or "").strip()) > 20
    ):
        # Note: _tier2_classify is async but we call it synchronously here
        # via a simple wrapper — the supervisor will have a running loop.
        # For now, we just return the Tier 1 result (LLM fallback is
        # a follow-up enhancement).
        pass

    return intent


async def classify_task_async(
    text: str,
    *,
    messages: list[dict] | None = None,
    attachments: list[dict] | None = None,
    llm: Any = None,
) -> TaskIntent:
    """Async variant — includes the Tier 2 LLM fallback."""
    intent = _tier1_classify(text, attachments)
    if (
        0.4 < intent.confidence < CONFIDENCE_THRESHOLD
        and llm is not None
        and len((text or "").strip()) > 20
    ):
        intent = await _tier2_classify(text, llm, intent)
    return intent
