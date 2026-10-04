"""Isolated live-path evaluation: record send intent, never send to X."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass
class _Recording:
    root: Path
    outage: bool = False
    intents: list[dict[str, str]] = field(default_factory=list)


_recording: ContextVar[_Recording | None] = ContextVar("x_shadow_recording", default=None)


def shadow_transport_blocked() -> bool:
    """A child-process flag also blocks transport if the scoped recorder is lost."""
    return os.environ.get("KAZMA_X_SHADOW") == "1" or _recording.get() is not None


def record_shadow_intent(row: dict[str, Any]) -> None:
    """Called only after the real publication preflight, before remote dispatch."""
    from kazma_core.paths import data_dir

    recording = _recording.get()
    if recording is None or data_dir().resolve() != recording.root:
        raise ValueError("Shadow recording requires its isolated store directory.")
    recording.intents.append({key: str(row[key]) for key in ("id", "text", "reply_to_id", "kind")})


def shadow_checker_outage() -> bool:
    recording = _recording.get()
    return bool(recording and recording.outage)


@contextmanager
def _scope(root: Path, *, outage: bool = False) -> Iterator[_Recording]:
    from kazma_core.paths import data_dir

    root = root.resolve()
    marker = root / ".x-shadow-isolated"
    if (data_dir().resolve() != root or not marker.is_file()
            or marker.read_text(encoding="utf-8") != str(os.getpid())):
        raise ValueError("Evaluation requires a fresh isolated child-process snapshot.")
    recording = _Recording(root, outage=outage)
    token = _recording.set(recording)
    try:
        yield recording
    finally:
        _recording.reset(token)


def _case_context(case: dict[str, Any]) -> Any:
    from kazma_core.x_api.context import ContextSnapshot

    raw = case.get("context")
    if not isinstance(raw, dict):
        raise ValueError("Case needs recorded source context.")
    fields = set(ContextSnapshot.__dataclass_fields__)
    raw = {key: value for key, value in raw.items() if key in fields}
    for key in ("verified_source", "author_resolved", "truncated", "media_present", "missing_quote", "fallback_text"):
        if key in raw and type(raw[key]) is not bool:
            raise ValueError("Context completeness flags must be booleans.")
    if any(not isinstance(raw.get(key, ""), str) for key in ("source_id", "author_handle", "text")):
        raise ValueError("Context identity and text must be strings.")
    if len(raw.get("text", "")) > 32000 or len(raw.get("source_id", "")) > 100:
        raise ValueError("Case context exceeds bounds.")
    quotes = raw.get("quotes", [])
    if (not isinstance(quotes, (list, tuple)) or len(quotes) > 10
            or any(not isinstance(quote, dict) or not isinstance(quote.get("text"), str)
                   or len(quote["text"]) > 32000 for quote in quotes)):
        raise ValueError("Quoted context exceeds the bounded schema.")
    return ContextSnapshot(**raw)


async def evaluate_case(case: dict[str, Any], *, isolated_root: Path) -> dict[str, Any]:
    """Simulate activation, preserving card permissions, all rails and live kills.

    Qualification itself is excluded because this is the measurement used to
    qualify. No production permission is changed: execution uses draft mode,
    exact manual approval locally, and a transport that records then refuses.
    """
    from kazma_core.x_api.reply import approve_summon, handle_summon
    from kazma_core.x_api.reply_store import get_reply_store
    from kazma_core.x_api.stance import get_reply_config
    from kazma_core.x_api.verification import CHECK_NAMES

    context = _case_context(case)
    summon = case.get("summon", {})
    if not isinstance(summon, dict) or not isinstance(summon.get("id"), str) or not summon["id"]:
        raise ValueError("Case needs a stable summon identity.")
    if (len(summon["id"]) > 300 or case.get("fault") not in (None, "checker_outage")
            or type(summon.get("target_followers")) not in (int, type(None))):
        raise ValueError("Summon identity, follower count or injected fault is invalid.")
    cfg = await asyncio.to_thread(get_reply_config)
    started = time.monotonic()
    with _scope(isolated_root, outage=case.get("fault") == "checker_outage") as recording:
        store = await asyncio.to_thread(get_reply_store)
        existing = await asyncio.to_thread(store.get, summon["id"])
        if existing and existing.status not in ("posted", "sending", "outcome_unknown"):
            await asyncio.to_thread(store.release, summon["id"], actor="shadow-replay")
        from kazma_core.x_api.config import get_x_config

        xcfg = await asyncio.to_thread(get_x_config)
        own_source = (context.verified_source and context.author_resolved
                      and bool(xcfg.handle) and context.author_handle.lstrip("@").lower() == xcfg.handle.lstrip("@").lower())
        result = await handle_summon(
            summon_id=summon["id"], parent_id=context.source_id, parent_text=context.text,
            parent_handle=context.author_handle, summoner=str(summon.get("author", "")),
            target_followers=summon.get("target_followers"),
            cfg=replace(cfg, enabled=True), force_mode="draft", summon_text=str(summon.get("text", "")),
            trusted=False, conversation_id=str(summon.get("conversation_id", "")),
            parent_authorized=own_source, context=context,
        )
        checks = {name: "unknown" for name in CHECK_NAMES}
        checks.update({check["check"]: check["verdict"] for check in result.checks})
        subject = cfg.subject_by_id(result.subject_id)
        reason = result.reason
        if (result.action == "awaiting_approval" and subject and subject.allow_auto
                and cfg.stance_check and os.environ.get("KAZMA_X_REPLY") != "0"
                and all(verdict == "pass" for verdict in checks.values())):
            attempted = await approve_summon(summon["id"], approval_token=result.approval_token, actor="shadow-evaluator")
            reason = "Would dispatch after all live preflight checks; no X request sent." if recording.intents else attempted.reason
        elif result.action == "awaiting_approval" and subject and not subject.allow_auto:
            reason = "This subject permits drafts only."
        rec = await asyncio.to_thread(get_reply_store().get, summon["id"])
        usage = result.usage or {}
        return {"id": case.get("id"), "language": case.get("language"), "categories": case.get("categories", []),
                "expected": case.get("expected"), "labeler": case.get("labeler", ""),
                "held_out": case.get("held_out") is True, "human_reviewed": False,
                "context": context.to_dict(), "summon": summon, "fault": case.get("fault"),
                "actual": {"auto": len(recording.intents) == 1, "target": result.subject_id,
                           "critical_violations": None, "checks": checks,
                           "latency_ms": round((time.monotonic() - started) * 1000),
                           "model_calls": usage.get("calls", 0), "output_tokens": usage.get("output_tokens", 0),
                           "usage_complete": not usage.get("missing_usage") and not usage.get("failed_calls")},
                "candidate": result.draft, "reason": reason, "models": list(result.models),
                "decision": {key: value for key, value in (rec.decision if rec else {}).items()
                             if key not in ("approval_basis", "summon")},
                "observation": "isolated-live-path-shadow; activation simulated; X transport blocked"}
