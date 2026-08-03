"""Evidence / claim extraction for deep research (map step).

Default path is **heuristic** (no nested LLM) so pipelines stay cheap.
Optional LLM map via ``extract_claims_llm`` when enabled.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "Claim",
    "extract_claims_heuristic",
    "extract_claims_from_file",
    "write_claims_json",
    "claims_to_markdown",
]

logger = logging.getLogger(__name__)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")
_URL_IN_TEXT = re.compile(r"https?://[^\s\)\]\>\"']+")


@dataclass
class Claim:
    text: str
    source_path: str = ""
    source_url: str = ""
    quote: str = ""
    confidence: float = 0.5
    method: str = "heuristic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load(path: str | Path) -> str | None:
    p = Path(path)
    if not p.is_file():
        # try workspace-relative
        try:
            from kazma_core.tools.file_write import _get_workspace

            p2 = _get_workspace() / path
            if p2.is_file():
                p = p2
        except Exception:
            pass
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _guess_url(body: str) -> str:
    m = re.search(r"(?im)^(?:source|url|canonical)[:\s]+(\S+)", body or "")
    if m and m.group(1).startswith("http"):
        return m.group(1).strip()
    m2 = _URL_IN_TEXT.search(body or "")
    return m2.group(0) if m2 else ""


def extract_claims_heuristic(
    body: str,
    *,
    source_path: str = "",
    source_url: str = "",
    max_claims: int = 8,
) -> list[Claim]:
    """Pick informative sentences as weak claims + short quotes."""
    text = (body or "").strip()
    if not text:
        return []
    url = source_url or _guess_url(text)
    # Drop markdown chrome
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("```"):
            continue
        if s.startswith("|") or s.startswith("- [") or s.startswith("* ["):
            continue
        lines.append(s)
    blob = " ".join(lines)
    sents = [s.strip() for s in _SENT_SPLIT.split(blob) if s.strip()]
    scored: list[tuple[float, str]] = []
    for s in sents:
        if len(s) < 40 or len(s) > 320:
            continue
        # Prefer sentences with numbers, named entities-ish capitals, verbs of fact
        sc = 0.0
        if re.search(r"\d", s):
            sc += 1.0
        if re.search(
            r"\b(is|are|was|were|has|have|provides|supports|shows|found|according)\b",
            s,
            re.I,
        ):
            sc += 0.8
        caps = len(re.findall(r"\b[A-Z][a-z]{2,}\b", s))
        sc += min(1.5, caps * 0.15)
        if s.lower().startswith(("this ", "that ", "it ", "we ", "our ")):
            sc -= 0.5
        scored.append((sc, s))
    scored.sort(key=lambda x: -x[0])
    out: list[Claim] = []
    seen: set[str] = set()
    for sc, s in scored:
        key = s[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        quote = s if len(s) <= 180 else s[:177] + "…"
        out.append(
            Claim(
                text=s,
                source_path=source_path,
                source_url=url,
                quote=quote,
                confidence=min(0.85, 0.4 + sc * 0.1),
                method="heuristic",
            )
        )
        if len(out) >= max(1, min(20, int(max_claims))):
            break
    return out


def extract_claims_from_file(
    path: str,
    *,
    source_url: str = "",
    max_claims: int = 8,
) -> list[Claim]:
    body = _load(path)
    if body is None:
        return []
    return extract_claims_heuristic(
        body, source_path=path, source_url=source_url, max_claims=max_claims
    )


def write_claims_json(path: str | Path, claims: list[Claim]) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = [c.to_dict() for c in claims]
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(p)


def claims_to_markdown(claims: list[Claim], *, title: str = "Evidence claims") -> str:
    if not claims:
        return f"## {title}\n\n_(none extracted)_\n"
    lines = [f"## {title}", ""]
    for i, c in enumerate(claims, 1):
        src = c.source_url or c.source_path or "?"
        lines.append(f"{i}. {c.text}")
        lines.append(f"   - source: `{src}`")
        if c.quote and c.quote != c.text:
            lines.append(f"   - quote: \"{c.quote}\"")
        lines.append("")
    return "\n".join(lines)
