"""``common`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "auth.relogin_hint": {
        "ar": "انتهت جلستك — سيتم توجيهك إلى صفحة تسجيل الدخول.",
        "en": "Your session expired — you'll be redirected to the login page.",
    },
    "auth.session_expired": {
        "ar": "انتهت جلستك. يرجى تسجيل الدخول مرة أخرى للمتابعة.",
        "en": "Your session has expired. Please sign in again to continue.",
    },
    "auth.session_expired_title": {
        "ar": "انتهت الجلسة",
        "en": "Session expired",
    },
    "common.actions": {
        "ar": "إجراءات",
        "en": "Actions",
    },
    "common.close": {
        "ar": "إغلاق",
        "en": "Close",
    },
    "common.confirm": {
        "ar": "تأكيد",
        "en": "Confirm",
    },
    "common.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "common.disable": {
        "ar": "تعطيل",
        "en": "Disable",
    },
    "common.disabled": {
        "ar": "معطّل",
        "en": "Disabled",
    },
    "common.edit": {
        "ar": "تعديل",
        "en": "Edit",
    },
    "common.enable": {
        "ar": "تفعيل",
        "en": "Enable",
    },
    "common.enabled": {
        "ar": "مفعّل",
        "en": "Enabled",
    },
    "common.error": {
        "ar": "خطأ",
        "en": "Error",
    },
    "common.loading": {
        "ar": "جاري التحميل…",
        "en": "Loading…",
    },
    "common.name": {
        "ar": "الاسم",
        "en": "Name",
    },
    "common.no": {
        "ar": "لا",
        "en": "No",
    },
    "common.search": {
        "ar": "بحث",
        "en": "Search",
    },
    "common.type": {
        "ar": "النوع",
        "en": "Type",
    },
    "common.yes": {
        "ar": "نعم",
        "en": "Yes",
    },
    "header.dashboard": {
        "ar": "لوحة التحكم",
        "en": "Dashboard",
    },
    "header.health_status": {
        "ar": "حالة النظام",
        "en": "Health Status",
    },
    "header.home": {
        "ar": "الرئيسية",
        "en": "Home",
    },
    "header.logout": {
        "ar": "تسجيل الخروج",
        "en": "Logout",
    },
    "header.new_chat": {
        "ar": "محادثة جديدة",
        "en": "New Chat",
    },
    "header.settings": {
        "ar": "الإعدادات",
        "en": "Settings",
    },
    "ide.chat": {
        "ar": "محادثة",
        "en": "Chat",
    },
    "ide.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "ide.diff": {
        "ar": "الفروقات",
        "en": "Diff",
    },
    "ide.files": {
        "ar": "الملفات",
        "en": "Files",
    },
    "ide.git_diff": {
        "ar": "فروقات Git",
        "en": "Git diff",
    },
    "ide.new": {
        "ar": "جديد",
        "en": "New",
    },
    "ide.no_file": {
        "ar": "لا يوجد ملف مفتوح",
        "en": "no file open",
    },
    "ide.run": {
        "ar": "تشغيل",
        "en": "Run",
    },
    "ide.save": {
        "ar": "حفظ",
        "en": "Save",
    },
    "ide.send_to_swarm": {
        "ar": "إرسال للسرب",
        "en": "Send to swarm",
    },
    "ide.skill": {
        "ar": "مهارة",
        "en": "Skill",
    },
    "ide.status": {
        "ar": "الحالة",
        "en": "Status",
    },
    "ide.title": {
        "ar": "بيئة التطوير المتكاملة",
        "en": "IDE",
    },
    "ide.unsaved": {
        "ar": "غير محفوظ",
        "en": "unsaved",
    },
    "lang.toggle_to_arabic": {
        "ar": "ع",
        "en": "ع",
    },
    "lang.toggle_to_english": {
        "ar": "EN",
        "en": "EN",
    },
    "load_failed": {
        "ar": "فشل التحميل",
        "en": "Failed to load",
    },
    "login.blurb": {
        "ar": "سجّل الدخول بمستخدم المنصة أو سر الخادم أو مزود الهوية المؤسسي.",
        "en": "Sign in with a platform user, the server secret, or your organization IdP.",
    },
    "login.or": {
        "ar": "أو",
        "en": "or",
    },
    "login.password": {
        "ar": "كلمة المرور",
        "en": "Password",
    },
    "login.server_secret": {
        "ar": "سر الخادم",
        "en": "Server secret",
    },
    "login.sign_in": {
        "ar": "تسجيل الدخول",
        "en": "Sign in",
    },
    "login.sso": {
        "ar": "المتابعة عبر SSO (OIDC)",
        "en": "Continue with SSO (OIDC)",
    },
    "login.tab_secret": {
        "ar": "سر",
        "en": "Secret",
    },
    "login.tab_user": {
        "ar": "مستخدم",
        "en": "User",
    },
    "login.username": {
        "ar": "اسم المستخدم",
        "en": "Username",
    },
    "login.with_secret": {
        "ar": "الدخول بالسر",
        "en": "Sign in with secret",
    },
    "mcp.add_btn": {
        "ar": "إضافة خادم",
        "en": "Add Server",
    },
    "mcp.add_server": {
        "ar": "إضافة خادم",
        "en": "Add Server",
    },
    "mcp.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "mcp.command_label": {
        "ar": "الأمر:",
        "en": "Command:",
    },
    "mcp.empty": {
        "ar": "لا توجد خوادم MCP مهيأة. انقر إضافة خادم للبدء.",
        "en": "No MCP servers configured. Click \"Add Server\" to get started.",
    },
    "mcp.field_auth_token": {
        "ar": "رمز المصادقة (Bearer)",
        "en": "Auth Token (Bearer)",
    },
    "mcp.field_command": {
        "ar": "الأمر (مفصول بمسافات)",
        "en": "Command (space-separated)",
    },
    "mcp.field_env": {
        "ar": "متغيرات البيئة",
        "en": "Environment variables",
    },
    "mcp.field_name": {
        "ar": "اسم الخادم",
        "en": "Server Name",
    },
    "mcp.field_sse_url": {
        "ar": "عنوان SSE",
        "en": "SSE URL",
    },
    "mcp.field_transport": {
        "ar": "النقل",
        "en": "Transport",
    },
    "mcp.field_trust": {
        "ar": "مستوى الثقة",
        "en": "Trust Level",
    },
    "mcp.field_url": {
        "ar": "العنوان",
        "en": "URL",
    },
    "mcp.field_working_dir": {
        "ar": "مجلد العمل (اختياري)",
        "en": "Working Directory (optional)",
    },
    "mcp.hide_tools": {
        "ar": "إخفاء الأدوات",
        "en": "Hide Tools",
    },
    "mcp.modal_title": {
        "ar": "إضافة خادم MCP",
        "en": "Add MCP Server",
    },
    "mcp.no_tools": {
        "ar": "لا توجد أدوات محمّلة. شغّل الخادم لرؤية الأدوات.",
        "en": "No tools loaded. Start the server to see tools.",
    },
    "mcp.oauth_login": {
        "ar": "تسجيل الدخول",
        "en": "Login",
    },
    "mcp.preset_label": {
        "ar": "إضافة سريعة (قالب)",
        "en": "Quick add (preset)",
    },
    "mcp.preset_none": {
        "ar": "— مخصص —",
        "en": "— Custom —",
    },
    "mcp.show_tools": {
        "ar": "إظهار الأدوات",
        "en": "Show Tools",
    },
    "mcp.start": {
        "ar": "تشغيل",
        "en": "Start",
    },
    "mcp.stop": {
        "ar": "إيقاف",
        "en": "Stop",
    },
    "mcp.test": {
        "ar": "اختبار",
        "en": "Test",
    },
    "mcp.title": {
        "ar": "خوادم MCP",
        "en": "MCP Servers",
    },
    "mcp.tools_suffix": {
        "ar": "أدوات",
        "en": "tools",
    },
    "mcp.transport_sse": {
        "ar": "SSE (HTTP)",
        "en": "SSE (HTTP)",
    },
    "mcp.transport_stdio": {
        "ar": "stdio (عملية محلية)",
        "en": "stdio (local process)",
    },
    "mcp.transport_streamable_http": {
        "ar": "HTTP قابل للتدفق",
        "en": "Streamable HTTP",
    },
    "mcp.trust_approval": {
        "ar": "يتطلب موافقة (HITL للأدوات الخطرة)",
        "en": "Approval Required (HITL for danger tools)",
    },
    "mcp.trust_trusted": {
        "ar": "موثوق (بدون HITL)",
        "en": "Trusted (no HITL)",
    },
    "mcp.url_label": {
        "ar": "العنوان:",
        "en": "URL:",
    },
    "nav.activity": {
        "ar": "النشاط",
        "en": "Activity",
    },
    "nav.agents": {
        "ar": "الوكلاء",
        "en": "Agents",
    },
    "nav.capabilities": {
        "ar": "القدرات",
        "en": "Capabilities",
    },
    "nav.chat": {
        "ar": "المحادثة",
        "en": "Chat",
    },
    "nav.configuration": {
        "ar": "الإعدادات",
        "en": "Settings",
    },
    "nav.dashboard": {
        "ar": "لوحة التحكم",
        "en": "Dashboard",
    },
    "nav.documents": {
        "ar": "المستندات",
        "en": "Documents",
    },
    "nav.ide": {
        "ar": "بيئة التطوير المتكاملة",
        "en": "IDE",
    },
    "nav.knowledge": {
        "ar": "المكتبة المعرفية",
        "en": "Knowledge",
    },
    "nav.mcp": {
        "ar": "خوادم MCP",
        "en": "MCP Servers",
    },
    "nav.memory": {
        "ar": "الذاكرة",
        "en": "Memory",
    },
    "nav.more": {
        "ar": "المزيد",
        "en": "More",
    },
    "nav.primary": {
        "ar": "العمل",
        "en": "Work",
    },
    "nav.replay": {
        "ar": "سجل التفرعات واللقطات",
        "en": "Time Travel",
    },
    "nav.research": {
        "ar": "الأبحاث",
        "en": "Research",
    },
    "nav.scheduled": {
        "ar": "المهام المجدولة",
        "en": "Scheduled",
    },
    "nav.settings": {
        "ar": "الإعدادات",
        "en": "Settings",
    },
    "nav.skills": {
        "ar": "المهارات",
        "en": "Skills",
    },
    "nav.swarm": {
        "ar": "منظومة الوكلاء المنسقة",
        "en": "Swarm",
    },
    "nav.tools": {
        "ar": "الأدوات",
        "en": "Tools",
    },
    "nav.workspace": {
        "ar": "مساحة العمل",
        "en": "Workspace",
    },
    "replay.about_browse": {
        "ar": "<strong>تصفّح</strong> الجدول الزمني لكل نقطة قرار في المحادثة.",
        "en": "<strong>Browse</strong> the timeline of every decision point in a conversation.",
    },
    "replay.about_compare": {
        "ar": "<strong>مقارنة</strong> لقطتين لرؤية كيف اختلفت الرسائل والتكلفة والنموذج والمسار.",
        "en": "<strong>Compare</strong> two snapshots to see how messages, cost, model, and routing diverged.",
    },
    "replay.about_desc": {
        "ar": "يلتقط Kazma لقطة من حالة الوكيل بعد كل تكرار للإشراف. يمكنك من:",
        "en": "Kazma captures a snapshot of the agent's state after every supervisor iteration. This lets you:",
    },
    "replay.about_fork": {
        "ar": "<strong>تفريع</strong> من أي لقطة إلى محادثة جديدة تماماً، مع بقاء الأصل سليماً. استكشف فروع «ماذا لو» دون فقدان السجل.",
        "en": "<strong>Fork</strong> from any snapshot into a brand-new thread, keeping the original intact. Explore \"what if\" branches without losing history.",
    },
    "replay.about_note": {
        "ar": "تُحفظ اللقطات في <code>kazma-data/snapshots.db</code> (قابلة للضبط عبر <code>time_travel</code> في kazma.yaml). المخزن في الذاكرة هو مصدر الحقيقة للجلسة الحالية؛ وSQLite يضمن البقاء عبر إعادة التشغيل.",
        "en": "Snapshots are stored in <code>kazma-data/snapshots.db</code> (configurable via <code>time_travel</code> in kazma.yaml). The in-memory store is the source of truth for the current session; SQLite provides durability across restarts.",
    },
    "replay.about_restore": {
        "ar": "<strong>استعادة</strong> (إرجاع) المحادثة الحية إلى أي لقطة — تستمر المحادثة من تلك النقطة وكأن الأدوار اللاحقة لم تحدث.",
        "en": "<strong>Restore</strong> (rewind) the live thread to any snapshot — the conversation continues from that point as if the later turns never happened.",
    },
    "replay.about_title": {
        "ar": "السفر عبر الزمن",
        "en": "Time Travel Replay",
    },
    "replay.diff_desc": {
        "ar": "قارن لقطتين من نفس المحادثة لرؤية كيف تباعدت المحادثة.",
        "en": "Compare two snapshots from the same thread to see how the conversation diverged.",
    },
    "replay.fork": {
        "ar": "تفريع",
        "en": "Fork",
    },
    "replay.iteration_a": {
        "ar": "التكرار أ",
        "en": "Iteration A",
    },
    "replay.iteration_b": {
        "ar": "التكرار ب",
        "en": "Iteration B",
    },
    "replay.restore": {
        "ar": "استعادة هنا",
        "en": "Restore here",
    },
    "replay.select_thread": {
        "ar": "— اختر محادثة —",
        "en": "— Select a thread —",
    },
    "replay.tab_about": {
        "ar": "حول",
        "en": "About",
    },
    "replay.tab_diff": {
        "ar": "مقارنة",
        "en": "Compare",
    },
    "replay.tab_timeline": {
        "ar": "الجدول الزمني",
        "en": "Timeline",
    },
    "replay.thread": {
        "ar": "المحادثة",
        "en": "Thread",
    },
    "replay.title": {
        "ar": "السفر عبر الزمن",
        "en": "Time Travel",
    },
    "research_no_archived": {
        "ar": "لا يوجد بحث مؤرشف.",
        "en": "No archived research.",
    },
    "research_no_results": {
        "ar": "لا توجد نتائج بحث بعد.",
        "en": "No research results yet.",
    },
    "search.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "search.loading": {
        "ar": "جارٍ البحث…",
        "en": "Searching…",
    },
    "search.no_results": {
        "ar": "لا توجد نتائج لـ “{query}”",
        "en": "No results for “{query}”",
    },
    "search.pages": {
        "ar": "الصفحات",
        "en": "Pages",
    },
    "search.pinned_hint": {
        "ar": "مثبتة في الأعلى",
        "en": "Pinned to top",
    },
    "search.placeholder": {
        "ar": "ابحث في الجلسات والصفحات…",
        "en": "Search sessions, pages…",
    },
    "search.prompt": {
        "ar": "اكتب للبحث في جلساتك وصفحاتك",
        "en": "Type to search your sessions and pages",
    },
    "search.sessions": {
        "ar": "الجلسات",
        "en": "Sessions",
    },
    "skill.desc.advanced-web-crawler": {
        "ar": "جلب صفحة ويب + بحث + تحليل مستندات محلية.",
        "en": "Single-page web fetch + search + local document parse.",
    },
    "skill.desc.arabic-bilingual-nlp": {
        "ar": "معالجة ثنائية اللغة، ترجمة، تواريخ هجرية، وتشكيل عربي.",
        "en": "Bilingual processing, translation, Hijri dates, and Arabic diacritics.",
    },
    "skill.desc.browser-automation": {
        "ar": "أتمتة متصفح بلا واجهة عبر Playwright.",
        "en": "Headless browser automation via Playwright.",
    },
    "skill.desc.calendar": {
        "ar": "قراءة وإدارة أحداث التقويم (Google / Microsoft).",
        "en": "Read and manage calendar events (Google / Microsoft).",
    },
    "skill.desc.chat-platform-dispatcher": {
        "ar": "إشعارات متعددة القنوات وبطاقات موافقة HITL.",
        "en": "Multi-channel notifications and HITL action cards.",
    },
    "skill.desc.code-analyzer-linter": {
        "ar": "تحليل ثابت وفحص وتنسيق وتشغيل pytest.",
        "en": "Static analysis, linting, formatting, and pytest.",
    },
    "skill.desc.code-review": {
        "ar": "مراجعة ملف أو فرق وإرجاع النتائج (للقراءة فقط).",
        "en": "Review a file or diff and return findings (read-only).",
    },
    "skill.desc.database-client": {
        "ar": "استخراج المخطط واستعلامات قاعدة بيانات للقراءة فقط.",
        "en": "Schema extraction and read-only database queries.",
    },
    "skill.desc.document-generator": {
        "ar": "توليد مستندات PDF وDOCX وXLSX وMarkdown.",
        "en": "Generate PDF, DOCX, XLSX, and Markdown documents.",
    },
    "skill.desc.email-manager": {
        "ar": "تكامل بريد كامل: سرد، جلب، إرسال، حذف، تحليل.",
        "en": "Full email integration: list, get, send, delete, analyze.",
    },
    "skill.desc.environment-bootstrapper": {
        "ar": "تشخيص البيئة ومثبتات الحزم.",
        "en": "Environment diagnostics and package installers.",
    },
    "skill.desc.fix-lint": {
        "ar": "تشغيل فاحص المشروع وإصلاح المشاكل المبلّغ عنها.",
        "en": "Run the project linter and fix reported issues.",
    },
    "skill.desc.git-github-manager": {
        "ar": "إدارة مستودعات git وواجهات GitHub.",
        "en": "Manage local git repositories and GitHub APIs.",
    },
    "skill.desc.refactor-file": {
        "ar": "إعادة هيكلة ملف مصدر للوضوح ثم اختبار/فحص.",
        "en": "Refactor a source file for readability, then test/lint.",
    },
    "skill.desc.secret-vault": {
        "ar": "خزنة مشفّرة لمفاتيح API وبيانات الاعتماد.",
        "en": "Encrypted vault for API keys and credentials.",
    },
    "skill.desc.system-health-monitor": {
        "ar": "موارد المضيف والعمليات وتدفق السجلات.",
        "en": "Host resources, processes, and log streaming.",
    },
    "skill.desc.task-scheduler-cron": {
        "ar": "جدولة مهام خلفية متكررة ولمرة واحدة.",
        "en": "Schedule recurring and one-shot background tasks.",
    },
    "skill.desc.tui-worker": {
        "ar": "عامل لمهام استبدال واجهة الطرفية (تنظيف، مكوّنات، اختبارات).",
        "en": "Worker for TUI replacement tasks (cleanup, components, tests).",
    },
    "skill.desc.visual-interpreter-generator": {
        "ar": "تحليل لقطات الشاشة وتوليد نماذج واجهة.",
        "en": "Screenshot analysis and UI mockup generation.",
    },
    "skill.desc.write-tests": {
        "ar": "توليد وتشغيل اختبارات وحدة لملف مصدر.",
        "en": "Generate and run unit tests for a source file.",
    },
    "skills.empty_installed": {
        "ar": "لا توجد مهارات مثبتة. تصفح المركز للعثور على مهارات.",
        "en": "No skills installed yet. Browse the Hub to find skills.",
    },
    "skills.install": {
        "ar": "تثبيت",
        "en": "Install",
    },
    "skills.install_heading": {
        "ar": "تثبيت من agentskills.io / GitHub",
        "en": "Install from agentskills.io / GitHub",
    },
    "skills.install_hint": {
        "ar": "الصق owner/repo أو رابط GitHub (مثل shadcn/improve). يستخدم صيغة Agent Skills المفتوحة — بدون Node/npm.",
        "en": "Paste owner/repo or a GitHub URL (e.g. shadcn/improve). Uses the open Agent Skills format — no Node/npm required.",
    },
    "skills.install_ph": {
        "ar": "shadcn/improve أو https://github.com/shadcn/improve",
        "en": "shadcn/improve or https://github.com/shadcn/improve",
    },
    "skills.install_skill": {
        "ar": "تثبيت مهارة",
        "en": "Install Skill",
    },
    "skills.installing": {
        "ar": "جاري التثبيت…",
        "en": "Installing…",
    },
    "skills.marketplace_empty": {
        "ar": "لا توجد مهارات مطابقة. جرّب كلمات أعم.",
        "en": "No matching skills found. Try broader terms.",
    },
    "skills.marketplace_heading": {
        "ar": "سوق المهارات المفتوح",
        "en": "Open Agent Skills Marketplace",
    },
    "skills.marketplace_hint": {
        "ar": "ابحث في المنظومة العامة (GitHub topic:agent-skills) وثبّت بنقرة واحدة. راجع ملف SKILL.md قبل التثبيت.",
        "en": "Search the public ecosystem (GitHub topic:agent-skills) and install in one click. Review a repo's SKILL.md before installing.",
    },
    "skills.marketplace_ph": {
        "ar": "ابحث عن مهارات (مثل react، تصميم، اختبار)…",
        "en": "Search skills (e.g. react, design, testing)…",
    },
    "skills.search_ph": {
        "ar": "بحث في المهارات...",
        "en": "Search skills...",
    },
    "skills.tab_hub": {
        "ar": "تصفح المركز",
        "en": "Hub Browse",
    },
    "skills.tab_installed": {
        "ar": "المثبتة",
        "en": "Installed",
    },
    "skills.tab_marketplace": {
        "ar": "السوق",
        "en": "Marketplace",
    },
    "skills.tab_validate": {
        "ar": "تحقق",
        "en": "Validate",
    },
    "skills.title": {
        "ar": "المهارات",
        "en": "Skills",
    },
    "skills.uninstall": {
        "ar": "إزالة",
        "en": "Uninstall",
    },
    "skills.validate_btn": {
        "ar": "تحقق",
        "en": "Validate",
    },
    "skills.validate_failed": {
        "ar": "فشل",
        "en": "FAILED",
    },
    "skills.validate_heading": {
        "ar": "تحقق من مهارة محلية",
        "en": "Validate Local Skill",
    },
    "skills.validate_passed": {
        "ar": "نجح",
        "en": "PASSED",
    },
    "skills.validate_path": {
        "ar": "مسار مجلد المهارة",
        "en": "Skill Directory Path",
    },
    "skills.validate_path_ph": {
        "ar": "/مسار/المهارة",
        "en": "/path/to/skill",
    },
    "voice.audio_format": {
        "ar": "صيغة الصوت",
        "en": "Audio Format",
    },
    "voice.format_flac": {
        "ar": "FLAC (بدون فقدان)",
        "en": "FLAC (lossless)",
    },
    "voice.format_mp3": {
        "ar": "MP3 (قياسي)",
        "en": "MP3 (standard)",
    },
    "voice.format_opus": {
        "ar": "Opus (محسّن جداً)",
        "en": "Opus (highly optimized)",
    },
    "voice.format_wav": {
        "ar": "WAV (غير مضغوط)",
        "en": "WAV (uncompressed)",
    },
    "voice.master_help": {
        "ar": "المفتاح الرئيسي لـ STT/TTS على تيليجرام وديسكورد وسلاك والويب. أوقفه لإيقاف كل الرسائل الصوتية والنسخ.",
        "en": "Master switch for STT/TTS on Telegram, Discord, Slack, and Web. Turn off to stop all voice notes and transcription.",
    },
    "voice.stt": {
        "ar": "تحويل الكلام إلى نص (STT)",
        "en": "Speech-to-Text (STT)",
    },
    "voice.stt_base_help": {
        "ar": "جذر OpenAI-compatible لـ Speech NIM (يجب أن يوفّر /v1/audio/transcriptions). لا تستخدم عنوان LLM.",
        "en": "OpenAI-compatible root of your Speech NIM (must expose /v1/audio/transcriptions). Do not use the LLM integrate.api.nvidia.com URL.",
    },
    "voice.stt_base_url": {
        "ar": "عنوان ASR الأساسي (Speech NIM)",
        "en": "NVIDIA ASR base URL (Speech NIM)",
    },
    "voice.stt_custom_help": {
        "ar": "اكتب أي اسم نموذج نسخ مخصص.",
        "en": "Type any specific custom transcription model name.",
    },
    "voice.stt_custom_id": {
        "ar": "معرّف نموذج STT مخصص",
        "en": "Custom STT Model ID",
    },
    "voice.stt_language": {
        "ar": "لغة STT",
        "en": "STT Language",
    },
    "voice.stt_language_help": {
        "ar": "استخدم \"auto\" أو صيغة ISO-639-1.",
        "en": "Use \"auto\" or ISO-639-1 format.",
    },
    "voice.stt_model": {
        "ar": "نموذج STT",
        "en": "STT Model",
    },
    "voice.stt_model_custom": {
        "ar": "معرّف نموذج مخصص...",
        "en": "Custom model ID...",
    },
    "voice.stt_model_empty": {
        "ar": "لا توجد نماذج — اختر مخصصاً أو غيّر المزوّد.",
        "en": "No models loaded — pick Custom or change provider.",
    },
    "voice.stt_model_loading": {
        "ar": "(جارٍ التحميل…)",
        "en": "(loading…)",
    },
    "voice.stt_nvidia_help": {
        "ar": "NVIDIA Whisper هو Speech NIM (مستضاف ذاتياً)، وليس نموذج محادثة. عيّن عنوان ASR أدناه، أو استخدم groq/openai لـ STT السحابي.",
        "en": "NVIDIA Whisper is a Speech NIM (self-hosted), not a chat model on integrate.api.nvidia.com. Set the ASR base URL below, or use groq/openai for cloud STT.",
    },
    "voice.stt_provider": {
        "ar": "مزوّد STT",
        "en": "STT Provider",
    },
    "voice.title": {
        "ar": "نظام الصوت",
        "en": "Voice Subsystem",
    },
    "voice.tts": {
        "ar": "تحويل النص إلى كلام (TTS)",
        "en": "Text-to-Speech (TTS)",
    },
    "voice.tts_custom_help": {
        "ar": "مثل معرّف صوت المزوّد أو مسار ملف محلي.",
        "en": "e.g. provider voice ID, or a local file path.",
    },
    "voice.tts_custom_id": {
        "ar": "معرّف صوت مخصص",
        "en": "Custom Voice ID",
    },
    "voice.tts_provider": {
        "ar": "مزوّد TTS",
        "en": "TTS Provider",
    },
    "voice.tts_reply_help": {
        "ar": "عند التفعيل: إذا أرسلت رسالة صوتية على تيليجرام/ديسكورد/سلاك، يرد الوكيل أيضاً بصوت. عند الإيقاف: الردود نصية فقط مع بقاء نسخ الصوت الوارد.",
        "en": "When on: if you send a voice note on Telegram/Discord/Slack, the agent also replies with a spoken voice note. When off: replies stay text-only; inbound STT still works.",
    },
    "voice.tts_reply_title": {
        "ar": "ردود صوتية تلقائية",
        "en": "Auto voice-note replies",
    },
    "voice.tts_voice": {
        "ar": "نموذج / معرّف الصوت",
        "en": "Voice Model / Voice ID",
    },
    "voice.tts_voice_custom": {
        "ar": "معرّف صوت مخصص...",
        "en": "Custom voice ID...",
    },
    "ws.task_completed": {
        "ar": "اكتملت المهمة.",
        "en": "Task completed.",
    },
    "ws.task_processing": {
        "ar": "اكتملت معالجة المهمة.",
        "en": "Task processing completed.",
    },
    "ws.tools_completed": {
        "ar": "تم التنفيذ: {tools}",
        "en": "Completed: {tools}",
    },
}
