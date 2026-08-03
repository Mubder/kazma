"""R0 baselines — golden topics + hard structural rubric for research reports.

Does not require live network. Use after a pipeline run to score artifacts.
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


@dataclass
class RubricScore:
    """Hard structural checks — not LLM-as-judge."""

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
) -> RubricScore:
    """Score a research report body for structural industry-ish floors."""
    text = report_md or ""
    checks: dict[str, bool] = {}
    detail: list[str] = []

    checks["has_title"] = bool(re.search(r"^#\s+\S", text, re.M))
    checks["min_length"] = len(text) >= min_chars
    checks["has_sources_section"] = bool(
        re.search(r"^##\s+sources\b", text, re.I | re.M)
    )
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

    weights = {
        "has_title": 10,
        "min_length": 15,
        "has_sources_section": 15,
        "min_urls": 15,
        "min_domains": 15,
        "has_headings": 10,
        "not_error_only": 10,
        "has_body": 10,
    }
    score = 0.0
    for k, w in weights.items():
        if checks.get(k):
            score += w
        else:
            detail.append(f"fail:{k}")

    ok = score >= 70 and checks.get("not_error_only", False)
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
        },
    )


def score_report_file(path: str | Path, **kwargs: Any) -> RubricScore:
    p = Path(path)
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
