"""Topic-drift detection + prior-tool-chain stubbing for subject soft-reset.

Embedding distance is **fail-open**: if the embedder is unavailable or encode
fails, we never force a shift (regex/heuristic classifiers still apply).

Tool stubbing collapses completed prior-turn tool chains so attention is not
dominated by old multi-step tool payloads after a pivot or finished task.
"""

from __future__ import annotations

import logging
import math
from typing import Any

__all__ = [
    "cosine_distance",
    "semantic_topic_drift",
    "topic_drift_config",
    "stub_prior_tool_chains",
    "should_stub_prior_tools",
]

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.55
_DEFAULT_ENABLED = True
_MIN_CHARS = 12


def topic_drift_config() -> dict[str, Any]:
    """Live-read ConfigStore/env defaults for embedding topic drift.

    Keys (flat ConfigStore):
      ``agent.topic_drift.enabled`` (bool, default True)
      ``agent.topic_drift.threshold`` (float, default 0.55) — cosine *distance*
        (1 - similarity); higher = only flag more dissimilar pairs.
    """
    enabled = _DEFAULT_ENABLED
    threshold = _DEFAULT_THRESHOLD
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        raw_en = store.get("agent.topic_drift.enabled", None)
        if raw_en is not None:
            if isinstance(raw_en, str):
                enabled = raw_en.strip().lower() in ("1", "true", "yes", "on")
            else:
                enabled = bool(raw_en)
        raw_th = store.get("agent.topic_drift.threshold", None)
        if raw_th is not None:
            threshold = float(raw_th)
    except Exception:
        pass
    try:
        threshold = max(0.05, min(0.95, float(threshold)))
    except (TypeError, ValueError):
        threshold = _DEFAULT_THRESHOLD
    return {"enabled": bool(enabled), "threshold": threshold}


def cosine_distance(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> float | None:
    """Return cosine distance ``1 - cos_sim`` in ``[0, 2]``, or None if undefined."""
    if not a or not b or len(a) != len(b):
        return None
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 0.0 or nb <= 0.0:
        return None
    sim = dot / (math.sqrt(na) * math.sqrt(nb))
    # Clamp numerical noise
    if sim > 1.0:
        sim = 1.0
    elif sim < -1.0:
        sim = -1.0
    return 1.0 - sim


def semantic_topic_drift(
    current: str,
    reference: str,
    *,
    threshold: float | None = None,
    enabled: bool | None = None,
) -> bool:
    """True when embedding distance between *current* and *reference* ≥ θ.

    Fail-open: returns False on any error, missing embedder, or short text.
    """
    cfg = topic_drift_config()
    if enabled is None:
        enabled = bool(cfg["enabled"])
    if not enabled:
        return False
    if threshold is None:
        threshold = float(cfg["threshold"])

    cur = (current or "").strip()
    ref = (reference or "").strip()
    if len(cur) < _MIN_CHARS or len(ref) < _MIN_CHARS:
        return False
    # Identical / near-identical text is never a shift
    if cur.lower() == ref.lower():
        return False

    try:
        from kazma_core.memory.embedder import get_embedder

        emb = get_embedder()
        if emb is None:
            return False
        # Prefer batch when available (one round-trip for remote providers)
        if hasattr(emb, "encode_batch"):
            try:
                vecs = emb.encode_batch([cur[:2000], ref[:2000]])
                if not vecs or len(vecs) < 2:
                    return False
                va, vb = vecs[0], vecs[1]
            except Exception:
                va = emb.encode(cur[:2000])
                vb = emb.encode(ref[:2000])
        else:
            va = emb.encode(cur[:2000])
            vb = emb.encode(ref[:2000])
        dist = cosine_distance(va, vb)
        if dist is None:
            return False
        drifted = dist >= float(threshold)
        if drifted:
            logger.info(
                "[topic_drift] semantic shift dist=%.3f threshold=%.3f cur=%r ref=%r",
                dist,
                threshold,
                cur[:60],
                ref[:60],
            )
        else:
            logger.debug(
                "[topic_drift] no shift dist=%.3f threshold=%.3f",
                dist,
                threshold,
            )
        return drifted
    except Exception:
        logger.debug("[topic_drift] embed check failed (fail-open)", exc_info=True)
        return False


def should_stub_prior_tools(
    *,
    intent_mode: str = "",
    prev_task_status: str = "",
) -> bool:
    """Whether completed/prior tool chains should be stubbed for this turn."""
    mode = (intent_mode or "").strip().lower()
    status = (prev_task_status or "").strip().lower()
    if mode == "continue":
        return False
    if mode == "shift":
        return True
    if status in ("completed", "superseded", "abandoned") and mode not in ("continue",):
        return True
    return False


def stub_prior_tool_chains(
    messages: list[dict[str, Any]] | None,
    *,
    keep_last_n_user_turns: int = 1,
) -> list[dict[str, Any]]:
    """Collapse tool chains that belong to *prior* user turns into short stubs.

    The last ``keep_last_n_user_turns`` user segments keep full tool fidelity
    (needed if the newest turn already has partial tools — rare at turn entry).
    Earlier assistant ``tool_calls`` + matching ``tool`` results become a single
    plain assistant line: ``[Executed tools: name1, name2: success]``.

    Always re-sanitizes chains so OpenAI tool-message pairing stays valid.
    """
    if not messages:
        return []

    msgs = [dict(m) if isinstance(m, dict) else m for m in messages]

    # Indices of user messages that demarcate turns
    user_idxs = [
        i
        for i, m in enumerate(msgs)
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    if not user_idxs:
        return msgs

    # Protect messages from the start of the Nth-last user turn to the end
    protect_from = user_idxs[-keep_last_n_user_turns] if keep_last_n_user_turns > 0 else len(msgs)

    out: list[dict[str, Any]] = []
    i = 0
    stubbed = 0
    while i < len(msgs):
        m = msgs[i]
        if not isinstance(m, dict):
            out.append(m)
            i += 1
            continue

        # Inside protected (recent) window — keep as-is
        if i >= protect_from:
            out.append(m)
            i += 1
            continue

        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            names: list[str] = []
            for tc in m.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = (fn or {}).get("name") or tc.get("name") or "tool"
                names.append(str(name))
            # Consume following tool responses belonging to this call set
            tc_ids = {
                (tc.get("id") or "")
                for tc in (m.get("tool_calls") or [])
                if isinstance(tc, dict)
            }
            j = i + 1
            ok = 0
            err = 0
            while j < len(msgs) and j < protect_from:
                nxt = msgs[j]
                if not isinstance(nxt, dict) or nxt.get("role") != "tool":
                    break
                tid = nxt.get("tool_call_id") or ""
                if tid and tid not in tc_ids:
                    break
                body = str(nxt.get("content") or "")
                if body.startswith("⚠️") or "error" in body[:80].lower():
                    err += 1
                else:
                    ok += 1
                j += 1
            label = ", ".join(names) if names else "tools"
            status_bit = f"{ok} ok" + (f", {err} err" if err else "")
            prior_text = m.get("content")
            if isinstance(prior_text, str) and prior_text.strip():
                # Keep a short slice of assistant narrative if any
                head = prior_text.strip()[:200]
                content = f"{head}\n[Executed tools: {label} — {status_bit}]"
            else:
                content = f"[Executed tools: {label} — {status_bit}]"
            out.append({"role": "assistant", "content": content})
            stubbed += 1
            i = j
            continue

        if role == "tool":
            # Orphan tool before protect window (no matching assistant kept) — drop
            stubbed += 1
            i += 1
            continue

        out.append(m)
        i += 1

    if stubbed:
        logger.info(
            "[topic_drift] stubbed %d prior tool-chain segments (%d → %d msgs)",
            stubbed,
            len(msgs),
            len(out),
        )
    try:
        from kazma_core.agent.graph_builder import sanitize_tool_chains

        return sanitize_tool_chains(out)
    except Exception:
        return out
