"""Multi-label act detection — high precision, no first-match-wins."""
from __future__ import annotations

import logging
import re
from typing import Any

from kazma_core.agent.intent.types import (
    HIGH_PRECISION,
    NO_MATCH,
    ActKind,
    IntentAct,
)

__all__ = ["detect_acts"]

logger = logging.getLogger(__name__)

# ─── Format tokens (whole words / extensions only — never docx?) ────────

_FORMAT_TOKENS = re.compile(
    r"\b(pdf|docx|xlsx|pptx|powerpoint)\b"
    r"|\.pdf\b|\.docx\b|\.xlsx\b|\.pptx\b"
    r"|\bms\s*word\b|\bword\s+doc\b|\bword\s+document\b|\bto\s+word\b"
    r"|\bexcel\b|\bspreadsheet\b"
    r"|ال?مستند|ال?وثيقة|ملف\s+pdf",
    re.IGNORECASE,
)

_FORMAT_MAP = {
    "pdf": "pdf", ".pdf": "pdf",
    "docx": "docx", ".docx": "docx",
    "xlsx": "xlsx", ".xlsx": "xlsx",
    "pptx": "pptx", ".pptx": "pptx",
    "powerpoint": "pptx",
    "ms word": "docx", "msword": "docx", "word doc": "docx",
    "word document": "docx", "to word": "docx",
    "excel": "xlsx", "spreadsheet": "xlsx",
    "مستند": "docx", "المستند": "docx", "وثيقة": "docx", "الوثيقة": "docx", "ملف pdf": "pdf",
}

_GENERATE_VERBS = re.compile(
    r"\b(reproduce|recreat\w*|create|generat\w*|make|convert|export|"
    r"write|draft|produc\w*)\b"
    r"|أنشئ|اصنع|أعد|أعيد|حول|صدر|أنجز|أكمل|استمر|تابع",
    re.IGNORECASE,
)

_NOT_GENERATE_VERBS = re.compile(
    r"\b(build|format|rebuild|run|finish)\b",
    re.IGNORECASE,
)

_READ_VERBS = re.compile(
    r"\b(read|open|view|check|look\s+at|show\s+me|tell\s+me|what\s+is|explain)\b"
    r"|اقرا|افتح|شاهد|اعرض|اشرح|ما هو",
    re.IGNORECASE,
)

_DOCUMENT_ATTACHMENT_RE = re.compile(
    r"\.(pdf|docx|xlsx|pptx)$",
    re.IGNORECASE,
)

_DOCUMENT_MIME_RE = re.compile(
    r"^application/(pdf|vnd\.)",
    re.IGNORECASE,
)

_SEND_TO_RE = re.compile(
    r"\b(?:send|deliver|share|share\s+it)\s+(?:it\s+)?(?:to|on|via|through)\s+"
    r"(?:my\s+|the\s+|our\s+|your\s+)?(telegram|discord|slack|whatsapp|email)",
    re.IGNORECASE,
)

# ─── Research (delegate to research_policy) ─────────────────────────────

_SWARM_RE = re.compile(
    r"\bswarm\b|multi[-\s]?agent|delegate\s+to\s+(?:workers|agents)|fan[.\s]?out|/swarm"
    r"|جواق|عدة\s+وكلاء",
    re.IGNORECASE,
)

_CODE_EXEC_RE = re.compile(
    r"\b(run|execute|eval)\b.*\b(script|code|file|\.py|\.sh)\b"
    r"|شغل|نفذ",
    re.IGNORECASE,
)

_CODE_NEGATION_RE = re.compile(
    r"\b(what\s+is|explain|how\s+does|tell\s+me|describe|question)\b"
    r"|اشرح|ما هو|كيف",
    re.IGNORECASE,
)

_FILE_MGMT_RE = re.compile(
    r"\b(organize|move|copy|delete|rename|archive)\b.*\bfiles?\b"
    r"|نظم|انقل|احذف|أعد\s+تسمية",
    re.IGNORECASE,
)

_ANALYSIS_RE = re.compile(
    r"\b(analyz\w*|chart|visuali[sz]\w*|statistic\w*)\b.*\b(dataset|csv|xlsx|numbers|results?)\b"
    r"|حلل|قارن|رسم\s+بياني|إحصائيات",
    re.IGNORECASE,
)

_DOC_INTEL_RE = re.compile(
    r"\b(ingest|index\s+this\s+(?:document|pdf)|redact|document\s+search|document\s+library)\b"
    r"|/documents|/docs",
    re.IGNORECASE,
)

_REMIND_RE = re.compile(
    r"\bremind\s+me\b|\bschedule\s+a\s+(?:reminder|task)\b|\bset\s+a\s+reminder\b"
    r"|ذكرني|تذكير",
    re.IGNORECASE,
)


def _extract_format(text: str) -> str:
    m = _FORMAT_TOKENS.search(text)
    if not m:
        return ""
    token = m.group(0).lower().strip()
    return _FORMAT_MAP.get(token, "")


def _extract_delivery(text: str) -> str:
    m = _SEND_TO_RE.search(text)
    if m:
        return m.group(1).lower()
    return ""


def _has_document_attachment(attachments: list[dict] | None) -> tuple[bool, str]:
    """Return (has_doc_attachment, format_from_attachment)."""
    if not attachments:
        return False, ""
    for att in attachments:
        mime = str(att.get("mime") or "")
        filename = str(att.get("filename") or "")
        if _DOCUMENT_MIME_RE.match(mime) or _DOCUMENT_ATTACHMENT_RE.search(filename):
            ext = ""
            m = _DOCUMENT_ATTACHMENT_RE.search(filename)
            if m:
                ext = m.group(1).lower()
            elif "pdf" in mime:
                ext = "pdf"
            elif "word" in mime or "document" in mime:
                ext = "docx"
            elif "spreadsheet" in mime or "excel" in mime:
                ext = "xlsx"
            elif "presentation" in mime or "powerpoint" in mime:
                ext = "pptx"
            return True, ext
    return False, ""


def detect_acts(
    text: str,
    attachments: list[dict] | None = None,
) -> tuple[IntentAct, ...]:
    """Multi-label act detection. Returns all matching acts, no first-match-wins."""
    t = (text or "").strip()
    if not t:
        return (IntentAct(kind=ActKind.GENERAL, confidence=NO_MATCH),)

    acts: list[IntentAct] = []
    fmt = _extract_format(t)
    has_gen_verb = bool(_GENERATE_VERBS.search(t))
    has_read_verb = bool(_READ_VERBS.search(t))
    has_not_gen_verb = bool(_NOT_GENERATE_VERBS.search(t))
    has_doc_att, att_fmt = _has_document_attachment(attachments)

    # ── document_generate ────────────────────────────────────────────
    # Require: generate verb + (format token OR document attachment)
    # Negation: read verb only (no generate verb) → skip
    # Both read+generate ("don't just read, create") → emit
    if has_gen_verb and not (has_not_gen_verb and not fmt and not has_doc_att):
        effective_fmt = fmt or att_fmt
        if effective_fmt:
            slots: dict[str, Any] = {"format": effective_fmt}
            deliver = _extract_delivery(t)
            if deliver:
                slots["deliver_to"] = deliver
            # Inline content: no attachment but user provided content in the message
            if not has_doc_att and len(t) > 60:
                slots["inline_content"] = True
            acts.append(IntentAct(
                kind=ActKind.DOCUMENT_GENERATE,
                confidence=HIGH_PRECISION,
                slots=slots,
            ))
        elif has_doc_att:
            # Attachment + generate verb but no explicit format → use attachment format
            slots = {"format": att_fmt or "pdf"}
            deliver = _extract_delivery(t)
            if deliver:
                slots["deliver_to"] = deliver
            acts.append(IntentAct(
                kind=ActKind.DOCUMENT_GENERATE,
                confidence=HIGH_PRECISION,
                slots=slots,
            ))

    # ── research / research_deep (delegate to research_policy) ──────
    try:
        from kazma_core.agent.research_policy import (
            extract_topic_hint,
            is_deep_research_intent,
            is_research_intent,
        )

        if is_deep_research_intent(t):
            acts.append(IntentAct(
                kind=ActKind.RESEARCH_DEEP,
                confidence=HIGH_PRECISION,
                slots={"topic": extract_topic_hint(t)},
            ))
        elif is_research_intent(t):
            acts.append(IntentAct(
                kind=ActKind.RESEARCH,
                confidence=0.70,
            ))
    except ImportError:
        logger.debug("[intent_heuristics] research_policy not available")

    # ── swarm ────────────────────────────────────────────────────────
    if _SWARM_RE.search(t):
        acts.append(IntentAct(kind=ActKind.SWARM, confidence=0.80))

    # ── code_exec ────────────────────────────────────────────────────
    if _CODE_EXEC_RE.search(t) and not _CODE_NEGATION_RE.search(t):
        acts.append(IntentAct(kind=ActKind.CODE_EXEC, confidence=0.70))

    # ── file_mgmt ────────────────────────────────────────────────────
    if _FILE_MGMT_RE.search(t):
        acts.append(IntentAct(kind=ActKind.FILE_MGMT, confidence=0.70))

    # ── analysis ─────────────────────────────────────────────────────
    if _ANALYSIS_RE.search(t):
        acts.append(IntentAct(kind=ActKind.ANALYSIS, confidence=0.70))

    # ── document_intel ───────────────────────────────────────────────
    if _DOC_INTEL_RE.search(t) and not (has_gen_verb and fmt):
        acts.append(IntentAct(kind=ActKind.DOCUMENT_INTEL, confidence=0.70))

    # ── remind ───────────────────────────────────────────────────────
    if _REMIND_RE.search(t):
        acts.append(IntentAct(kind=ActKind.REMIND, confidence=0.70))

    if not acts:
        return (IntentAct(kind=ActKind.GENERAL, confidence=NO_MATCH),)

    return tuple(acts)
