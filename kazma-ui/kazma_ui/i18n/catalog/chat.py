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
    # A step's tool, as the chat's steps name it (chat.js _TOOL_FRIENDLY).
    "chat.tool_search": {"ar": "بحث", "en": "Search"},
    "chat.tool_read_page": {"ar": "قراءة صفحة", "en": "Read page"},
    "chat.tool_save_page": {"ar": "حفظ صفحة", "en": "Save page"},
    "chat.tool_crawl_site": {"ar": "تصفّح موقع", "en": "Crawl site"},
    "chat.tool_crawl_page": {"ar": "تصفّح صفحة", "en": "Crawl page"},
    "chat.tool_kb_ingest": {"ar": "إضافة إلى المكتبة", "en": "KB ingest"},
    "chat.tool_kb_crawl": {"ar": "تصفّح إلى المكتبة", "en": "KB crawl"},
    "chat.tool_kb_search": {"ar": "بحث في المكتبة", "en": "KB search"},
    "chat.tool_kb_create": {"ar": "إنشاء مكتبة", "en": "KB create"},
    "chat.tool_kb_list": {"ar": "المكتبات", "en": "KB list"},
    "chat.tool_read_file": {"ar": "قراءة ملف", "en": "Read file"},
    "chat.tool_write_file": {"ar": "كتابة ملف", "en": "Write file"},
    "chat.tool_delete_file": {"ar": "حذف ملف", "en": "Delete file"},
    "chat.tool_list_files": {"ar": "عرض الملفات", "en": "List files"},
    "chat.tool_find_files": {"ar": "البحث عن ملفات", "en": "Find files"},
    "chat.tool_shell": {"ar": "الطرفية", "en": "Shell"},
    "chat.tool_code": {"ar": "شيفرة", "en": "Code"},
    "chat.tool_python": {"ar": "Python", "en": "Python"},
    "chat.tool_digest": {"ar": "استخلاص", "en": "Digest"},
    "chat.tool_chunks": {"ar": "المقاطع", "en": "Chunks"},
    "chat.tool_chunk": {"ar": "مقطع", "en": "Chunk"},
    "chat.tool_summarize": {"ar": "تلخيص", "en": "Summarize"},
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
    "chat.no_archived_sessions": {
        "ar": "لا توجد جلسات مؤرشفة",
        "en": "No archived sessions",
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
    "chat.sessions_load_failed": {
        "ar": "تعذّر تحميل الجلسات",
        "en": "Failed to load sessions",
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
    "chat.notify_done_title": {
        "ar": "Kazma — اكتملت المهمة",
        "en": "Kazma — task finished",
    },
    "chat.notify_done_body": {
        "ar": "اكتملت مهمتك.",
        "en": "Your task completed.",
    },
    "chat.placeholder_paused": {
        "ar": "وافق أعلاه — أو /steer /abort /long /yolo",
        "en": "Approve above — or /steer /abort /long /yolo",
    },
    "chat.send_title": {
        "ar": "إرسال (Enter / Ctrl+Enter)",
        "en": "Send (Enter / Ctrl+Enter)",
    },
    "chat.send_steer": {
        "ar": "إرسال توجيه أو أمر",
        "en": "Send steer or command",
    },
    "chat.build_badge": {
        "ar": "الإصدار {commit}",
        "en": "build {commit}",
    },
    "chat.build_since": {
        "ar": "يعمل منذ {time}",
        "en": "up since {time}",
    },
    "chat.build_badge_hint": {
        "ar": "إصدار الخادم العامل (الإيداع ووقت التشغيل) — تحقّق من أن إعادة التشغيل التقطت آخر تحديث",
        "en": "Running server build (commit + start time) — verify your restart picked up the latest pull",
    },
    "chat.session_empty": {
        "ar": "لا توجد رسائل في هذه الجلسة بعد.",
        "en": "No messages in this session yet.",
    },
    "chat.mode_chat": {
        "ar": "محادثة",
        "en": "Chat",
    },
    "chat.mode_long": {
        "ar": "مطوّل",
        "en": "Long",
    },
    "chat.mode_mission": {
        "ar": "مهمة",
        "en": "Mission",
    },
    "chat.hitl_on": {
        "ar": "الموافقات مفعّلة",
        "en": "HITL on",
    },
    "chat.tok_unit": {
        "ar": "رمز",
        "en": "tok",
    },
    "chat.ctx_unit": {
        "ar": "سياق",
        "en": "ctx",
    },
    "chat.skip_approvals_session": {
        "ar": "تخطَّ موافقات الأدوات الخطرة في هذه الجلسة",
        "en": "Skip danger-tool approvals for this session",
    },
    "chat.cap_unrestricted": {
        "ar": "بلا قيود",
        "en": "Unrestricted",
    },
    "chat.cap_unrestricted_hint": {
        "ar": "مهمة + YOLO — أنجز هذا العمل دون أن تسأل",
        "en": "Mission + YOLO — finish this job without asking",
    },
    "chat.cap_unrestricted_short_hint": {
        "ar": "مهمة + YOLO — أنجز هذا العمل",
        "en": "Mission + YOLO — finish this job",
    },
    "chat.help_chip": {
        "ar": "مساعدة",
        "en": "help",
    },
    "chat.loading_models": {
        "ar": "— جارٍ تحميل النماذج —",
        "en": "— loading models —",
    },
    "chat.ws_status": {
        "ar": "الاتصال",
        "en": "Connection",
    },
    "chat.ws_connected": {
        "ar": "متصل",
        "en": "connected",
    },
    "chat.ws_connecting": {
        "ar": "جارٍ الاتصال",
        "en": "connecting",
    },
    "chat.ws_disconnected": {
        "ar": "غير متصل",
        "en": "disconnected",
    },
    "chat.record_voice": {
        "ar": "تسجيل صوتي",
        "en": "Record voice",
    },
    "chat.record_voice_hint": {
        "ar": "تسجيل صوتي (اضغط مطوّلًا للتسجيل)",
        "en": "Record voice (Hold to record)",
    },
    "chat.live_voice": {
        "ar": "محادثة صوتية مباشرة",
        "en": "Live duplex voice",
    },
    "chat.live_voice_hint": {
        "ar": "محادثة صوتية مباشرة (قاطع متى شئت)",
        "en": "Live duplex voice (interrupt anytime)",
    },
    "chat.cap_toolbar": {
        "ar": "ميزانية الدور والموافقات",
        "en": "Turn budget and HITL",
    },
    "chat.cap_status_hint": {
        "ar": "ميزانية الدور الحالية",
        "en": "Current turn budget",
    },
    "chat.cap_budget": {
        "ar": "الميزانية",
        "en": "Budget",
    },
    "chat.cap_long_hint": {
        "ar": "ميزانية بحث أكبر، وتبقى الموافقات مفعّلة",
        "en": "Research budget, HITL stays on",
    },
    "chat.cap_mission_hint": {
        "ar": "يعمل حتى ينتهي (نحو 500 جولة)",
        "en": "Run until done (~500 rounds)",
    },
    "chat.cap_plan_hint": {
        "ar": "افحص واقترح — الكتابة والتنفيذ ممنوعان حتى /plan go",
        "en": "Inspect and propose — write/exec blocked until /plan go",
    },
    "chat.cap_approvals": {
        "ar": "الموافقات",
        "en": "Approvals",
    },
    "chat.cap_yolo_hint": {
        "ar": "تخطَّ موافقات الأدوات الخطرة",
        "en": "Skip danger-tool approvals",
    },
    "chat.cap_reset": {
        "ar": "إعادة الضبط",
        "en": "Reset",
    },
    "chat.cap_reset_hint": {
        "ar": "استعادة الميزانية الأساسية والموافقات",
        "en": "Restore baseline budget and HITL",
    },
    "chat.session_usage": {
        "ar": "استهلاك الجلسة",
        "en": "Session usage",
    },
    "chat.session_heading": {
        "ar": "الجلسة {id}",
        "en": "Session {id}",
    },
    "chat.yolo_active": {
        "ar": "YOLO مفعّل",
        "en": "YOLO on",
    },
    "chat.generation_stopped": {
        "ar": "توقف التوليد",
        "en": "Generation stopped",
    },
    "chat.file_read_failed": {
        "ar": "تعذّرت قراءة {name}",
        "en": "Failed to read {name}",
    },
    "chat.file_too_large": {
        "ar": "الملف كبير جدًا (الحد 20MB): {name}",
        "en": "File too large (max 20MB): {name}",
    },
    "chat.upload_failed": {
        "ar": "فشل الرفع: {error}",
        "en": "Upload failed: {error}",
    },
    "chat.provider_unknown": {
        "ar": "غير معروف",
        "en": "Unknown",
    },
    "chat.models_active": {
        "ar": "النشط",
        "en": "Active",
    },
    "chat.model_switch_failed": {
        "ar": "فشل طلب تبديل النموذج",
        "en": "Model switch request failed",
    },
    "chat.aborting_task": {
        "ar": "⛔ جارٍ إيقاف المهمة…",
        "en": "⛔ Aborting task…",
    },
    "chat.no_task_to_steer": {
        "ar": "لا مهمة نشطة لتوجيهها.",
        "en": "No active task to steer.",
    },
    "chat.steer_failed": {
        "ar": "فشل التوجيه: {reason}",
        "en": "Steer failed: {reason}",
    },
    "chat.no_reply_title": {
        "ar": "انتهى الدور بلا رد — راجع منطقة الرسائل أو window.KazmaChat.diagnostics()",
        "en": "Turn ended without a reply — see the message area or window.KazmaChat.diagnostics()",
    },
    "chat.yolo_session_on": {
        "ar": "YOLO مفعّل لهذه الجلسة — تُعتمد الأدوات الخطرة تلقائيًا",
        "en": "YOLO on for this session — danger tools auto-approved",
    },
    "chat.tool_allowed_session": {
        "ar": "سُمح بـ {tool} لهذه الجلسة (~30 دقيقة)",
        "en": "Allowed {tool} for this session (~30m)",
    },
    "chat.edit_resend_hint": {
        "ar": "عدّل رسالتك واضغط Enter لإعادة الإرسال",
        "en": "Edit your message and press Enter to resend",
    },
    "chat.copied": {
        "ar": "نُسخ إلى الحافظة",
        "en": "Copied to clipboard",
    },
    "chat.nothing_to_read": {
        "ar": "لا شيء يُقرأ في هذه الرسالة",
        "en": "Nothing to read in this message",
    },
    "chat.memory_setting_failed": {
        "ar": "فشل ضبط الذاكرة",
        "en": "Memory setting failed",
    },
    "chat.pin_failed": {
        "ar": "فشل التثبيت",
        "en": "Pin failed",
    },
    "chat.session_archived": {
        "ar": "أُرشفت الجلسة",
        "en": "Session archived",
    },
    "chat.archive_failed": {
        "ar": "فشلت الأرشفة",
        "en": "Archive failed",
    },
    "chat.session_restored": {
        "ar": "استُرجعت الجلسة",
        "en": "Session restored",
    },
    "chat.restore_failed": {
        "ar": "فشل الاسترجاع",
        "en": "Restore failed",
    },
    "chat.copied_session_id": {
        "ar": "نُسخ المعرّف — /session {id} في Telegram/Discord",
        "en": "Copied ID — /session {id} on Telegram/Discord",
    },
    "chat.rename_session": {
        "ar": "إعادة تسمية الجلسة",
        "en": "Rename session",
    },
    "chat.session_title_label": {
        "ar": "عنوان الجلسة",
        "en": "Session title",
    },
    "chat.session_renamed": {
        "ar": "أُعيدت تسمية الجلسة",
        "en": "Session renamed",
    },
    "chat.rename_failed": {
        "ar": "فشلت إعادة التسمية",
        "en": "Rename failed",
    },
    "chat.wait_or_abort": {
        "ar": "انتظر حتى ينتهي التوليد أو أوقفه أولًا",
        "en": "Please wait for generation to finish or abort first",
    },
    "chat.forget_failed": {
        "ar": "فشل النسيان",
        "en": "Forget failed",
    },
    "chat.context_compacted": {
        "ar": "ضُغط السياق السابق",
        "en": "Earlier context compacted",
    },
    "chat.untitled_chat": {
        "ar": "محادثة جديدة",
        "en": "New chat",
    },
    "chat.context_was_compacted": {
        "ar": "ضُغط السياق السابق",
        "en": "Earlier context was compacted",
    },
    "chat.typing_thinking": {
        "ar": "يفكّر",
        "en": "Thinking",
    },
    "chat.needs_clarification": {
        "ar": "يحتاج إلى توضيح",
        "en": "Needs clarification",
    },
    "chat.approval_failed": {
        "ar": "فشلت الموافقة",
        "en": "Approval failed",
    },
    "chat.phase_queued": {
        "ar": "جارٍ البدء…",
        "en": "Starting…",
    },
    "chat.phase_approval_required": {
        "ar": "مطلوب موافقة",
        "en": "Approval required",
    },
    "chat.phase_resuming": {
        "ar": "جارٍ الاستئناف",
        "en": "Resuming",
    },
    "chat.phase_stopping": {
        "ar": "جارٍ الإيقاف…",
        "en": "Stopping…",
    },
    "chat.phase_completed": {
        "ar": "اكتمل",
        "en": "Completed",
    },
    "chat.phase_failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "chat.phase_cancelled": {
        "ar": "أُلغي",
        "en": "Cancelled",
    },
    "chat.phase_recovering": {
        "ar": "جارٍ الاستعادة…",
        "en": "Recovering…",
    },
    "chat.phase_interrupted": {
        "ar": "انقطع",
        "en": "Interrupted",
    },
    "chat.hitl_title": {
        "ar": "⚠ مطلوب موافقة",
        "en": "⚠ Approval Required",
    },
    "chat.hitl_tool_label": {
        "ar": "الأداة:",
        "en": "Tool:",
    },
    "chat.hitl_args_label": {
        "ar": "المعاملات:",
        "en": "Args:",
    },
    "chat.hitl_tip_yolo": {
        "ar": "تلميح: <strong>السماح بالأداة</strong> يوقف تكرار الطلب لهذه الأداة فقط. <strong>جلسة YOLO</strong> تتخطى كل أداة خطرة (الأصلية وأدوات MCP) حتى <code>/yolo off</code> أو انتهاء المدة.",
        "en": "Tip: <strong>Allow tool</strong> stops repeat prompts for this tool only. <strong>YOLO session</strong> skips every danger tool (native + MCP) until you <code>/yolo off</code> or TTL.",
    },
    "chat.hitl_tip_always": {
        "ar": "هذه الأداة <strong>تتطلب الموافقة دائمًا</strong> (حماية أمان) — لا يتخطاها YOLO ولا أذونات الجلسة.",
        "en": "This tool <strong>always requires approval</strong> (safety fail-safe) — YOLO and session grants cannot skip it.",
    },
    "chat.hitl_approve_once": {
        "ar": "موافقة لمرة واحدة",
        "en": "Approve once",
    },
    "chat.hitl_approve_once_title": {
        "ar": "هذا الاستدعاء فقط",
        "en": "This call only",
    },
    "chat.hitl_allow_n": {
        "ar": "السماح بهذه الأدوات ({n}) للجلسة",
        "en": "Allow these {n} tools (session)",
    },
    "chat.hitl_allow_n_title": {
        "ar": "السماح بهذه الأدوات ({n}) لمدة ~30 دقيقة في هذه الجلسة: {names}",
        "en": "Allow these {n} tools for ~30m in this session: {names}",
    },
    "chat.hitl_allow_tool": {
        "ar": "السماح بالأداة (للجلسة)",
        "en": "Allow tool (session)",
    },
    "chat.hitl_allow_tool_title": {
        "ar": "السماح بهذه الأداة لمدة ~30 دقيقة في هذه الجلسة",
        "en": "Allow this tool for ~30m in this session",
    },
    "chat.hitl_yolo": {
        "ar": "جلسة YOLO",
        "en": "YOLO session",
    },
    "chat.hitl_yolo_title": {
        "ar": "تخطي كل الأدوات الخطرة لهذه الجلسة",
        "en": "Skip all danger tools for this session",
    },
    "chat.hitl_deny": {
        "ar": "رفض",
        "en": "Deny",
    },
    "chat.hitl_publish": {
        "ar": "المحتوى المراد نشره",
        "en": "Content to publish",
    },
    "chat.hitl_stored_proposal": {
        "ar": "المسودة المحفوظة {pid}",
        "en": "stored proposal {pid}",
    },
    "chat.hitl_verified": {
        "ar": "(مُطابق لما وافقت عليه):",
        "en": "(verified against what you approved):",
    },
    "chat.hitl_already_resolved": {
        "ar": "حُسم مسبقًا",
        "en": "Already resolved",
    },
    "chat.hitl_no_longer_pending": {
        "ar": "لم يعد معلّقًا",
        "en": "No longer pending",
    },
    "chat.hitl_status_denied": {
        "ar": "رُفض",
        "en": "Denied",
    },
    "chat.hitl_status_approved": {
        "ar": "تمت الموافقة",
        "en": "Approved",
    },
    "chat.hitl_status_running": {
        "ar": "تمت الموافقة — جارٍ التنفيذ…",
        "en": "Approved — running…",
    },
    "chat.no_response_md": {
        "ar": "_لم يصل رد._ راجع سجلات الخادم أو الموافقات المعلّقة.",
        "en": "_No response received._ Check server logs or Pending Approvals.",
    },
    "chat.no_response_received": {
        "ar": "لم يصل رد.",
        "en": "No response received.",
    },
    "chat.you_avatar": {
        "ar": "أنت",
        "en": "You",
    },
    "chat.slash_commands_heading": {
        "ar": "أوامر الشرطة المائلة",
        "en": "Slash commands",
    },
    "chat.slash_allow_tool_tip": {
        "ar": "مع الأدوات الخطرة يمكنك أيضًا **السماح بالأداة (للجلسة)** لإيقاف تكرار الطلب دون تفعيل YOLO كاملًا.",
        "en": "On danger tools you can also **Allow tool (session)** to stop repeat prompts without full YOLO.",
    },
    "chat.msg_edit": {
        "ar": "تعديل",
        "en": "Edit",
    },
    "chat.msg_copy": {
        "ar": "نسخ",
        "en": "Copy",
    },
    "chat.msg_regenerate": {
        "ar": "إعادة التوليد",
        "en": "Regenerate",
    },
    "chat.msg_read_aloud": {
        "ar": "قراءة بصوت عالٍ",
        "en": "Read aloud",
    },
    "chat.msg_read_aloud_aria": {
        "ar": "اقرأ هذه الرسالة بصوت عالٍ",
        "en": "Read this message aloud",
    },
    "chat.msg_helpful": {
        "ar": "مفيد",
        "en": "Helpful",
    },
    "chat.msg_not_helpful": {
        "ar": "غير مفيد",
        "en": "Not helpful",
    },
    "chat.session_msgs": {
        "ar": "الرسائل: {n}",
        "en": "{n} msgs",
    },
    "chat.hitl_wants_to_run": {
        "ar": "يريد الوكيل تشغيل:",
        "en": "Agent wants to run:",
    },
    "chat.hitl_wants_to_run_n": {
        "ar": "يريد الوكيل تشغيل {n} أدوات خطرة:",
        "en": "Agent wants to run {n} danger tools:",
    },
    "chat.node_label": {
        "ar": "العقدة",
        "en": "Node",
    },
    "chat.slash.yolo": {
        "ar": "تخطَّ موافقات الأدوات الخطرة في هذه الجلسة (مؤقتًا)",
        "en": "Skip danger-tool approvals for this session (TTL)",
    },
    "chat.slash.yolo_off": {
        "ar": "أعد الموافقات وامسح أذونات الأدوات",
        "en": "Restore HITL approvals + clear tool grants",
    },
    "chat.slash.yolo_status": {
        "ar": "اعرض حالة YOLO والأذونات في هذه الجلسة",
        "en": "Show YOLO / grant status for this session",
    },
    "chat.slash.long": {
        "ar": "اعرض ميزانية الجولات وحالة الموافقات",
        "en": "Show iteration budget + HITL status",
    },
    "chat.slash.long_on": {
        "ar": "ميزانية بحث (40 جولة) — الموافقات تبقى مفعّلة",
        "en": "Research budget (40 rounds) — HITL still on",
    },
    "chat.slash.long_mission": {
        "ar": "اعمل حتى الإنجاز (حد أقصى نحو 500 جولة)",
        "en": "Run until done (hard wall ~500 rounds)",
    },
    "chat.slash.long_yolo": {
        "ar": "ميزانية بحث مع تخطي موافقات الأدوات الخطرة",
        "en": "Research budget AND skip danger-tool approvals",
    },
    "chat.slash.unrestricted": {
        "ar": "مهمة + YOLO — أنجز هذا العمل دون أن تسأل",
        "en": "Mission + YOLO — finish this job, don’t ask",
    },
    "chat.slash.unrestricted_off": {
        "ar": "أعد ميزانية الإعدادات والموافقات",
        "en": "Restore Settings budget + HITL",
    },
    "chat.slash.long_off": {
        "ar": "أوقف الميزانية فقط (الموافقات كما هي)",
        "en": "Budget only off (HITL unchanged)",
    },
    "chat.slash.plan": {
        "ar": "اعرض حالة وضع التخطيط (افحص ثم اقترح)",
        "en": "Show plan-mode status (inspect then propose)",
    },
    "chat.slash.plan_on": {
        "ar": "وضع التخطيط — أدوات الكتابة والتنفيذ محجوبة حتى ‎/plan go",
        "en": "Plan mode — write/exec tools blocked until /plan go",
    },
    "chat.slash.plan_go": {
        "ar": "اعتمد الخطة ونفّذ (الموافقات تبقى مفعّلة)",
        "en": "Approve the plan and execute (HITL still on)",
    },
    "chat.slash.plan_off": {
        "ar": "اخرج من وضع التخطيط",
        "en": "Leave plan mode",
    },
    "chat.slash.new": {
        "ar": "ابدأ محادثة جديدة",
        "en": "Start a new chat session",
    },
    "chat.slash.reset": {
        "ar": "امسح سجل هذه المحادثة",
        "en": "Clear this conversation history",
    },
    "chat.slash.steer": {
        "ar": "أضف ملاحظة للمهمة الجارية — عدّلها ثم اضغط Enter",
        "en": "Queue a note for the running task — edit, then Enter",
    },
    "chat.slash.steer_bang": {
        "ar": "أوقف المهمة الجارية مؤقتًا وأدخل متطلبًا",
        "en": "Pause the running task and inject a requirement",
    },
    "chat.slash.abort": {
        "ar": "أوقف المهمة الجارية وتخلَّ عنها",
        "en": "Stop and abandon the running task",
    },
    "chat.slash.help": {
        "ar": "اعرض أوامر الشرطة المائلة المتاحة",
        "en": "List available slash commands",
    },
}
