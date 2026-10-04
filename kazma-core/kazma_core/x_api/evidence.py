"""Deterministic source timestamp and provenance checks before factual release."""

from __future__ import annotations

import hashlib
import math
import time
from datetime import UTC, datetime
from typing import Any


def _publication_time(value: Any, *, now: float | None = None) -> float | None:
    """Accept a past finite epoch or an unambiguous ISO date/time, never truthiness."""
    now = time.time() if now is None else now
    if type(value) in (int, float):
        try:
            stamp = float(value)
        except OverflowError:
            return None
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if len(text) == 10:
                parsed = parsed.replace(tzinfo=UTC)
            elif parsed.tzinfo is None:
                return None
            stamp = parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    else:
        return None
    return stamp if math.isfinite(stamp) and 0 < stamp <= now else None


def source_hold(source: dict[str, Any], *, max_age_days: int = 30, now: float | None = None) -> str:
    """A stale, shortened or unverifiable passage needs attended factual review."""
    now = time.time() if now is None else now
    stamp = _publication_time(source.get("published_at"), now=now)
    if stamp is None:
        return "Source publication time is missing, invalid or future-dated."
    if now - stamp > max_age_days * 86400:
        return "Source exceeds this card's permitted factual evidence age."
    content = source.get("content")
    if (source.get("truncated") is not False or not isinstance(content, str) or not content
            or source.get("content_hash") != hashlib.sha256(content.encode("utf-8")).hexdigest()):
        return "Source passage is incomplete or its content hash cannot be verified."
    if not all(isinstance(source.get(key), str) and source[key] for key in ("library_id", "document_id", "version_id")):
        return "Source lacks bound library, document or version provenance."
    return ""
