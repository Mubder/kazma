"""R0 baselines — golden topics + a deterministic rubric for research reports.

Does not require live network. Use after a pipeline run to score artifacts.

The rubric scores the report's content more than its shape (2026-11, FA-235):
it was structural only -- a title, headings, a Sources section, a length --
so a long report with every claim uncited and paragraphs repeated scored
100/100. Most of the score now comes from what the reader relies on: the
paragraphs cite their sources, the listed sources are used, every cited
link is listed, nothing is said twice, and the report covers the question.
Still no model judges it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "GOLDEN_TOPICS",
    "RubricScore",
    "score_report_markdown",
    "score_report_file",
    "evaluate_report_path",
]

# Baseline topics for manual / live deep-research regression (not CI-gated).
GOLDEN_TOPICS: list[dict[str, str]] = [
    {
        "id": "python-gil",
        "topic": "Python GIL concurrency limitations and free-threading plans",
        "notes": "Should cite docs.python.org or PEPs when possible",
    },
    {
        "id": "sqlite-wal",
        "topic": "SQLite WAL mode benefits and concurrency caveats",
        "notes": "Prefer sqlite.org documentation",
    },
    {
        "id": "oauth2-pkce",
        "topic": "OAuth 2.0 PKCE why public clients need it",
        "notes": "RFC / OAuth specs preferred",
    },
    {
        "id": "rag-evaluation",
        "topic": "How to evaluate RAG systems: retrieval metrics and faithfulness",
        "notes": "Multi-source survey style",
    },
    {
        "id": "arabic-nlp",
        "topic": "Challenges in Arabic NLP dialect and morphology",
        "notes": "Academic + practical sources",
    },
]


_URL = re.compile(r"https?://[^\s\)\]\>\"']+")
_FILE_REF = re.compile(r"\bresearch/[\w./-]+\.md\b")
#: A paragraph shorter than this is a caption or a list line, not a claim.
_SUBSTANTIVE_CHARS = 120


def _split_sources(text: str) -> tuple[str, str]:
    """``(body, sources section)``: the body is everything before the last
    ``## Sources`` heading (in either language), the section what follows."""
    found = list(re.finditer(r"^##\s+(sources|\u0627\u0644\u0645\u0635\u0627\u062f\u0631)\b.*$",
                             text, re.I | re.M))
    if not found:
        return text, ""
    cut = found[-1]
    return text[: cut.start()], text[cut.end():]


def _paragraphs(body: str) -> list[str]:
    """The body's paragraphs of prose: no heading, code, table or short line."""
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    out: list[str] = []
    for block in re.split(r"\n\s*\n", body):
        lines = [line for line in block.strip().splitlines()
                 if line.strip() and not line.lstrip().startswith(("#", "|", "---"))]
        para = " ".join(line.strip() for line in lines)
        if len(para) >= _SUBSTANTIVE_CHARS:
            out.append(para)
    return out


def _normal(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _url_key(url: str) -> str:
    return url.rstrip(".,;:").rstrip("/").lower()


@dataclass
class RubricScore:
    """Deterministic checks of shape and content — not LLM-as-judge."""

    ok: bool
    score: float  # 0–100
    checks: dict[str, bool] = field(default_factory=dict)
    detail: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "score": self.score,
            "checks": self.checks,
            "detail": self.detail,
            "meta": self.meta,
        }


def score_report_markdown(
    report_md: str,
    *,
    min_sources: int = 4,
    min_chars: int = 1500,
    topic: str = "",
) -> RubricScore:
    """Score a research report: its shape a little, its content mostly.

    *topic*: the question the report answers; with it, the body must cover
    half of its content words (EN/AR, ``memory.query_terms``).
    """
    text = report_md or ""
    checks: dict[str, bool] = {}
    detail: list[str] = []

    checks["has_title"] = bool(re.search(r"^#\s+\S", text, re.M))
    checks["min_length"] = len(text) >= min_chars
    # Either language's heading (the Arabic report's "المصادر" failed the English-only check).
    checks["has_sources_section"] = bool(_split_sources(text)[1].strip())
    urls = re.findall(r"https?://[^\s\)\]\>\"']+", text)
    unique_urls = sorted(set(urls))
    checks["min_urls"] = len(unique_urls) >= max(1, min_sources // 2)
    domains = set()
    for u in unique_urls:
        m = re.match(r"https?://([^/]+)", u)
        if m:
            domains.add(m.group(1).lower().removeprefix("www."))
    checks["min_domains"] = len(domains) >= min(2, max(1, min_sources // 2))
    checks["has_headings"] = len(re.findall(r"^##\s+", text, re.M)) >= 3
    checks["not_error_only"] = not text.strip().lower().startswith("error:")
    # Pipeline log is fine but report should not be ONLY the log
    checks["has_body"] = len(text) > 800 and (
        "key findings" in text.lower()
        or "executive" in text.lower()
        or "conclusion" in text.lower()
        or "background" in text.lower()
    )

    # ── content (FA-235) ─────────────────────────────────────────────
    body, sources_section = _split_sources(text)
    paragraphs = _paragraphs(body)
    cited = [p for p in paragraphs if _URL.search(p) or _FILE_REF.search(p)]
    checks["cited_paragraphs"] = bool(paragraphs) and len(cited) >= 0.5 * len(paragraphs)
    listed = {_url_key(u) for u in _URL.findall(sources_section)}
    in_body = {_url_key(u) for u in _URL.findall(body)}
    checks["sources_used"] = bool(listed) and len(listed & in_body) >= 0.5 * len(listed)
    checks["citations_resolve"] = bool(in_body) and bool(listed) and in_body <= listed
    normal = [_normal(p) for p in paragraphs]
    sentences = [s for s in (_normal(x) for x in re.split(r"(?<=[.!?\u061f])\s+", body)) if len(s) > 40]
    repeated = len(normal) - len(set(normal)) + (len(sentences) - len(set(sentences)))
    checks["no_repetition"] = repeated == 0
    if topic.strip():
        from kazma_core.memory.query_terms import content_terms, coverage

        terms = content_terms(topic)
        checks["answers_topic"] = not terms or coverage(terms, body) >= 0.5
    else:
        checks["answers_topic"] = True  # nothing to compare: not held against the report

    # Shape is a small part of the score; the content is most of it.
    weights = {
        "has_title": 5,
        "min_length": 5,
        "has_sources_section": 5,
        "has_headings": 5,
        "not_error_only": 5,
        "has_body": 5,
        "min_urls": 5,
        "min_domains": 5,
        "cited_paragraphs": 20,
        "sources_used": 10,
        "citations_resolve": 10,
        "no_repetition": 10,
        "answers_topic": 10,
    }
    score = 0.0
    for k, w in weights.items():
        if checks.get(k):
            score += w
        else:
            detail.append(f"fail:{k}")

    ok = score >= 70 and checks.get("not_error_only", False) and checks.get("cited_paragraphs", False)
    return RubricScore(
        ok=ok,
        score=round(score, 1),
        checks=checks,
        detail=detail,
        meta={
            "chars": len(text),
            "url_count": len(unique_urls),
            "domain_count": len(domains),
            "min_sources_target": min_sources,
            "paragraphs": len(paragraphs),
            "cited_paragraphs": len(cited),
            "topic_checked": bool(topic.strip()),
        },
    )


def score_report_file(path: str | Path, **kwargs: Any) -> RubricScore:
    p = Path(path)
    # Kazma's own databases are refused here as at every other door
    # (store_registry, AGENTS.md section 37): on an install whose workspace
    # holds kazma-data/, these read-tier readers returned raw store bytes
    # that file_read refuses (audit 2026-10-26 AUD-086).
    from kazma_core.store_registry import is_kazma_store

    if is_kazma_store(p):
        return RubricScore(ok=False, score=0.0, checks={"file_readable": False},
                           detail=["fail:kazma_store"])
    if not p.is_file():
        return RubricScore(
            ok=False,
            score=0.0,
            checks={"file_exists": False},
            detail=["fail:file_missing"],
        )
    try:
        body = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return RubricScore(
            ok=False,
            score=0.0,
            checks={"file_readable": False},
            detail=[f"fail:read:{exc}"],
        )
    return score_report_markdown(body, **kwargs)


def evaluate_report_path(
    path: str | Path,
    *,
    min_sources: int = 4,
    min_chars: int = 1500,
) -> dict[str, Any]:
    """Score a report and return a UI/API-friendly dict (R4)."""
    rub = score_report_file(path, min_sources=min_sources, min_chars=min_chars)
    d = rub.to_dict()
    d["path"] = str(path)
    d["grade"] = (
        "pass"
        if rub.ok
        else ("weak" if rub.score >= 40 else "fail")
    )
    return d
