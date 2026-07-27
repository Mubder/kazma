"""Research intent detection + soft multi-source guardrails.

Prevents the shallow attractor: one ``web_search`` + snippet summary when
the user asked for thorough research. Used by the supervisor graph and
product-knowledge protocol.
"""

from __future__ import annotations

import os
import re
from typing import Any

__all__ = [
    "RESEARCH_PROTOCOL",
    "is_deep_research_intent",
    "is_research_intent",
    "soft_min_sources",
    "should_nudge_more_sources",
]

# Injected into product knowledge / tool guidance.
RESEARCH_PROTOCOL = """
### Research protocol (follow when the user asks to research / investigate / report)
1. Open with a short ```plan (3–7 steps) when you will use tools.
2. Run **≥2 distinct** `web_search` queries (different angles), not one query only.
3. Acquire **≥2 full sources** via `read_url_to_file` (preferred) or multi-window `read_url`.
4. For long pages: `digest_research_file` before writing conclusions.
5. For documentation roots: `crawl_site` or `knowledge_ingest_site` / `knowledge_search`.
6. For comprehensive papers / "deep research": call `run_research_pipeline` (or `/research deep …`).
7. Final answer: structured sections + **URL citations**. Never claim thorough research from titles/snippets alone.
Casual factual Q&A may use a single search.
""".strip()

_DEEP_RE = re.compile(
    r"("
    r"deep\s*research|research\s+thorough\w*|thoroughly\s+research|"
    r"comprehensive\s+(report|research|paper|analysis)|"
    r"full\s+report|research\s+paper|in[- ]depth|"
    r"write\s+a\s+(report|paper)|investigate\s+deeply|"
    r"/research\s+deep"
    r")",
    re.I,
)

_RESEARCH_RE = re.compile(
    r"\b("
    r"research|investigate|look\s+up|find\s+out|what\s+do\s+sources|"
    r"survey|literature|compare\s+vendors|market\s+analysis"
    r")\b",
    re.I,
)

_ACQUIRE_TOOLS = frozenset(
    {
        "read_url",
        "read_url_to_file",
        "crawl_site",
        "crawl_page",
        "digest_research_file",
        "list_research_chunks",
        "read_research_chunk",
        "summarize_research_file",
        "run_research_pipeline",
        "synthesize_from_digests",
        "knowledge_search",
        "knowledge_ingest_url",
        "knowledge_ingest_site",
    }
)


def is_deep_research_intent(text: str) -> bool:
    return bool(text and _DEEP_RE.search(text))


def is_research_intent(text: str) -> bool:
    if not text:
        return False
    if is_deep_research_intent(text):
        return True
    return bool(_RESEARCH_RE.search(text))


def soft_min_sources() -> int:
    raw = (os.environ.get("KAZMA_RESEARCH_MIN_SOURCES") or "2").strip()
    try:
        return max(1, min(8, int(raw)))
    except ValueError:
        return 2


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                parts = [
                    str(p.get("text", ""))
                    for p in c
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                return " ".join(parts)
    return ""


def should_nudge_more_sources(
    messages: list[dict[str, Any]],
    tool_names_this_turn: list[str],
    *,
    already_nudged: bool = False,
) -> str | None:
    """Return a system nudge string if research is too shallow, else None."""
    if already_nudged:
        return None
    user = _last_user_text(messages)
    if not is_research_intent(user):
        return None
    # Default: only deep-worded requests get the soft min-sources gate.
    mode = (os.environ.get("KAZMA_RESEARCH_SOFT_NUDGE") or "deep").strip().lower()
    if mode in ("0", "off", "false", "no"):
        return None
    if mode == "deep" and not is_deep_research_intent(user):
        return None

    names = [str(n or "").lower() for n in tool_names_this_turn]
    if not names:
        return None
    acquire = sum(1 for n in names if n in _ACQUIRE_TOOLS)
    searches = sum(1 for n in names if n in ("web_search", "web_search_duckduckgo"))
    min_src = soft_min_sources()

    if acquire >= min_src:
        return None
    if searches == 0 and acquire == 0:
        return None
    # Search without enough full-source acquires
    if acquire < min_src:
        return (
            "RESEARCH DEPTH: You have not acquired enough full sources yet "
            f"(need ≥{min_src} of read_url_to_file / read_url / crawl / digests). "
            "Do **not** write the final comprehensive answer from search snippets alone. "
            "Call tools to fetch and digest primary pages now, then answer with citations."
        )
    return None
