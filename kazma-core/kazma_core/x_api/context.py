"""Source completeness is explicit; missing material never means verified text."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ContextSnapshot:
    source_id: str = ""
    author_handle: str = ""
    text: str = ""
    verified_source: bool = False
    author_resolved: bool = False
    truncated: bool = False
    media_present: bool = False
    missing_quote: bool = False
    fallback_text: bool = False
    quotes: tuple[dict[str, Any], ...] = ()

    def missing(self) -> tuple[str, ...]:
        flags = (("unverified_source", not self.verified_source), ("unresolved_author", not self.author_resolved),
                 ("truncated_text", self.truncated), ("unresolved_media", self.media_present),
                 ("missing_quote", self.missing_quote), ("fallback_text", self.fallback_text), ("empty_source", not self.text.strip()))
        return tuple(name for name, missing in flags if missing)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "missing": list(self.missing()), "complete": not self.missing()}

    @classmethod
    def from_x(cls, post: dict[str, Any], author_handle: str, included: dict[str, Any]) -> ContextSnapshot:
        text = source_text(post)
        quoted = [str(ref.get("id") or "") for ref in (post.get("referenced_tweets") or post.get("referenced_posts") or []) if ref.get("type") == "quoted"]
        quotes = tuple({"id": ident, "author_id": str(included[ident].get("author_id") or ""),
                        "text": source_text(included[ident])} for ident in quoted if ident in included)
        return cls(source_id=str(post.get("id") or ""), author_handle=author_handle, text=text,
                   verified_source=bool(post.get("id")), author_resolved=bool(post.get("author_id") and author_handle),
                   truncated=bool(post.get("truncated") or post.get("_kazma_context_incomplete")) or text.rstrip().endswith(("…", "..."))
                   or any(q["text"].rstrip().endswith(("…", "...")) for q in quotes)
                   or any(included[ident].get("_kazma_context_incomplete") or included[ident].get("truncated") for ident in quoted if ident in included),
                   media_present=bool((post.get("attachments") or {}).get("media_keys"))
                   or any((included[ident].get("attachments") or {}).get("media_keys") for ident in quoted if ident in included),
                   missing_quote=len(quotes) != len(quoted) or any(not q["text"] or not q["author_id"] for q in quotes), quotes=quotes)


def source_text(post: dict[str, Any]) -> str:
    """Accept current and legacy long-form response names, preserving exact text."""
    return str((post.get("note_tweet") or post.get("note_post") or {}).get("text") or post.get("text") or "")
