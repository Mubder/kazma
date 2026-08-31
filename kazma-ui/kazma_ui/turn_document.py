"""TurnDocument parts — reasoning / tool / status / hitl / text.

A chat turn is not one string. Supervisor working notes live in
``reasoning``; tools in ``tool``; the user-facing answer in ``text``.
``content`` and ``activity`` are derived so older clients keep working.

Merge never deletes reasoning/tool/status/hitl. Replacing ``text`` with a
shorter final (the tweet-post hop) moves the previous text into
``reasoning`` instead of erasing it.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "activity_of",
    "merge_parts",
    "parts_from_stream",
    "split_stream_and_final",
    "text_of",
]


def text_of(parts: list[dict[str, Any]] | None) -> str:
    """Last ``text`` part, else empty."""
    text = ""
    for p in parts or []:
        if isinstance(p, dict) and p.get("type") == "text":
            t = str(p.get("text") or "").strip()
            if t:
                text = t
    return text


def activity_of(parts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Workbench rows the restored CoT accordion already knows how to render."""
    rows: list[dict[str, Any]] = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        kind = str(p.get("type") or "")
        if kind == "reasoning":
            detail = str(p.get("text") or "")
            if not detail.strip():
                continue
            rows.append({
                "kind": "thought",
                "title": "Working notes",
                "detail": detail,
                "state": "done",
            })
        elif kind == "tool":
            rows.append({
                "kind": "tool",
                "title": str(p.get("name") or p.get("title") or "tool"),
                "detail": str(p.get("result") or p.get("detail") or p.get("args") or ""),
                "state": str(p.get("state") or "done"),
                **({"ts": p["ts"]} if p.get("ts") else {}),
            })
        elif kind == "status":
            title = str(p.get("title") or "").strip()
            if not title:
                continue
            rows.append({
                "kind": "status",
                "title": title,
                "state": str(p.get("state") or "done"),
                **({"ts": p["ts"]} if p.get("ts") else {}),
            })
        elif kind == "hitl":
            rows.append({
                "kind": "status",
                "title": "Waiting for approval",
                "detail": str(p.get("tool") or p.get("detail") or ""),
                "state": "info",
                **({"ts": p["ts"]} if p.get("ts") else {}),
            })
    return rows


def split_stream_and_final(streamed: str, final: str) -> tuple[str, str]:
    """Return ``(reasoning, text)``. Prefix-related hops are one text part."""
    streamed_s = str(streamed or "").strip()
    final_s = str(final or "").strip()
    if not streamed_s and not final_s:
        return "", ""
    if not streamed_s:
        return "", final_s
    if not final_s:
        return "", streamed_s
    if streamed_s == final_s:
        return "", final_s
    probe = streamed_s[:80] if len(streamed_s) > 80 else streamed_s
    if final_s.startswith(probe) or streamed_s.startswith(
        final_s[:80] if len(final_s) > 80 else final_s
    ):
        return "", final_s if len(final_s) >= len(streamed_s) else streamed_s
    return streamed_s, final_s


def _part_key(part: dict[str, Any]) -> tuple[Any, ...]:
    kind = str(part.get("type") or "")
    if kind == "text":
        return ("text",)
    if kind == "reasoning":
        return ("reasoning", str(part.get("text") or "")[:240])
    if kind == "tool":
        return (
            "tool",
            str(part.get("name") or part.get("title") or ""),
            str(part.get("state") or ""),
            str(part.get("result") or part.get("detail") or "")[:80],
        )
    if kind == "status":
        return ("status", str(part.get("title") or ""))
    if kind == "hitl":
        return ("hitl", str(part.get("tool") or part.get("detail") or ""))
    return (kind, repr(part)[:80])


def _activity_to_parts(activity: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in activity or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if kind == "tool":
            out.append({
                "type": "tool",
                "name": str(row.get("title") or "tool"),
                "result": str(row.get("detail") or ""),
                "state": str(row.get("state") or "done"),
                **({"ts": row["ts"]} if row.get("ts") else {}),
            })
        elif kind == "thought":
            detail = str(row.get("detail") or "")
            if detail.strip():
                out.append({"type": "reasoning", "text": detail})
        elif kind in ("status", "info"):
            title = str(row.get("title") or "").strip()
            if title:
                out.append({
                    "type": "status",
                    "title": title,
                    "state": str(row.get("state") or "done"),
                    **({"ts": row["ts"]} if row.get("ts") else {}),
                })
    return out


def merge_parts(
    existing: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Union of non-text parts; ``text`` last-write-wins; displaced text → reasoning."""
    existing = [p for p in (existing or []) if isinstance(p, dict)]
    incoming = [p for p in (incoming or []) if isinstance(p, dict)]
    old_text = text_of(existing)
    new_text = text_of(incoming)

    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def _add(part: dict[str, Any]) -> None:
        if part.get("type") == "text":
            return
        key = _part_key(part)
        if key in seen:
            return
        seen.add(key)
        out.append(dict(part))

    for p in existing:
        _add(p)
    for p in incoming:
        _add(p)

    if (
        old_text
        and new_text
        and old_text.strip() != new_text.strip()
        and not new_text.startswith(old_text[:80] if len(old_text) > 80 else old_text)
    ):
        key = _part_key({"type": "reasoning", "text": old_text})
        if key not in seen:
            out.insert(0, {"type": "reasoning", "text": old_text})
            seen.add(key)

    chosen = new_text or old_text
    if chosen:
        out.append({"type": "text", "text": chosen})
    return out


def parts_from_stream(
    *,
    streamed: str = "",
    final: str = "",
    activity: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build parts from a live token buffer, a final hop, and CoT activity."""
    reasoning, text = split_stream_and_final(streamed, final)
    incoming: list[dict[str, Any]] = []
    if reasoning:
        incoming.append({"type": "reasoning", "text": reasoning})
    incoming.extend(_activity_to_parts(activity))
    if text:
        incoming.append({"type": "text", "text": text})
    return merge_parts([], incoming)
