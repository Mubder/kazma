"""A turn's closing answer matches what its tools did (resilience plan D3).

A turn's final answer is the model's own account of its work. When it says a
file was written or sent and no tool did that, nothing said so: a report
"saved as kuwait_history.pdf" after a refused ``generate_pdf`` read like a
finished job. At the terminal boundary (``graph_respond.respond_node``, after
the answer's formatting) the answer is checked against the running turn's
tool receipts -- the calls the model made and the results they got:

* A sentence that says a named file was written, saved, created, exported or
  sent, where no write or send of that file succeeded this turn, gets a
  correction line in the answer's language ("Not done: ...").
* When the turn wrote or sent files the answer does not name, a short line
  lists them, built from the receipts, not from the model.

Conservative by design: only file names with an extension are judged, a
sentence that negates, offers, plans or points to an earlier turn is not a
claim, and nothing is added to a failed turn, a JSON or code answer, or a
reply the user asked to keep to one paragraph.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["check_answer_receipts"]

#: Tools whose success writes or sends a file.
WRITERS = frozenset({
    "file_write", "file_append", "file_apply_patch", "file_apply_patch_set",
    "generate_pdf", "generate_docx", "generate_xlsx", "generate_markdown_doc",
    "document_convert", "document_redact", "convert_document", "send_file",
    "x_download_media",
})
_EXT = r"(?:md|markdown|pdf|docx?|xlsx?|csv|tsv|txt|json|html?|py|js|ts|tsx|css|ya?ml|png|jpe?g|svg|zip|pptx|mp4|xml)"
_FILE = re.compile(rf"[\w؀-ۿ][\w؀-ۿ.\-]*\.{_EXT}\b", re.I)
_PATHISH = re.compile(rf"[\w؀-ۿ.:\-/\\]*[\w؀-ۿ]\.{_EXT}\b", re.I)
_AR = "\u0600-\u06FF"
_CLAIM = re.compile(
    r"\b(?:saved|wrote|written|created|generated|exported|sent|attached|stored|produced|uploaded)\b"
    rf"|(?<![{_AR}])[وف]?(?:حفظت|كتبت|أنشأت|انشأت|أرسلت|ارسلت|صدّرت|صدرت|أعددت|اعددت|جهّزت|جهزت)"
    rf"(?:ه|ها|هم|لك)?(?![{_AR}])"
    r"|تم (?:حفظ|إنشاء|انشاء|إرسال|ارسال|تصدير|إعداد|اعداد)",
    re.I,
)
#: Words that make a sentence no claim of a write in this turn: a negation,
#: an offer or plan, a condition, an earlier time (whole words in Arabic:
#: "لم" is inside "الملف").
_NOT_A_CLAIM = re.compile(
    r"\b(?:not|n't|never|failed|unable|cannot|can't|couldn't|didn't|wasn't|won't|will|would|could|can|"
    r"should|shall|let me|i'll|going to|earlier|previously|before|last time|yesterday|if)\b"
    rf"|(?<![{_AR}])[وف]?(?:لم|لن|ما|تعذر|تعذّر|فشل|فشلت|سوف|راح|سابقا|سابقاً|أمس|امس|إذا|اذا|لو|يمكن|ممكن)(?![{_AR}])"
    rf"|(?<![{_AR}])سأ[{_AR}]+",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?؟])\s+|\n+")


def _calls(turn: list[dict[str, Any]]) -> dict[str, tuple[str, dict[str, Any]]]:
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for m in turn:
        for tc in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = str(fn.get("name") or tc.get("name") or "")
            args = tc.get("arguments", fn.get("arguments"))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"raw": args}
            calls[str(tc.get("id") or "")] = (name, args if isinstance(args, dict) else {})
    return calls


def _receipts(state: dict[str, Any], turn: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(texts of the writes and sends that succeeded, the files they wrote or sent)."""
    from kazma_core.agent.tool_loop_breaker import ToolOutcome, classify_tool_result

    calls = _calls(turn)
    recorded = state.get("tool_results") or {}
    texts: list[str] = []
    files: list[str] = []
    for m in turn:
        if not isinstance(m, dict) or m.get("role") not in ("tool", "function"):
            continue
        call_id = str(m.get("tool_call_id") or "")
        name, args = calls.get(call_id, (str(m.get("name") or ""), {}))
        if name not in WRITERS:
            continue
        result = dict(recorded.get(call_id) or {})
        result.setdefault("content", m.get("content") or "")
        result["name"] = name
        if classify_tool_result(result) != ToolOutcome.OK or result.get("is_error"):
            continue
        content = str(result.get("content") or "")
        texts.append(json.dumps(args, ensure_ascii=False) + "\n" + content)
        named = [str(args[key]) for key in ("path",) if isinstance(args.get(key), str) and args[key]]
        for patch in args.get("patches") or []:
            if isinstance(patch, dict) and isinstance(patch.get("path"), str):
                named.append(patch["path"])
        if not named:
            named = _PATHISH.findall(content)[:4]
        for path in named:
            base = _base(path)
            if base and base.lower() not in {f.lower() for f in files}:
                files.append(base)
    return texts, files


def _base(path: str) -> str:
    return re.split(r"[/\\]", path.strip().strip("`'\""))[-1]


def _unconfirmed_claims(answer: str, texts: list[str]) -> list[str]:
    haystack = "\n".join(texts).lower()
    missing: list[str] = []
    for sentence in _SENTENCE.split(answer):
        if not _CLAIM.search(sentence) or _NOT_A_CLAIM.search(sentence):
            continue
        for name in _FILE.findall(sentence):
            base = _base(name)
            if base.lower() not in haystack and base not in missing:
                missing.append(base)
    return missing


def check_answer_receipts(messages: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """The terminal answer with its correction and files lines, when due.
    Pure: no I/O, never raises on odd input (returns *messages*)."""
    if state.get("turn_failed") or not messages:
        return messages
    final = messages[-1]
    text = final.get("content") if isinstance(final, dict) else None
    if (not isinstance(final, dict) or final.get("role") not in ("assistant", "ai") or final.get("tool_calls")
            or not isinstance(text, str) or not text.strip()):
        return messages
    from kazma_core.agent.answer_format import _protected, _requests_one_paragraph
    from kazma_core.agent.plan_fence import running_turn
    from kazma_core.agent.turn_input import extract_latest_user_text

    if _protected(text) or _requests_one_paragraph(extract_latest_user_text(messages)):
        return messages
    turn = running_turn(messages)
    texts, files = _receipts(state, turn)
    missing = _unconfirmed_claims(text, texts)
    unnamed = [f for f in files if f.lower() not in text.lower()]
    if not missing and not unnamed:
        return messages
    from kazma_core.language_lock import in_their_language

    lines: list[str] = []
    for name in missing:
        lines.append(in_their_language(
            text,
            f"Not done: no tool wrote or sent {name} in this turn.",
            f"لم يتم: لم تكتب أي أداة الملف {name} ولم ترسله في هذه الدورة.",
        ))
    if unnamed:
        shown = ", ".join(files[:8]) + (f" (+{len(files) - 8})" if len(files) > 8 else "")
        lines.append(in_their_language(text, f"Files written or sent this turn: {shown}.",
                                       f"الملفات المكتوبة أو المرسلة في هذه الدورة: {shown}."))
    return [*messages[:-1], {**final, "content": text.rstrip() + "\n\n" + "\n".join(lines)}]
