"""Runs and incidents as text: ``/runs`` on every surface and the agent's tools.

One wording, in English and Arabic (the web chat answers in the page's
language; the chat apps' replies are English, AGENTS.md "UI Conventions").
A run's title and failure reason are its own data (a prompt, a file name, an
error text): the agent's tools fence them (``fence_untrusted``), the chat
replies show them as they are.
"""

from __future__ import annotations

import time
from typing import Any

from kazma_core.runs.contract import ACTIVE_STATES, Run, RunState, Verdict
from kazma_core.runs.kinds import KINDS

__all__ = ["ago", "attention", "describe_run", "format_listing", "state_word"]

_STATE: dict[str, tuple[str, str]] = {
    RunState.QUEUED: ("Queued", "في الانتظار"),
    RunState.RUNNING: ("Running", "قيد التنفيذ"),
    RunState.WAITING_HUMAN: ("Waiting for you", "بانتظارك"),
    RunState.STALLED: ("Stalled", "متوقف بلا تقدم"),
    RunState.SLOW: ("Slow", "بطيء"),
    RunState.FAILED: ("Failed", "فشل"),
    RunState.DONE: ("Done", "اكتمل"),
    RunState.CANCELLED: ("Cancelled", "أُلغي"),
    RunState.UNKNOWN: ("Outcome unknown", "النتيجة غير معروفة"),
}
_DEAD = ("Stuck: nothing is running it", "عالق: لا شيء يشغّله")
_ICON = {
    RunState.STALLED: "⚠️", RunState.FAILED: "❌", RunState.UNKNOWN: "❓",
    RunState.WAITING_HUMAN: "✋", RunState.SLOW: "🐢", RunState.RUNNING: "▶️",
    RunState.QUEUED: "⏳", RunState.DONE: "✅", RunState.CANCELLED: "⏹️",
}
_WORDS = {
    "header": ("Running work", "الأعمال الجارية"),
    "none": ("Nothing is running, and nothing needs attention.", "لا يوجد عمل جارٍ ولا شيء يحتاج انتباهك."),
    "attention": ("need attention", "تحتاج انتباهك"),
    "active": ("active", "جارية"),
    "unreadable": ("Could not read", "تعذّرت قراءة"),
    "no_progress": ("no progress for", "بلا تقدم منذ"),
    "progress": ("last progress", "آخر تقدم"),
    "actions": ("Actions", "الإجراءات"),
    "page": ("Page", "الصفحة"),
    "effects_unknown": ("Its effects are unknown: check before you retry.",
                        "آثاره غير معروفة: تحقّق قبل إعادة المحاولة."),
    "more": ("Show one: /runs show <id>. Everything from the last day: /runs all.",
             "لعرض واحد: /runs show <id>. كل ما جرى خلال يوم: /runs all."),
}
_ACTION = {
    "cancel": ("Cancel", "إلغاء"), "retry": ("Retry", "إعادة المحاولة"),
    "retry_checked": ("Retry (I checked its effects)", "إعادة المحاولة (تحقّقت من آثاره)"),
    "close": ("Close the turn", "إغلاق الدورة"), "dismiss": ("Dismiss", "تجاهل"),
    "diagnose": ("Ask Kazma to diagnose", "اطلب من كازما التشخيص"),
}


def _w(pair: tuple[str, str], lang: str) -> str:
    return pair[1] if lang == "ar" else pair[0]


def ago(seconds: float, lang: str = "en") -> str:
    """``12 min`` / ``3 h`` / ``40 s`` (Latin digits in both languages)."""
    s = max(0, int(seconds))
    if s < 90:
        return f"{s} {'ث' if lang == 'ar' else 's'}"
    if s < 90 * 60:
        return f"{s // 60} {'د' if lang == 'ar' else 'min'}"
    return f"{s // 3600} {'س' if lang == 'ar' else 'h'}"


def state_word(run: Run, lang: str = "en") -> str:
    if run.verdict == Verdict.DEAD:
        return _w(_DEAD, lang)
    return _w(_STATE.get(run.state, _STATE[RunState.UNKNOWN]), lang)


def action_word(action: str, lang: str = "en") -> str:
    return _w(_ACTION.get(action, (action, action)), lang)


def attention(run: Run) -> bool:
    """Whether the run needs the owner: stalled, stuck, failed or unknown."""
    return run.state in (RunState.STALLED, RunState.FAILED, RunState.UNKNOWN)


def _line(run: Run, now: float, lang: str) -> str:
    spec = KINDS.get(run.kind)
    label = (spec.label_ar if lang == "ar" else spec.label) if spec else run.kind
    parts = [f"{_ICON.get(run.state, '•')} {label}: {run.title}", state_word(run, lang)]
    last = run.progress_at or run.started_at
    if run.state == RunState.STALLED and last:
        parts.append(f"{_w(_WORDS['no_progress'], lang)} {ago(now - last, lang)}")
    elif run.state == RunState.FAILED and run.detail:
        # A failed run's reason, never its last step ("task_completed").
        parts.append(run.detail)
    elif run.progress_note and run.state in ACTIVE_STATES:
        parts.append(run.progress_note)
    # In backticks: chat apps read ``_`` as italics ("swarmtask:").
    parts.append(f"`{run.run_id}`")
    return " · ".join(parts)


def format_listing(runs: list[Run], errors: dict[str, str], *, lang: str = "en",
                   include_finished: bool = False, now: float | None = None) -> str:
    """The ``/runs`` answer: what needs attention first, then what runs."""
    stamp = time.time() if now is None else now
    needs = [r for r in runs if attention(r)]
    active = [r for r in runs if r.state in ACTIVE_STATES and not attention(r)]
    rest = [r for r in runs if r not in needs and r not in active] if include_finished else []
    lines: list[str] = []
    if needs or active:
        lines.append(f"{_w(_WORDS['header'], lang)} ({len(needs)} {_w(_WORDS['attention'], lang)}, "
                     f"{len(active)} {_w(_WORDS['active'], lang)})")
    else:
        lines.append(_w(_WORDS["none"], lang))
    lines += [_line(r, stamp, lang) for r in needs + active + rest][:40]
    for kind, reason in sorted(errors.items()):
        spec = KINDS.get(kind)
        label = (spec.label_ar if lang == "ar" else spec.label) if spec else kind
        lines.append(f"⚠️ {_w(_WORDS['unreadable'], lang)}: {label} ({reason})")
    if runs:
        lines.append(_w(_WORDS["more"], lang))
    return "\n".join(lines)


def describe_run(run: Run, *, lang: str = "en", now: float | None = None,
                 incident: dict[str, Any] | None = None) -> str:
    """One run in detail: ``/runs show`` and the ``run_status`` tool."""
    stamp = time.time() if now is None else now
    lines = [_line(run, stamp, lang)]
    last = run.progress_at or run.started_at
    if last:
        lines.append(f"{_w(_WORDS['progress'], lang)}: {ago(stamp - last, lang)}")
    if run.detail and run.state != RunState.FAILED:  # a failed run's line holds it
        lines.append(run.detail)
    if run.effects == "unknown":
        lines.append(_w(_WORDS["effects_unknown"], lang))
    offered = list(run.actions)
    if incident is not None:
        offered += [a for a in incident.get("actions", []) if a not in offered]
    if offered:
        lines.append(f"{_w(_WORDS['actions'], lang)}: " + ", ".join(action_word(a, lang) for a in offered))
    if run.link:
        lines.append(f"{_w(_WORDS['page'], lang)}: {run.link}")
    return "\n".join(lines)
