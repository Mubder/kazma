"""Current-fact rotation — single-valued entitlements that must supersede.

Problem this solves
-------------------
``memory_store`` historically wrote every explicit remember as::

    mutate_belief(subject="user", predicate="noted", object=<full text>,
                  predicate_type="set")

So weekly Grok/ZCode reset updates stacked as many active "user noted …"
beliefs. Recall returned all of them; the model "picked newest" by judgment.

Industry contract
-----------------
- **Current fact** (one active row): ``user.grok_next_reset = <iso or local>``
- **History**: closed via functional supersede (``valid_until`` / ``supersedes_id``)
- **Reminders/cron jobs**: stay set-valued (``has_reminder`` never-supersede)

This module classifies free-text + metadata into functional mutations so
the store tool and extractors share one SoT.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "FUNCTIONAL_CURRENT_PREDICATES",
    "is_functional_current_predicate",
    "parse_current_facts",
    "normalize_service_slug",
]

# Canonical single-valued entitlement / schedule predicates.
# Also matched by suffix rules in :func:`is_functional_current_predicate`.
FUNCTIONAL_CURRENT_PREDICATES = frozenset(
    {
        "next_reset",
        "weekly_reset",
        "quota_reset",
        "grok_next_reset",
        "supergrok_next_reset",
        "zcode_next_reset",
        "claude_next_reset",
        "cursor_next_reset",
        "copilot_next_reset",
        "openai_next_reset",
        "chatgpt_next_reset",
    }
)

_FUNCTIONAL_SUFFIXES = (
    "_next_reset",
    "_weekly_reset",
    "_quota_reset",
    "_reset_at",
    "_renews_at",
)

# Service name → stable predicate prefix (snake_case).
_SERVICE_ALIASES: dict[str, str] = {
    "grok": "grok",
    "supergrok": "grok",
    "super grok": "grok",
    "grok next": "grok",
    "xai": "grok",
    "zcode": "zcode",
    "z code": "zcode",
    "claude": "claude",
    "anthropic": "claude",
    "cursor": "cursor",
    "copilot": "copilot",
    "github copilot": "copilot",
    "openai": "openai",
    "chatgpt": "chatgpt",
    "gpt": "chatgpt",
}


def normalize_service_slug(name: str) -> str:
    """Map a free-text product name to a stable slug (e.g. SuperGrok → grok)."""
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not key:
        return ""
    if key in _SERVICE_ALIASES:
        return _SERVICE_ALIASES[key]
    # Strip trailing "next" / "weekly" noise
    key2 = re.sub(r"\b(next|weekly|pool|quota)\b", "", key).strip()
    key2 = re.sub(r"\s+", " ", key2)
    if key2 in _SERVICE_ALIASES:
        return _SERVICE_ALIASES[key2]
    slug = re.sub(r"[^a-z0-9]+", "_", key2).strip("_")
    return slug[:40]


def is_functional_current_predicate(predicate: str) -> bool:
    """True when *predicate* is a single-valued current fact (must supersede)."""
    p = (predicate or "").strip().lower().replace(" ", "_")
    if not p:
        return False
    if p in FUNCTIONAL_CURRENT_PREDICATES:
        return True
    return any(p.endswith(suf) for suf in _FUNCTIONAL_SUFFIXES)


def _meta_facts(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Explicit metadata wins: predicate + object, or facts[]."""
    if not meta or not isinstance(meta, dict):
        return []
    out: list[dict[str, Any]] = []

    # facts: [{predicate, object, ...}, ...]
    raw_facts = meta.get("facts")
    if isinstance(raw_facts, list):
        for item in raw_facts:
            if not isinstance(item, dict):
                continue
            pred = str(item.get("predicate") or "").strip()
            obj = str(item.get("object") or item.get("value") or "").strip()
            if pred and obj:
                out.append(_fact(pred, obj, item.get("subject") or "user"))

    pred = str(meta.get("predicate") or meta.get("key") or "").strip()
    obj = str(meta.get("object") or meta.get("value") or "").strip()
    if pred and obj:
        out.append(_fact(pred, obj, meta.get("subject") or "user"))

    # service + next_reset value
    service = str(meta.get("service") or meta.get("product") or "").strip()
    reset = str(
        meta.get("next_reset")
        or meta.get("reset_at")
        or meta.get("weekly_reset")
        or ""
    ).strip()
    if service and reset:
        slug = normalize_service_slug(service)
        if slug:
            out.append(_fact(f"{slug}_next_reset", reset, "user"))

    return out


def _fact(predicate: str, obj: str, subject: str = "user") -> dict[str, Any]:
    pred = (predicate or "").strip().lower().replace(" ", "_")
    ptype = "functional" if is_functional_current_predicate(pred) else "set"
    return {
        "subject": str(subject or "user").strip() or "user",
        "predicate": pred,
        "object": (obj or "").strip()[:1000],
        "predicate_type": ptype,
        "confidence": 1.0,
        "importance": 5,
    }


# "My Grok weekly next reset: August 10, 02:48 local"
# "ZCode weekly next reset: 2026-08-05 13:47 +8"
_RESET_LINE = re.compile(
    r"(?:my\s+)?(?P<svc>grok(?:\s+next)?|supergrok|zcode|z\s*code|claude|cursor|"
    r"copilot|openai|chatgpt|gpt)"
    r"(?:\s+(?:weekly|pool|quota))?"
    r"(?:\s+next)?\s+reset"
    r"(?:\s*(?:time|at|is|:))?\s*"
    r"(?P<val>.+?)(?=(?:\.?\s*(?:and|my)\s+(?:grok|zcode|claude|cursor))|$)",
    re.IGNORECASE | re.DOTALL,
)

# Fallback: "Next reset: August 10, 02:48" near a service name earlier in text
_BARE_NEXT_RESET = re.compile(
    r"(?:next\s+reset|reset\s+time)\s*:\s*(?P<val>[^\n]+)",
    re.IGNORECASE,
)


def parse_current_facts(
    text: str,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Parse *text*/*meta* into typed belief payloads for ``mutate_belief``.

    Returns an empty list when nothing looks like a rotating current fact
    (caller should fall back to ``noted`` / set append).
    """
    facts = _meta_facts(meta)
    if facts:
        # Deduplicate by (subject, predicate) — last wins
        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for f in facts:
            by_key[(f["subject"], f["predicate"])] = f
        return list(by_key.values())

    body = (text or "").strip()
    if not body:
        return []

    found: list[dict[str, Any]] = []
    for m in _RESET_LINE.finditer(body):
        slug = normalize_service_slug(m.group("svc"))
        val = (m.group("val") or "").strip().rstrip(".,;")
        # Trim trailing "And my ZCode…" leakage
        val = re.split(r"\s+And\s+my\s+", val, maxsplit=1, flags=re.I)[0].strip()
        val = re.sub(r"\s+", " ", val)[:500]
        if slug and val and len(val) >= 4:
            found.append(_fact(f"{slug}_next_reset", val))

    if found:
        by_key = {(f["subject"], f["predicate"]): f for f in found}
        return list(by_key.values())

    # Single bare "Next reset: …" only if exactly one service is mentioned
    bare = _BARE_NEXT_RESET.search(body)
    if bare:
        services = []
        for name in ("grok", "supergrok", "zcode", "claude", "cursor", "copilot", "chatgpt"):
            if re.search(rf"\b{re.escape(name)}\b", body, re.I):
                services.append(normalize_service_slug(name))
        services = list(dict.fromkeys(s for s in services if s))
        if len(services) == 1:
            val = bare.group("val").strip().rstrip(".,;")
            if val:
                return [_fact(f"{services[0]}_next_reset", val)]

    return []
