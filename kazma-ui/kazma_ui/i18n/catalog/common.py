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
    "ide.review_title": {
        "ar": "مراجعة التعديلات",
        "en": "Review patches",
    },
    "ide.review_accept": {
        "ar": "قبول الكل",
        "en": "Accept all",
    },
    "ide.review_reject": {
        "ar": "رفض واستعادة",
        "en": "Reject and restore",
    },
    "ide.review_reject_file": {
        "ar": "رفض هذا الملف",
        "en": "Reject this file",
    },
    "ide.review_reject_hunk": {
        "ar": "رفض هذا الجزء",
        "en": "Reject hunk",
    },
    "ide.undo_patch": {
        "ar": "تراجع عن الرقعة",
        "en": "Undo patch",
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
    # Sidebar section headings. "Work", "Activity", "Capabilities" and
    # "Settings" already existed here, unused — the nav had been collapsed to
    # four links plus a "More" disclosure, so the categories were written and
    # never shown. These two complete the set.
    "nav.section_knowledge": {
        "ar": "المعرفة والذاكرة",
        "en": "Knowledge & memory",
    },
    "nav.section_automation": {
        "ar": "الوكلاء والأدوات",
        "en": "Agents & tools",
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
    "nav.x_studio": {
        "ar": "استوديو إكس",
        "en": "X Studio",
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
    "skills.builtin": {
        "ar": "مدمج في Kazma — أوقفه بالمفتاح",
        "en": "Built in — switch it off instead",
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
        "ar": "المفتاح الرئيسي لـ STT/TTS. المفاتيح من الإعدادات ← المزودون (نفس بطاقة OpenAI/Groq). هنا تختار كيف يسمع كازما ويتكلم — لا تضع Whisper كنموذج محادثة.",
        "en": "Master switch for STT/TTS. API keys come from Settings → Providers (the same OpenAI/Groq card). This tab only picks how Kazma hears and speaks — never set Whisper as the chat model.",
    },
    "voice.keys_help": {
        "ar": "مفاتيح API مشتركة مع المزودين. نماذج الكلام (Whisper) ليست نماذج محادثة — اختبار المزود على /chat/completions سيرفضها عن قصد.",
        "en": "Keys are shared with Providers. Speech models (Whisper) are not chat models — Provider Test talks to /chat/completions and will refuse them on purpose.",
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
    "voice.stt_language_code": {
        "ar": "رمز اللغة (ISO-639-1)",
        "en": "Language code (ISO-639-1)",
    },
    "voice.stt_language_help": {
        "ar": "تلقائي يترك الاكتشاف للمزوّد. العربية (ar) تفعّل مسار كوهير العربي.",
        "en": "Auto lets the provider detect. Arabic (ar) selects Cohere's Arabic STT path.",
    },
    "voice.lang_auto": {
        "ar": "تلقائي (كشف)",
        "en": "Auto (detect)",
    },
    "voice.lang_custom": {
        "ar": "رمز مخصص…",
        "en": "Custom code…",
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
    "common.menu": {
        "ar": "القائمة",
        "en": "Menu",
    },
    "common.search_shortcut": {
        "ar": "بحث (Ctrl+K)",
        "en": "Search (Ctrl+K)",
    },
    "common.notifications": {
        "ar": "الإشعارات",
        "en": "Notifications",
    },
    "common.no_system_alerts": {
        "ar": "لا توجد تنبيهات للنظام",
        "en": "No system alerts",
    },
    "common.alert": {
        "ar": "تنبيه",
        "en": "alert",
    },
    "common.switch_to_light": {
        "ar": "التبديل إلى الوضع الفاتح",
        "en": "Switch to light",
    },
    "common.switch_to_dark": {
        "ar": "التبديل إلى الوضع الداكن",
        "en": "Switch to dark",
    },
    "common.account": {
        "ar": "الحساب",
        "en": "Account",
    },
    "common.dismiss": {
        "ar": "إخفاء",
        "en": "Dismiss",
    },
    "common.open": {
        "ar": "فتح",
        "en": "Open",
    },
    "common.resolve": {
        "ar": "معالجة",
        "en": "Resolve",
    },
    "common.installing": {
        "ar": "جارٍ التثبيت…",
        "en": "Installing…",
    },
    "common.primary_navigation": {
        "ar": "التنقل الرئيسي",
        "en": "Primary navigation",
    },
    "common.close_menu": {
        "ar": "إغلاق القائمة",
        "en": "Close menu",
    },
    "common.expand_sidebar": {
        "ar": "توسيع الشريط الجانبي (Ctrl+B)",
        "en": "Expand sidebar (Ctrl+B)",
    },
    "common.collapse_sidebar": {
        "ar": "طيّ الشريط الجانبي (Ctrl+B)",
        "en": "Collapse sidebar (Ctrl+B)",
    },
    "common.agent_name": {
        "ar": "وكيل Kazma",
        "en": "Kazma Agent",
    },
    "common.no_model_selected": {
        "ar": "لم يُحدَّد نموذج",
        "en": "No model selected",
    },
    "common.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "common.ok": {
        "ar": "حسنًا",
        "en": "OK",
    },
    "common.refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "common.remove": {
        "ar": "إزالة",
        "en": "Remove",
    },
    "common.retry": {
        "ar": "إعادة المحاولة",
        "en": "Retry",
    },
    "common.approve": {
        "ar": "موافقة",
        "en": "Approve",
    },
    "common.deny": {
        "ar": "رفض",
        "en": "Deny",
    },
    "common.back": {
        "ar": "→ رجوع",
        "en": "← Back",
    },
    "common.detail": {
        "ar": "التفاصيل",
        "en": "Detail",
    },
    "common.role_admin": {
        "ar": "مسؤول",
        "en": "admin",
    },
    "common.role_operator": {
        "ar": "مشغّل",
        "en": "operator",
    },
    "common.role_viewer": {
        "ar": "مشاهد",
        "en": "viewer",
    },
    "common.error_title": {
        "ar": "خطأ {code}",
        "en": "Error {code}",
    },
    "common.error_404": {
        "ar": "الصفحة التي تبحث عنها غير موجودة أو نُقلت.",
        "en": "The page you're looking for doesn't exist or has been moved.",
    },
    "common.error_500": {
        "ar": "حدث خطأ لدينا. حاول مرة أخرى.",
        "en": "Something went wrong on our end. Please try again.",
    },
    "common.error_other": {
        "ar": "حدث خطأ غير متوقع.",
        "en": "An unexpected error occurred.",
    },
    "common.go_home": {
        "ar": "الصفحة الرئيسية",
        "en": "Go Home",
    },
    "common.go_back": {
        "ar": "رجوع",
        "en": "Go Back",
    },
    "common.input": {
        "ar": "إدخال",
        "en": "Input",
    },
    "common.notice": {
        "ar": "تنبيه",
        "en": "Notice",
    },
    "common.not_found": {
        "ar": "الصفحة غير موجودة",
        "en": "Page not found",
    },
    "common.server_error": {
        "ar": "خطأ داخلي في الخادم",
        "en": "Internal server error",
    },
    "replay.unavailable_option": {
        "ar": "السفر عبر الزمن غير متاح (الواجهة {status})",
        "en": "Time travel unavailable (API {status})",
    },
    "replay.unavailable_body": {
        "ar": "السفر عبر الزمن غير متاح على هذا الخادم (أعادت واجهة الإعادة {status}). ابحث في سجل الخادم عن \"[Replay] snapshot recorder creation failed\" ثم أعد تشغيل الخادم.",
        "en": "Time travel is unavailable on this server (replay API returned {status}). Check the server log for \"[Replay] snapshot recorder creation failed\" and restart the server.",
    },
    "replay.pick_thread_hint": {
        "ar": "اختر محادثة في الأعلى لعرض الجدول الزمني للقطاتها.",
        "en": "Select a thread above to see its snapshot timeline.",
    },
    "replay.snapshot_count": {
        "ar": "اللقطات: {n}",
        "en": "Snapshots: {n}",
    },
    "replay.iteration_n": {
        "ar": "التكرار {n}",
        "en": "Iteration {n}",
    },
    "replay.no_snapshots": {
        "ar": "لا توجد لقطات لهذه المحادثة بعد. تُلتقط اللقطات بعد كل دور للوكيل.",
        "en": "No snapshots for this thread yet. Snapshots are captured after each agent turn.",
    },
    "replay.messages_n": {
        "ar": "الرسائل: {n}",
        "en": "Messages: {n}",
    },
    "replay.load_failed": {
        "ar": "تعذّر التحميل: {error}",
        "en": "Failed to load: {error}",
    },
    "replay.snapshot_failed": {
        "ar": "تعذّر تحميل اللقطة",
        "en": "Could not load snapshot",
    },
    "replay.detail_failed": {
        "ar": "تعذّر تحميل تفاصيل اللقطة",
        "en": "Failed to load snapshot detail",
    },
    "replay.meta_model": {
        "ar": "النموذج",
        "en": "Model",
    },
    "replay.meta_cost": {
        "ar": "التكلفة",
        "en": "Cost",
    },
    "replay.role_user": {
        "ar": "المستخدم",
        "en": "user",
    },
    "replay.role_assistant": {
        "ar": "المساعد",
        "en": "assistant",
    },
    "replay.role_tool": {
        "ar": "أداة",
        "en": "tool",
    },
    "replay.role_system": {
        "ar": "النظام",
        "en": "system",
    },
    "replay.pick_snapshot": {
        "ar": "اختر لقطة أولًا",
        "en": "Select a snapshot first",
    },
    "replay.confirm_restore": {
        "ar": "إرجاع هذه المحادثة إلى التكرار {n}؟ ستضيع الأدوار اللاحقة (استخدم التفريع للاحتفاظ بها).",
        "en": "Rewind this thread to iteration {n}? Later turns will be lost (use Fork to preserve them).",
    },
    "replay.restore_failed": {
        "ar": "فشلت الاستعادة: {error}",
        "en": "Restore failed: {error}",
    },
    "replay.restored": {
        "ar": "استُعيد التكرار {n} ({count} رسالة)",
        "en": "Restored iteration {n} ({count} messages)",
    },
    "replay.restore_request_failed": {
        "ar": "فشل طلب الاستعادة",
        "en": "Restore request failed",
    },
    "replay.fork_failed": {
        "ar": "فشل التفريع: {error}",
        "en": "Fork failed: {error}",
    },
    "replay.forked": {
        "ar": "فُرّعت إلى {thread}",
        "en": "Forked into {thread}",
    },
    "replay.fork_request_failed": {
        "ar": "فشل طلب التفريع",
        "en": "Fork request failed",
    },
    "replay.pick_thread_first": {
        "ar": "اختر محادثة أولًا",
        "en": "Select a thread first",
    },
    "replay.pick_two": {
        "ar": "اختر تكرارين",
        "en": "Pick two iterations",
    },
    "replay.comparing": {
        "ar": "جارٍ المقارنة…",
        "en": "Comparing…",
    },
    "replay.no_diff": {
        "ar": "لا توجد فروق متاحة.",
        "en": "No diff available.",
    },
    "replay.col_metric": {
        "ar": "المقياس",
        "en": "Metric",
    },
    "replay.col_delta": {
        "ar": "الفرق",
        "en": "Delta",
    },
    "replay.row_messages": {
        "ar": "الرسائل",
        "en": "Messages",
    },
    "replay.row_iteration": {
        "ar": "رقم التكرار",
        "en": "Iteration #",
    },
    "replay.row_cost": {
        "ar": "التكلفة (USD)",
        "en": "Cost (USD)",
    },
    "replay.row_tool_calls": {
        "ar": "استدعاءات الأدوات",
        "en": "Tool calls",
    },
    "replay.row_next_node": {
        "ar": "العقدة التالية",
        "en": "Next node",
    },
    "replay.changed": {
        "ar": "تغيّر",
        "en": "changed",
    },
    "replay.same": {
        "ar": "بلا تغيير",
        "en": "same",
    },
    "replay.identical": {
        "ar": "الحالتان متطابقتان.",
        "en": "States are identical.",
    },
    "replay.compare_failed": {
        "ar": "فشلت المقارنة",
        "en": "Compare failed",
    },
    "replay.snapshot_detail": {
        "ar": "تفاصيل اللقطة",
        "en": "Snapshot detail",
    },
    "mcp.server_removed": {
        "ar": "أُزيل الخادم",
        "en": "Server removed",
    },
    "common.request_failed": {
        "ar": "فشل الطلب",
        "en": "Request failed",
    },
    "skills.cert_native": {
        "ar": "مدمجة",
        "en": "native",
    },
    "skills.cert_verified": {
        "ar": "موثّقة",
        "en": "verified",
    },
    "skills.cert_community": {
        "ar": "مجتمعية",
        "en": "community",
    },
    "skills.cert_unverified": {
        "ar": "غير موثّقة",
        "en": "unverified",
    },
    "skills.score": {
        "ar": "الدرجة: {n}/100",
        "en": "Score: {n}/100",
    },
    "skills.integrity_verified": {
        "ar": "التوقيع موثّق",
        "en": "Signature verified",
    },
    "skills.integrity_unsigned": {
        "ar": "غير موقّعة: تُحمَّل مع تحذير",
        "en": "Unsigned: loads with a warning",
    },
    "skills.integrity_refused": {
        "ar": "يُرفض تفعيلها",
        "en": "Refused at activation",
    },
    "ide.new_file_title": {
        "ar": "إنشاء ملف جديد",
        "en": "Create a new file",
    },
    "ide.delete_file_title": {
        "ar": "حذف الملف المفتوح",
        "en": "Delete the open file",
    },
    "ide.run_skill_title": {
        "ar": "تشغيل مهارة البرمجة المختارة عبر السرب",
        "en": "Run selected coding skill via the swarm",
    },
    "ide.toggle_chat_title": {
        "ar": "إظهار لوحة المحادثة الذكية أو إخفاؤها",
        "en": "Toggle the AI chat panel",
    },
    "ide.root": {
        "ar": "(الجذر)",
        "en": "(root)",
    },
    "ide.empty": {
        "ar": "فارغ",
        "en": "Empty",
    },
    "ide.drag_resize": {
        "ar": "اسحب لتغيير الحجم",
        "en": "Drag to resize",
    },
    "ide.editor": {
        "ar": "المحرر",
        "en": "Editor",
    },
    "ide.plain_file": {
        "ar": "ملف عادي",
        "en": "plain file",
    },
    "ide.editor_features": {
        "ar": "أرقام الأسطر · تلوين الصياغة",
        "en": "line numbers · syntax",
    },
    "ide.close_tab": {
        "ar": "إغلاق التبويب",
        "en": "Close tab",
    },
    "ide.open_file_ph": {
        "ar": "افتح ملفًا من الشجرة…",
        "en": "Open a file from the tree…",
    },
    "ide.command_ph": {
        "ar": "أمر الطرفية (مثل pytest -q)",
        "en": "Shell command (e.g. pytest -q)",
    },
    "ide.run_command": {
        "ar": "تشغيل الأمر",
        "en": "Run command",
    },
    "ide.grep_ph": {
        "ar": "نمط البحث",
        "en": "Grep pattern",
    },
    "ide.glob_ph": {
        "ar": "نمط الملفات (*.py)",
        "en": "glob (*.py)",
    },
    "ide.grep": {
        "ar": "بحث",
        "en": "Grep",
    },
    "ide.swarm_ph": {
        "ar": "أرسل تعليمة إلى السرب…",
        "en": "Send instruction to swarm…",
    },
    "ide.output": {
        "ar": "المخرجات",
        "en": "Output",
    },
    "ide.clear": {
        "ar": "مسح",
        "en": "Clear",
    },
    "ide.no_output": {
        "ar": "(لا مخرجات بعد)",
        "en": "(no output yet)",
    },
    "ide.ai_chat": {
        "ar": "المحادثة الذكية",
        "en": "AI Chat",
    },
    "ide.clear_chat_title": {
        "ar": "مسح المحادثة",
        "en": "Clear conversation",
    },
    "ide.close_panel_title": {
        "ar": "إغلاق اللوحة",
        "en": "Close panel",
    },
    "ide.you": {
        "ar": "أنت",
        "en": "You",
    },
    "ide.thinking": {
        "ar": "يفكّر…",
        "en": "thinking…",
    },
    "ide.result": {
        "ar": "النتيجة",
        "en": "result",
    },
    "ide.approval_required": {
        "ar": "مطلوب موافقة",
        "en": "Approval required",
    },
    "ide.tool": {
        "ar": "الأداة: {tool}",
        "en": "Tool: {tool}",
    },
    "ide.approve_hint": {
        "ar": "وافق عبر المحادثة أو Telegram/Discord.",
        "en": "Approve via the chat or Telegram/Discord.",
    },
    "ide.chat_ph": {
        "ar": "اسأل عن الملف المفتوح أو اطلب تعديلات… (Enter للإرسال، Shift+Enter لسطر جديد)",
        "en": "Ask about the open file, request edits… (Enter to send, Shift+Enter for newline)",
    },
    "ide.welcome": {
        "ar": "اسأل عن الملف المفتوح أو اطلب تعديلات أو شغّل أوامر. يعرف الوكيل مساحة عملك ومستودعك وأدواتك.",
        "en": "Ask about the open file, request edits, or run commands. The agent knows your workspace, repo, and tools.",
    },
    "ide.toast_skill_dispatched": {
        "ar": "أُرسلت {skill}",
        "en": "{skill} dispatched",
    },
    "ide.toast_skill_failed": {
        "ar": "فشلت المهارة",
        "en": "Skill failed",
    },
    "ide.res_skill": {
        "ar": "المهارة: {skill}",
        "en": "Skill: {skill}",
    },
    "ide.task_id": {
        "ar": "معرّف المهمة: {id}",
        "en": "Task ID: {id}",
    },
    "ide.unknown": {
        "ar": "(غير معروف)",
        "en": "(unknown)",
    },
    "ide.unknown_error": {
        "ar": "خطأ غير معروف",
        "en": "Unknown error",
    },
    "ide.toast_list_failed": {
        "ar": "تعذّر عرض الملفات",
        "en": "Failed to list files",
    },
    "ide.res_read_failed": {
        "ar": "فشلت القراءة",
        "en": "Read failed",
    },
    "ide.toast_open_failed": {
        "ar": "فشل الفتح",
        "en": "Open failed",
    },
    "ide.toast_new_file": {
        "ar": "ملف جديد — اضغط «حفظ» لإنشائه",
        "en": "New file — press Save to create it",
    },
    "ide.toast_deleted": {
        "ar": "حُذف {path}",
        "en": "Deleted {path}",
    },
    "ide.res_delete_failed": {
        "ar": "فشل الحذف",
        "en": "Delete failed",
    },
    "ide.toast_delete_pending": {
        "ar": "فشل الحذف (قد تكون الموافقة معلّقة)",
        "en": "Delete failed (approval may be pending)",
    },
    "ide.toast_saved": {
        "ar": "حُفظ {path}",
        "en": "Saved {path}",
    },
    "ide.res_save": {
        "ar": "حفظ",
        "en": "Save",
    },
    "ide.ok": {
        "ar": "تم",
        "en": "OK",
    },
    "ide.res_save_failed": {
        "ar": "فشل الحفظ",
        "en": "Save failed",
    },
    "ide.toast_save_pending": {
        "ar": "فشل الحفظ (قد تكون الموافقة معلّقة)",
        "en": "Save failed (approval may be pending)",
    },
    "ide.toast_hunk_restored": {
        "ar": "استُعيد الجزء",
        "en": "Hunk restored",
    },
    "ide.toast_hunk_failed": {
        "ar": "فشلت استعادة الجزء",
        "en": "Hunk restore failed",
    },
    "ide.toast_restored_path": {
        "ar": "استُعيد {path}",
        "en": "Restored {path}",
    },
    "ide.res_restore_failed": {
        "ar": "فشلت الاستعادة",
        "en": "Restore failed",
    },
    "ide.toast_restored_checkpoint": {
        "ar": "استُعيدت نقطة الحفظ",
        "en": "Restored checkpoint",
    },
    "ide.toast_no_checkpoints": {
        "ar": "لا توجد نقاط حفظ بعد",
        "en": "No checkpoints yet",
    },
    "ide.res_restore": {
        "ar": "استعادة",
        "en": "Restore",
    },
    "ide.res_run": {
        "ar": "تشغيل: {path}",
        "en": "Run: {path}",
    },
    "ide.toast_run_failed": {
        "ar": "فشل التشغيل",
        "en": "Run failed",
    },
    "ide.toast_command_failed": {
        "ar": "فشل الأمر",
        "en": "Command failed",
    },
    "ide.clean": {
        "ar": "(نظيف)",
        "en": "(clean)",
    },
    "ide.toast_git_failed": {
        "ar": "فشل Git",
        "en": "Git failed",
    },
    "ide.res_diff": {
        "ar": "الفروقات: {path}",
        "en": "Diff: {path}",
    },
    "ide.no_changes": {
        "ar": "(لا تغييرات)",
        "en": "(no changes)",
    },
    "ide.toast_diff_failed": {
        "ar": "فشل عرض الفروقات",
        "en": "Diff failed",
    },
    "ide.res_grep": {
        "ar": "بحث: {pattern}",
        "en": "Grep: {pattern}",
    },
    "ide.no_matches": {
        "ar": "(لا نتائج)",
        "en": "(no matches)",
    },
    "ide.toast_grep_failed": {
        "ar": "فشل البحث",
        "en": "Grep failed",
    },
    "ide.res_swarm_dispatched": {
        "ar": "أُرسل إلى السرب",
        "en": "Swarm dispatched",
    },
    "ide.toast_sent_swarm": {
        "ar": "أُرسل إلى السرب",
        "en": "Sent to swarm",
    },
    "ide.res_swarm_failed": {
        "ar": "فشل السرب",
        "en": "Swarm failed",
    },
    "ide.toast_swarm_dispatch_failed": {
        "ar": "فشل الإرسال إلى السرب",
        "en": "Swarm dispatch failed",
    },
    "ide.toast_no_streaming": {
        "ar": "بثّ المحادثة غير متاح (لم يُحمَّل streaming.js)",
        "en": "Chat streaming unavailable (streaming.js not loaded)",
    },
    "ide.toast_file_deleted": {
        "ar": "حُذف {path}",
        "en": "{path} was deleted",
    },
    "ide.toast_open_deleted": {
        "ar": "حُذف الملف المفتوح",
        "en": "Open file was deleted",
    },
    "ide.toast_updated": {
        "ar": "حُدّث {path}",
        "en": "Updated {path}",
    },
    "ide.toast_changed_on_disk": {
        "ar": "تغيّر {path} على القرص — احفظ أو تجاهل للتحديث",
        "en": "{path} changed on disk — save or discard to refresh",
    },
    "ide.toast_modified": {
        "ar": "عُدّل {path} — انتقل إليه وأعد التحميل",
        "en": "{path} was modified — switch to it and reload",
    },
    "voice.ui.transcribed": {
        "ar": "نُسخ: «{text}...»",
        "en": "Transcribed: \"{text}...\"",
    },
    "voice.ui.please_stop_live_voice_mode": {
        "ar": "أوقف وضع الصوت المباشر أولًا",
        "en": "Please stop Live Voice Mode first",
    },
    "voice.ui.hold_the_mic_to_record": {
        "ar": "اضغط مطوّلًا على الميكروفون للتسجيل",
        "en": "Hold the mic to record",
    },
    "voice.ui.microphone_access_denied_please_allow": {
        "ar": "رُفض الوصول إلى الميكروفون. اسمح بالوصول إليه.",
        "en": "Microphone access denied. Please allow microphone access.",
    },
    "voice.ui.transcribing": {
        "ar": "جارٍ التفريغ…",
        "en": "Transcribing...",
    },
    "voice.ui.transcription_failed": {
        "ar": "فشل التفريغ: ",
        "en": "Transcription failed: ",
    },
    "voice.ui.no_speech_detected": {
        "ar": "لم يُكتشف كلام",
        "en": "No speech detected",
    },
    "voice.ui.transcription_request_failed": {
        "ar": "فشل طلب التفريغ",
        "en": "Transcription request failed",
    },
    "voice.ui.voice_output_unavailable_tts_not": {
        "ar": "إخراج الصوت غير متاح (تحويل النص إلى كلام غير مُعدّ) — صامت حتى /voice on.",
        "en": "Voice output unavailable (TTS not configured) — silenced until /voice on.",
    },
    "voice.ui.voice_replies_enabled": {
        "ar": "فُعّلت الردود الصوتية",
        "en": "Voice replies enabled",
    },
    "voice.ui.voice_replies_disabled": {
        "ar": "عُطّلت الردود الصوتية",
        "en": "Voice replies disabled",
    },
    "voice.ui.stt_provider_set_to": {
        "ar": "مزوّد تحويل الكلام إلى نص: ",
        "en": "STT provider set to: ",
    },
    "voice.ui.tts_provider_set_to": {
        "ar": "مزوّد تحويل النص إلى كلام: ",
        "en": "TTS provider set to: ",
    },
    "voice.ui.starting_live_streaming_mode": {
        "ar": "جارٍ بدء وضع البث المباشر…",
        "en": "Starting live streaming mode...",
    },
    "voice.ui.live_mode_stopped": {
        "ar": "توقف الوضع المباشر",
        "en": "Live mode stopped",
    },
    "voice.ui.live_voice_mode_active_speak": {
        "ar": "وضع الصوت المباشر نشط — تحدّث؛ يمكنك المقاطعة",
        "en": "Live voice mode active — speak; you can interrupt",
    },
    "voice.ui.voice_connection_error": {
        "ar": "خطأ في اتصال الصوت",
        "en": "Voice connection error",
    },
    "voice.ui.cannot_access_microphone_for_streaming": {
        "ar": "تعذّر الوصول إلى الميكروفون للبث",
        "en": "Cannot access microphone for streaming",
    },
    "voice.ui.microphone_capture_failed": {
        "ar": "فشل التقاط الميكروفون",
        "en": "Microphone capture failed",
    },
    "voice.ui.listening": {
        "ar": "جارٍ الاستماع…",
        "en": "Listening...",
    },
    "voice.ui.tool": {
        "ar": "أداة: ",
        "en": "Tool: ",
    },
    "voice.ui.approval_needed_answer_the_card": {
        "ar": "مطلوب موافقة — أجب على البطاقة في المحادثة للمتابعة",
        "en": "Approval needed — answer the card in chat to continue",
    },
    "voice.ui.voice_error": {
        "ar": "خطأ صوتي: ",
        "en": "Voice error: ",
    },
    "voice.ui.voice_config_updated": {
        "ar": "حُدّثت إعدادات الصوت",
        "en": "Voice config updated",
    },
    "voice.ui.duplex_livekit_webrtc_brain_is": {
        "ar": "ثنائي الاتجاه: LiveKit WebRTC (العقل ما زال Kazma)",
        "en": "Duplex: LiveKit WebRTC (brain is still Kazma)",
    },
    "voice.ui.live_voice_mode_stopped": {
        "ar": "توقف وضع الصوت المباشر",
        "en": "Live voice mode stopped",
    },
    # /voice status and the live button's title (2026-10-01): English in
    # every language until then.
    "voice.ui.status_line": {
        "ar": "تحويل الكلام إلى نص: {stt} | تحويل النص إلى كلام: {tts} | الردود المسموعة: {replies}",
        "en": "STT: {stt} | TTS: {tts} | Spoken replies: {replies}",
    },
    "voice.ui.replies_on": {
        "ar": "مفعّلة",
        "en": "on",
    },
    "voice.ui.replies_off": {
        "ar": "متوقفة",
        "en": "off",
    },
    "voice.ui.status_live": {
        "ar": "الوضع المباشر يعمل",
        "en": "live mode on",
    },
    "voice.ui.live_stop_title": {
        "ar": "أوقف الصوت المباشر",
        "en": "Stop live voice",
    },
    "ide.dlg.close_tab_title": {
        "ar": "إغلاق التبويب",
        "en": "Close tab",
    },
    "ide.dlg.close_tab_message": {
        "ar": "في «{name}» تغييرات غير محفوظة. أتغلقه على أي حال؟",
        "en": "\"{name}\" has unsaved changes. Close anyway?",
    },
    "ide.dlg.close": {
        "ar": "إغلاق",
        "en": "Close",
    },
    "ide.dlg.new_file_title": {
        "ar": "ملف جديد",
        "en": "New file",
    },
    "ide.dlg.new_file_message": {
        "ar": "المسار (بالنسبة إلى جذر مساحة العمل)",
        "en": "Path (relative to workspace root)",
    },
    "ide.dlg.new_file_placeholder": {
        "ar": "مثلًا src/new_module.py",
        "en": "e.g. src/new_module.py",
    },
    "ide.dlg.create": {
        "ar": "إنشاء",
        "en": "Create",
    },
    "ide.dlg.delete_file_title": {
        "ar": "حذف الملف",
        "en": "Delete file",
    },
    "ide.dlg.delete_file_message": {
        "ar": "حذف «{path}»؟\nلا يمكن التراجع عن ذلك.",
        "en": "Delete \"{path}\"?\nThis cannot be undone.",
    },
    "ide.dlg.reject_file_title": {
        "ar": "رفض هذا الملف",
        "en": "Reject this file",
    },
    "ide.dlg.reject_file_message": {
        "ar": "استعادة هذا الملف من نقطة الحفظ السابقة للتعديل؟",
        "en": "Restore this file from the pre-patch checkpoint?",
    },
    "ide.dlg.restore_file": {
        "ar": "استعادة الملف",
        "en": "Restore file",
    },
    "ide.dlg.reject_patches_title": {
        "ar": "رفض التعديلات",
        "en": "Reject patches",
    },
    "ide.dlg.reject_patches_message": {
        "ar": "استعادة نقطة الحفظ السابقة للتعديل؟ سيكتب هذا فوق الملفات على القرص.",
        "en": "Restore the pre-patch checkpoint? This overwrites files on disk.",
    },
    "ide.dlg.restore": {
        "ar": "استعادة",
        "en": "Restore",
    },
    "ide.dlg.restore_checkpoint_title": {
        "ar": "استعادة نقطة الحفظ",
        "en": "Restore checkpoint",
    },
    "ide.dlg.restore_checkpoint_message": {
        "ar": "استعادة آخر نقطة حفظ لملفات مساحة العمل؟ سيكتب هذا فوق الملفات على القرص.",
        "en": "Restore the last workspace file checkpoint? This overwrites files on disk.",
    },
    "ide.dlg.stream_error": {
        "ar": "خطأ في البث",
        "en": "Stream error",
    },
    "mcp.ui.oauth_failed": {
        "ar": "فشل تسجيل الدخول عبر OAuth",
        "en": "OAuth login failed",
    },
    "mcp.ui.test_failed_detail": {
        "ar": "فشل اختبار الاتصال: {error}",
        "en": "Connection test failed: {error}",
    },
    "mcp.ui.rewrite_npm": {
        "ar": "حُوِّل \"npm install {pkg}\" إلى أمر التشغيل \"npx -y {pkg}\" (يثبّت npm install الحزمة فقط ولا يشغّل خادم MCP).",
        "en": "Rewrote \"npm install {pkg}\" to the RUN command \"npx -y {pkg}\" (npm install only installs the package; it does not start the MCP server).",
    },
    "mcp.ui.rewrite_pip": {
        "ar": "حُوِّل \"pip install {pkg}\" إلى أمر التشغيل \"python -m {mod}\".",
        "en": "Rewrote \"pip install {pkg}\" to the RUN command \"python -m {mod}\".",
    },
    "mcp.ui.rewrite_pipx": {
        "ar": "حُوِّل \"pipx install {pkg}\" إلى \"pipx run {pkg}\".",
        "en": "Rewrote \"pipx install {pkg}\" to \"pipx run {pkg}\".",
    },
    "mcp.ui.no_error_detail": {
        "ar": "لا تفاصيل للخطأ",
        "en": "no error detail",
    },
    "mcp.ui.start_failed": {
        "ar": "فشل: {error}",
        "en": "Failed: {error}",
    },
    "mcp.ui.unable_to_start": {
        "ar": "تعذّر تشغيل الخادم",
        "en": "Unable to start server",
    },
    "mcp.ui.unable_to_stop": {
        "ar": "تعذّر إيقاف الخادم",
        "en": "Unable to stop server",
    },
    "mcp.ui.test_failed": {
        "ar": "فشل الاختبار: {error}",
        "en": "Test failed: {error}",
    },
    "mcp.ui.no_detail": {
        "ar": "لا تفاصيل",
        "en": "no detail",
    },
    "mcp.ui.unknown_error": {
        "ar": "خطأ غير معروف",
        "en": "unknown error",
    },
    "common.ui.model_not_switched": {
        "ar": "لم يُبدَّل النموذج: {error}",
        "en": "Model not switched: {error}",
    },
    "common.ui.st_install_started": {
        "ar": "بدأ تثبيت sentence-transformers في الخلفية",
        "en": "Installation of sentence-transformers started asynchronously",
    },
    "common.ui.install_start_failed": {
        "ar": "تعذّر بدء التثبيت",
        "en": "Failed to start installation",
    },
    "common.ui.copied": {
        "ar": "نُسخ إلى الحافظة",
        "en": "Copied to clipboard",
    },
    "common.ui.copy_failed": {
        "ar": "فشل النسخ",
        "en": "Failed to copy",
    },
    "common.ui.messages_count": {
        "ar": "الرسائل: {n}",
        "en": "{n} msgs",
    },
    "login.page_title": {
        "ar": "Kazma — تسجيل الدخول",
        "en": "Kazma — Login",
    },
    "login.secret_placeholder": {
        "ar": "قيمة KAZMA_SECRET",
        "en": "KAZMA_SECRET value",
    },
    "login.failed": {
        "ar": "فشل تسجيل الدخول",
        "en": "Login failed",
    },
    "login.network_error": {
        "ar": "خطأ في الشبكة — حاول مرة أخرى",
        "en": "Network error — try again",
    },
    "mcp.command_hint": {
        "ar": "الصق أمر <strong>التشغيل</strong> (عادةً <code>npx -y &lt;pkg&gt;</code>)، لا <code>npm install</code>. كثير من الوثائق تذكر <code>npm install</code> — ويحوّله Kazma تلقائيًا إلى <code>npx -y</code>.",
        "en": "Paste the <strong>run</strong> command (usually <code>npx -y &lt;pkg&gt;</code>), not <code>npm install</code>. Common docs say <code>npm install</code> — Kazma auto-rewrites that to <code>npx -y</code>.",
    },
    "mcp.bearer_placeholder": {
        "ar": "رمز bearer اختياري",
        "en": "optional bearer token",
    },
    "mcp.env_value_placeholder": {
        "ar": "القيمة",
        "en": "value",
    },
    "mcp.add_variable": {
        "ar": "+ إضافة متغير",
        "en": "+ Add variable",
    },
    "mcp.env_hint": {
        "ar": "تحتاج معظم خوادم MCP إلى مفتاح API — راجع وثائق الخادم لمعرفة اسم متغير البيئة.",
        "en": "Most MCP servers need an API key — check the server's docs for the env var name.",
    },
    "mcp.validating": {
        "ar": "جارٍ التحقق…",
        "en": "Validating…",
    },
    "mcp.ui.name_required": {
        "ar": "اسم الخادم مطلوب.",
        "en": "Server name is required.",
    },
    "mcp.ui.command_required": {
        "ar": "الأمر مطلوب لنقل stdio.",
        "en": "Command is required for stdio transport.",
    },
    "mcp.ui.zero_tools": {
        "ar": "اتصل الخادم لكنه لم يعرض أي أداة. يعني هذا عادةً أن اسم الحزمة خاطئ أو أن الخادم فشل في التهيئة. لن يُحفظ.",
        "en": "Server connected but exposed 0 tools. This usually means the package name is wrong or the server failed to initialise. Not saving.",
    },
    "mcp.ui.zero_tools_toast": {
        "ar": "لا أدوات — لم يُحفظ الخادم",
        "en": "0 tools — server not saved",
    },
    "mcp.ui.validate_failed": {
        "ar": "تعذّر التحقق من الخادم: {error}",
        "en": "Could not validate server: {error}",
    },
    "mcp.ui.save_failed_unknown": {
        "ar": "فشل الحفظ (سبب غير معروف)",
        "en": "Save failed (unknown reason)",
    },
    "mcp.ui.save_failed": {
        "ar": "فشل الحفظ: {error}",
        "en": "Save failed: {error}",
    },
    "mcp.cat_ai": {
        "ar": "الذكاء الاصطناعي",
        "en": "AI",
    },
    "mcp.cat_arabic": {
        "ar": "العربية",
        "en": "Arabic",
    },
    "mcp.cat_code": {
        "ar": "البرمجة",
        "en": "Code",
    },
    "mcp.cat_communication": {
        "ar": "التواصل",
        "en": "Communication",
    },
    "mcp.cat_data": {
        "ar": "البيانات",
        "en": "Data",
    },
    "mcp.cat_database": {
        "ar": "قواعد البيانات",
        "en": "Databases",
    },
    "mcp.cat_devops": {
        "ar": "التشغيل والنشر",
        "en": "DevOps",
    },
    "mcp.cat_filesystem": {
        "ar": "الملفات",
        "en": "Files",
    },
    "mcp.cat_finance": {
        "ar": "المال",
        "en": "Finance",
    },
    "mcp.cat_media": {
        "ar": "الوسائط",
        "en": "Media",
    },
    "mcp.cat_productivity": {
        "ar": "الإنتاجية",
        "en": "Productivity",
    },
    "mcp.cat_web": {
        "ar": "الويب",
        "en": "Web",
    },
    "mcp.cat_general": {
        "ar": "عام",
        "en": "General",
    },
}
