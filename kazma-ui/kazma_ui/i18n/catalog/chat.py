"""``chat`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "chat.actions": {
        "ar": "إجراءات",
        "en": "Actions",
    },
    "chat.activity": {
        "ar": "النشاط",
        "en": "Activity",
    },
    "chat.approval_complete": {
        "ar": "اكتملت الموافقة بنجاح!",
        "en": "Approval completed successfully!",
    },
    "chat.approved": {
        "ar": "تمت الموافقة ✓",
        "en": "Approved ✓",
    },
    "chat.archive": {
        "ar": "أرشفة",
        "en": "Archive",
    },
    "chat.archived": {
        "ar": "المؤرشفة",
        "en": "Archived",
    },
    "chat.archived_msg": {
        "ar": "تمت أرشفة الجلسة",
        "en": "Session archived",
    },
    "chat.attach_file": {
        "ar": "إرفاق ملف",
        "en": "Attach file",
    },
    "chat.attached": {
        "ar": "مرفق",
        "en": "Attached",
    },
    "chat.beliefs": {
        "ar": "معتقدات",
        "en": "beliefs",
    },
    "chat.composer_chars": {
        "ar": "عدد الأحرف",
        "en": "Characters typed",
    },
    "chat.context_size": {
        "ar": "{chars} حرفًا ≈ {tokens} رمزًا",
        "en": "{chars} chars ≈ {tokens} tokens",
    },
    "chat.context_size_hint": {
        "ar": "تقدير حجم سياق المحادثة",
        "en": "Conversation context estimate",
    },
    "chat.continuing_after_deny": {
        "ar": "جارٍ المتابعة بعد الرفض…",
        "en": "Continuing after deny…",
    },
    "chat.cot_title": {
        "ar": "التفكير والنشاط",
        "en": "Thinking & Activity",
    },
    "chat.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "chat.delete_title": {
        "ar": "حذف الجلسة",
        "en": "Delete session",
    },
    "chat.deleted_msg": {
        "ar": "تم حذف الجلسة",
        "en": "Session deleted",
    },
    "chat.denied": {
        "ar": "تم الرفض ✗",
        "en": "Denied ✗",
    },
    "chat.denying_tool": {
        "ar": "جارٍ رفض الأداة…",
        "en": "Denying tool…",
    },
    "chat.done": {
        "ar": "تم",
        "en": "Done",
    },
    "chat.drop_files": {
        "ar": "أفلت الملفات للإرفاق",
        "en": "Drop files to attach",
    },
    "chat.episodes": {
        "ar": "حلقات",
        "en": "episodes",
    },
    "chat.error": {
        "ar": "خطأ",
        "en": "Error",
    },
    "chat.executing_approved": {
        "ar": "جارٍ تنفيذ الإجراء الموافق عليه…",
        "en": "Executing approved action…",
    },
    "chat.loading_sessions": {
        "ar": "جاري تحميل الجلسات…",
        "en": "Loading sessions…",
    },
    "chat.memory_context": {
        "ar": "سياق الذاكرة",
        "en": "Memory context",
    },
    "chat.messages_count.few": {
        "ar": "{n} رسائل",
        "en": "{n} messages",
    },
    "chat.messages_count.many": {
        "ar": "{n} رسالةً",
        "en": "{n} messages",
    },
    "chat.messages_count.one": {
        "ar": "رسالة واحدة",
        "en": "1 message",
    },
    "chat.messages_count.other": {
        "ar": "{n} رسالة",
        "en": "{n} messages",
    },
    "chat.messages_count.two": {
        "ar": "رسالتان",
        "en": "2 messages",
    },
    "chat.messages_count.zero": {
        "ar": "لا توجد رسائل",
        "en": "no messages",
    },
    "chat.model": {
        "ar": "النموذج",
        "en": "Model",
    },
    "chat.new": {
        "ar": "جديد",
        "en": "New",
    },
    "chat.new_session": {
        "ar": "+ جديد",
        "en": "+ New",
    },
    "chat.newline_shortcut": {
        "ar": "سطر جديد",
        "en": "newline",
    },
    "chat.no_matching_sessions": {
        "ar": "لا توجد جلسات مطابقة",
        "en": "No matching sessions",
    },
    "chat.no_sessions_yet": {
        "ar": "لا توجد جلسات بعد",
        "en": "No sessions yet",
    },
    "chat.older": {
        "ar": "أقدم",
        "en": "Older",
    },
    "chat.phase_act": {
        "ar": "نفّذ",
        "en": "Act",
    },
    "chat.phase_think": {
        "ar": "فكر",
        "en": "Think",
    },
    "chat.phase_write": {
        "ar": "اكتب",
        "en": "Write",
    },
    "chat.pin": {
        "ar": "تثبيت",
        "en": "Pin",
    },
    "chat.pinned": {
        "ar": "مثبتة",
        "en": "Pinned",
    },
    "chat.placeholder": {
        "ar": "اكتب رسالة أو /yolo … (Enter للإرسال، / للأوامر)",
        "en": "Type a message or /yolo … (Enter to send, / for commands)",
    },
    "chat.plan": {
        "ar": "الخطة",
        "en": "Plan",
    },
    "chat.plan_locked": {
        "ar": "تم قفل الخطة ({n} خطوات)",
        "en": "Plan locked ({n} steps)",
    },
    "chat.plan_progress": {
        "ar": "الخطة {done}/{total}",
        "en": "plan {done}/{total}",
    },
    "chat.preparing_n_tools": {
        "ar": "جارٍ التحضير لتنفيذ {n} أدوات…",
        "en": "Preparing to execute {n} tools…",
    },
    "chat.preparing_tool": {
        "ar": "جارٍ التحضير لتنفيذ {tool}…",
        "en": "Preparing to execute {tool}…",
    },
    "chat.previous_7_days": {
        "ar": "آخر 7 أيام",
        "en": "Previous 7 days",
    },
    "chat.processing_approval": {
        "ar": "جارٍ معالجة الموافقة…",
        "en": "Processing approval…",
    },
    "chat.reasoning": {
        "ar": "الاستدلال",
        "en": "Reasoning",
    },
    "chat.remove_attachment": {
        "ar": "إزالة المرفق",
        "en": "Remove attachment",
    },
    "chat.rename": {
        "ar": "إعادة تسمية",
        "en": "Rename",
    },
    "chat.rename_title": {
        "ar": "إعادة تسمية الجلسة",
        "en": "Rename session",
    },
    "chat.renamed_msg": {
        "ar": "تمت إعادة تسمية الجلسة",
        "en": "Session renamed",
    },
    "chat.restore": {
        "ar": "استرجاع",
        "en": "Restore",
    },
    "chat.restored_msg": {
        "ar": "تم استرجاع الجلسة",
        "en": "Session restored",
    },
    "chat.resuming_execution": {
        "ar": "جارٍ استئناف التنفيذ…",
        "en": "Resuming execution…",
    },
    "chat.resuming_graph": {
        "ar": "جارٍ استئناف تنفيذ المخطط…",
        "en": "Resuming graph execution…",
    },
    "chat.routing": {
        "ar": "التوجيه: {node}",
        "en": "Routing: {node}",
    },
    "chat.routing_arrow": {
        "ar": "التوجيه ← {node}",
        "en": "Routing → {node}",
    },
    "chat.running": {
        "ar": "جارٍ التنفيذ…",
        "en": "Running…",
    },
    "chat.running_after_approval": {
        "ar": "جارٍ التشغيل بعد موافقة {scope}…",
        "en": "Running after {scope} approval…",
    },
    "chat.running_tool": {
        "ar": "جارٍ تشغيل {tool}…",
        "en": "Running {tool}…",
    },
    "chat.search": {
        "ar": "بحث",
        "en": "Search",
    },
    "chat.search_sessions": {
        "ar": "ابحث في الجلسات…",
        "en": "Search sessions…",
    },
    "chat.send": {
        "ar": "إرسال",
        "en": "Send",
    },
    "chat.send_shortcut": {
        "ar": "إرسال",
        "en": "send",
    },
    "chat.session_title": {
        "ar": "عنوان الجلسة",
        "en": "Session title",
    },
    "chat.sessions": {
        "ar": "الجلسات",
        "en": "Sessions",
    },
    "chat.show_less": {
        "ar": "عرض أقل ▴",
        "en": "Show less ▴",
    },
    "chat.show_more": {
        "ar": "عرض المزيد ▾",
        "en": "Show more ▾",
    },
    "chat.start_new_chat": {
        "ar": "ابدأ محادثة جديدة",
        "en": "Start a new chat",
    },
    "chat.step": {
        "ar": "خطوة",
        "en": "step",
    },
    "chat.step_done": {
        "ar": "تم",
        "en": "Done",
    },
    "chat.step_failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "chat.steps": {
        "ar": "خطوات",
        "en": "steps",
    },
    "chat.still_working_approval": {
        "ar": "ما زال يعمل بعد الموافقة ({s}ث)…",
        "en": "Still working after approval ({s}s)…",
    },
    "chat.still_working_sec": {
        "ar": "ما زال يعمل… ({s}ث)",
        "en": "Still working… ({s}s)",
    },
    "chat.stop_generation": {
        "ar": "إيقاف التوليد",
        "en": "Stop generation",
    },
    "chat.stopped": {
        "ar": "توقف",
        "en": "Stopped",
    },
    "chat.summary_tools": {
        "ar": "{n} أدوات",
        "en": "{n} tools",
    },
    "chat.thinking": {
        "ar": "كاظمه تفكر…",
        "en": "Kazma is thinking…",
    },
    "chat.thinking_queue": {
        "ar": "كاظمه تفكر… اكتب لترتيب رسالتك التالية",
        "en": "Kazma is thinking… type to queue your next message",
    },
    "chat.title": {
        "ar": "المحادثة",
        "en": "Chat",
    },
    "chat.today": {
        "ar": "اليوم",
        "en": "Today",
    },
    "chat.tokens": {
        "ar": "رمز",
        "en": "tokens",
    },
    "chat.tool_allowed": {
        "ar": "تم السماح بالأداة ✓",
        "en": "Tool allowed ✓",
    },
    "chat.type_message": {
        "ar": "اكتب رسالتك… (Enter للإرسال)",
        "en": "Type your message… (Enter to send)",
    },
    "chat.unpin": {
        "ar": "إلغاء التثبيت",
        "en": "Unpin",
    },
    "chat.uploading": {
        "ar": "جارٍ الرفع…",
        "en": "Uploading…",
    },
    "chat.waiting_approval": {
        "ar": "بانتظار الموافقة",
        "en": "Waiting for approval",
    },
    "chat.welcome_subtitle": {
        "ar": "كيف يمكنني مساعدتك اليوم؟",
        "en": "How can I help you today?",
    },
    "chat.welcome_title": {
        "ar": "كاظمه",
        "en": "Kazma",
    },
    "chat.working": {
        "ar": "جارٍ العمل…",
        "en": "Working…",
    },
    "chat.writing_reply": {
        "ar": "جارٍ كتابة الرد…",
        "en": "Writing reply…",
    },
    "chat.yesterday": {
        "ar": "أمس",
        "en": "Yesterday",
    },
    "chat.yolo_on": {
        "ar": "YOLO مفعّل ✓",
        "en": "YOLO on ✓",
    },
    "chat.yolo_running": {
        "ar": "YOLO مفعّل — جارٍ التشغيل…",
        "en": "YOLO on — running…",
    },
}
