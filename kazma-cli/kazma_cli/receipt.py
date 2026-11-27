"""A signed receipt of one run, from the records Kazma already keeps.

Read-only. A record that is missing or only partly there is "not recorded":
this command does not guess, and it does not copy a decision out of a second
store. The turn journal lives in the server process, so a separate ``kazma
receipt`` usually cannot see it. The signing key is made once, kept in the
vault, and never written into the file.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import html
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger(__name__)

_KEY_NAME = "receipt.signing_key"
_TRAILER = ".kazma-receipt-ed25519"
_NOT_RECORDED_EN = "not recorded"
_NOT_RECORDED_AR = "غير مسجّل"
_RECORDED_EN = "recorded"
_RECORDED_AR = "مسجّل"
_NOT_SHOWN = _NOT_RECORDED_EN + " / " + _NOT_RECORDED_AR
_PROVES_EN = "A valid signature proves this install wrote this file and it is unchanged."
_PROVES_AR = "التوقيع الصحيح يثبت أن هذا التثبيت كتب هذا الملف وأنه لم يتغيّر."
_DOES_NOT_EN = "It does not prove that an outside effect happened exactly once."
_DOES_NOT_AR = "لا يثبت أن أثراً خارجياً حدث مرة واحدة بالضبط."
_PUB_EN = "The public half of this install's receipt key. The private half stays in the vault."
_PUB_AR = "النصف العلني لمفتاح إيصالات هذا التثبيت. النصف الخاص يبقى في الخزنة."
_NO_VAULT_EN = "not recorded. This install has no vault key, so a receipt cannot be signed."
_NO_VAULT_AR = "غير مسجّل. لا مفتاح خزنة لهذا التثبيت، لذا لا يمكن توقيع إيصال."
_NO_SIGN_EN = "not recorded. This install has no signing key."
_NO_SIGN_AR = "غير مسجّل. لا مفتاح توقيع لهذا التثبيت."
_BAD_KEY_EN = "not recorded. This install's signing key does not open. It was not replaced."
_BAD_KEY_AR = "غير مسجّل. مفتاح توقيع هذا التثبيت لا يُفتح. لم يُستبدل."
_MISMATCH_EN = "This file was not signed by this install."
_MISMATCH_AR = "لم يوقّع هذا التثبيت هذا الملف."
_CHANGED_EN = "A byte of this file changed, or it was not signed by this install."
_CHANGED_AR = "تغيّر بايت من هذا الملف، أو لم يوقّعه هذا التثبيت."
_NOT_FILE_EN = "not recorded. This file is not a Kazma receipt."
_NOT_FILE_AR = "غير مسجّل. هذا الملف ليس إيصال كاظمه."
_WROTE_EN = "Wrote the receipt. The private half of the key stays in the vault."
_WROTE_AR = "كُتب الإيصال. النصف الخاص من المفتاح يبقى في الخزنة."
_NO_THREAD_EN = "not recorded. This run's record has no thread id to read by."
_NO_THREAD_AR = "غير مسجّل. سجل هذا العمل بلا معرّف محادثة يُقرأ به."
_BAD_ID_EN = "not recorded. This is not a run id."
_BAD_ID_AR = "غير مسجّل. هذا ليس معرّف عمل."
_UNLISTED_EN = "not recorded. This run is not in the current listing."
_UNLISTED_AR = "غير مسجّل. هذا العمل ليس في القائمة الحالية."
_JOURNAL_EN = "not recorded. This process has no complete turn journal for this run."
_JOURNAL_AR = "غير مسجّل. لا سجل دورة كاملاً لهذا العمل في هذه العملية."
_NO_EFFECTS_EN = "No effect receipt is stored for this thread."
_NO_EFFECTS_AR = "لا إيصال أثر مخزّن لهذه المحادثة."
_NO_APPROVALS_EN = "No approval is stored for this thread."
_NO_APPROVALS_AR = "لا موافقة مخزّنة لهذه المحادثة."
_NO_COMMITMENTS_EN = "No commitment check is stored for this thread."
_NO_COMMITMENTS_AR = "لا فحص التزام مخزّن لهذه المحادثة."
_LEDGER_EN = "not recorded. The chat's model ledger could not be read."
_LEDGER_AR = "غير مسجّل. تعذّر قراءة سجل النموذج لهذه المحادثة."
_CHAT_LEDGER_EN = "The chat's model ledger, not one turn's total."
_CHAT_LEDGER_AR = "سجل النموذج لهذه المحادثة، وليس مجموع دورة واحدة."
_TURN_LEDGER_EN = "not recorded. This command does not know the turn id."
_TURN_LEDGER_AR = "غير مسجّل. هذا الأمر لا يعرف معرّف الدورة."
_MEMORY_KEY_EN = (
    "not recorded. The memory record is keyed by a hash this command cannot rebuild from the run id."
)
_MEMORY_KEY_AR = "غير مسجّل. سجل الذاكرة مفتاحه تجزئة لا يستطيع هذا الأمر إعادة بناءها من معرّف العمل."
_EFFECT_OPEN_EN = "not recorded whether this effect finished"
_EFFECT_OPEN_AR = "غير مسجّل هل اكتمل هذا الأثر"
_HEADER_EN = "Kazma receipt"
_HEADER_AR = "إيصال كاظمه"
_EFFECT_KEYS = ("effect_id", "tool", "state", "started_at", "completed_at", "result_uncertain")
_USAGE_KEYS = ("calls", "tokens", "cost", "unpriced", "prompt_tokens", "cached_tokens")
_TITLES = {
    "supervision": ("Run supervision", "الإشراف على العمل"),
    "turn_journal": ("Turn journal", "سجل الدورة"),
    "effects": ("Effect receipts", "إيصالات الأثر"),
    "approvals": ("Approvals", "الموافقات"),
    "commitment": ("Commitment checks", "فحوص الالتزام"),
    "llm": ("Model ledger", "سجل النموذج"),
    "memories": ("Memories used", "الذكريات المستخدمة"),
    "unverified": ("What could not be verified", "ما لم يُتحقّق منه"),
}
_READ_FAILURES = (
    OSError,
    sqlite3.Error,
    ValueError,
    TypeError,
    RuntimeError,
    ImportError,
    AttributeError,
    LookupError,
    UnicodeError,
)
_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'none'"

# A memory record's key is a hash of the turn. This command does not rebuild
# it: it only reads a key a complete journal frame already carries.


class _NoKeyError(Exception):
    """This install has no receipt signing key."""


class _BadKeyError(Exception):
    """A signing key is stored and does not open. It is left as it is."""


def _word(status: str, lang: str) -> str:
    recorded = status == "recorded"
    if lang == "ar":
        return _RECORDED_AR if recorded else _NOT_RECORDED_AR
    return _RECORDED_EN if recorded else _NOT_RECORDED_EN


def _section(
    sid: str, status: str, *, detail_en: str = "", detail_ar: str = "", items: list | None = None,
) -> dict[str, Any]:
    title_en, title_ar = _TITLES[sid]
    return {
        "id": sid,
        "status": status,
        "title_en": title_en,
        "title_ar": title_ar,
        "detail_en": detail_en,
        "detail_ar": detail_ar,
        "items": list(items or []),
    }


def _missing(sid: str, detail_en: str, detail_ar: str, items: list | None = None) -> dict[str, Any]:
    return _section(sid, "not_recorded", detail_en=detail_en, detail_ar=detail_ar, items=items)


def _found(
    sid: str, *, detail_en: str = "", detail_ar: str = "", items: list | None = None,
) -> dict[str, Any]:
    return _section(sid, "recorded", detail_en=detail_en, detail_ar=detail_ar, items=items)


def _read(label: str, fn: Any) -> Any:
    """What *fn* returns, or None when that record cannot be read.

    A write refused because this command is a diagnostic is not a missing
    record: it propagates.
    """
    from kazma_core.diagnostic_scope import DiagnosticWriteRefused

    try:
        return fn()
    except DiagnosticWriteRefused:
        raise
    except _READ_FAILURES as exc:
        logger.warning("[receipt] %s could not be read (%s)", label, type(exc).__name__)
        return None


def _as_data(value: Any, source: str) -> str:
    """Mask *value*, then show it as data. Empty stays empty. A failure is not the raw text."""
    from kazma_core.diagnostic_scope import DiagnosticWriteRefused
    from kazma_core.safety.prompt_fence import format_untrusted_block
    from kazma_core.security.config_mask import mask_deep

    try:
        masked = mask_deep(value)
        if isinstance(masked, str):
            text = masked
        else:
            text = json.dumps(
                masked, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False,
            )
        if not str(text).strip():
            return ""
        fenced = format_untrusted_block(str(text), source=source)
    except DiagnosticWriteRefused:
        raise
    except _READ_FAILURES:
        return _NOT_SHOWN
    return fenced or _NOT_SHOWN


def _show_id(run_id: str) -> str:
    from kazma_core.security.config_mask import mask_deep

    shown = mask_deep(run_id)
    if isinstance(shown, str) and shown:
        return shown
    return _NOT_RECORDED_EN


def _file(name: str) -> bool:
    from kazma_core.paths import data_dir

    return (data_dir(create=False) / name).is_file()


def _ops_present() -> bool:
    """Whether the memory ops database is already on disk. Does not create it."""
    import os

    from kazma_core.paths import data_dir

    env = os.environ.get("KAZMA_MEMORY_OPS_DB")
    if env:
        return Path(env).expanduser().is_file()
    return (data_dir(create=False) / "memory_ops.db").is_file()


def _split(run_id: str) -> tuple[str | None, str | None]:
    from kazma_core.runs.registry import _split_run_id

    try:
        return _split_run_id(run_id)
    except ValueError:
        return None, None


async def _lookup(run_id: str) -> Any:
    from kazma_core.runs.registry import get_run

    return await get_run(run_id)


def _thread_of(kind: str | None, native: str | None, run: Any) -> str | None:
    """The thread id the journals are keyed by. A chat turn's id carries it.

    Another kind uses the thread id on its own record only. Its native id
    is not a thread id, and the delivery address on the record is not one.
    """
    if kind == "chat_turn" and native:
        return native
    origin = getattr(run, "origin", None)
    if not isinstance(origin, dict):
        return None
    thread = origin.get("thread_id")
    if thread:
        return str(thread)
    return None


def _run_view(run: Any) -> dict[str, Any]:
    from kazma_core.security.config_mask import mask_deep

    data = run.to_dict()
    origin = data.get("origin") if isinstance(data.get("origin"), dict) else {}
    view = {
        "state": data.get("state"),
        "native_state": data.get("native_state"),
        "started_at": data.get("started_at"),
        "ended_at": data.get("ended_at"),
        "progress_at": data.get("progress_at"),
        "effects": data.get("effects"),
        "verdict": data.get("verdict"),
        "owner_alive": data.get("owner_alive"),
        "handled": data.get("handled"),
        "space": data.get("space") or "",
        "platform": origin.get("platform", ""),
        "title": _as_data(getattr(run, "title", ""), "run_title") if getattr(run, "title", "") else "",
        "detail": _as_data(getattr(run, "detail", ""), "run_detail") if getattr(run, "detail", "") else "",
        "progress_note": (
            _as_data(getattr(run, "progress_note", ""), "run_progress")
            if getattr(run, "progress_note", "") else ""
        ),
    }
    masked = mask_deep(view)
    return masked if isinstance(masked, dict) else view


def _supervision(run_id: str) -> tuple[str | None, str | None, Any, dict[str, Any]]:
    kind, native = _split(run_id)
    if kind is None:
        return None, None, None, _missing("supervision", _BAD_ID_EN, _BAD_ID_AR)
    from kazma_core.diagnostic_scope import DiagnosticWriteRefused

    try:
        run = asyncio.run(_lookup(run_id))
    except DiagnosticWriteRefused:
        raise
    except _READ_FAILURES as exc:
        logger.warning("[receipt] supervision could not be read (%s)", type(exc).__name__)
        return kind, native, None, _missing("supervision", _NOT_RECORDED_EN, _NOT_RECORDED_AR)
    if run is None or not hasattr(run, "to_dict"):
        return kind, native, None, _missing("supervision", _UNLISTED_EN, _UNLISTED_AR)
    view = _read("supervision", lambda: _run_view(run))
    if not isinstance(view, dict):
        return kind, native, run, _missing("supervision", _NOT_RECORDED_EN, _NOT_RECORDED_AR)
    return kind, native, run, _found("supervision", items=[view])


def _kind_field(kind: str | None) -> dict[str, Any]:
    if not kind:
        return {"status": "not_recorded", "en": _NOT_RECORDED_EN, "ar": _NOT_RECORDED_AR}
    from kazma_core.runs.kinds import KINDS

    spec = KINDS[kind]
    return {
        "status": "recorded", "id": kind, "label_en": spec.label, "label_ar": spec.label_ar,
    }


def _keep(value: Any, found: list[str]) -> None:
    if isinstance(value, str) and value and value not in found:
        found.append(value)


def _frame_ids(frames: list) -> tuple[list[str], list[str]]:
    keys: list[str] = []
    turns: list[str] = []
    for frame in frames:
        nodes = [frame]
        if isinstance(frame, dict) and isinstance(frame.get("data"), dict):
            nodes.append(frame["data"])
        for node in nodes:
            if isinstance(node, dict):
                _keep(node.get("turn_key"), keys)
                _keep(node.get("turn_id"), turns)
    return keys, turns


def _journal(thread: str | None) -> tuple[list, dict[str, Any]]:
    if not thread:
        return [], _missing("turn_journal", _NO_THREAD_EN, _NO_THREAD_AR)
    from kazma_ui.delivery import get_turn_broker

    found = _read("turn_journal", lambda: get_turn_broker().resume(thread, 0))
    if not isinstance(found, tuple) or len(found) < 2:
        return [], _missing("turn_journal", _JOURNAL_EN, _JOURNAL_AR)
    frames, gap = found[0], found[1]
    if gap or not isinstance(frames, list) or not frames:
        return [], _missing("turn_journal", _JOURNAL_EN, _JOURNAL_AR)
    items = []
    hidden = False
    for frame in frames:
        shown = _as_data(frame, "turn_journal")
        if not shown or shown == _NOT_SHOWN:
            hidden = True
            items.append({"en": _NOT_RECORDED_EN, "ar": _NOT_RECORDED_AR})
        else:
            items.append({"data": shown})
    if hidden:
        return list(frames), _missing("turn_journal", _NOT_RECORDED_EN, _NOT_RECORDED_AR, items)
    return list(frames), _found("turn_journal", items=items)


def _effects(thread: str | None) -> tuple[dict[str, Any], list[str]]:
    if not thread:
        return _missing("effects", _NO_THREAD_EN, _NO_THREAD_AR), []
    if not _file("tool_effects.db"):
        return _missing("effects", _NOT_RECORDED_EN, _NOT_RECORDED_AR), []
    from kazma_core.agent.effect_journal import inspect_effects
    from kazma_core.security.config_mask import mask_deep

    rows = _read("effects", lambda: inspect_effects(thread))
    if not isinstance(rows, list):
        return _missing("effects", _NOT_RECORDED_EN, _NOT_RECORDED_AR), []
    items = []
    open_ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        masked = mask_deep(row)
        item = {key: masked.get(key) if isinstance(masked, dict) else None for key in _EFFECT_KEYS}
        items.append(item)
        state = str(item.get("state") or "")
        if state != "completed" or item.get("result_uncertain"):
            open_ids.append(str(item.get("effect_id") or _NOT_RECORDED_EN))
    if not items:
        return _found("effects", detail_en=_NO_EFFECTS_EN, detail_ar=_NO_EFFECTS_AR), []
    return _found("effects", items=items), open_ids


def _who(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    if text:
        return text, text
    return _NOT_RECORDED_EN, _NOT_RECORDED_AR


def _approval(row: dict[str, Any]) -> dict[str, Any]:
    who_en, who_ar = _who(row.get("actor"))
    decision_en, decision_ar = _who(row.get("decision"))
    message = row.get("message") or ""
    return {
        "gate_id": row.get("gate_id"),
        "tool": row.get("tool"),
        "state": row.get("state"),
        "decision_en": decision_en,
        "decision_ar": decision_ar,
        "who_en": who_en,
        "who_ar": who_ar,
        "created_at": row.get("created_at"),
        "claimed_at": row.get("claimed_at"),
        "settled_at": row.get("settled_at"),
        "message": _as_data(message, "approval") if str(message).strip() else "",
    }


def _approvals(thread: str | None) -> dict[str, Any]:
    if not thread:
        return _missing("approvals", _NO_THREAD_EN, _NO_THREAD_AR)
    from kazma_core.safety.hitl_gates import thread_decisions
    from kazma_core.security.config_mask import mask_deep

    rows = _read("approvals", lambda: thread_decisions(thread))
    if not isinstance(rows, list):
        return _missing("approvals", _NOT_RECORDED_EN, _NOT_RECORDED_AR)
    items = []
    for row in rows:
        if isinstance(row, dict):
            masked = mask_deep(row)
            if isinstance(masked, dict):
                items.append(_approval(masked))
    if not items:
        return _found("approvals", detail_en=_NO_APPROVALS_EN, detail_ar=_NO_APPROVALS_AR)
    return _found("approvals", items=items)


def _commitment_item(row: Any) -> dict[str, Any]:
    return {
        "commitment_id": str(getattr(row, "commitment_id", "") or ""),
        "status": str(getattr(row, "status", "") or ""),
        "act": str(getattr(row, "act", "") or ""),
        "tool_name": str(getattr(row, "tool_name", "") or ""),
        "policy_decision": str(getattr(row, "policy_decision", "") or ""),
        "args_digest": str(getattr(row, "args_digest", "") or ""),
        "goal_text": _as_data(getattr(row, "goal_text", ""), "commitment"),
        "slots": _as_data(getattr(row, "slots", {}), "commitment"),
        "evidence": _as_data(getattr(row, "evidence", {}), "commitment"),
        "conflicts": _as_data(getattr(row, "conflicts", []), "commitment"),
        "result": _as_data(getattr(row, "result", {}), "commitment"),
    }


def _commitments(thread: str | None, tenant: str | None) -> dict[str, Any]:
    if not thread:
        return _missing("commitment", _NO_THREAD_EN, _NO_THREAD_AR)
    if not _ops_present():
        return _missing("commitment", _NOT_RECORDED_EN, _NOT_RECORDED_AR)
    from kazma_core.safety.commitment.store import list_by_thread

    rows = _read("commitment", lambda: list_by_thread(thread, tenant_id=tenant))
    if not isinstance(rows, list):
        return _missing("commitment", _NOT_RECORDED_EN, _NOT_RECORDED_AR)
    items = [_commitment_item(row) for row in rows]
    if not items:
        return _found("commitment", detail_en=_NO_COMMITMENTS_EN, detail_ar=_NO_COMMITMENTS_AR)
    return _found("commitment", items=items)


def _usage(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in _USAGE_KEYS}


def _no_turn() -> dict[str, Any]:
    return {"status": "not_recorded", "en": _TURN_LEDGER_EN, "ar": _TURN_LEDGER_AR}


def _llm(thread: str | None, turn_ids: list[str]) -> dict[str, Any]:
    if not thread:
        section = _missing("llm", _NO_THREAD_EN, _NO_THREAD_AR)
        section["turn"] = _no_turn()
        return section
    if not _file("llm_calls.db"):
        section = _missing("llm", _LEDGER_EN, _LEDGER_AR)
        section["turn"] = _no_turn()
        return section
    from kazma_core.observability.llm_ledger import thread_usage, turn_usage

    usage = _read("llm", lambda: thread_usage(thread))
    if not isinstance(usage, dict) or "calls" not in usage:
        section = _missing("llm", _LEDGER_EN, _LEDGER_AR)
        section["turn"] = _no_turn()
        return section
    section = _found(
        "llm", detail_en=_CHAT_LEDGER_EN, detail_ar=_CHAT_LEDGER_AR,
        items=[{"ledger": "chat", **_usage(usage)}],
    )
    own = [turn for turn in turn_ids if turn and turn != thread]
    if not own:
        section["turn"] = _no_turn()
        return section
    turns = []
    for turn_id in own:
        one = _read("llm_turn", lambda turn_id=turn_id: turn_usage(turn_id))
        if not isinstance(one, dict) or "calls" not in one:
            section["turn"] = {"status": "not_recorded", "en": _NOT_RECORDED_EN, "ar": _NOT_RECORDED_AR}
            return section
        turns.append({"turn_id": turn_id, **_usage(one)})
    section["turn"] = {"status": "recorded", "items": turns}
    return section


def _memory_item(key: str, row: dict[str, Any]) -> dict[str, Any]:
    used = row.get("used")
    if used is None:
        used_en, used_ar = _NOT_RECORDED_EN, _NOT_RECORDED_AR
    elif used:
        used_en, used_ar = "used", "استُخدمت"
    else:
        used_en, used_ar = "not used", "لم تُستخدم"
    shown = row.get("shown") if isinstance(row.get("shown"), dict) else {}
    return {
        "turn_key": key,
        "shown": shown,
        "shown_count": row.get("shown_count"),
        "empty": row.get("empty"),
        "degraded": row.get("degraded"),
        "failed": row.get("failed"),
        "latency_ms": row.get("latency_ms"),
        "used_en": used_en,
        "used_ar": used_ar,
    }


def _memories(keys: list[str]) -> dict[str, Any]:
    if not keys:
        return _missing("memories", _MEMORY_KEY_EN, _MEMORY_KEY_AR)
    if not _ops_present():
        return _missing("memories", _NOT_RECORDED_EN, _NOT_RECORDED_AR)
    from kazma_core.memory.recall_records import shown_record

    items = []
    missing = False
    for key in keys:
        row = _read("memories", lambda key=key: shown_record(key))
        if not isinstance(row, dict):
            missing = True
            items.append({
                "turn_key": key, "status": "not_recorded",
                "en": _NOT_RECORDED_EN, "ar": _NOT_RECORDED_AR,
            })
            continue
        items.append(_memory_item(key, row))
    if missing:
        return _missing("memories", _NOT_RECORDED_EN, _NOT_RECORDED_AR, items)
    return _found("memories", items=items)


def _gap(what: str, en: str, ar: str) -> dict[str, str]:
    return {"what": what, "en": en, "ar": ar}


def _unverified(
    sections: list[dict[str, Any]], open_ids: list[str], llm: dict[str, Any], kind: dict[str, Any],
) -> dict[str, Any]:
    items = []
    if kind.get("status") != "recorded":
        items.append(_gap("kind", _NOT_RECORDED_EN, _NOT_RECORDED_AR))
    for section in sections:
        if section.get("status") != "recorded":
            items.append(_gap(str(section.get("id") or ""), _NOT_RECORDED_EN, _NOT_RECORDED_AR))
    turn = llm.get("turn") if isinstance(llm, dict) else None
    if llm.get("status") == "recorded" and isinstance(turn, dict) and turn.get("status") != "recorded":
        items.append(_gap("llm_turn", str(turn.get("en") or _NOT_RECORDED_EN), str(turn.get("ar") or _NOT_RECORDED_AR)))
    for effect_id in open_ids:
        items.append({
            "what": "effect", "effect_id": effect_id, "en": _EFFECT_OPEN_EN, "ar": _EFFECT_OPEN_AR,
        })
    items.append(_gap("exactly_once", _DOES_NOT_EN, _DOES_NOT_AR))
    return _found("unverified", items=items)


def _body(run_id: str) -> dict[str, Any]:
    from kazma_core.security.config_mask import mask_deep

    kind, native, run, supervision = _supervision(run_id)
    thread = _thread_of(kind, native, run)
    tenant = getattr(run, "space", "") or None
    frames, journal = _journal(thread)
    keys, turn_ids = _frame_ids(frames)
    effects, open_ids = _effects(thread)
    approvals = _approvals(thread)
    commitment = _commitments(thread, tenant if isinstance(tenant, str) else None)
    llm = _llm(thread, turn_ids)
    memories = _memories(keys)
    sections = [supervision, journal, effects, approvals, commitment, llm, memories]
    kind_field = _kind_field(kind)
    sections.append(_unverified(sections, open_ids, llm, kind_field))
    body = {
        "kazma_receipt": 1,
        "run_id": _show_id(run_id),
        "kind": kind_field,
        "sections": sections,
        "proves": {"en": _PROVES_EN, "ar": _PROVES_AR},
        "does_not_prove": {"en": _DOES_NOT_EN, "ar": _DOES_NOT_AR},
    }
    masked = mask_deep(body)
    return masked if isinstance(masked, dict) else body


def _item_line(item: Any) -> str:
    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False)
    except (TypeError, ValueError):
        return _NOT_SHOWN


def _kind_line(kind: Any) -> str:
    if not isinstance(kind, dict) or kind.get("status") != "recorded":
        return "kind: " + _NOT_RECORDED_EN + " / " + _NOT_RECORDED_AR
    return "kind: " + str(kind.get("label_en", "")) + " / " + str(kind.get("label_ar", ""))


def _text(body: dict[str, Any]) -> str:
    lines = [_HEADER_EN, _HEADER_AR, "run: " + str(body.get("run_id", "")), _kind_line(body.get("kind"))]
    for section in body.get("sections") or []:
        if not isinstance(section, dict):
            continue
        lines.append("")
        lines.append(str(section.get("title_en", "")) + " / " + str(section.get("title_ar", "")))
        status = str(section.get("status", ""))
        lines.append(_word(status, "en") + " / " + _word(status, "ar"))
        if section.get("detail_en"):
            lines.append(str(section["detail_en"]))
        if section.get("detail_ar"):
            lines.append(str(section["detail_ar"]))
        turn = section.get("turn")
        if isinstance(turn, dict):
            lines.append("turn: " + _word(str(turn.get("status", "")), "en") + " / " + _word(str(turn.get("status", "")), "ar"))
            for item in turn.get("items") or []:
                lines.append(_item_line(item))
        for item in section.get("items") or []:
            lines.append(_item_line(item))
    proves = body.get("proves") if isinstance(body.get("proves"), dict) else {}
    denies = body.get("does_not_prove") if isinstance(body.get("does_not_prove"), dict) else {}
    lines.extend(["", str(proves.get("en", "")), str(proves.get("ar", "")), str(denies.get("en", "")), str(denies.get("ar", "")), ""])
    return "\n".join(lines) + "\n"


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _html_lang(body: dict[str, Any], lang: str) -> str:
    title_key = "title_en" if lang == "en" else "title_ar"
    detail_key = "detail_en" if lang == "en" else "detail_ar"
    parts = ["<h1>" + _esc(_HEADER_EN if lang == "en" else _HEADER_AR) + "</h1>"]
    parts.append("<p>" + _esc(body.get("run_id", "")) + "</p>")
    kind = body.get("kind") if isinstance(body.get("kind"), dict) else {}
    if kind.get("status") == "recorded":
        label = kind.get("label_en") if lang == "en" else kind.get("label_ar")
        parts.append("<p>" + _esc(label) + "</p>")
    else:
        parts.append("<p>" + _esc(_word("not_recorded", lang)) + "</p>")
    for section in body.get("sections") or []:
        if not isinstance(section, dict):
            continue
        parts.append("<h2>" + _esc(section.get(title_key, "")) + "</h2>")
        parts.append("<p>" + _esc(_word(str(section.get("status", "")), lang)) + "</p>")
        if section.get(detail_key):
            parts.append("<p>" + _esc(section.get(detail_key, "")) + "</p>")
        turn = section.get("turn")
        if isinstance(turn, dict):
            parts.append("<p>" + _esc(_word(str(turn.get("status", "")), lang)) + "</p>")
            for item in turn.get("items") or []:
                parts.append("<pre>" + _esc(_item_line(item)) + "</pre>")
        for item in section.get("items") or []:
            parts.append("<pre>" + _esc(_item_line(item)) + "</pre>")
    proves = body.get("proves") if isinstance(body.get("proves"), dict) else {}
    denies = body.get("does_not_prove") if isinstance(body.get("does_not_prove"), dict) else {}
    parts.append("<h2>" + _esc("What the signature proves" if lang == "en" else "ماذا يثبت التوقيع") + "</h2>")
    parts.append("<p>" + _esc(proves.get(lang, "")) + "</p>")
    parts.append("<p>" + _esc(denies.get(lang, "")) + "</p>")
    return "\n".join(parts) + "\n"


def _html(body: dict[str, Any]) -> str:
    style = "body{font-family:sans-serif;margin:1.5rem}section{margin-bottom:2rem}pre{white-space:pre-wrap}"
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta http-equiv="Content-Security-Policy" content="' + _esc(_CSP) + '">\n'
        "<title>" + _esc(_HEADER_EN) + "</title>\n"
        "<style>" + style + "</style>\n"
        "</head>\n<body>\n"
        '<section lang="en" dir="ltr">\n' + _html_lang(body, "en") + "</section>\n"
        '<section lang="ar" dir="rtl">\n' + _html_lang(body, "ar") + "</section>\n"
        "</body>\n</html>\n"
    )


def _render(body: dict[str, Any], kind: str) -> str:
    if kind == "json":
        return json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ) + "\n"
    if kind == "html":
        return _html(body)
    return _text(body)


def _vault() -> Any:
    from kazma_core.security.vault import get_vault

    return get_vault()


def _load_private(pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("receipt signing key is not Ed25519")
    return key


def _public_b64(public: Any) -> str:
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def _read_pem(vault: Any) -> str:
    """The stored PEM, or "" when no row is there. A row that does not open raises ``_BadKeyError``.

    Nothing is written. A bad PEM is not a missing key: replacing it would
    make every receipt the first key signed fail to verify.
    """
    try:
        pem = vault.retrieve(_KEY_NAME)
    except (InvalidTag, ValueError, UnicodeError, TypeError):
        raise _BadKeyError() from None
    return pem or ""


def _signing_key(vault: Any) -> Ed25519PrivateKey:
    """The install's receipt key. Made on first use; an existing key is kept."""
    pem = _read_pem(vault)
    if not pem:
        fresh = Ed25519PrivateKey.generate()
        generated = fresh.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        pem = vault.store_install_once(_KEY_NAME, generated, category="receipt") or ""
    if not pem:
        raise _BadKeyError()
    try:
        return _load_private(pem)
    except (ValueError, UnicodeError, TypeError):
        raise _BadKeyError() from None


def _installed_private(vault: Any) -> Ed25519PrivateKey:
    """The key already in the vault. Verify does not make one."""
    pem = _read_pem(vault)
    if not pem:
        raise _NoKeyError()
    try:
        return _load_private(pem)
    except (ValueError, UnicodeError, TypeError):
        raise _BadKeyError() from None


def _sign(payload: bytes, private: Ed25519PrivateKey) -> bytes:
    """Sign the payload bytes and the newline that ends them. The trailer is not signed."""
    signed = payload if payload.endswith(b"\n") else payload + b"\n"
    pub = _public_b64(private.public_key())
    sig = base64.b64encode(private.sign(signed)).decode("ascii")
    return signed + f"{_TRAILER} {pub} {sig}\n".encode("ascii")


def _split_file(data: bytes) -> tuple[bytes, str, str] | None:
    if not data.endswith(b"\n"):
        return None
    head = data[:-1]
    cut = head.rfind(b"\n")
    if cut < 0:
        return None
    try:
        trailer = head[cut + 1:].decode("ascii")
    except UnicodeError:
        return None
    parts = trailer.split(" ")
    if len(parts) != 3 or parts[0] != _TRAILER or not parts[1] or not parts[2]:
        return None
    return head[:cut + 1], parts[1], parts[2]


def _same_key(stated: str, ours: str) -> bool:
    """This install's public key. Bytes, so a non-ASCII trailer is a mismatch."""
    from kazma_core.security.secret_compare import same_secret

    return same_secret(stated, ours)


def _verify_bytes(data: bytes) -> tuple[int, list[str]]:
    parsed = _split_file(data)
    if parsed is None:
        return 1, [_NOT_FILE_EN, _NOT_FILE_AR]
    signed, stated, sig_b64 = parsed
    vault = _vault()
    if vault is None:
        return 1, [_NO_VAULT_EN, _NO_VAULT_AR]
    try:
        private = _installed_private(vault)
    except _NoKeyError:
        return 1, [_NO_SIGN_EN, _NO_SIGN_AR]
    except _BadKeyError:
        return 1, [_BAD_KEY_EN, _BAD_KEY_AR]
    except (ValueError, UnicodeError, TypeError):
        return 1, [_BAD_KEY_EN, _BAD_KEY_AR]
    ours = _public_b64(private.public_key())
    if not _same_key(stated, ours):
        return 1, [_MISMATCH_EN, _MISMATCH_AR]
    try:
        sig = base64.b64decode(sig_b64, validate=True)
        private.public_key().verify(sig, signed)
    except (binascii.Error, ValueError, InvalidSignature):
        return 1, [_CHANGED_EN, _CHANGED_AR]
    return 0, [_PROVES_EN, _PROVES_AR, _DOES_NOT_EN, _DOES_NOT_AR]


def _say(lines: list[str], *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    for line in lines:
        print(line, file=stream)


def _write_out(path: str, blob: bytes) -> int:
    from kazma_core.diagnostic_scope import DiagnosticWriteRefused
    from kazma_core.errors import described_error
    from kazma_core.security.file_access import write_private

    try:
        write_private(Path(path), blob)
    except DiagnosticWriteRefused:
        raise
    except OSError as exc:
        info = described_error("The receipt could not be written", exc)
        _say([info.text, "تعذّر كتابة الإيصال."], err=True)
        return 1
    _say([_WROTE_EN, _WROTE_AR])
    return 0


def _emit(args: argparse.Namespace) -> int:
    from kazma_core.diagnostic_scope import read_only_diagnostic

    vault = _vault()
    if vault is None:
        _say([_NO_VAULT_EN, _NO_VAULT_AR])
        return 1
    try:
        private = _signing_key(vault)
    except _NoKeyError:
        _say([_NO_SIGN_EN, _NO_SIGN_AR])
        return 1
    except _BadKeyError:
        _say([_BAD_KEY_EN, _BAD_KEY_AR])
        return 1
    except (ValueError, UnicodeError, TypeError):
        _say([_BAD_KEY_EN, _BAD_KEY_AR])
        return 1
    with read_only_diagnostic("kazma receipt"):
        body = _body(args.run_id)
    kind = "json" if args.json else "html" if args.html else "text"
    try:
        blob = _sign(_render(body, kind).encode("utf-8"), private)
    except (TypeError, ValueError, UnicodeError) as exc:
        from kazma_core.errors import described_error

        info = described_error("The receipt could not be signed", exc)
        _say([info.text, "تعذّر توقيع الإيصال."], err=True)
        return 1
    if args.out:
        return _write_out(args.out, blob)
    sys.stdout.buffer.write(blob)
    return 0


def _print_public_key() -> int:
    vault = _vault()
    if vault is None:
        _say([_NO_VAULT_EN, _NO_VAULT_AR])
        return 1
    try:
        private = _signing_key(vault)
    except _NoKeyError:
        _say([_NO_SIGN_EN, _NO_SIGN_AR])
        return 1
    except _BadKeyError:
        _say([_BAD_KEY_EN, _BAD_KEY_AR])
        return 1
    except (ValueError, UnicodeError, TypeError):
        _say([_BAD_KEY_EN, _BAD_KEY_AR])
        return 1
    _say([_public_b64(private.public_key()), _PUB_EN, _PUB_AR])
    return 0


def run(argv: list[str] | None = None) -> int:
    """``kazma receipt``. Exit 0 when a receipt was written, 1 when it cannot be signed."""
    from kazma_cli.main import _print_utf8

    _print_utf8()
    parser = argparse.ArgumentParser(
        prog="kazma receipt",
        description="Write a signed receipt of one run. A partial record says not recorded.",
    )
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--json", action="store_true", help="canonical JSON, signed over those exact bytes")
    parser.add_argument("--html", action="store_true", help="HTML in English and Arabic, with no scripts")
    parser.add_argument("--out", metavar="FILE", help="write the receipt to FILE, readable by this account only")
    parser.add_argument("--public-key", action="store_true", help="print this install's receipt public key")
    args = parser.parse_args(argv)
    if args.json and args.html:
        parser.error("--json and --html are mutually exclusive")
    if args.public_key:
        if args.run_id or args.json or args.html or args.out:
            parser.error("--public-key prints the key and takes no receipt")
        return _print_public_key()
    if not args.run_id:
        parser.error("a run id is required")
    return _emit(args)


def run_verify(argv: list[str] | None = None) -> int:
    """``kazma verify-receipt``. Exit 0 only when this install's key signed the file."""
    from kazma_core.errors import described_error

    from kazma_cli.main import _print_utf8

    _print_utf8()
    parser = argparse.ArgumentParser(
        prog="kazma verify-receipt",
        description="Check a receipt with this install's key. Does not read the run again.",
    )
    parser.add_argument("file")
    args = parser.parse_args(argv)
    try:
        data = Path(args.file).read_bytes()
    except OSError as exc:
        info = described_error("The receipt could not be read", exc)
        _say([info.text, "تعذّر قراءة الإيصال."], err=True)
        return 1
    code, lines = _verify_bytes(data)
    _say(lines)
    return code
