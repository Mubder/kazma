"""``scheduled`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "scheduled.actions": {
        "ar": "إجراءات",
        "en": "Actions",
    },
    "scheduled.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "scheduled.confirm_delete_task": {
        "ar": "هل تريد إلغاء وحذف هذه المهمة المجدولة؟",
        "en": "Cancel and delete this scheduled task?",
    },
    "scheduled.confirm_delete_x": {
        "ar": "هل تريد إلغاء منشور إكس المجدول؟ لن يتم نشره.",
        "en": "Cancel this scheduled X post? It will not be published.",
    },
    "scheduled.created": {
        "ar": "تمت جدولة المهمة.",
        "en": "Task scheduled.",
    },
    "scheduled.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "scheduled.deleted": {
        "ar": "تم الحذف.",
        "en": "Deleted.",
    },
    "scheduled.edit": {
        "ar": "تعديل",
        "en": "Edit",
    },
    "scheduled.edit_task": {
        "ar": "تعديل المهمة",
        "en": "Edit task",
    },
    "scheduled.edit_x": {
        "ar": "إعادة جدولة منشور إكس",
        "en": "Reschedule X post",
    },
    "scheduled.empty": {
        "ar": "لا توجد مهام مجدولة بعد. أنشئ مهمة أو جدول منشور إكس للبدء.",
        "en": "No scheduled tasks yet. Create a task or schedule an X post to get started.",
    },
    "scheduled.empty_history": {
        "ar": "لم يتم تشغيل أي شيء بعد.",
        "en": "Nothing has run yet.",
    },
    "scheduled.empty_upcoming": {
        "ar": "لا يوجد شيء مجدول. تظهر هنا المهام ومنشورات إكس الجديدة — من هذه الصفحة أو من المحادثة.",
        "en": "Nothing scheduled. New tasks and X posts appear here — from this page or from chat.",
    },
    "scheduled.hint": {
        "ar": "كل ما سيشغّله كازما أو ينشره مستقبلًا — مهام مجدولة ومنشورات إكس المجدولة. أدرها هنا أو في المحادثة؛ يبقيان متزامنين.",
        "en": "Everything Kazma will run or post in the future — cron tasks and scheduled X posts. Manage them here or in chat; both stay in sync.",
    },
    "scheduled.kind_task": {
        "ar": "مهمة",
        "en": "Task",
    },
    "scheduled.kind_x": {
        "ar": "منشور إكس",
        "en": "X post",
    },
    "scheduled.new_task": {
        "ar": "مهمة جديدة",
        "en": "New task",
    },
    "scheduled.new_x": {
        "ar": "منشور إكس جديد",
        "en": "New X post",
    },
    "scheduled.outcome": {
        "ar": "آخر تشغيل",
        "en": "Last run",
    },
    "scheduled.overdue": {
        "ar": "متأخرة",
        "en": "Overdue",
    },
    "scheduled.prompt": {
        "ar": "نص المهمة",
        "en": "Task prompt",
    },
    "scheduled.prompt_ph": {
        "ar": "ماذا يجب أن يفعل الوكيل في الوقت المحدد؟",
        "en": "What should the agent do at the scheduled time?",
    },
    "scheduled.prompt_required": {
        "ar": "نص المهمة مطلوب.",
        "en": "Task prompt is required.",
    },
    "scheduled.refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "scheduled.save": {
        "ar": "حفظ",
        "en": "Save",
    },
    "scheduled.sort_newest": {
        "ar": "الأحدث أولاً — اضغط للأقدم أولاً",
        "en": "Newest first — click for oldest first",
    },
    "scheduled.sort_oldest": {
        "ar": "الأقدم أولاً — اضغط للأحدث أولاً",
        "en": "Oldest first — click for newest first",
    },
    "scheduled.sort_toggle": {
        "ar": "تغيير ترتيب الفرز",
        "en": "Change sort order",
    },
    "scheduled.st_cancelled": {
        "ar": "ملغاة",
        "en": "Cancelled",
    },
    "scheduled.st_done": {
        "ar": "تم",
        "en": "Done",
    },
    "scheduled.st_failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "scheduled.st_fired": {
        "ar": "تم النشر",
        "en": "Posted",
    },
    "scheduled.st_pending": {
        "ar": "قيد الانتظار",
        "en": "Pending",
    },
    "scheduled.st_running": {
        "ar": "قيد التنفيذ",
        "en": "Running",
    },
    "scheduled.stat_done": {
        "ar": "منتهية",
        "en": "Completed",
    },
    "scheduled.stat_failed": {
        "ar": "فاشلة",
        "en": "Failed",
    },
    "scheduled.stat_overdue": {
        "ar": "متأخرة",
        "en": "Overdue",
    },
    "scheduled.stat_upcoming": {
        "ar": "قادمة",
        "en": "Upcoming",
    },
    "scheduled.status": {
        "ar": "الحالة",
        "en": "Status",
    },
    "scheduled.summary": {
        "ar": "التفاصيل",
        "en": "Details",
    },
    "scheduled.tab_history": {
        "ar": "السجل",
        "en": "History",
    },
    "scheduled.tab_upcoming": {
        "ar": "القادمة",
        "en": "Upcoming",
    },
    "scheduled.tab_x_activity": {
        "ar": "نشاط إكس",
        "en": "X activity",
    },
    "scheduled.timing": {
        "ar": "الوقت",
        "en": "When",
    },
    "scheduled.timing_hint": {
        "ar": "نسبي (5m، 1h)، أو متكرر (daily at 9am، weekdays at 08:30، every mon,wed at 21:00، مع until 2026-12-31 أو count 5 اختياريًا)، أو تاريخ ووقت.",
        "en": "Relative (5m, 1h), recurring (daily at 9am, weekdays at 08:30, every mon,wed at 21:00, optionally with until 2026-12-31 or count 5), or a date and time.",
    },
    "scheduled.timing_ph": {
        "ar": "مثال: 5m أو 1h أو daily at 9am أو 2026-12-01T09:00",
        "en": "e.g. 5m, 1h, daily at 9am, or 2026-12-01T09:00",
    },
    "scheduled.x_timing_hint": {
        "ar": "وقت واحد: نسبي (2h) أو تاريخ ووقت. منشورات إكس لا تتكرر.",
        "en": "One time: relative (2h) or a date and time. X posts do not repeat.",
    },
    "scheduled.x_timing_ph": {
        "ar": "مثال: 2h أو 2026-12-01T09:00",
        "en": "e.g. 2h or 2026-12-01T09:00",
    },
    "scheduled.zone_hint": {
        "ar": "الأوقات بلا فرق توقيت تُقرأ بالمنطقة الزمنية: {zone}.",
        "en": "Times without an offset are read in {zone}.",
    },
    "scheduled.single_operator": {
        "ar": "المهام المجدولة تعمل في تثبيت بمشغل واحد. في هذا التثبيت مستخدمون للمنصة، والمهام المجدولة لم تُفصل لكل مستخدم بعد، لذا الجدولة متوقفة هنا.",
        "en": "Scheduled tasks work on a single-operator install. This install has platform users, and scheduled tasks are not separated per user yet, so scheduling is off here.",
    },
    "scheduled.stat_attention": {
        "ar": "تحتاج انتباهك",
        "en": "Needs attention",
    },
    "scheduled.st_attention": {
        "ar": "تحتاج انتباهك",
        "en": "Needs attention",
    },
    "scheduled.st_delivering": {
        "ar": "قيد الإرسال",
        "en": "Sending",
    },
    "scheduled.st_undeliverable": {
        "ar": "لم تُسلَّم",
        "en": "Not delivered",
    },
    "scheduled.st_delivery_unknown": {
        "ar": "الإرسال غير مؤكد",
        "en": "Delivery unknown",
    },
    "scheduled.st_missed": {
        "ar": "فاتت",
        "en": "Missed",
    },
    "scheduled.st_scheduled": {
        "ar": "مجدول",
        "en": "Scheduled",
    },
    "scheduled.st_deferred": {
        "ar": "مؤجل",
        "en": "Deferred",
    },
    "scheduled.st_sending": {
        "ar": "جارٍ النشر",
        "en": "Publishing",
    },
    "scheduled.st_awaiting_approval": {
        "ar": "موقوف للمراجعة",
        "en": "Held for review",
    },
    "scheduled.st_outcome_unknown": {
        "ar": "النتيجة غير معروفة",
        "en": "Outcome unknown",
    },
    "scheduled.st_published": {
        "ar": "نُشر",
        "en": "Published",
    },
    "scheduled.st_failed_permanent": {
        "ar": "فشل",
        "en": "Failed",
    },
    "scheduled.st_expired": {
        "ar": "انتهت صلاحيته",
        "en": "Expired",
    },
    "scheduled.kind_reminder": {
        "ar": "تذكير",
        "en": "Reminder",
    },
    "scheduled.kind_label": {
        "ar": "ماذا يحدث في الموعد",
        "en": "What happens at that time",
    },
    "scheduled.kind_opt_reminder": {
        "ar": "تذكير: يُرسل هذا النص كما هو",
        "en": "Reminder: sends this text as written",
    },
    "scheduled.kind_opt_task": {
        "ar": "مهمة للوكيل: ينفّذها كاظمه ويرسل الجواب",
        "en": "Agent task: Kazma runs it and sends the answer",
    },
    "scheduled.tools_label": {
        "ar": "الأدوات المسموحة",
        "en": "Tools it may use",
    },
    "scheduled.tools_read": {
        "ar": "أدوات القراءة فقط",
        "en": "Read-only tools",
    },
    "scheduled.tools_read_write": {
        "ar": "أدوات القراءة والكتابة",
        "en": "Read and write tools",
    },
    "scheduled.tools_hint": {
        "ar": "لا أحد يراقب عند التشغيل: الأدوات التي تحتاج موافقة لا تُتاح أبدًا.",
        "en": "Nobody is watching when it runs: tools that need an approval are never offered.",
    },
    "scheduled.repeats": {
        "ar": "يتكرر",
        "en": "Repeats",
    },
    "scheduled.destination": {
        "ar": "يُرسل إلى",
        "en": "Sends to",
    },
    "scheduled.failures": {
        "ar": "إخفاقات متتالية",
        "en": "Failures in a row",
    },
    "scheduled.last_delivery": {
        "ar": "آخر إرسال",
        "en": "Last delivery",
    },
    "scheduled.delivery_pending": {
        "ar": "قيد الإرسال",
        "en": "sending",
    },
    "scheduled.delivery_retrying": {
        "ar": "يُعاد المحاولة",
        "en": "retrying",
    },
    "scheduled.delivery_delivered": {
        "ar": "سُلّم",
        "en": "delivered",
    },
    "scheduled.delivery_undeliverable": {
        "ar": "لم يُسلَّم",
        "en": "not delivered",
    },
    "scheduled.delivery_unknown": {
        "ar": "غير معروف",
        "en": "unknown",
    },
    "scheduled.delivery_cancelled": {
        "ar": "أُلغي",
        "en": "cancelled",
    },
    "scheduled.edit_pending": {
        "ar": "المهمة تعمل الآن؛ يُطبَّق التعديل عند انتهاء هذا التشغيل.",
        "en": "The task is running now; the change applies when this run ends.",
    },
    "scheduled.cancel_pending": {
        "ar": "المهمة تعمل الآن؛ تُلغى عند انتهاء هذا التشغيل.",
        "en": "The task is running now; it is cancelled when this run ends.",
    },
    "scheduled.restore": {
        "ar": "شغّل مرة أخرى",
        "en": "Run again",
    },
    "scheduled.restore_when": {
        "ar": "متى يعمل مرة أخرى؟",
        "en": "When should it run again?",
    },
    "scheduled.restored": {
        "ar": "جُدولت المهمة مرة أخرى.",
        "en": "Scheduled again.",
    },
    "scheduled.err_conflict": {
        "ar": "تغيّرت المهمة منذ فتحتها. حدّث الصفحة وراجعها مرة أخرى.",
        "en": "The task changed since you opened it. Refresh and review it again.",
    },
    "scheduled.err_not_editable": {
        "ar": "انتهت المهمة. استخدم «شغّل مرة أخرى» لجدولتها من جديد.",
        "en": "The task has finished. Use Run again to schedule it again.",
    },
    "scheduled.err_not_cancellable": {
        "ar": "انتهت المهمة بالفعل.",
        "en": "The task has already finished.",
    },
    "scheduled.err_not_found": {
        "ar": "لا توجد مهمة مجدولة بهذا المعرّف.",
        "en": "No such scheduled task.",
    },
    "scheduled.err_not_finished": {
        "ar": "ما زالت المهمة قيد العمل؛ عدّلها بدلًا من ذلك.",
        "en": "The task still has work to do; edit it instead.",
    },
    "scheduled.err_scheduled_single_operator": {
        "ar": "الجدولة متوقفة في تثبيت فيه مستخدمون للمنصة.",
        "en": "Scheduling is off on an install with platform users.",
    },
    "scheduled.err_tenant_job_limit": {
        "ar": "وصلت المهام المجدولة إلى حدها. ألغِ بعضها أو ارفع الحد في الإعدادات.",
        "en": "The scheduled jobs reached their limit. Cancel some, or raise the limit in Settings.",
    },
    "scheduled.err_thread_job_limit": {
        "ar": "وصلت مهام هذه المحادثة المجدولة إلى حدها.",
        "en": "This conversation's scheduled jobs reached their limit.",
    },
    "scheduled.err_prompt_too_long": {
        "ar": "نص المهمة أطول من 8000 حرف.",
        "en": "The task prompt is longer than 8000 characters.",
    },
    "scheduled.err_no_delivery_address": {
        "ar": "لا يوجد عنوان للإرسال. اضبط محادثة تيليجرام في الإعدادات.",
        "en": "No delivery address. Set a Telegram chat in Settings.",
    },
    "scheduled.clarify_remind_when": {
        "ar": "متى تريد التذكير؟ في الذاكرة موعد قريب، فاختر الوقت المقصود.",
        "en": "When should the reminder go off? Memory holds a date nearby, so pick the time you mean.",
    },
    "scheduled.clarify_cancel_which": {
        "ar": "أي مهمة مجدولة تريد إلغاءها؟",
        "en": "Which scheduled task should be cancelled?",
    },
    "scheduled.clarify_opt_memory_anchor": {
        "ar": "ذكّرني في {when} (من الموعد المحفوظ)",
        "en": "Remind me at {when} (from the date in memory)",
    },
    "scheduled.clarify_opt_from_now": {
        "ar": "ذكّرني في {when} (محسوبًا من الآن)",
        "en": "Remind me at {when} (counted from now)",
    },
    "scheduled.clarify_opt_cancel": {
        "ar": "لا تجدوله",
        "en": "Don't schedule it",
    },
    "scheduled.clarify_opt_cancel_job": {
        "ar": "ألغِ {job}: {what}",
        "en": "Cancel {job}: {what}",
    },
    "scheduled.clarify_opt_cancel_job_at": {
        "ar": "ألغِ {job} (موعده التالي {when}): {what}",
        "en": "Cancel {job} (next {when}): {what}",
    },
    "scheduled.clarify_opt_keep": {
        "ar": "لا تلغِ شيئًا",
        "en": "Don't cancel anything",
    },
    "scheduled.err_unchanged": {
        "ar": "لم يتغير شيء.",
        "en": "Nothing changed.",
    },
    "scheduled.timing_required": {
        "ar": "الوقت مطلوب.",
        "en": "A time is required.",
    },
    "scheduled.title": {
        "ar": "المهام المجدولة",
        "en": "Scheduled Tasks",
    },
    "scheduled.type": {
        "ar": "النوع",
        "en": "Type",
    },
    "scheduled.tz_change": {
        "ar": "تغيير",
        "en": "Change",
    },
    "scheduled.tz_note": {
        "ar": "الأوقات معروضة بمنطقتك الزمنية ({viewer}). المهام المتكررة تُفسَّر بمنطقة المجدول الزمنية ({cron}). ",
        "en": "Times shown in your timezone ({viewer}). Recurring tasks are interpreted in the scheduler's timezone ({cron}). ",
    },
    "scheduled.updated": {
        "ar": "تم الحفظ.",
        "en": "Saved.",
    },
    "scheduled.when": {
        "ar": "الوقت",
        "en": "When",
    },
    "scheduled.x_activity_empty": {
        "ar": "لا يوجد نشاط إكس مسجل بعد.",
        "en": "No X activity recorded yet.",
    },
    "scheduled.x_activity_failed": {
        "ar": "تعذر تحميل نشاط إكس",
        "en": "Could not load X activity",
    },
    "scheduled.x_activity_hint": {
        "ar": "كل طلب أرسله كازما إلى إكس — منشورات وردود وحذف. اضغط صفًا لقراءة النص.",
        "en": "Every X API call Kazma made — posts, replies, deletions. Click a row to read the tweet.",
    },
    "scheduled.x_booked": {
        "ar": "تمت جدولة منشور إكس.",
        "en": "X post scheduled.",
    },
    "scheduled.x_fire_note": {
        "ar": "يُنشر المنشور فقط أثناء تشغيل خادم كازما.",
        "en": "The post fires only while the Kazma server is running.",
    },
    "scheduled.x_text": {
        "ar": "نص المنشور",
        "en": "Post text",
    },
    "scheduled.x_text_ph": {
        "ar": "التغريدة التي ستنشر كما هي",
        "en": "The exact tweet to publish",
    },
    "scheduled.x_text_required": {
        "ar": "نص المنشور مطلوب.",
        "en": "Post text is required.",
    },
    "scheduled.ui.load_failed": {
        "ar": "فشل تحميل المهام المجدولة",
        "en": "Failed to load scheduled tasks",
    },
    "scheduled.ui.load_failed_error": {
        "ar": "فشل تحميل المهام المجدولة: {error}",
        "en": "Failed to load scheduled tasks: {error}",
    },
    "scheduled.ui.save_failed": {
        "ar": "فشل الحفظ",
        "en": "Save failed",
    },
    "scheduled.ui.save_failed_error": {
        "ar": "فشل الحفظ: {error}",
        "en": "Save failed: {error}",
    },
    "scheduled.ui.delete_failed": {
        "ar": "فشل الحذف",
        "en": "Delete failed",
    },
    "scheduled.ui.delete_failed_error": {
        "ar": "فشل الحذف: {error}",
        "en": "Delete failed: {error}",
    },
    "scheduled.x_st_success": {
        "ar": "نجاح",
        "en": "Success",
    },
    "scheduled.x_st_error": {
        "ar": "خطأ",
        "en": "Error",
    },
}
