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
        "ar": "نسبي (5m، 1h) أو متكرر (daily at 9am) أو طابع زمني ISO.",
        "en": "Relative (5m, 1h), recurring (daily at 9am), or an ISO timestamp.",
    },
    "scheduled.timing_ph": {
        "ar": "مثال: 5m أو 1h أو daily at 9am أو 2026-12-01T09:00",
        "en": "e.g. 5m, 1h, daily at 9am, or 2026-12-01T09:00",
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
        "ar": "كل طلب أرسله كازما إلى إكس — منشورات وردود وعمليات حذف — مع النص المُرسَل وردّ إكس. اضغط على أي صف للتفاصيل.",
        "en": "Every X API call Kazma made — posts, replies, deletions — with the exact content sent and what X returned. Click a row for detail.",
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
}
