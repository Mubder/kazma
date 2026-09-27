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
    "chat.chat_memory": {
        "ar": "الذاكرة…",
        "en": "Memory…",
    },
    # The turn's memory row (restored 2026-09-27): what memory the model was
    # shown, counted by kind, one line per hit.
    "chat.memory_used": {
        "ar": "الذاكرة المستخدمة",
        "en": "Memory used",
    },
    "chat.memory_nothing": {
        "ar": "لا شيء مطابق",
        "en": "nothing matched",
    },
    "chat.memory_kind_fact": {
        "ar": "حقيقة",
        "en": "Fact",
    },
    "chat.memory_kind_turn": {
        "ar": "ذكرى",
        "en": "Memory",
    },
    "chat.memory_kind_weekly": {
        "ar": "ملخص أسبوعي",
        "en": "Weekly summary",
    },
    "chat.memory_kind_knowledge": {
        "ar": "المكتبة",
        "en": "Library",
    },
    # Keys chat.js used with no catalog entry, so they showed in English in
    # every language (found 2026-09-27 by tests/test_chat_i18n_bridge.py).
    "chat.copy_id": {
        "ar": "نسخ المعرّف",
        "en": "Copy ID",
    },
    "chat.no_response": {
        "ar": "لا يوجد رد",
        "en": "No response",
    },
    "chat.no_signal_for": {
        "ar": "لا إشارة منذ {t}",
        "en": "No signal for {t}",
    },
    "chat.planning": {
        "ar": "يخطط…",
        "en": "Planning…",
    },
    "chat.reconnecting": {
        "ar": "يعيد الاتصال…",
        "en": "Reconnecting…",
    },
    "chat.sending_decision": {
        "ar": "يرسل القرار…",
        "en": "Sending decision…",
    },
    "chat.still_working_bg": {
        "ar": "ما زال يعمل في الخلفية…",
        "en": "Still working in background…",
    },
    "chat.synthesizing": {
        "ar": "يكتب الرد…",
        "en": "Composing response…",
    },
    "chat.thoughts": {
        "ar": "الأفكار",
        "en": "Thoughts",
    },
    "chat.waiting_server": {
        "ar": "بانتظار الخادم…",
        "en": "Waiting for the server…",
    },
    "chat.delete_chat_title": {
        "ar": "حذف المحادثة",
        "en": "Delete chat",
    },
    "chat.delete_chat_body": {
        "ar": "حذف هذه المحادثة؟ لا يمكن التراجع عن ذلك. ما تعلّمه Kazma منها يبقى في ذاكرته إلا إذا اخترت المربع أدناه.",
        "en": "Delete this chat? This cannot be undone. What Kazma learned from it stays in its memory unless you tick the box below.",
    },
    "chat.delete_chat_confirm": {
        "ar": "حذف",
        "en": "Delete",
    },
    "chat.delete_chat_forget": {
        "ar": "انسَ أيضًا ما تعلّمه Kazma من هذه المحادثة",
        "en": "Also forget what Kazma learned from this chat",
    },
    "chat.delete_chat_done": {
        "ar": "حُذفت المحادثة",
        "en": "Chat deleted",
    },
    "chat.delete_chat_forgot": {
        "ar": "حُذفت المحادثة ونُسي ما تعلّمه Kazma منها",
        "en": "Chat deleted, and what Kazma learned from it forgotten",
    },
    "chat.delete_chat_failed": {
        "ar": "تعذّر الحذف",
        "en": "Delete failed",
    },
    "chat.memory_off_title": {
        "ar": "ألا يتذكر Kazma هذه المحادثة؟",
        "en": "Don't remember this chat?",
    },
    "chat.memory_off_body": {
        "ar": "يتوقف Kazma عن تذكر الرسائل الجديدة في هذه المحادثة، وينسى ما يتذكره منها الآن. المحادثة نفسها تبقى كما هي.",
        "en": "Kazma stops remembering new messages in this chat, and forgets what it already remembers from it. The chat itself stays as it is.",
    },
    "chat.memory_off_confirm": {
        "ar": "لا تتذكر",
        "en": "Don't remember",
    },
    "chat.memory_off_done": {
        "ar": "لن يتذكر Kazma هذه المحادثة",
        "en": "Kazma won't remember this chat",
    },
    "chat.memory_on_title": {
        "ar": "يتذكر Kazma هذه المحادثة من جديد؟",
        "en": "Remember this chat again?",
    },
    "chat.memory_on_body": {
        "ar": "يتذكر Kazma الرسائل الجديدة في هذه المحادثة من جديد. ما نسيه يبقى منسيًا.",
        "en": "Kazma remembers new messages in this chat again. What it forgot stays forgotten.",
    },
    "chat.memory_on_confirm": {
        "ar": "تذكر",
        "en": "Remember",
    },
    "chat.memory_on_done": {
        "ar": "يتذكر Kazma هذه المحادثة من جديد",
        "en": "Kazma remembers this chat again",
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
    # Count labels: every form, picked by chat.js tiCount with t_plural's
    # CLDR rule (i18n.plural_forms). One way to print a count -- the
    # hand-built ones printed "1 approvals", "3 3 tools" and, through the
    # wrong key, "1 step" for one tool (2026-09-24).
    "chat.count_tools.zero": {"ar": "لا أدوات", "en": "{n} tools"},
    "chat.count_tools.one": {"ar": "أداة واحدة", "en": "{n} tool"},
    "chat.count_tools.two": {"ar": "أداتان", "en": "{n} tools"},
    "chat.count_tools.few": {"ar": "{n} أدوات", "en": "{n} tools"},
    "chat.count_tools.many": {"ar": "{n} أداةً", "en": "{n} tools"},
    "chat.count_tools.other": {"ar": "{n} أداة", "en": "{n} tools"},
    "chat.count_steps.zero": {"ar": "لا خطوات", "en": "{n} steps"},
    "chat.count_steps.one": {"ar": "خطوة واحدة", "en": "{n} step"},
    "chat.count_steps.two": {"ar": "خطوتان", "en": "{n} steps"},
    "chat.count_steps.few": {"ar": "{n} خطوات", "en": "{n} steps"},
    "chat.count_steps.many": {"ar": "{n} خطوةً", "en": "{n} steps"},
    "chat.count_steps.other": {"ar": "{n} خطوة", "en": "{n} steps"},
    "chat.count_approvals.zero": {"ar": "لا موافقات", "en": "{n} approvals"},
    "chat.count_approvals.one": {"ar": "موافقة واحدة", "en": "{n} approval"},
    "chat.count_approvals.two": {"ar": "موافقتان", "en": "{n} approvals"},
    "chat.count_approvals.few": {"ar": "{n} موافقات", "en": "{n} approvals"},
    "chat.count_approvals.many": {"ar": "{n} موافقةً", "en": "{n} approvals"},
    "chat.count_approvals.other": {"ar": "{n} موافقة", "en": "{n} approvals"},
    "chat.count_requests.zero": {"ar": "لا طلبات", "en": "{n} requests"},
    "chat.count_requests.one": {"ar": "طلب واحد", "en": "{n} request"},
    "chat.count_requests.two": {"ar": "طلبان", "en": "{n} requests"},
    "chat.count_requests.few": {"ar": "{n} طلبات", "en": "{n} requests"},
    "chat.count_requests.many": {"ar": "{n} طلبًا", "en": "{n} requests"},
    "chat.count_requests.other": {"ar": "{n} طلب", "en": "{n} requests"},
    # The turn's memory row: what the model was shown, by kind.
    "chat.count_facts.zero": {"ar": "لا حقائق", "en": "{n} facts"},
    "chat.count_facts.one": {"ar": "حقيقة واحدة", "en": "{n} fact"},
    "chat.count_facts.two": {"ar": "حقيقتان", "en": "{n} facts"},
    "chat.count_facts.few": {"ar": "{n} حقائق", "en": "{n} facts"},
    "chat.count_facts.many": {"ar": "{n} حقيقةً", "en": "{n} facts"},
    "chat.count_facts.other": {"ar": "{n} حقيقة", "en": "{n} facts"},
    "chat.count_memories.zero": {"ar": "لا ذكريات", "en": "{n} memories"},
    "chat.count_memories.one": {"ar": "ذكرى واحدة", "en": "{n} memory"},
    "chat.count_memories.two": {"ar": "ذكريان", "en": "{n} memories"},
    "chat.count_memories.few": {"ar": "{n} ذكريات", "en": "{n} memories"},
    "chat.count_memories.many": {"ar": "{n} ذكرى", "en": "{n} memories"},
    "chat.count_memories.other": {"ar": "{n} ذكرى", "en": "{n} memories"},
    "chat.count_weekly.zero": {"ar": "لا ملخصات أسبوعية", "en": "{n} weekly summaries"},
    "chat.count_weekly.one": {"ar": "ملخص أسبوعي واحد", "en": "{n} weekly summary"},
    "chat.count_weekly.two": {"ar": "ملخصان أسبوعيان", "en": "{n} weekly summaries"},
    "chat.count_weekly.few": {"ar": "{n} ملخصات أسبوعية", "en": "{n} weekly summaries"},
    "chat.count_weekly.many": {"ar": "{n} ملخصًا أسبوعيًا", "en": "{n} weekly summaries"},
    "chat.count_weekly.other": {"ar": "{n} ملخص أسبوعي", "en": "{n} weekly summaries"},
    "chat.count_passages.zero": {"ar": "لا مقاطع من المكتبة", "en": "{n} library passages"},
    "chat.count_passages.one": {"ar": "مقطع واحد من المكتبة", "en": "{n} library passage"},
    "chat.count_passages.two": {"ar": "مقطعان من المكتبة", "en": "{n} library passages"},
    "chat.count_passages.few": {"ar": "{n} مقاطع من المكتبة", "en": "{n} library passages"},
    "chat.count_passages.many": {"ar": "{n} مقطعًا من المكتبة", "en": "{n} library passages"},
    "chat.count_passages.other": {"ar": "{n} مقطع من المكتبة", "en": "{n} library passages"},
    "chat.awaiting_decisions": {
        "ar": "{n} بانتظار قرارك",
        "en": "{n} awaiting your decision",
    },
    "chat.approvals_title": {
        "ar": "الموافقات",
        "en": "Approvals",
    },
    "chat.thinking": {
        "ar": "كاظمه تفكر…",
        "en": "Kazma is thinking…",
    },
    "chat.thinking_queue": {
        "ar": "كاظمه تفكر… اكتب لترتيب رسالتك التالية",
        "en": "Kazma is thinking… type to queue your next message",
    },
    "chat.approval_expired_short": {
        "ar": "انتهت المهلة",
        "en": "expired",
    },
    "chat.auto_deny_seconds": {
        "ar": "رفض تلقائي خلال {n} ثانية",
        "en": "auto-denies in {n} seconds",
    },
    "chat.approval_expired": {
        "ar": "انتهت مهلة الموافقة — سنكمل بدون هذه الأداة.",
        "en": "Approval timed out — continuing without this tool.",
    },
    "chat.auto_deny_in": {
        "ar": "رفض تلقائي إن لم يُرد خلال",
        "en": "Auto-denies if unanswered in",
    },
    "chat.task_auto_deny_in": {
        "ar": "رفض تلقائي خلال",
        "en": "auto-denies in",
    },
    "chat.task_awaiting": {
        "ar": "بانتظار موافقتك",
        "en": "Awaiting your approval",
    },
    "chat.task_checking": {
        "ar": "جارٍ التحقق…",
        "en": "checking…",
    },
    "chat.task_details": {
        "ar": "تفاصيل المهمة",
        "en": "Task details",
    },
    "chat.task_done": {
        "ar": "تم",
        "en": "Done",
    },
    "chat.task_error": {
        "ar": "فشلت الجولة",
        "en": "Turn failed",
    },
    "chat.task_in_tool": {
        "ar": "{d} في هذه الأداة",
        "en": "{d} in this tool",
    },
    "chat.task_no_reply": {
        "ar": "لم يصل أي رد",
        "en": "No reply received",
    },
    "chat.task_no_signal": {
        "ar": "لا توجد إشارة",
        "en": "no signal",
    },
    "chat.task_not_responding": {
        "ar": "لا يستجيب",
        "en": "not responding",
    },
    "chat.task_plan": {
        "ar": "خطة",
        "en": "plan",
    },
    "chat.task_resuming": {
        "ar": "استئناف بعد الموافقة",
        "en": "Resuming after approval",
    },
    "chat.task_retry": {
        "ar": "إعادة المحاولة",
        "en": "Retry",
    },
    "chat.task_review": {
        "ar": "مراجعة ↑",
        "en": "Review ↑",
    },
    "chat.task_running_tool": {
        "ar": "تشغيل",
        "en": "Running",
    },
    "chat.task_step": {
        "ar": "خطوة",
        "en": "step",
    },
    "chat.task_thinking": {
        "ar": "تفكير",
        "en": "Thinking",
    },
    "chat.task_tool": {
        "ar": "أداة",
        "en": "tool",
    },
    "chat.task_tools": {
        "ar": "أدوات",
        "en": "tools",
    },
    "chat.task_writing": {
        "ar": "كتابة الرد…",
        "en": "Writing the reply…",
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
    "chat.setup_title": {
        "ar": "ابدأ المحادثة",
        "en": "Start chatting",
    },
    "chat.setup_subtitle": {
        "ar": "مفتاح واحد ونموذج واحد. يمكنك تغييرهما لاحقاً من الإعدادات.",
        "en": "One key and one model. You can change them later in Settings.",
    },
    "chat.setup_provider": {
        "ar": "المزوّد",
        "en": "Provider",
    },
    "chat.setup_api_key": {
        "ar": "مفتاح API",
        "en": "API key",
    },
    "chat.setup_model": {
        "ar": "النموذج",
        "en": "Model",
    },
    "chat.setup_start": {
        "ar": "ابدأ",
        "en": "Start",
    },
    "chat.setup_settings_hint": {
        "ar": "المحليون (Ollama / LM Studio) لا يحتاجون مفتاحاً. الإعدادات تبقى متاحة.",
        "en": "Local (Ollama / LM Studio) needs no key. Settings stays available.",
    },
    "chat.setup_key_placeholder": {
        "ar": "اختياري للمحلي",
        "en": "Optional for local",
    },
}
