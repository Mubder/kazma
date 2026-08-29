"""``tool`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "tool.desc.activate_skill": {
        "ar": "تفعيل مهارة وكيل مثبتة للجلسة.",
        "en": "Activate an installed agent skill for the session.",
    },
    "tool.desc.analyze_image": {
        "ar": "تحليل صورة باستخدام رؤية النموذج.",
        "en": "Analyze an image using LLM vision.",
    },
    "tool.desc.analyze_local_image": {
        "ar": "تحليل لقطة شاشة أو رسم بياني محلي.",
        "en": "Analyze a local screenshot or diagram.",
    },
    "tool.desc.arabic_translate": {
        "ar": "الترجمة بين العربية والإنجليزية.",
        "en": "Translate between Arabic and English.",
    },
    "tool.desc.browser_click": {
        "ar": "النقر على عنصر في المتصفح.",
        "en": "Click an element in the browser.",
    },
    "tool.desc.browser_eval_js": {
        "ar": "تنفيذ JavaScript في سياق الصفحة.",
        "en": "Evaluate JavaScript in the page context.",
    },
    "tool.desc.browser_extract_text": {
        "ar": "استخراج النص من الصفحة الحالية.",
        "en": "Extract text from the current page.",
    },
    "tool.desc.browser_fill_form": {
        "ar": "تعبئة حقول نموذج في المتصفح.",
        "en": "Fill form fields in the browser.",
    },
    "tool.desc.browser_navigate": {
        "ar": "فتح رابط في المتصفح.",
        "en": "Navigate the browser to a URL.",
    },
    "tool.desc.browser_screenshot": {
        "ar": "التقاط لقطة شاشة للصفحة.",
        "en": "Capture a screenshot of the page.",
    },
    "tool.desc.cancel_scheduled": {
        "ar": "إلغاء مهمة مجدولة باستخدام معرّفها.",
        "en": "Cancel a scheduled task by job ID.",
    },
    "tool.desc.check_environment": {
        "ar": "تشخيص ملفات النظام ومسار PATH.",
        "en": "Diagnose system binaries and PATH.",
    },
    "tool.desc.check_swarm_task": {
        "ar": "التحقق من حالة أو نتيجة مهمة سرب.",
        "en": "Check status or result of a swarm task.",
    },
    "tool.desc.code_exec": {
        "ar": "تنفيذ كود في بيئة معزولة.",
        "en": "Execute code in a sandboxed environment.",
    },
    "tool.desc.config_read": {
        "ar": "قراءة قيمة إعداد.",
        "en": "Read a configuration value.",
    },
    "tool.desc.config_save": {
        "ar": "حفظ قيمة إعداد.",
        "en": "Save a configuration value.",
    },
    "tool.desc.context_info": {
        "ar": "عرض استخدام نافذة السياق.",
        "en": "Show context window usage.",
    },
    "tool.desc.crawl_page": {
        "ar": "جلب واستخراج محتوى Markdown نظيف من رابط.",
        "en": "Fetch and extract clean markdown from a URL.",
    },
    "tool.desc.crawl_site": {
        "ar": "زحف محدود متعدد الصفحات لموقع وثائق.",
        "en": "Bounded multi-page crawl of a documentation site.",
    },
    "tool.desc.create_event": {
        "ar": "إنشاء حدث في التقويم.",
        "en": "Create a calendar event.",
    },
    "tool.desc.current_datetime": {
        "ar": "الحصول على التاريخ والوقت والمنطقة الزمنية الحالية.",
        "en": "Get the current date, time, and timezone.",
    },
    "tool.desc.delete_event": {
        "ar": "حذف حدث من التقويم.",
        "en": "Delete a calendar event.",
    },
    "tool.desc.digest_research_file": {
        "ar": "المرور على كل المقاطع وإرجاع ملخص استخراجي محدود.",
        "en": "Walk all chunks and return a bounded extractive digest.",
    },
    "tool.desc.dispatch_notification": {
        "ar": "إرسال إشعار إلى منصة.",
        "en": "Send a notification to a platform.",
    },
    "tool.desc.dispatch_swarm": {
        "ar": "إرسال مهمة بحث أو تحليل إلى السرب.",
        "en": "Dispatch a research or analysis task to the swarm.",
    },
    "tool.desc.email_analyze": {
        "ar": "تحليل رسالة بريد للقصد/الملخص.",
        "en": "Analyze an email for intent/summary.",
    },
    "tool.desc.email_categorize": {
        "ar": "تصنيف رسالة بريد.",
        "en": "Categorize an email.",
    },
    "tool.desc.email_delete": {
        "ar": "حذف رسالة بريد.",
        "en": "Delete an email message.",
    },
    "tool.desc.email_get": {
        "ar": "جلب رسالة بريد بمعرّفها.",
        "en": "Get a single email by id.",
    },
    "tool.desc.email_list": {
        "ar": "سرد الرسائل من صندوق البريد.",
        "en": "List emails from the mailbox.",
    },
    "tool.desc.email_send": {
        "ar": "إرسال رسالة بريد.",
        "en": "Send an email message.",
    },
    "tool.desc.execute_db_query": {
        "ar": "تنفيذ استعلام SQL للقراءة فقط.",
        "en": "Execute a read-only SQL SELECT query.",
    },
    "tool.desc.export_session": {
        "ar": "تصدير جلسة المحادثة إلى ملف.",
        "en": "Export the conversation session to a file.",
    },
    "tool.desc.file_delete": {
        "ar": "حذف ملف داخل مساحة العمل.",
        "en": "Delete a file inside the workspace.",
    },
    "tool.desc.file_list": {
        "ar": "سرد الملفات والمجلدات في مسار محدد.",
        "en": "List files and directories at a path.",
    },
    "tool.desc.file_read": {
        "ar": "قراءة ملف من نظام الملفات المحلي.",
        "en": "Read a file from the local filesystem.",
    },
    "tool.desc.file_search": {
        "ar": "البحث عن نص داخل الملفات باستخدام التعابير المنطقية.",
        "en": "Search text inside files using regex.",
    },
    "tool.desc.file_write": {
        "ar": "كتابة محتوى إلى ملف محلي.",
        "en": "Write content to a local file.",
    },
    "tool.desc.find_free_slots": {
        "ar": "العثور على أوقات فارغة في التقويم.",
        "en": "Find free time slots on the calendar.",
    },
    "tool.desc.format_code": {
        "ar": "تنسيق الملفات المصدرية باستخدام ruff.",
        "en": "Format source files using ruff format.",
    },
    "tool.desc.generate_docx": {
        "ar": "توليد مستند Word منسّق (أنماط عناوين، قوائم، تبرير، RTL للعربية). markdown في body الأقسام.",
        "en": "Generate a styled Word document (Heading styles, lists, justify, RTL for Arabic). Markdown in section bodies.",
    },
    "tool.desc.generate_image": {
        "ar": "توليد صورة من وصف نصي.",
        "en": "Generate an image from a text prompt.",
    },
    "tool.desc.generate_markdown_doc": {
        "ar": "توليد مستند Markdown.",
        "en": "Generate a Markdown document.",
    },
    "tool.desc.generate_pdf": {
        "ar": "توليد PDF منسّق (عناوين، قوائم، عريض/مائل، تبرير). استخدم markdown في body. العربية تُشكَّل تلقائياً (lang=ar).",
        "en": "Generate a styled PDF (headings, lists, bold/italic, justified). Use markdown in section bodies. Arabic is auto-shaped (lang=ar).",
    },
    "tool.desc.generate_ui_mockup": {
        "ar": "توليد تصميم واجهة من وصف نصي.",
        "en": "Generate a wireframe UI design from text.",
    },
    "tool.desc.generate_xlsx": {
        "ar": "توليد جدول Excel.",
        "en": "Generate an Excel spreadsheet.",
    },
    "tool.desc.get_system_stats": {
        "ar": "الحصول على استخدام المعالج والذاكرة والقرص.",
        "en": "Fetch CPU, RAM, and Disk utilization.",
    },
    "tool.desc.git_commit": {
        "ar": "تثبيت الملفات برسالة.",
        "en": "Commit files with a message.",
    },
    "tool.desc.git_pull": {
        "ar": "سحب التغييرات البعيدة إلى الفرع المحلي.",
        "en": "Pull (fetch and merge) remote changes into local branch.",
    },
    "tool.desc.git_push": {
        "ar": "دفع التغييرات المحلية إلى GitHub.",
        "en": "Push (upload) local commits to GitHub.",
    },
    "tool.desc.git_push_pull": {
        "ar": "مزامنة الفرع المحلي عبر git pull/push.",
        "en": "Sync local branch via git pull/push.",
    },
    "tool.desc.git_status": {
        "ar": "الحصول على حالة مستودع Git والفرع.",
        "en": "Get git repository status and branch.",
    },
    "tool.desc.github_create_pr": {
        "ar": "إنشاء طلب سحب على GitHub.",
        "en": "Create a Pull Request on GitHub.",
    },
    "tool.desc.github_list_issues": {
        "ar": "سرد المشاكل المفتوحة في المستودع.",
        "en": "List open issues on the repository.",
    },
    "tool.desc.hijri_convert": {
        "ar": "تحويل التواريخ بين الميلادي والهجري.",
        "en": "Convert dates between Gregorian and Hijri.",
    },
    "tool.desc.insert_diacritics": {
        "ar": "إضافة التشكيل (الحركات) إلى النص العربي.",
        "en": "Apply vowel diacritics to Arabic text.",
    },
    "tool.desc.inspect_db_schema": {
        "ar": "استخراج المخطط من قواعد بيانات SQLite.",
        "en": "Extract schema from SQLite databases.",
    },
    "tool.desc.install_agent_skill": {
        "ar": "تثبيت مهارة وكيل من GitHub.",
        "en": "Install an Agent Skill from GitHub.",
    },
    "tool.desc.install_npm_packages": {
        "ar": "تثبيت حزم Node/npm.",
        "en": "Install Node/npm packages.",
    },
    "tool.desc.install_python_packages": {
        "ar": "تثبيت حزم Python في البيئة الافتراضية.",
        "en": "Install Python packages in the runtime venv.",
    },
    "tool.desc.knowledge_create_library": {
        "ar": "إنشاء مكتبة معرفة لاستيعاب الوثائق.",
        "en": "Create a knowledge library for doc ingestion.",
    },
    "tool.desc.knowledge_ingest_site": {
        "ar": "زحف شجرة وثائق واستيعابها في مكتبة معرفة.",
        "en": "Crawl and ingest a doc tree into a knowledge library.",
    },
    "tool.desc.knowledge_ingest_url": {
        "ar": "استيعاب رابط واحد في مكتبة معرفة.",
        "en": "Ingest one URL into a knowledge library.",
    },
    "tool.desc.knowledge_list_libraries": {
        "ar": "سرد مكتبات المعرفة وعدد المقاطع.",
        "en": "List knowledge libraries and chunk counts.",
    },
    "tool.desc.knowledge_search": {
        "ar": "البحث في مكتبات المعرفة المستوعَبة مع الاستشهاد بالمصادر.",
        "en": "Search ingested knowledge libraries with citations.",
    },
    "tool.desc.lint_code": {
        "ar": "إجراء فحوصات ثابتة باستخدام ruff.",
        "en": "Run static checks using ruff linter.",
    },
    "tool.desc.list_active_processes": {
        "ar": "سرد العمليات النشطة تحت كاظمه.",
        "en": "List active subprocesses under Kazma.",
    },
    "tool.desc.list_agent_skills": {
        "ar": "سرد مهارات الوكيل المثبتة.",
        "en": "List installed agent skills.",
    },
    "tool.desc.list_events": {
        "ar": "سرد أحداث التقويم.",
        "en": "List calendar events.",
    },
    "tool.desc.list_research_chunks": {
        "ar": "سرد فهارس المقاطع ومعايناتها لملف بحث محفوظ.",
        "en": "List chunk indices and previews for a saved research file.",
    },
    "tool.desc.list_scheduled": {
        "ar": "سرد المهام المجدولة.",
        "en": "List scheduled background tasks.",
    },
    "tool.desc.memory_search": {
        "ar": "البحث في الذاكرة طويلة المدى عن محادثات سابقة ذات صلة.",
        "en": "Search long-term memory for relevant past conversations.",
    },
    "tool.desc.memory_store": {
        "ar": "تخزين معلومة أو تفضيل في الذاكرة طويلة المدى.",
        "en": "Store a fact or preference in long-term memory.",
    },
    "tool.desc.parse_document": {
        "ar": "تحليل نص منظم من ملفات محلية.",
        "en": "Parse structured text from local files.",
    },
    "tool.desc.python_exec": {
        "ar": "تنفيذ كود Python في بيئة معزولة.",
        "en": "Execute Python code in a sandboxed subprocess.",
    },
    "tool.desc.read_research_chunk": {
        "ar": "قراءة مقطع واحد من ملف بحث محفوظ.",
        "en": "Read one chunk of a saved research file.",
    },
    "tool.desc.read_system_logs": {
        "ar": "عرض أسطر حديثة من سجلات كاظمه.",
        "en": "Stream recent lines of Kazma logs.",
    },
    "tool.desc.read_url": {
        "ar": "جلب واستخراج المحتوى المقروء من رابط.",
        "en": "Fetch and extract readable content from a URL.",
    },
    "tool.desc.read_url_to_file": {
        "ar": "جلب رابط وحفظ النص الكامل داخل مساحة العمل.",
        "en": "Fetch a URL and save the full extract under the workspace.",
    },
    "tool.desc.run_research_pipeline": {
        "ar": "مسار بحث عميق: بحث، جلب صفحات، تلخيص، توليف، تقرير.",
        "en": "Deep research pipeline: search, acquire, digest, synthesize, report.",
    },
    "tool.desc.run_unit_tests": {
        "ar": "تنفيذ الاختبارات باستخدام pytest.",
        "en": "Execute tests using pytest.",
    },
    "tool.desc.schedule_task": {
        "ar": "جدولة مهمة للتشغيل في وقت لاحق.",
        "en": "Schedule a task to run at a future time.",
    },
    "tool.desc.send_approval_request": {
        "ar": "إرسال بطاقة موافقة تفاعلية للتحقق البشري.",
        "en": "Dispatch an interactive approval card for HITL.",
    },
    "tool.desc.send_message": {
        "ar": "إرسال رسالة نصية إلى المحادثة.",
        "en": "Send a text message to the conversation.",
    },
    "tool.desc.shell_exec": {
        "ar": "تنفيذ أمر في الطرفية وإرجاع النتيجة.",
        "en": "Execute a shell command and return output.",
    },
    "tool.desc.spawn_agent": {
        "ar": "إنشاء وكيل فرعي لمهمة محددة.",
        "en": "Spawn a sub-agent for a focused task.",
    },
    "tool.desc.spawn_agents": {
        "ar": "إنشاء عدة وكلاء فرعيين بالتوازي.",
        "en": "Spawn multiple sub-agents in parallel.",
    },
    "tool.desc.sqlite_query": {
        "ar": "تنفيذ استعلام SQL للقراءة فقط.",
        "en": "Execute a read-only SQL query.",
    },
    "tool.desc.summarize_research_file": {
        "ar": "مخطط استخراجي خفيف لملف بحث.",
        "en": "Light extractive outline of a research file.",
    },
    "tool.desc.synthesize_from_digests": {
        "ar": "توليف متعدد المصادر بالذكاء الاصطناعي من ملخصات البحث.",
        "en": "LLM multi-source synthesis from saved research digests.",
    },
    "tool.desc.uninstall_agent_skill": {
        "ar": "إزالة مهارة وكيل.",
        "en": "Uninstall an Agent Skill.",
    },
    "tool.desc.update_event": {
        "ar": "تحديث حدث في التقويم.",
        "en": "Update a calendar event.",
    },
    "tool.desc.vault_delete": {
        "ar": "حذف سر من الخزنة.",
        "en": "Delete a secret from the vault.",
    },
    "tool.desc.vault_list": {
        "ar": "سرد أسرار الخزنة (الأسماء فقط).",
        "en": "List secrets in the vault (names only).",
    },
    "tool.desc.vault_retrieve": {
        "ar": "استرجاع سر من الخزنة.",
        "en": "Retrieve a secret from the vault.",
    },
    "tool.desc.vault_store": {
        "ar": "تخزين سر في الخزنة المشفّرة.",
        "en": "Store a secret in the encrypted vault.",
    },
    "tool.desc.web_search": {
        "ar": "البحث في الويب باستخدام DuckDuckGo.",
        "en": "Search the web using DuckDuckGo.",
    },
    "tool.desc.web_search_duckduckgo": {
        "ar": "البحث في الويب عبر DuckDuckGo.",
        "en": "Search the web via DuckDuckGo.",
    },
}
