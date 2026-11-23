"""Session-scoped HITL tool grants — stop approval floods without full YOLO.

When the user approves a danger tool they can choose:

* ``once``  — this call only (default)
* ``tool``  — allow *this tool name* for the thread until TTL expires
* ``yolo``  — full session bypass (delegates to :mod:`kazma_core.safety.yolo`)

Grants are stored in ConfigStore under ``hitl_grant.{holder}.{tool}``:
the holder is the chat's thread id, and once Spaces is enforced the thread
and the person (``safety.grant_holder``, S2-06): a grant one member of a
shared chat made never skips another member's card.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

__all__ = [
    "clear_grants",
    "grant_tool",
    "has_tool_grant",
    "list_grants",
    "one_time_only",
    "tool_batch_label",
    "tools_to_grant",
]

logger = logging.getLogger(__name__)

_DEFAULT_GRANT_TTL = 30 * 60  # 30 minutes — shorter than YOLO default

_BATCH_LABEL = re.compile(r"\d+ tools")


def tool_batch_label(count: int) -> str:
    """The ``tool`` of a gate that asks about several danger tools at once."""
    return f"{int(count)} tools"


def _is_tool_batch_label(name: Any) -> bool:
    """True for :func:`tool_batch_label`'s text: a card's label, never a tool."""
    return bool(_BATCH_LABEL.fullmatch(str(name or "").strip()))


def tools_to_grant(
    pending_tools: Any, pending_tool_name: str = "", explicit: str = ""
) -> list[str]:
    """The tools an "allow for this chat" answer grants: the ones its
    question asked about.

    *pending_tools* is the gate's list (``[{"name": ...}]``); a gate for one
    tool may carry only *pending_tool_name*. *explicit* is the tool the page
    named. A gate for several tools is labelled "2 tools", and the page sent
    that label as the tool to grant, so a live grant was written for a tool
    called "2 tools" (2026-10-03). The label is never granted, and when the
    question's tools are known a named tool must be one of them.
    """
    asked: list[str] = []
    for t in pending_tools or []:
        if isinstance(t, dict) and t.get("name"):
            asked.append(str(t["name"]))
    if not asked and pending_tool_name and not _is_tool_batch_label(pending_tool_name):
        asked.append(str(pending_tool_name))
    chosen = list(asked)
    named = str(explicit or "").strip()
    if named and not _is_tool_batch_label(named) and (not asked or named in asked):
        chosen.append(named)
    return list(dict.fromkeys(chosen))


def one_time_only(pending_tools: Any, pending_tool_name: str = "") -> list[str]:
    """The always-ask tools (``ALWAYS_HITL_TOOLS``: X writes, folder access)
    a question asks about. They take a one-time approval only: a grant can never skip them
    (``requires_approval`` checks the list first), so "allow for this chat"
    on their card only approved THIS call -- live 2026-10-16 a click on it
    published a post the owner read as "it will still ask me before posting".
    The approve route refuses that answer; :func:`grant_tool` never stores it.
    """
    from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS

    asked = tools_to_grant(pending_tools, pending_tool_name)
    return [name for name in asked if name in ALWAYS_HITL_TOOLS]


def _ttl_seconds() -> int:
    """Grant lifetime. A number is seconds, at least 60 — so ``0`` is 60,
    not "forever": for an approval dial the safe reading of zero is the
    shortest window. Only the words ``off`` / ``none`` / ``infinite`` mean no
    expiry (``tests/test_ttl_env_parsing.py``)."""
    raw = (os.environ.get("KAZMA_HITL_GRANT_TTL_SECONDS") or "").strip()
    if raw.isdigit():
        return max(60, int(raw))
    if raw in ("off", "none", "infinite"):
        return 0
    return _DEFAULT_GRANT_TTL


def _key(holder: str, tool: str) -> str:
    return f"hitl_grant.{holder}.{tool}"


def grant_tool(
    thread_id: str,
    tool: str,
    *,
    actor: str = "unknown",
    ttl_seconds: int | None = None,
    person: str | None = None,
) -> dict[str, Any]:
    """Grant *tool* for *thread_id* -- for *person* once Spaces is enforced
    (the person bound now when None; never for no one). Returns status dict."""
    from kazma_core.config_store import get_config_store
    from kazma_core.safety.grant_holder import holder

    if not thread_id or not tool:
        return {"active": False, "error": "thread_id and tool required"}
    key_holder = holder(thread_id, person)
    if not key_holder:
        return {"active": False, "error": "no person to grant it to"}
    from kazma_core.spaces.approvals import standing_grant_refusal

    refused = standing_grant_refusal(person)
    if refused:
        # A person whose cards go to approvers does not skip them (Spaces
        # S2-05, N4); an approver's grant from the card they answered passes.
        logger.warning("[SECURITY] grant refused thread=%s actor=%s: %s", thread_id, actor, refused)
        return {"active": False, "error": refused}
    from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS

    if tool in ALWAYS_HITL_TOOLS:
        # A grant it could never honour (see one_time_only): not stored.
        logger.warning(
            "[SECURITY] HITL grant refused for always-ask tool=%s thread=%s actor=%s",
            tool, thread_id, actor,
        )
        return {"active": False, "tool": tool, "error": "one_time_only"}

    cs = get_config_store()
    now = time.time()
    ttl = _ttl_seconds() if ttl_seconds is None else max(0, int(ttl_seconds))
    from kazma_core.spaces.approvals import made_by_a_person

    payload = {
        "enabled": True,
        "tool": tool,
        "since": now,
        "actor": actor,
        "granted_by": made_by_a_person(),
        "ttl_seconds": ttl,
        "expires_at": (now + ttl) if ttl > 0 else None,
    }
    cs.set(_key(key_holder, tool), payload, category="safety")
    logger.warning(
        "[SECURITY] HITL GRANT tool=%s thread=%s actor=%s ttl=%ss",
        tool,
        thread_id,
        actor,
        ttl or "none",
    )
    return {
        "active": True,
        "tool": tool,
        "thread_id": thread_id,
        "ttl_seconds": ttl,
        "expires_at": payload["expires_at"],
        "remaining_seconds": ttl if ttl > 0 else None,
    }


def has_tool_grant(thread_id: str | None, tool: str, person: str | None = None) -> bool:
    """True if *tool* is granted and not expired for this thread -- and,
    once Spaces is enforced, for *person* (the person bound now when None)."""
    from kazma_core.safety.grant_holder import holder

    key_holder = holder(thread_id, person)
    if not key_holder or not tool:
        return False
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    raw = cs.get(_key(key_holder, tool))
    if not raw:
        return False

    if raw is True or raw in (1, "1", "true", "True"):
        return True

    if not isinstance(raw, dict) or not raw.get("enabled"):
        return False

    expires = raw.get("expires_at")
    if expires is not None:
        try:
            if time.time() > float(expires):
                cs.delete(_key(key_holder, tool))
                logger.info(
                    "[SECURITY] HITL GRANT EXPIRED tool=%s thread=%s",
                    tool,
                    thread_id,
                )
                return False
        except (TypeError, ValueError):
            pass
    return True


def tool_grant_by(thread_id: str | None, tool: str, person: str | None = None) -> str:
    """Who made the tool grant (``granted_by``), "" when nobody is recorded
    (a grant made before the field, a bare flag)."""
    from kazma_core.config_store import get_config_store
    from kazma_core.safety.grant_holder import holder

    key_holder = holder(thread_id, person)
    raw = get_config_store().get(_key(key_holder, tool)) if key_holder and tool else None
    return str(raw.get("granted_by") or "") if isinstance(raw, dict) else ""


def _grant_keys(cs: Any, key_holder: str) -> list[str]:
    """Return ConfigStore keys for grants of *key_holder*."""
    prefix = f"hitl_grant.{key_holder}."
    keys: list[str] = []
    try:
        cat = cs.get_category("safety") if hasattr(cs, "get_category") else None
        if isinstance(cat, dict):
            keys = [k for k in cat if str(k).startswith(prefix)]
        if not keys and hasattr(cs, "get_all"):
            all_items = cs.get_all() or {}
            # get_all → {category: {key: value}} or flat
            if isinstance(all_items, dict):
                safety = all_items.get("safety")
                if isinstance(safety, dict):
                    keys = [k for k in safety if str(k).startswith(prefix)]
                else:
                    keys = [k for k in all_items if str(k).startswith(prefix)]
    except Exception:
        keys = []
    return keys


def clear_grants(thread_id: str, *, actor: str = "unknown", person: str | None = None) -> int:
    """Remove all tool grants for *thread_id* (*person*'s once Spaces is
    enforced: the person bound now when None). Returns count cleared."""
    from kazma_core.safety.grant_holder import holder

    key_holder = holder(thread_id, person)
    if not key_holder:
        return 0
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    cleared = 0
    for key in _grant_keys(cs, key_holder):
        try:
            cs.delete(key)
            cleared += 1
        except Exception:
            # NOT safe to ignore silently (audit O3): a grant that survives
            # deletion keeps skipping HITL for that tool. The count returned
            # to the caller stays accurate, and this is logged so the failure
            # is visible rather than reported as a successful revoke.
            logger.error(
                "[hitl_grants] failed to clear grant %s — approval may still "
                "be bypassed for it",
                key,
                exc_info=True,
            )

    if cleared:
        logger.warning(
            "[SECURITY] HITL GRANTS CLEARED thread=%s count=%d actor=%s",
            thread_id,
            cleared,
            actor,
        )
    return cleared


def list_grants(thread_id: str, person: str | None = None) -> list[dict[str, Any]]:
    """List active (non-expired) grants for a thread (*person*'s once Spaces
    is enforced: the person bound now when None)."""
    from kazma_core.safety.grant_holder import holder

    key_holder = holder(thread_id, person)
    if not key_holder:
        return []
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    prefix = f"hitl_grant.{key_holder}."
    out: list[dict[str, Any]] = []
    for key in _grant_keys(cs, key_holder):
        tool = str(key)[len(prefix) :]
        if not has_tool_grant(thread_id, tool, person):
            continue
        raw = cs.get(key) or {}
        rem = None
        if isinstance(raw, dict) and raw.get("expires_at") is not None:
            try:
                rem = max(0, int(float(raw["expires_at"]) - time.time()))
            except (TypeError, ValueError):
                rem = None
        out.append(
            {
                "tool": tool,
                "remaining_seconds": rem,
                "actor": raw.get("actor") if isinstance(raw, dict) else None,
            }
        )
    return out
