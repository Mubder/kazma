"""``tool`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "tool.desc.codebase_status": {"en": "Show the project code index status.", "ar": "عرض حالة فهرس ملفات المشروع."},
    "tool.desc.send_file": {"en": "Send a file through the configured chat platform.", "ar": "إرسال ملف عبر منصة المحادثة المهيأة."},
    "tool.desc.memory_admin": {"en": "Inspect and administer stored agent memory.", "ar": "فحص ذاكرة الوكيل المخزنة وإدارتها."},
    "tool.desc.memory_merge_entities": {"en": "Merge duplicate entities in memory.", "ar": "دمج الكيانات المكررة في الذاكرة."},
    "tool.desc.memory_link_entities": {"en": "Link related memory entities.", "ar": "ربط كيانات الذاكرة المرتبطة."},
    "tool.desc.memory_list_beliefs": {"en": "List stored beliefs with their sources and confidence.", "ar": "عرض المعتقدات المخزنة ومصادرها ودرجة الثقة بها."},
    "tool.desc.memory_invalidate": {"en": "Invalidate outdated memory facts.", "ar": "إبطال حقائق الذاكرة التي لم تعد صالحة."},
    "tool.desc.memory_list_entities": {"en": "List stored memory entities.", "ar": "عرض كيانات الذاكرة المخزنة."},
    "tool.desc.memory_delete_entity": {"en": "Delete a stored memory entity.", "ar": "حذف كيان مخزن في الذاكرة."},
    "tool.desc.memory_purge_empty_entities": {"en": "Remove empty memory entities.", "ar": "إزالة كيانات الذاكرة الفارغة."},
    "tool.desc.mcp_test_server": {"en": "Test a configured MCP server connection.", "ar": "اختبار اتصال خادم MCP مهيأ."},
    "tool.desc.repository_metrics": {"en": "Inspect repository size and code metrics.", "ar": "فحص حجم المستودع ومقاييس الشفرة."},
    "tool.desc.computer_use": {"en": "Inspect and operate the configured computer-use surface.", "ar": "فحص واجهة استخدام الحاسوب المهيأة والتفاعل معها."},
    "tool.desc.plan_research_queries": {"en": "Plan focused research queries for a topic.", "ar": "إعداد استعلامات بحث مركزة لموضوع محدد."},
    "tool.desc.critique_synthesis_gaps": {"en": "Identify evidence gaps in a research synthesis.", "ar": "تحديد فجوات الأدلة في خلاصة البحث."},
    "tool.desc.list_research_papers": {"en": "List the available research papers.", "ar": "عرض الأوراق البحثية المتاحة."},
    "tool.desc.research_readiness": {"en": "Check whether deep research is ready to run.", "ar": "التحقق من جاهزية البحث المتعمق للتنفيذ."},
    "tool.desc.start_deep_research": {"en": "Start a deep research task.", "ar": "بدء مهمة بحث متعمق."},
    "tool.desc.mcp_list_resources": {"en": "List resources exposed by connected MCP servers.", "ar": "عرض الموارد التي توفرها خوادم MCP المتصلة."},
    "tool.desc.mcp_read_resource": {"en": "Read a resource from a connected MCP server.", "ar": "قراءة مورد من خادم MCP متصل."},
    "tool.desc.mcp_list_prompts": {"en": "List prompts exposed by connected MCP servers.", "ar": "عرض قوالب التوجيه التي توفرها خوادم MCP المتصلة."},
    "tool.desc.mcp_get_prompt": {"en": "Load a prompt from a connected MCP server.", "ar": "تحميل قالب توجيه من خادم MCP متصل."},
    "tool.desc.search_agent_skills": {"en": "Search for available agent skills.", "ar": "البحث عن مهارات الوكيل المتاحة."},
    "tool.desc.update_scratchpad": {"en": "Update the current task scratchpad.", "ar": "تحديث ملاحظات المهمة الحالية."},
    "tool.desc.save_proposal": {"en": "Save a draft proposal for review before execution.", "ar": "حفظ مسودة مقترح لمراجعتها قبل التنفيذ."},
    "tool.desc.list_proposals": {"en": "List saved proposals and their review status.", "ar": "عرض المقترحات المحفوظة وحالة مراجعتها."},
    "tool.desc.discard_proposal": {"en": "Discard a saved draft proposal.", "ar": "تجاهل مسودة مقترح محفوظة."},
    "tool.desc.task_ledger_update": {"en": "Update the current task progress ledger.", "ar": "تحديث سجل تقدم المهمة الحالية."},

    "tool.desc.document_import": {"en": "Ingest a workspace-safe local file into the durable document platform (quarantine, validate, parse out-of-process) and return its opaque document_id/job_id and final state. Only files inside the active workspace are accepted; arbitrary server paths are refused.", "ar": "استيراد ملف من مساحة العمل إلى منصة المستندات الدائمة وإرجاع معرّف المستند والمهمة."},
    "tool.desc.document_status": {"en": "With no ids: tenant document-platform overview (enabled, workers, queue, catalog count, recent titles). With document_id or job_id: that job's stage, attempt count, and any safe error diagnostics.", "ar": "عرض حالة منصة المستندات أو مراحل مهمة مستند محدد."},
    "tool.desc.document_read": {"en": "Read paged, fenced content of an already-processed document by its opaque document_id, with page/offset/max_chars selectors and deterministic continuation.", "ar": "قراءة المحتوى المعالج حسب معرّف المستند مع ترقيم الصفحات وإمكانية المتابعة."},
    "tool.desc.document_index": {"en": "Publish a processed document's current immutable version into a Knowledge library for retrieval and citation.", "ar": "فهرسة نسخة المستند المعالجة في مكتبة المعرفة للاسترجاع والاستشهاد."},
    "tool.desc.document_search": {"en": "Search a Knowledge library and return matching document chunks inside exactly one untrusted-data fence with page/version citations.", "ar": "البحث في مكتبة المعرفة وإرجاع مقاطع المستندات مع مراجع الصفحات والنسخ."},
    "tool.desc.document_cancel": {"en": "Request cooperative cancellation of a running or pending document processing job by its opaque job_id.", "ar": "طلب إلغاء مهمة معالجة مستند معلقة أو جارية."},
    "tool.desc.document_convert": {"en": "Convert an already-processed document (by opaque document_id) to another format through the isolated renderer. Only the immutable original bytes are used; no raw file path is accepted. Returns a downloadable artifact_id.", "ar": "تحويل مستند معالج إلى صيغة أخرى داخل عامل معزول وإرجاع معرّف الناتج."},
    "tool.desc.document_redact": {"en": "Physically redact a list of terms from a processed PDF document by opaque document_id, creating a new independently-verified immutable artifact. Terms are never logged; mixed image/vector PDFs fail closed.", "ar": "إزالة مصطلحات من ملف PDF معالج وإنشاء ناتج مستقل تم التحقق منه."},
    "tool.desc.read_document": {"en": "TRANSIENT, PATH-BASED. Reads a file at a workspace path and returns text now; nothing is stored and no document_id exists afterwards. If the file should persist, be versioned, searchable, or referenced later, use document_import + document_read from the document-platform skill instead. Read runtime-ready PDF, DOCX, XLSX, PPTX, CSV/TSV, JSON, text/Markdown/log, HTML, or RTF with page/sheet/slide/block selectors and deterministic continuation. Legacy DOC/XLS/PPT are available only when a healthy headless LibreOffice is detected.", "ar": "قراءة ملف من مساحة العمل مؤقتًا؛ استخدم استيراد المستند إذا احتجت حفظه وفهرسته."},
    "tool.desc.pdf_merge": {"en": "Merge workspace-approved PDFs in an isolated pypdf worker with file, aggregate-size, page, checksum, sniff, and round-trip bounds.", "ar": "دمج ملفات PDF المسموح بها من مساحة العمل داخل عامل معزول."},
    "tool.desc.pdf_split": {"en": "Extract a validated bounded page range in an isolated pypdf worker.", "ar": "استخراج نطاق صفحات محدد من ملف PDF داخل عامل معزول."},
    "tool.desc.pdf_info": {"en": "Inspect PDF metadata, dimensions, and form fields in an isolated pypdf worker.", "ar": "فحص بيانات ملف PDF وأبعاد صفحاته وحقول النموذج."},
    "tool.desc.ocr_document": {"en": "OCR selected pages of a PDF or PNG/JPEG/TIFF/BMP/WebP image through the isolated DocumentService. Live readiness verifies the Tesseract binary, requested eng/ara language data, Pillow, and a one-page PDF rasterizer; unavailable components return an actionable error instead of a false claim.", "ar": "استخراج النص بصريًا من صفحات PDF أو الصور عبر خدمة المستندات المعزولة."},
    "tool.desc.convert_document": {"en": "TRANSIENT, PATH-BASED. Converts a file at a workspace path. For a document already in the platform, use document_convert (document-platform) — it converts the immutable stored bytes and returns a downloadable artifact_id under the tenant ACL. Convert runtime-supported formats in an isolated renderer. HTML/Markdown→PDF denies external resources and requires healthy WeasyPrint. Legacy Office conversion requires healthy headless LibreOffice.", "ar": "تحويل ملف من مساحة العمل مؤقتًا؛ استخدم تحويل المستند للمستندات المحفوظة."},
    "tool.desc.pdf_fill_form": {"en": "Fill only known AcroForm fields in an isolated worker after rejecting scripts/actions. Output fields may remain editable and this limitation is reported.", "ar": "تعبئة حقول نموذج PDF المعروفة بعد رفض السكربتات والإجراءات غير الآمنة."},
    "tool.desc.pdf_redact": {"en": "TRANSIENT, PATH-BASED. Redacts a file at a workspace path. For a document already in the platform, use document_redact (document-platform) — it produces a new independently-verified immutable artifact and an audit record. Redaction terms are never logged by either tool. Secure rasterize-redact-rebuild PDF redaction with text, byte, structure, and rendered-page verification. Requires healthy PyMuPDF and Pillow; otherwise refuses without producing an artifact.", "ar": "إزالة مصطلحات من ملف PDF في مساحة العمل مع التحقق من الناتج."},
    "tool.desc.generate_pptx": {"en": "Generate and round-trip validate a PowerPoint artifact in an isolated python-pptx renderer.", "ar": "إنشاء عرض PowerPoint والتحقق من إمكانية قراءته داخل عامل معزول."},
    "tool.desc.email_accounts": {"en": "List every mail account Kazma can use: the main Gmail and Microsoft accounts and any others added in Settings, with each one's name, address, whether it is signed in and whether its calendar is usable. Call it when the user names an account (\"my work email\", an address) or before choosing one; pass the name or address as account= to the email and calendar tools.", "ar": "عرض حسابات البريد التي يستطيع كازما استخدامها."},
    "tool.desc.git_checkout": {"en": "Switch branches or create a new branch locally.", "ar": "تبديل الفرع المحلي أو إنشاء فرع جديد."},
    "tool.desc.git_merge": {"en": "Merge a branch into the currently active local branch.", "ar": "دمج فرع في الفرع المحلي النشط."},
    "tool.desc.github_merge_pr": {"en": "Merge an open Pull Request on GitHub using GitHub APIs.", "ar": "دمج طلب سحب مفتوح عبر واجهة GitHub."},
    "tool.desc.github_create_issue": {"en": "Create a new Issue on the remote GitHub repository.", "ar": "إنشاء مشكلة جديدة في مستودع GitHub."},
    "tool.desc.github_comment_issue": {"en": "Post a comment on a GitHub Issue or Pull Request.", "ar": "إضافة تعليق إلى مشكلة أو طلب سحب في GitHub."},
    "tool.desc.edit_scheduled": {"en": "Edit an existing scheduled task's timing and/or prompt. Provide the job ID plus at least one of timing ('5m', '1h', 'daily at 9am') or a new prompt.", "ar": "تعديل توقيت مهمة مجدولة أو نص توجيهها باستخدام معرّف المهمة."},
    "tool.desc.x_status": {"en": "Read-only X connector status: configured?, handle, remaining Kazma caps, and the latest posts. Args: recent (how many latest posts to list, 1-50, default 5) — this is how to read Kazma's post history; its database is not queryable directly. Never returns secrets. If unconfigured, tell the operator to open Settings → X (not chat) and paste the four OAuth 1.0a keys with Read + Write app permissions.", "ar": "عرض حالة اتصال X والحساب والحصص المتبقية وأحدث المنشورات دون تعديل."},
    "tool.desc.x_post": {"en": "Post one tweet via official POST /2/tweets (OAuth 1.0a). CONTRACT: persist drafts with save_proposal FIRST, then call this ONCE PER ITEM with proposal_id=<that item's id> — the commitment gate refuses without it and rewrites text from the stored proposal (the id wins). HITL is ALWAYS required — YOLO and standing grants cannot skip it. Policy denies empty text, over-length, duplicate 30d, too many @mentions / $cashtags / hashtags, and over Kazma daily/monthly caps. Args: text (required), proposal_id (required — the saved proposal item id), reply_to_id (optional, for a thread reply). All approval cards surface immediately on every path — never advise the operator to batch or wait between posts. Never ask the operator to paste API secrets in chat. Do not use browser/computer_use to tweet — that violates X ToU.", "ar": "نشر مسودة معتمدة عبر واجهة X الرسمية بعد التحقق من الموافقة."},
    "tool.desc.x_delete_post": {"en": "Delete a tweet the connector posted (DELETE /2/tweets/:id). HITL always required. Args: tweet_id.", "ar": "حذف منشور نشره اتصال X؛ يتطلب موافقة بشرية دائمًا."},
    "tool.desc.x_schedule_post": {"en": "Schedule a tweet to be posted automatically at a future time. CONTRACT: persist drafts with save_proposal FIRST, then call this ONCE PER ITEM with proposal_id=<that item's id> — the commitment gate refuses without it and rewrites text from the stored proposal. HITL is ALWAYS required at booking — the operator approves the exact draft and time once. Kazma stores it and publishes it at the appointed time (X has no native post scheduling). Daily/monthly caps and the 30-day duplicate rule are enforced at booking. Args: text (required), when (required — '5m', '1h', 'daily at 9am', or ISO timestamp), proposal_id (required — the saved proposal item id), reply_to_id (optional). The post fires only while the Kazma server is running.", "ar": "جدولة مسودة معتمدة للنشر على X في وقت لاحق."},
    "tool.desc.x_list_scheduled": {"en": "List scheduled X posts with their status (pending, fired, cancelled, failed), text and fire time. Read-only. Saved drafts that were never posted or booked are read with list_proposals.", "ar": "عرض منشورات X المجدولة ونصوصها وأوقاتها وحالاتها."},
    "tool.desc.x_cancel_scheduled_post": {"en": "Cancel a scheduled X post before it fires (releases its reserved quota). HITL always required. Args: post_id (the scheduled post id).", "ar": "إلغاء منشور X مجدول قبل نشره؛ يتطلب موافقة بشرية."},
    "tool.desc.file_apply_patch": {"en": "Apply a targeted patch to an existing workspace file.", "ar": "تطبيق تعديلات محددة على ملف موجود مع الحفاظ على نهايات أسطره."},
    "tool.desc.file_apply_patch_set": {"en": "Apply and verify a group of patches across workspace files.", "ar": "تطبيق مجموعة تعديلات على عدة ملفات مع التحقق وإمكانية التراجع."},
    "tool.desc.file_append": {"en": "Append text to a workspace file.", "ar": "إضافة نص إلى نهاية ملف في مساحة العمل."},
    "tool.desc.request_path_access": {"en": "Request permission to access a path outside the workspace.", "ar": "طلب إذن للوصول إلى مسار خارج حدود مساحة العمل."},
    "tool.desc.codebase_search": {"en": "Search the project code index for relevant context.", "ar": "البحث في فهرس ملفات المشروع للحصول على سياق برمجي ذي صلة."},

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
