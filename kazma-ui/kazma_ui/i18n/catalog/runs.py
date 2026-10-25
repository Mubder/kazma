"""Running work: the Dashboard's Runs card and the web chat's ``/runs``."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "runs.title": {"en": "Running work", "ar": "الأعمال الجارية"},
    "runs.description": {
        "en": "Work that stalled, got stuck or failed, and what you can do about it.",
        "ar": "الأعمال التي توقفت أو علقت أو فشلت، وما يمكنك فعله حيالها.",
    },
    "runs.loading": {"en": "Reading running work…", "ar": "جارٍ قراءة الأعمال الجارية…"},
    "runs.empty": {
        "en": "Nothing is running and nothing needs attention.",
        "ar": "لا يوجد عمل جارٍ ولا شيء يحتاج انتباهك.",
    },
    "runs.load_failed": {
        "en": "Could not read running work. Trying again shortly.",
        "ar": "تعذّرت قراءة الأعمال الجارية. ستُعاد المحاولة قريبًا.",
    },
    "runs.supervisor_off": {
        "en": "The run supervisor is not running: stuck or failed work is not being reported.",
        "ar": "مراقب الأعمال متوقف: لا يُبلَّغ عن الأعمال العالقة أو الفاشلة.",
    },
    "runs.kind_unreadable": {
        "en": "{kind}: could not be read ({reason})",
        "ar": "{kind}: تعذّرت القراءة ({reason})",
    },
    "runs.noticed": {"en": "Noticed {when}", "ar": "رُصد {when}"},
    "runs.progress": {"en": "Last progress {when}", "ar": "آخر تقدم {when}"},
    "runs.reason": {"en": "Reason: {detail}", "ar": "السبب: {detail}"},
    "runs.effects_unknown": {
        "en": "Its effects outside Kazma are unknown: check them before you retry.",
        "ar": "آثاره خارج كازما غير معروفة: تحقّق منها قبل إعادة المحاولة.",
    },
    "runs.decided": {"en": "{action}: {outcome}", "ar": "{action}: {outcome}"},
    "runs.outcome_resolved": {"en": "Settled by itself: {reason}", "ar": "انتهى من تلقاء نفسه: {reason}"},
    "runs.outcome_dismissed": {"en": "Dismissed", "ar": "تم التجاهل"},
    "runs.outcome_expired": {"en": "Expired unanswered", "ar": "انتهت مدته دون رد"},
    "runs.outcome_interrupted": {
        "en": "Kazma stopped while the action ran; check the run before acting again.",
        "ar": "توقف كازما أثناء تنفيذ الإجراء؛ تحقّق من العمل قبل أن تتخذ إجراءً آخر.",
    },
    "runs.automatic": {"en": "Kazma's automatic first step", "ar": "الخطوة الأولى التلقائية من كازما"},
    "runs.diagnosis_title": {"en": "Diagnosis", "ar": "التشخيص"},
    "runs.diagnosis_running": {"en": "Diagnosing…", "ar": "جارٍ التشخيص…"},
    "runs.open_page": {"en": "Open its page", "ar": "افتح صفحته"},
    "runs.history_title": {"en": "Recent incidents", "ar": "الحوادث الأخيرة"},
    "runs.message_delivered": {"en": "Sent to your chat", "ar": "أُرسل إلى محادثتك"},
    "runs.message_refused": {"en": "The message could not be sent", "ar": "تعذّر إرسال الرسالة"},
    "runs.message_uncertain": {"en": "The message may not have arrived", "ar": "ربما لم تصل الرسالة"},
    "runs.message_announced": {
        "en": "No message: another Kazma notice already reports this",
        "ar": "بلا رسالة: يُبلغك به تنبيه آخر من كازما",
    },
    "runs.message_before_start": {
        "en": "No message: it ended before Kazma started watching",
        "ar": "بلا رسالة: انتهى قبل أن يبدأ كازما المراقبة",
    },
    "runs.message_pending": {"en": "Waiting to be sent", "ar": "بانتظار الإرسال"},
    "runs.message_sending": {"en": "Being sent", "ar": "قيد الإرسال"},
    "runs.action_done": {"en": "Done: {message}", "ar": "تم: {message}"},
    "runs.action_refused": {"en": "Not done: {message}", "ar": "لم يتم: {message}"},
    "runs.runbooks_title": {"en": "Automatic first steps", "ar": "الخطوات الأولى التلقائية"},
    "runs.runbooks_hint": {
        "en": "Each one is off until you switch it on. Kazma takes at most one such step per piece of "
              "work a day, never on work whose effects are unknown, and then tells you what it did.",
        "ar": "كل منها متوقف حتى تشغّله. يتخذ كازما خطوة واحدة على الأكثر لكل عمل في اليوم، ولا يتخذها "
              "أبدًا على عمل آثاره غير معروفة، ثم يخبرك بما فعل.",
    },
    "runs.runbook.swarm_task.failed": {
        "en": "Retry a failed swarm task that changed nothing outside Kazma",
        "ar": "أعد محاولة مهمة سرب فاشلة لم تغيّر شيئًا خارج كازما",
    },
    "runs.runbook.document_job.failed": {
        "en": "Retry a document job that ran out of attempts",
        "ar": "أعد محاولة مهمة مستند استنفدت محاولاتها",
    },
    "runs.runbook.memory_task.failed": {
        "en": "Retry a memory task that ran out of attempts",
        "ar": "أعد محاولة مهمة ذاكرة استنفدت محاولاتها",
    },
    "runs.runbook.chat_turn.dead": {
        "en": "Close a chat turn left open with nothing running it",
        "ar": "أغلق دورة محادثة بقيت مفتوحة ولا شيء يشغّلها",
    },
    "runs.runbook_saved": {"en": "Saved", "ar": "تم الحفظ"},
    "runs.runbook_failed": {"en": "Not saved: {message}", "ar": "لم يُحفظ: {message}"},
    "runs.count_attention.zero": {"en": "nothing needs attention", "ar": "لا شيء يحتاج انتباهك"},
    "runs.count_attention.one": {"en": "1 needs attention", "ar": "واحد يحتاج انتباهك"},
    "runs.count_attention.two": {"en": "2 need attention", "ar": "اثنان يحتاجان انتباهك"},
    "runs.count_attention.few": {"en": "{n} need attention", "ar": "{n} تحتاج انتباهك"},
    "runs.count_attention.many": {"en": "{n} need attention", "ar": "{n} تحتاج انتباهك"},
    "runs.count_attention.other": {"en": "{n} need attention", "ar": "{n} تحتاج انتباهك"},
    "runs.count_active.zero": {"en": "nothing running", "ar": "لا عمل جارٍ"},
    "runs.count_active.one": {"en": "1 running", "ar": "عمل واحد جارٍ"},
    "runs.count_active.two": {"en": "2 running", "ar": "عملان جاريان"},
    "runs.count_active.few": {"en": "{n} running", "ar": "{n} أعمال جارية"},
    "runs.count_active.many": {"en": "{n} running", "ar": "{n} عملًا جاريًا"},
    "runs.count_active.other": {"en": "{n} running", "ar": "{n} عمل جارٍ"},
    # The web chat's /runs (sse_chat/_runs.py)
    "runs.chat_single_operator": {
        "en": "Running work is shown on a single-operator install only.",
        "ar": "تُعرض الأعمال الجارية في التثبيت ذي المشغّل الواحد فقط.",
    },
    "runs.chat_dashboard_link": {
        "en": "[Open Running work on the Dashboard](/dashboard#runs-panel) to act on a run.",
        "ar": "[افتح الأعمال الجارية في لوحة المعلومات](/dashboard#runs-panel) لاتخاذ إجراء على عمل.",
    },
    "runs.chat_not_a_run": {
        "en": "'{id}' is not a run id. /runs lists them.",
        "ar": "'{id}' ليس معرّف عمل. يعرضها الأمر /runs.",
    },
    "runs.chat_not_listed": {
        "en": "No run {id} is listed now: it finished more than a day ago or was removed.",
        "ar": "العمل {id} غير موجود الآن: انتهى قبل أكثر من يوم أو أُزيل.",
    },
    "runs.chat_unreadable": {
        "en": "Running work could not be read (the server log says why).",
        "ar": "تعذّرت قراءة الأعمال الجارية (سجل الخادم يذكر السبب).",
    },
}
