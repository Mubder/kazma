"""TurnDocument parts — reasoning / tool / status / hitl / text.

A chat turn is not one string. Supervisor working notes live in
``reasoning``; tools in ``tool``; the user-facing answer in ``text``.
``content`` and ``activity`` are derived so older clients keep working.

Merge never deletes reasoning/tool/status/hitl. Replacing ``text`` with a
shorter final (the tweet-post hop) moves the previous text into
``reasoning`` instead of erasing it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "HITL_RANK",
    "TURN_SCHEMA_VERSION",
    "activity_of",
    "assign_interrupt_id",
    "fnv1a32",
    "hitl_part_of",
    "hitl_parts_of",
    "hitl_rank",
    "hydrate_message",
    "merge_reasoning_part",
    "legacy_turn_id",
    "make_interrupt_id",
    "merge_hitl_part",
    "merge_parts",
    "merge_tool_part",
    "part_key_str",
    "parts_from_stream",
    "split_stream_and_final",
    "text_of",
    "tool_call_id_of",
    "tool_rank",
]

#: Wire/storage schema for a turn row. Bumped when the SHAPE of ``parts``,
#: ``activity`` or the row's own metadata changes in a way an older client
#: could misread. ``docs/plans/UNIFIED_TURN_BLOCK.md`` §6 requires it to
#: exist before such a change, not after: a reader with no version has no
#: way to tell "field absent" from "field not yet invented".
#:
#: 1 — pre-versioned rows (no ``schema`` key at all).
#: 2 — activity rows carry ``id``; tool parts carry ``call_id``; rows carry
#:     ``rev``.
TURN_SCHEMA_VERSION = 2

# Monotonic HITL part states. Replay of approval_required after Approve
# must not walk this backwards for the same interrupt_id.
HITL_RANK: dict[str, int] = {
    "pending": 0,
    "approved": 1,
    "denied": 1,
    "inflight": 2,
    "settled": 3,
    "done": 3,
    "timeout": 3,
    "error": 3,
}

_hitl_emit_seq: dict[str, int] = {}


def hitl_rank(state: str | None) -> int:
    return HITL_RANK.get(str(state or "pending").strip().lower(), 0)


def tool_rank(state: str | None) -> int:
    """A tool call only moves forward: running -> anything terminal."""
    s = str(state or "done").strip().lower()
    return 0 if s in ("running", "pending", "") else 1


def tool_call_id_of(part: dict[str, Any] | None) -> str:
    """The tool call this part belongs to, as the graph named it."""
    if not isinstance(part, dict):
        return ""
    return str(part.get("call_id") or part.get("tool_call_id") or "")


_FNV32_PRIME = 0x01000193
_FNV32_MASK = 0xFFFFFFFF


def fnv1a32(text: str, seed: int = 0x811C9DC5) -> int:
    """FNV-1a over UTF-8 bytes, 32 bits.

    Must equal ``turn_document.js:fnv1a32`` exactly. JavaScript cannot do
    64-bit integer arithmetic, but ``Math.imul`` is an exact 32-bit
    multiply, so this is the widest primitive both languages can agree on
    digit for digit.
    """
    h = seed & _FNV32_MASK
    for b in str(text or "").encode("utf-8", "replace"):
        h = ((h ^ b) * _FNV32_PRIME) & _FNV32_MASK
    return h


def hitl_parts_of(parts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Every gate in this turn, in the order they were asked.

    A turn holds one part per ``interrupt_id`` (see :func:`_part_key`), so a
    turn that paused twice returns two parts. Callers that render the
    transcript must walk all of them; callers that want "what is being asked
    right now" want :func:`hitl_part_of`.
    """
    return [
        p for p in (parts or [])
        if isinstance(p, dict) and p.get("type") == "hitl"
    ]


def hitl_part_of(parts: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The gate that still needs an answer, else the most recent one.

    Prefers a ``pending`` gate over a newer settled one: gates normally
    settle in order, but a replayed or out-of-order stamp must not let a
    resolved gate mask one that is still holding the graph.
    """
    found: dict[str, Any] | None = None
    for p in hitl_parts_of(parts):
        if str(p.get("state") or "pending") == "pending":
            return p
        found = p
    return found


def _interrupt_id_of(part: dict[str, Any] | None) -> str:
    if not isinstance(part, dict):
        return ""
    raw = part.get("interrupt_id")
    if raw:
        return str(raw)
    payload = part.get("payload")
    if isinstance(payload, dict) and payload.get("interrupt_id"):
        return str(payload.get("interrupt_id"))
    return ""


def _hitl_tool_of(part: dict[str, Any] | None) -> str:
    if not isinstance(part, dict):
        return ""
    raw = str(part.get("tool") or part.get("tool_name") or "").strip()
    if raw:
        return raw
    payload = part.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("tool") or payload.get("tool_name") or "").strip()
    return ""


def make_interrupt_id(
    *,
    thread_id: str = "",
    tool: str = "",
    args: Any = None,
    interrupt: Any = None,
    checkpoint_id: str = "",
    seq: int | None = None,
) -> str:
    """Stable id for one graph interrupt. Prefer LangGraph's own id."""
    if interrupt is not None:
        for attr in ("id", "ns"):
            v = getattr(interrupt, attr, None)
            if v:
                return str(v)
    payload = json.dumps(
        {
            "th": str(thread_id or ""),
            "tool": str(tool or ""),
            "args": args if args is not None else {},
            "ck": str(checkpoint_id or ""),
            "n": int(seq or 0),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:16]


def assign_interrupt_id(
    payload: dict[str, Any] | None,
    *,
    thread_id: str = "",
    interrupt: Any = None,
    checkpoint_id: str = "",
) -> str:
    """Stamp ``interrupt_id`` on a HITL payload. Idempotent. Never raises."""
    data = payload if isinstance(payload, dict) else {}
    existing = str(data.get("interrupt_id") or "").strip()
    if existing:
        return existing
    tid = str(thread_id or data.get("thread_id") or "")
    n = 0
    if tid:
        n = _hitl_emit_seq.get(tid, 0) + 1
        _hitl_emit_seq[tid] = n
    iid = make_interrupt_id(
        thread_id=tid,
        tool=str(data.get("tool") or data.get("tool_name") or ""),
        args=data.get("args") or data.get("arguments"),
        interrupt=interrupt,
        checkpoint_id=checkpoint_id,
        seq=n,
    )
    if isinstance(payload, dict):
        payload["interrupt_id"] = iid
        if tid and not payload.get("thread_id"):
            payload["thread_id"] = tid
    return iid


def merge_reasoning_part(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Grow one thoughts fold. Never drop the longer text."""
    if not isinstance(incoming, dict):
        return dict(existing) if isinstance(existing, dict) else {}
    if not isinstance(existing, dict) or existing.get("type") != "reasoning":
        out = dict(incoming)
        out["type"] = "reasoning"
        return out
    old = str(existing.get("text") or "")
    new = str(incoming.get("text") or "")
    out = dict(existing)
    out.update(incoming)
    out["type"] = "reasoning"
    if not new:
        out["text"] = old
    elif not old or new == old or old in new:
        out["text"] = new
    elif new in old:
        out["text"] = old
    else:
        cut = _earlier_cut_of(old, new)
        out["text"] = old[:cut] + new if cut is not None else old.rstrip() + "\n\n" + new
    return out


#: Shortest overlap that counts as "the same narration, later". Shorter
#: overlaps are coincidences ("OK" ending one note and starting the next).
REASONING_MIN_OVERLAP = 64


def _earlier_cut_of(old: str, new: str) -> int | None:
    """Where *old* ends with an earlier cut of *new*, or None.

    A durable checkpoint stores the narration mid-stream; the full narration
    arrives later. When the old text is "earlier notes + that cut", neither
    contains the other, and appending printed the notes twice -- the first
    copy stopping mid-sentence (turn e99d06a0b33f, 2026-09-24). The earliest
    such position wins, so the whole stale tail is replaced. Mirrors
    turn_document.js ``earlierCutOf``; shared cases in
    tests/fixtures/unified_turn/merge/reasoning_merge.json.
    """
    if len(new) < REASONING_MIN_OVERLAP:
        return None
    probe = new[:REASONING_MIN_OVERLAP]
    at = old.find(probe)
    while at >= 0:
        if new.startswith(old[at:]):
            return at
        at = old.find(probe, at + 1)
    return None


def merge_hitl_part(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge one gate's part with a newer stamp of the SAME gate.

    Newer rank wins; pending never overwrites a claim (``HITL_RANK``).

    Since :func:`_part_key` keys HITL parts by ``interrupt_id``, gates with
    ids never reach each other here — each one owns its own slot and its own
    card. The id/tool mismatch branches below are the fallback for *id-less*
    legacy frames, which all share the empty-id slot: there, a different
    interrupt id or tool name still means "this is a different gate, take the
    incoming one" rather than advancing the old gate's state.
    """
    if not isinstance(incoming, dict):
        return dict(existing) if isinstance(existing, dict) else {}
    if not isinstance(existing, dict) or existing.get("type") != "hitl":
        out = dict(incoming)
        if not out.get("type"):
            out["type"] = "hitl"
        return out
    old_id = _interrupt_id_of(existing)
    new_id = _interrupt_id_of(incoming)
    if old_id and new_id and old_id != new_id:
        out = dict(incoming)
        out["type"] = "hitl"
        return out
    old_tool = _hitl_tool_of(existing)
    new_tool = _hitl_tool_of(incoming)
    if old_tool and new_tool and old_tool != new_tool:
        out = dict(incoming)
        out["type"] = "hitl"
        return out
    if hitl_rank(incoming.get("state")) < hitl_rank(existing.get("state")):
        return dict(existing)
    out = dict(existing)
    out.update(incoming)
    out["type"] = "hitl"
    inc_payload = incoming.get("payload")
    old_payload = existing.get("payload")
    if not inc_payload and isinstance(old_payload, dict):
        out["payload"] = dict(old_payload)
    elif isinstance(inc_payload, dict) and isinstance(old_payload, dict):
        merged_payload = dict(old_payload)
        merged_payload.update(inc_payload)
        out["payload"] = merged_payload
    iid = new_id or old_id
    if iid:
        out["interrupt_id"] = iid
        payload = out.get("payload")
        if isinstance(payload, dict) and not payload.get("interrupt_id"):
            payload = dict(payload)
            payload["interrupt_id"] = iid
            out["payload"] = payload
    return out


def text_of(parts: list[dict[str, Any]] | None) -> str:
    """Last ``text`` part, else empty."""
    text = ""
    for p in parts or []:
        if isinstance(p, dict) and p.get("type") == "text":
            t = str(p.get("text") or "").strip()
            if t:
                text = t
    return text


def _stale_running_twin(parts: list[dict[str, Any]], i: int) -> bool:
    """A key-less "running" tool part that a later stamp of its tool finished.

    Key-less tool parts are keyed name + state + text (``_part_key``), so a
    running stamp and its done stamp never merge. Producers that dropped the
    call id wrote exactly that pair, and the workbench showed "Running..."
    forever beside the finished row (turn e99d06a0b33f, 2026-09-24). The
    producers carry the id now; this keeps rows already stored that way
    honest. A keyed part is never touched -- its stamps merge by id -- and a
    key-less running part with no finished twin stays, because nothing here
    says it is over. Mirrors ``turn_document.js:staleRunningTwin``.
    """
    p = parts[i]
    if tool_call_id_of(p) or str(p.get("state") or "") != "running":
        return False
    name = str(p.get("name") or p.get("title") or "")
    for later in parts[i + 1:]:
        if (
            str(later.get("type") or "") == "tool"
            and str(later.get("name") or later.get("title") or "") == name
            and str(later.get("state") or "") in ("done", "failed")
        ):
            return True
    return False


def activity_of(parts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Workbench rows the restored CoT accordion already knows how to render.

    Every row carries ``id``, the part's own key. The renderer needs a
    stable handle to keep an expanded row expanded and a focused control
    focused while the row's content changes
    (``docs/plans/UNIFIED_TURN_BLOCK.md`` §3), and deriving it here means
    the row id and the part key cannot drift apart. Mirrors
    ``turn_document.js:activityOf``.
    """
    rows: list[dict[str, Any]] = []
    parts = [p for p in (parts or []) if isinstance(p, dict)]
    for i, p in enumerate(parts):
        kind = str(p.get("type") or "")
        if kind == "reasoning":
            detail = str(p.get("text") or "")
            if not detail.strip():
                continue
            rows.append({
                "id": part_key_str(p),
                "kind": "thought",
                "title": "Thoughts",
                "detail": detail,
                "state": "done",
            })
        elif kind == "tool":
            if _stale_running_twin(parts, i):
                continue
            rows.append({
                "id": part_key_str(p),
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
                "id": part_key_str(p),
                "kind": "status",
                "title": title,
                "state": str(p.get("state") or "done"),
                **({"ts": p["ts"]} if p.get("ts") else {}),
            })
        elif kind == "hitl":
            state = str(p.get("state") or "pending")
            title = "Waiting for approval"
            if state in ("approved", "inflight"):
                title = "Approved"
            elif state == "denied":
                title = "Denied"
            elif state in ("timeout", "error", "settled", "done"):
                title = "Approval resolved"
            rows.append({
                "id": part_key_str(p),
                "kind": "status",
                "title": title,
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
        # One thoughts fold per turn. Keying on text[:240] minted a new
        # part per hop and the live fold could not re-open the same notes.
        return ("reasoning",)
    if kind == "tool":
        # One slot PER CALL, keyed by the graph's own run id when the
        # producer sent one. The old key was name + state + result[:80], so
        # the SAME call changed identity the moment its state went
        # running->done or its result grew: a re-keyed row loses its
        # expanded state and its focus on every update, and two concurrent
        # calls to one tool collided into a single key. Mirrors
        # turn_document.js:partKey.
        call_id = tool_call_id_of(part)
        if call_id:
            return ("tool#" + call_id,)
        # Legacy frames carry no call id. Keep the content-derived key so
        # old transcripts still dedupe the way they were written.
        return (
            "tool",
            str(part.get("name") or part.get("title") or ""),
            str(part.get("state") or ""),
            str(part.get("result") or part.get("detail") or "")[:80],
        )
    if kind == "status":
        return ("status", str(part.get("title") or ""))
    if kind == "hitl":
        # One slot PER GATE, keyed by interrupt id — NOT one slot per turn.
        #
        # A turn can pause more than once (sequential "Allow this tool"
        # clicks, or file_write then file_delete). Collapsing every gate
        # into a single ("hitl",) slot meant the second gate OVERWROTE the
        # first: the document could represent one decision while the
        # transcript showed two cards. The renderer then had no authority
        # to reconcile against and compensated with DOM archaeology
        # (_findHitlCard / _hitlCardIsClaimed / _hitlAlreadyClaimed), which
        # is why "the reply never replaces the approval placeholder" kept
        # coming back through a new path every week (2026-09-19).
        #
        # Gates with no id still share one slot, which preserves the
        # pending → approved advance for legacy id-less frames.
        return ("hitl", _interrupt_id_of(part))
    # Unknown type. Spelled as JavaScript spells it (`JSON.stringify`
    # sliced to 80) rather than as `repr`, so the two languages cannot
    # disagree about a key nobody is looking at until they do.
    try:
        blob = json.dumps(part, separators=(",", ":"), ensure_ascii=False)
    except Exception:  # noqa: BLE001 - an unserializable part still needs a key
        blob = repr(part)
    return (kind, blob[:80])


def part_key_str(part: dict[str, Any]) -> str:
    """:func:`_part_key` as the single string JavaScript produces.

    ``turn_document.js:partKey`` returns a string; this returns the tuple
    joined with ``":"``. Activity row ids and any cross-language fixture
    use this form so "which part is this" has ONE spelling on the wire.
    """
    return ":".join(str(x) for x in _part_key(part))


def _activity_to_parts(activity: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in activity or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if kind == "tool":
            # A stored row keyed by call id must come back as the same
            # part, or a history load would split one call into two rows.
            rid = str(row.get("id") or "")
            call_id = rid[5:] if rid.startswith("tool#") else str(row.get("call_id") or "")
            out.append({
                "type": "tool",
                "name": str(row.get("title") or "tool"),
                "result": str(row.get("detail") or ""),
                "state": str(row.get("state") or "done"),
                **({"call_id": call_id} if call_id else {}),
                **({"ts": row["ts"]} if row.get("ts") else {}),
            })
        elif kind == "thought":
            detail = str(row.get("detail") or "")
            if detail.strip():
                out.append({"type": "reasoning", "text": detail})
        elif kind in ("status", "info"):
            # A row whose id names another part is that part's RENDERING,
            # not a part of its own. Reviving it mints a duplicate with a
            # different key: a gate's "Approved" row came back as a
            # `status:Approved` part beside the `hitl:<id>` part it was
            # derived from. Mirrors turn_document.js:activityToParts.
            rid = str(row.get("id") or "")
            if rid.startswith(("hitl:", "reasoning", "tool")):
                continue
            title = str(row.get("title") or "").strip()
            if title:
                out.append({
                    "type": "status",
                    "title": title,
                    "state": str(row.get("state") or "done"),
                    **({"ts": row["ts"]} if row.get("ts") else {}),
                })
    return out


def merge_tool_part(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge two stamps of the SAME tool call.

    Reached only when both share a key, which with a call id means they are
    genuinely the same call. A late ``running`` frame must not un-finish a
    call that already reported, and a terminal frame carrying no result
    must not blank the one the earlier frame delivered. Mirrors
    ``turn_document.js:mergeToolPart``.
    """
    if not isinstance(incoming, dict):
        return dict(existing) if isinstance(existing, dict) else {}
    if not isinstance(existing, dict) or existing.get("type") != "tool":
        return dict(incoming)
    if tool_rank(incoming.get("state")) < tool_rank(existing.get("state")):
        return dict(existing)
    out = dict(existing)
    out.update(incoming)
    out["type"] = "tool"
    if not str(incoming.get("result") or "").strip() and str(
        existing.get("result") or ""
    ).strip():
        out["result"] = existing["result"]
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

    def _add(part: dict[str, Any], *, replace: bool = False) -> None:
        if part.get("type") == "text":
            return
        key = _part_key(part)
        if key in seen:
            if replace and part.get("type") == "hitl":
                for i, x in enumerate(out):
                    if _part_key(x) == key:
                        out[i] = merge_hitl_part(x, part)
                        return
            if replace and part.get("type") == "reasoning":
                for i, x in enumerate(out):
                    if _part_key(x) == key:
                        out[i] = merge_reasoning_part(x, part)
                        return
            if replace and part.get("type") == "tool":
                # With a call id the key is stable across running->done, so
                # the second stamp of a call arrives HERE rather than as a
                # new part. Dropping it (the old behavior for every
                # duplicate key) would freeze every tool row at "running".
                for i, x in enumerate(out):
                    if _part_key(x) == key:
                        out[i] = merge_tool_part(x, part)
                        return
            return
        seen.add(key)
        out.append(dict(part))

    for p in existing:
        _add(p)
    for p in incoming:
        _add(p, replace=True)

    if (
        old_text
        and new_text
        and old_text.strip() != new_text.strip()
        and not new_text.startswith(old_text[:80] if len(old_text) > 80 else old_text)
    ):
        incoming_r = {"type": "reasoning", "text": old_text}
        key = _part_key(incoming_r)
        if key in seen:
            for i, x in enumerate(out):
                if _part_key(x) == key:
                    out[i] = merge_reasoning_part(x, incoming_r)
                    break
        else:
            out.insert(0, incoming_r)
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


def legacy_turn_id(msg: dict[str, Any] | None) -> str:
    """Stable id for an assistant row that never got a turn_id.

    MUST equal ``turn_document.js:legacyTurnId`` byte for byte. It did not:
    this hashed with sha256[:16] while the browser used a 32-bit string
    hash, so the same stored row was ``legacy-879abd24dca7291f`` here and
    ``legacy-9587b3b6`` there. Both suites were green because each had its
    own examples — the drift the shared fixtures under
    ``tests/fixtures/unified_turn/`` exist to catch
    (``docs/plans/UNIFIED_TURN_BLOCK_PHASE0.md`` §6.1).

    Two FNV-1a-32 passes with different offset bases give 64 bits without
    64-bit arithmetic, which JavaScript cannot do exactly. sha256 was the
    other option and would have meant shipping a hash implementation to
    the browser for an id nobody verifies.

    Read-side only: the value is recomputed on every load, so the change
    of algorithm renames nothing that was stored.
    """
    msg = msg or {}
    raw = f"{msg.get('ts') or ''}|{msg.get('content') or ''}"
    return "legacy-" + format(fnv1a32(raw, 0x811C9DC5), "08x") + format(
        fnv1a32(raw, 0x9E3779B1), "08x"
    )


def hydrate_message(msg: dict[str, Any] | None) -> dict[str, Any]:
    """Fill ``parts`` / ``activity`` / ``turn_id`` on older assistant rows.

    Read-side only: does not write the store. Rows with no activity still
    get a text part so the projector has something to bind.
    """
    if not isinstance(msg, dict):
        return {}
    out = dict(msg)
    role = str(out.get("role") or "").lower()
    if role != "assistant":
        return out
    parts = out.get("parts") if isinstance(out.get("parts"), list) else None
    activity = out.get("activity") if isinstance(out.get("activity"), list) else None
    content = str(out.get("content") or "")
    if not parts:
        parts = parts_from_stream(streamed="", final=content, activity=activity)
    if parts:
        out["parts"] = parts
        if not (isinstance(activity, list) and activity):
            derived = activity_of(parts)
            if derived:
                out["activity"] = derived
    if not str(out.get("turn_id") or "").strip():
        out["turn_id"] = legacy_turn_id(out)
    # A row written before revisions existed reads as rev 0 / schema 1.
    # Absent is NOT the same as zero to a client that has to decide whether
    # a snapshot is stale, so say it explicitly rather than leaving the
    # reader to infer it.
    if "rev" not in out:
        out["rev"] = 0
    if "schema" not in out:
        out["schema"] = 1
    return out
