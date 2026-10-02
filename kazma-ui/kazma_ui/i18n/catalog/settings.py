"""``settings`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "settings.about_me_title": {
        "ar": "عني",
        "en": "About me",
    },
    "settings.about_me_hint": {
        "ar": "ما تكتبه هنا يقرؤه Kazma في بداية كل رد: اسمك، عملك، مكانك، وكيف تحب أن يجيبك. يكتبه أنت فقط، ولا يستنتجه Kazma؛ وما تقوله في المحادثة لاحقًا يتقدم عليه.",
        "en": "Kazma reads this at the start of every reply: your name, your work, where you are, how you like to be answered. Only you write it; Kazma never infers it, and what you say later in a chat wins over it.",
    },
    "settings.about_me_placeholder": {
        "ar": "مثال: اسمي سارة، مديرة منتجات في دبي. أفضّل الإجابات المختصرة، وبالعربية إذا كتبت بالعربية.",
        "en": "e.g. I'm Sara, a product manager in Dubai. I prefer short answers, and Arabic when I write in Arabic.",
    },
    "settings.about_me_save": {
        "ar": "حفظ",
        "en": "Save About me",
    },
    "settings.about_me_saved": {
        "ar": "تم حفظ «عني»",
        "en": "About me saved",
    },
    "settings.accent_color": {
        "ar": "لون التمييز",
        "en": "Accent Color",
    },
    "settings.active_model": {
        "ar": "النموذج النشط",
        "en": "Active Model",
    },
    "settings.active_sessions": {
        "ar": "الجلسات النشطة",
        "en": "Active Sessions",
    },
    "settings.add_connector": {
        "ar": "إضافة موصل",
        "en": "Add Connector",
    },
    "settings.add_provider": {
        "ar": "إضافة مزود",
        "en": "Add Provider",
    },
    "settings.add_server": {
        "ar": "إضافة خادم",
        "en": "Add Server",
    },
    "settings.add_tenant": {
        "ar": "إضافة مستأجر",
        "en": "Add tenant",
    },
    "settings.add_user": {
        "ar": "إضافة مستخدم",
        "en": "Add user",
    },
    "settings.adding": {
        "ar": "جاري الإضافة…",
        "en": "Adding…",
    },
    "settings.agent_config": {
        "ar": "إعدادات الوكيل",
        "en": "Agent Configuration",
    },
    "settings.agent_name": {
        "ar": "اسم الوكيل",
        "en": "Agent Name",
    },
    "settings.all_none": {
        "ar": "الكل/لا شيء",
        "en": "All/None",
    },
    "settings.allowed_user_ids": {
        "ar": "معرّفات المستخدمين المسموح لهم (مفصولة بفواصل)",
        "en": "Allowed User IDs (comma-separated)",
    },
    "settings.api_key": {
        "ar": "مفتاح API",
        "en": "API Key",
    },
    "settings.api_tokens": {
        "ar": "رموز API",
        "en": "API Tokens",
    },
    "settings.app_token_xapp": {
        "ar": "رمز التطبيق (xapp-...)",
        "en": "App Token (xapp-...)",
    },
    "settings.approval_timeout_seconds": {
        "ar": "مهلة الموافقة (ثانية)",
        "en": "Approval Timeout (seconds)",
    },
    "settings.approval_timeout_hint": {
        "ar": "الافتراضي 300 ثانية. أقل من دقيقة يولّد بطاقات رفض متتالية إذا ابتعدت.",
        "en": "Default 300 seconds. Under a minute mints a deny-retry card storm if you step away.",
    },
    "settings.soul_requires_confirm": {
        "ar": "تأكيد دلتا الروح قبل التطبيق",
        "en": "Confirm Soul deltas before apply",
    },
    "settings.soul_requires_confirm_hint": {
        "ar": "عند التفعيل تُحفظ تحسينات الموجّه حتى تؤكدها أو ترفضها هنا. يُفعَّل تلقائياً في وضع الإنتاج / متعدد المستخدمين.",
        "en": "When on, supervisor Soul refinements wait here until you confirm or reject. Auto-on in production / multi-user.",
    },
    "settings.soul_pending": {
        "ar": "دلتا الروح بانتظار التأكيد",
        "en": "Pending Soul deltas",
    },
    "settings.soul_confirm": {
        "ar": "تأكيد",
        "en": "Confirm",
    },
    "settings.soul_reject": {
        "ar": "رفض",
        "en": "Reject",
    },
    "settings.soul_confirmed": {
        "ar": "تم تأكيد دلتا الروح.",
        "en": "Soul delta confirmed.",
    },
    "settings.soul_rejected": {
        "ar": "رُفضت دلتا الروح.",
        "en": "Soul delta rejected.",
    },
    "settings.safety_saved": {
        "ar": "حُفظت إعدادات السلامة",
        "en": "Safety settings saved",
    },
    "settings.save_failed": {
        "ar": "فشل الحفظ",
        "en": "Save failed",
    },
    "settings.appearance_saved": {
        "ar": "حُفظ المظهر",
        "en": "Appearance saved",
    },
    "settings.agent_saved": {
        "ar": "حُفظت إعدادات الوكيل",
        "en": "Agent settings saved",
    },
    "settings.context_saved": {
        "ar": "حُفظت إعدادات السياق",
        "en": "Context settings saved",
    },
    "settings.arabic": {
        "ar": "العربية",
        "en": "Arabic",
    },
    "settings.auto": {
        "ar": "تلقائي",
        "en": "Auto",
    },
    "settings.auto_deny_on_timeout": {
        "ar": "رفض تلقائي عند انتهاء المهلة",
        "en": "Auto-deny on Timeout",
    },
    "settings.backend_label": {
        "ar": "الخلفية:",
        "en": "Backend:",
    },
    "settings.backup_desc": {
        "ar": "ينسخ احتياطياً كل بيانات كاظمه: كل قواعد البيانات، سجل المحادثات، المعتقدات، المتجهات، الإعدادات، المستندات، مساحة العمل، و Postgres.",
        "en": "Backs up ALL Kazma data: every database, chat history, beliefs, vectors, settings, documents, workspace, and Postgres. Runs automatically every 24h. Use this button to run it now.",
    },
    "settings.backup_done": {
        "ar": "اكتمل:",
        "en": "Done:",
    },
    "settings.backup_failed": {
        "ar": "فشل:",
        "en": "Failed:",
    },
    "settings.backup_heading": {
        "ar": "النسخ الاحتياطي الشامل",
        "en": "Universal Backup",
    },
    "settings.backup_history": {
        "ar": "سجل النسخ الاحتياطي",
        "en": "Backup History",
    },
    "settings.backup_maintenance": {
        "ar": "النسخ الاحتياطي والصيانة",
        "en": "Backup & Maintenance",
    },
    "settings.backup_none": {
        "ar": "لا توجد نسخ بعد.",
        "en": "No backups yet.",
    },
    "settings.backup_now": {
        "ar": "نسخ احتياطي الآن",
        "en": "Back Up Now",
    },
    "settings.backup_retention": {
        "ar": "الاحتفاظ بالنسخ",
        "en": "Keep backups",
    },
    "settings.backup_retention_hint": {
        "ar": "عدد النسخ المحلية المطلوب الاحتفاظ بها. بعد كل تشغيل تُحذف النسخ الأقدم من هذا العدد نهائياً. النسخ السحابية لا تُحذف أبداً.",
        "en": "How many local backups to keep. After every run, backups older than this are permanently deleted. Cloud copies are never pruned.",
    },
    "settings.backup_running": {
        "ar": "جاري النسخ…",
        "en": "Backing up…",
    },
    "settings.base_font_size": {
        "ar": "حجم الخط الأساسي:",
        "en": "Base Font Size:",
    },
    "settings.base_url": {
        "ar": "عنوان الخادم",
        "en": "Base URL",
    },
    "settings.bot_token": {
        "ar": "رمز البوت",
        "en": "Bot Token",
    },
    "settings.bot_token_xoxb": {
        "ar": "رمز البوت (xoxb-...)",
        "en": "Bot Token (xoxb-...)",
    },
    "settings.by": {
        "ar": "بواسطة",
        "en": "by",
    },
    "settings.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "settings.category_automation": {
        "ar": "أتمتة",
        "en": "automation",
    },
    "settings.category_code": {
        "ar": "أكواد",
        "en": "code",
    },
    "settings.category_communication": {
        "ar": "تواصل",
        "en": "communication",
    },
    "settings.category_database": {
        "ar": "قاعدة بيانات",
        "en": "database",
    },
    "settings.category_delegation": {
        "ar": "تفويض",
        "en": "delegation",
    },
    "settings.category_diagnostics": {
        "ar": "تشخيص",
        "en": "diagnostics",
    },
    "settings.category_filesystem": {
        "ar": "نظام الملفات",
        "en": "filesystem",
    },
    "settings.category_general": {
        "ar": "عام",
        "en": "general",
    },
    "settings.category_git": {
        "ar": "Git",
        "en": "git",
    },
    "settings.category_media": {
        "ar": "وسائط",
        "en": "media",
    },
    "settings.category_memory": {
        "ar": "الذاكرة",
        "en": "memory",
    },
    "settings.category_nlp": {
        "ar": "معالجة اللغة",
        "en": "nlp",
    },
    "settings.category_search": {
        "ar": "بحث",
        "en": "search",
    },
    "settings.category_system": {
        "ar": "النظام",
        "en": "system",
    },
    "settings.category_utility": {
        "ar": "أدوات مساعدة",
        "en": "utility",
    },
    "settings.category_web": {
        "ar": "الويب",
        "en": "web",
    },
    "settings.change_password": {
        "ar": "تغيير كلمة المرور",
        "en": "Change Password",
    },
    "settings.check_updates": {
        "ar": "التحقق من التحديثات",
        "en": "Check for Updates",
    },
    "settings.checked_models_hint": {
        "ar": "النماذج المحددة فقط تظهر في الشريط الجانبي وقوائم المحادثة المنسدلة.",
        "en": "Only checked models appear in the sidebar & chat dropdowns.",
    },
    "settings.choose_preset": {
        "ar": "اختر قالباً (اختياري)",
        "en": "Choose a Preset (Optional)",
    },
    "settings.choose_preset_placeholder": {
        "ar": "-- اختر قالباً --",
        "en": "-- Choose a Preset --",
    },
    "settings.clear_discovered": {
        "ar": "مسح",
        "en": "Clear",
    },
    "settings.command_space_separated": {
        "ar": "الأمر (مفصول بمسافات)",
        "en": "Command (space-separated)",
    },
    "settings.config_data": {
        "ar": "بيانات الإعدادات",
        "en": "Configuration Data",
    },
    "settings.confirm_password": {
        "ar": "تأكيد كلمة المرور",
        "en": "Confirm Password",
    },
    "settings.conflicts_detected": {
        "ar": "تم اكتشاف تعارضات",
        "en": "Conflicts Detected",
    },
    "settings.connected": {
        "ar": "تم الاتصال!",
        "en": "Connected!",
    },
    "settings.connected_latency": {
        "ar": "تم الاتصال! زمن الاستجابة:",
        "en": "Connected! Latency:",
    },
    "settings.connected_model": {
        "ar": "تم الاتصال!",
        "en": "Connected!",
    },
    "settings.connector_name": {
        "ar": "اسم الموصل",
        "en": "Connector Name",
    },
    "settings.connectors_moved": {
        "ar": "تم نقل الموصلات",
        "en": "Connectors have moved",
    },
    "settings.connectors_moved_description": {
        "ar": "تم نقل مزودي النماذج وجميع رموز موصلات المنصات إلى تبويب \"المزودون والموصلات\" الموحد.",
        "en": "LLM providers and all platform connector tokens are now managed in the unified \"Providers & Connectors\" tab.",
    },
    "settings.context_window": {
        "ar": "نافذة السياق",
        "en": "Context Window",
    },
    "settings.create_token": {
        "ar": "إنشاء رمز",
        "en": "Create Token",
    },
    "settings.cron_tz_hint": {
        "ar": "المنطقة الزمنية للمهام المجدولة والتذكيرات — \"يومياً الساعة 9 صباحاً\" تُطلق 9 صباحاً بتوقيتها هنا. تنطبق على المهام الجديدة؛ المهمّات الحالية تحتفظ بوقتها المخزَّن. اكتب اسم منطقة IANA (القائمة اختصار وليست حداً).",
        "en": "Timezone for scheduled tasks and reminders — \"daily at 9am\" fires at 9am here. Applies to newly scheduled tasks; existing jobs keep their stored fire times. Type an IANA zone (the list is a shortcut, not a limit).",
    },
    "settings.cron_tz_invalid": {
        "ar": "ليست منطقة زمنية يعرفها المتصفح. استخدم اسم IANA مثل Asia/Kuwait.",
        "en": "Not a timezone your browser recognises. Use an IANA name such as Asia/Kuwait.",
    },
    "settings.cron_tz_label": {
        "ar": "منطقة زمنية (IANA)",
        "en": "IANA timezone",
    },
    "settings.cron_tz_save": {
        "ar": "حفظ المنطقة الزمنية",
        "en": "Save schedule timezone",
    },
    "settings.cron_tz_save_failed": {
        "ar": "فشل الحفظ",
        "en": "Save failed",
    },
    "settings.cron_tz_saved": {
        "ar": "تم حفظ المنطقة الزمنية — تنطبق على المهام المجدولة الجديدة.",
        "en": "Schedule timezone saved — applies to newly scheduled tasks.",
    },
    "settings.cron_tz_source_config": {
        "ar": "مضبوطة من الإعدادات.",
        "en": "Set from Settings.",
    },
    "settings.cron_tz_source_default": {
        "ar": "الافتراضي: UTC. احفظ لضبط منطقتك المحلية.",
        "en": "Default: UTC. Save to set your local zone.",
    },
    "settings.cron_tz_source_env": {
        "ar": "مضبوطة عبر متغير البيئة KAZMA_TZ (قيمة الإعدادات مُتجاوَزة ما دام موجوداً).",
        "en": "Set via the KAZMA_TZ environment variable (Settings value is overridden while it is present).",
    },
    "settings.cron_tz_title": {
        "ar": "المنطقة الزمنية للمهام المجدولة",
        "en": "Scheduled tasks timezone",
    },
    "settings.cron_tz_use_mine": {
        "ar": "استخدم منطقتي الزمنية",
        "en": "Use my timezone",
    },
    "settings.current": {
        "ar": "الحالي",
        "en": "Current",
    },
    "settings.current_password": {
        "ar": "كلمة المرور الحالية",
        "en": "Current Password",
    },
    "settings.custom": {
        "ar": "— مخصص —",
        "en": "— Custom —",
    },
    "settings.custom_css": {
        "ar": "CSS مخصص",
        "en": "Custom CSS",
    },
    "settings.dark": {
        "ar": "داكن",
        "en": "Dark",
    },
    "settings.default_models_per_task": {
        "ar": "النماذج الافتراضية حسب المهمة",
        "en": "Default Models per Task",
    },
    "settings.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "settings.depth_chat": {
        "ar": "محادثة · 15",
        "en": "Chat · 15",
    },
    "settings.depth_deep": {
        "ar": "عميق · 30",
        "en": "Deep · 30",
    },
    "settings.depth_research": {
        "ar": "بحث · 40",
        "en": "Research · 40",
    },
    "settings.disable": {
        "ar": "تعطيل",
        "en": "Disable",
    },
    "settings.disabled_label": {
        "ar": "معطّل",
        "en": "Disabled",
    },
    "settings.discord": {
        "ar": "ديسكورد",
        "en": "Discord",
    },
    "settings.discover": {
        "ar": "اكتشاف",
        "en": "Discover",
    },
    "settings.discovered_n": {
        "ar": "تم الاكتشاف ({n})",
        "en": "Discovered ({n})",
    },
    "settings.disk_free": {
        "ar": "مساحة فارغة",
        "en": "Disk Free",
    },
    "settings.display_name": {
        "ar": "اسم العرض",
        "en": "Display Name",
    },
    "settings.display_name_tenant": {
        "ar": "اسم العرض",
        "en": "Display name",
    },
    "settings.documents_hint": {
        "ar": "مفاتيح ConfigStore الحية لمنصة المستندات. تُطبَّق التغييرات دون إعادة تشغيل.",
        "en": "Live ConfigStore keys for the document platform. Changes apply without restart.",
    },
    "settings.documents_save": {
        "ar": "حفظ إعدادات المستندات",
        "en": "Save document settings",
    },
    "settings.documents_title": {
        "ar": "ذكاء المستندات",
        "en": "Document Intelligence",
    },
    "settings.download_backup": {
        "ar": "تنزيل نسخة احتياطية من الإعدادات",
        "en": "Download settings backup",
    },
    "settings.download_complete_config": {
        "ar": "نزّل إعداداتك في ملف. تظهر المفاتيح بأسمائها فقط، كإشارات إلى خزنة هذا التثبيت، ولا تظهر المفاتيح نفسها.",
        "en": "Download your settings as a file. Keys appear only by name, as references to this install's vault, never the keys themselves.",
    },
    "settings.download_config": {
        "ar": "تنزيل الإعدادات",
        "en": "Download Config",
    },
    "settings.edit_connector": {
        "ar": "تعديل الموصل",
        "en": "Edit Connector",
    },
    "settings.edit_profile": {
        "ar": "تعديل الملف",
        "en": "Edit Profile",
    },
    "settings.edit_provider": {
        "ar": "تعديل المزود",
        "en": "Edit Provider",
    },
    "settings.email": {
        "ar": "البريد الإلكتروني",
        "en": "Email",
    },
    "settings.email_active_provider": {
        "ar": "المزود النشط (تلقائي)",
        "en": "Active provider (auto)",
    },
    "settings.email_address": {
        "ar": "العنوان",
        "en": "Address",
    },
    "settings.email_always_on": {
        "ar": "متاح دائماً",
        "en": "Always available",
    },
    "settings.email_auto_hint": {
        "ar": "الدردشة تستخدم الوضع التلقائي: حساب حقيقي إن وُجد، وإلا sandbox. تظهر بادئة الوضع في الرد.",
        "en": "Chat uses auto: real account if connected, otherwise sandbox. Banner shows [sandbox|gmail|gmail_pop|microsoft_graph|microsoft_imap|imap|pop] mode.",
    },
    "settings.calendar_active": {
        "ar": "المزوّد النشط",
        "en": "Active backend",
    },
    "settings.calendar_connect_google": {
        "ar": "ربط تقويم Google",
        "en": "Connect Google Calendar",
    },
    "settings.calendar_desc": {
        "ar": "يستخدم المهارة الأصلية list_events / create_event تقويم Google الحقيقي عند الاتصال. بدون رمز تقويم كانت الأداة تسقط بصمت إلى sandbox وتبدو كتقويم فارغ.",
        "en": "The calendar skill (list_events / create_event) uses your real Google Calendar when connected. Without a calendar token it used to fall back silently to an empty sandbox.",
    },
    "settings.calendar_disconnect_confirm": {
        "ar": "فصل تقويم Google؟ يتوقف Kazma عن قراءته وتعديله حتى تربطه من هنا مرة أخرى. يبقى Gmail مربوطًا.",
        "en": "Disconnect Google Calendar? Kazma stops reading and changing it until you connect it again here. Gmail stays connected.",
    },
    "settings.calendar_gmail_oauth_note": {
        "ar": "ربط Google يطلب أيضاً نطاق التقويم. فعّل Google Calendar API في مشروع Cloud.",
        "en": "Connect with Google also requests Calendar scope. Enable the Google Calendar API in the Cloud project.",
    },
    "settings.calendar_outlook_hint": {
        "ar": "زر ربط تقويم Outlook يسجّل الدخول إلى Microsoft للتقويم وحده، ولا يغيّر بريد Microsoft. ويُستخدم معرّف تطبيق Azure (client ID) المحفوظ في بطاقة بريد Microsoft.",
        "en": "Connect Outlook Calendar signs in to Microsoft for the calendar only; Microsoft mail is not changed. The Azure application (client) ID is the one saved on the Microsoft mail card.",
    },
    "settings.calendar_title": {
        "ar": "التقويم (Google / Outlook)",
        "en": "Calendar (Google / Outlook)",
    },
    "settings.email_connect_google": {
        "ar": "الربط مع Google",
        "en": "Connect with Google",
    },
    "settings.email_connect_microsoft": {
        "ar": "ربط Microsoft",
        "en": "Connect Microsoft",
    },
    "settings.email_connect_microsoft_browser": {
        "ar": "الربط مع Microsoft",
        "en": "Connect with Microsoft",
    },
    "settings.email_connect_microsoft_device": {
        "ar": "الربط برمز الجهاز",
        "en": "Connect via device code",
    },
    "settings.email_connected": {
        "ar": "متصل",
        "en": "Connected",
    },
    "settings.email_disconnect": {
        "ar": "قطع الاتصال",
        "en": "Disconnect",
    },
    "settings.email_disconnect_gmail_confirm": {
        "ar": "فصل Gmail؟ يتوقف Kazma عن قراءة بريده والإرسال منه. ولتقويم Google زر فصل خاص به في بطاقة التقويم أدناه.",
        "en": "Disconnect Gmail? Kazma stops reading and sending its mail. Google Calendar has its own switch on the calendar card below.",
    },
    "settings.email_disconnect_ms_confirm": {
        "ar": "فصل بريد Microsoft؟ يتوقف Kazma عن قراءة بريده والإرسال منه. ولتقويم Outlook زر فصل خاص به في بطاقة التقويم أدناه.",
        "en": "Disconnect Microsoft mail? Kazma stops reading and sending its mail. Outlook Calendar has its own switch on the calendar card below.",
    },
    "settings.email_docs_hint": {
        "ar": "تفاصيل الإعداد:",
        "en": "Full setup notes:",
    },
    "settings.email_docs_link": {
        "ar": "دليل تكامل البريد",
        "en": "Email integration guide",
    },
    "settings.email_gmail_address": {
        "ar": "عنوان Gmail",
        "en": "Gmail address",
    },
    "settings.email_gmail_app_password": {
        "ar": "كلمة مرور التطبيق",
        "en": "App password",
    },
    "settings.email_gmail_app_password_fallback": {
        "ar": "بديل: كلمة مرور التطبيق (Gmail شخصي / إن سمح المسؤول)",
        "en": "Fallback: app password (personal Gmail / if admin allows)",
    },
    "settings.email_gmail_app_password_help": {
        "ar": "حساب Google ← الأمان ← كلمات مرور التطبيقات. ليست كلمة مرور Gmail العادية.",
        "en": "Google Account → Security → App passwords. Not your normal Gmail password.",
    },
    "settings.email_gmail_desc": {
        "ar": "بديل فقط: كلمة مرور تطبيق Google (غالباً محظورة في Workspace). فضّل الربط عبر Google OAuth.",
        "en": "Fallback only: Google App Password (often blocked on Workspace). Prefer Connect with Google OAuth.",
    },
    "settings.email_gmail_imap_desc": {
        "ar": "IMAP + SMTP مع كلمة مرور تطبيق Google. Workspace غالباً يحظرها — فضّل OAuth.",
        "en": "IMAP + SMTP with a Google App Password (enable IMAP in Gmail settings). Workspace often blocks this — prefer OAuth.",
    },
    "settings.email_gmail_oauth_client_id": {
        "ar": "معرّف عميل Google OAuth",
        "en": "Google OAuth Client ID",
    },
    "settings.email_gmail_oauth_client_missing": {
        "ar": "غير محفوظ — الصق المعرّف والسر أولاً",
        "en": "Not saved yet — paste Client ID + secret first",
    },
    "settings.email_gmail_oauth_client_ready": {
        "ar": "محفوظ على الخادم — يمكنك الربط",
        "en": "Saved on server — you can Connect",
    },
    "settings.email_gmail_oauth_client_required": {
        "ar": "الصق معرّف وسر عميل Google OAuth، احفظ، ثم اربط مع Google.",
        "en": "Paste Google OAuth Client ID + secret, click Save OAuth client, then Connect with Google.",
    },
    "settings.email_gmail_oauth_client_secret": {
        "ar": "سر عميل Google OAuth",
        "en": "Google OAuth Client secret",
    },
    "settings.email_gmail_oauth_client_status": {
        "ar": "عميل OAuth",
        "en": "OAuth client",
    },
    "settings.email_gmail_oauth_desc": {
        "ar": "موصى به لـ Google Workspace: تسجيل الدخول عبر Google OAuth (بدون كلمة مرور تطبيق). يستخدم Gmail API ويطلب نطاق التقويم.",
        "en": "Recommended for Google Workspace: sign in with Google OAuth (no app password). Uses Gmail API and requests Calendar so list_events can use your Google Calendar.",
    },
    "settings.email_gmail_oauth_help": {
        "ar": "Google Cloud Console ← عميل OAuth ويب. يجب أن يتضمن Redirect URI المسار /api/email/oauth/gmail/callback (التقويم يستخدم نفس المسار). فعّل Gmail API و Google Calendar API.",
        "en": "Google Cloud Console → OAuth Web client. Authorized redirect URI must include /api/email/oauth/gmail/callback (Calendar uses the same callback). Enable Gmail API and Google Calendar API.",
    },
    "settings.email_gmail_oauth_secret_again": {
        "ar": "أعد إدخال سر العميل (أو امسح المعرّف لاستخدام المحفوظ).",
        "en": "Re-enter Client secret (or clear Client ID and use the already-saved client).",
    },
    "settings.email_gmail_pop_desc": {
        "ar": "POP3 + SMTP مع كلمة مرور تطبيق Google. صندوق الوارد فقط؛ فضّل IMAP أو OAuth.",
        "en": "POP3 + SMTP with a Google App Password (enable POP in Gmail). Inbox-only; prefer IMAP or OAuth when possible.",
    },
    "settings.email_gmail_required": {
        "ar": "البريد وكلمة مرور التطبيق مطلوبان",
        "en": "Email and app password are required",
    },
    "settings.email_mode_imap": {
        "ar": "IMAP",
        "en": "IMAP",
    },
    "settings.email_mode_oauth": {
        "ar": "OAuth",
        "en": "OAuth",
    },
    "settings.email_mode_pop": {
        "ar": "POP",
        "en": "POP",
    },
    "settings.email_ms_address": {
        "ar": "عنوان Microsoft / Outlook",
        "en": "Microsoft / Outlook address",
    },
    "settings.email_ms_client_id": {
        "ar": "معرّف تطبيق Azure (Client ID)",
        "en": "Azure application (client) ID",
    },
    "settings.email_ms_client_required": {
        "ar": "معرّف عميل Azure مطلوب",
        "en": "Azure client ID is required",
    },
    "settings.email_ms_client_secret": {
        "ar": "سر العميل (إن وُجد)",
        "en": "Client secret (if confidential app)",
    },
    "settings.email_ms_desc": {
        "ar": "Microsoft Graph عبر رمز الجهاز. سجّل تطبيقاً في Azure (عميل عام) بصلاحيات البريد + offline_access.",
        "en": "Microsoft Graph via device code. Register an Azure app (public client) with Mail.Read/ReadWrite/Send + offline_access.",
    },
    "settings.email_ms_device_fallback": {
        "ar": "بديل: رمز الجهاز (بدون إعادة توجيه)",
        "en": "Alternative: device code (no browser redirect)",
    },
    "settings.email_ms_device_hint": {
        "ar": "افتح الرابط وأدخل هذا الرمز ثم وافق على صلاحيات البريد:",
        "en": "Open the link and enter this code, then approve mail access:",
    },
    "settings.email_ms_enter_code": {
        "ar": "أدخل الرمز في Microsoft لإكمال الربط",
        "en": "Enter the code at Microsoft to finish connecting",
    },
    "settings.email_ms_imap_desc": {
        "ar": "IMAP + SMTP إلى Outlook/M365. كثير من المستأجرين يعطّلون المصادقة الأساسية — استخدم OAuth إن فشل الدخول.",
        "en": "IMAP + SMTP to Outlook/M365 (outlook.office365.com). Many tenants disable basic auth — use OAuth if login fails.",
    },
    "settings.email_ms_oauth_desc": {
        "ar": "تسجيل الدخول مع Microsoft في المتصفح. الأفضل لـ M365/Outlook. رمز الجهاز بديل اختياري.",
        "en": "Sign in with Microsoft in the browser (authorization code). Best for M365/Outlook. Device code is optional fallback.",
    },
    "settings.email_ms_password": {
        "ar": "كلمة المرور أو كلمة مرور التطبيق",
        "en": "Password or app password",
    },
    "settings.email_ms_polling": {
        "ar": "جارٍ انتظار التفويض… أبقِ هذه الصفحة مفتوحة.",
        "en": "Polling for authorization… keep this page open.",
    },
    "settings.email_ms_pop_desc": {
        "ar": "POP3 + SMTP إلى Outlook/M365. ميزات محدودة مقارنة بـ Graph/IMAP؛ قد تُحظر المصادقة الأساسية.",
        "en": "POP3 + SMTP to Outlook/M365. Limited features vs Graph/IMAP; basic auth may be blocked.",
    },
    "settings.email_ms_protocol_required": {
        "ar": "عنوان البريد وكلمة المرور مطلوبان لـ IMAP/POP",
        "en": "Email address and password are required for IMAP/POP",
    },
    "settings.email_ms_redirect_help": {
        "ar": "يجب أن يتضمن Redirect URI في Azure المسار /api/email/oauth/microsoft/callback.",
        "en": "Azure app redirect URI must include /api/email/oauth/microsoft/callback (and your public host).",
    },
    "settings.email_ms_tenant": {
        "ar": "معرّف المستأجر",
        "en": "Tenant ID",
    },
    "settings.email_not_connected": {
        "ar": "غير متصل",
        "en": "Not connected",
    },
    "settings.email_refresh": {
        "ar": "تحديث الحالة",
        "en": "Refresh status",
    },
    "settings.email_sandbox": {
        "ar": "تجريبي",
        "en": "Sandbox",
    },
    "settings.email_sandbox_desc": {
        "ar": "صندوق بريد تجريبي محلي — بلا بيانات اعتماد. آمن لاختبار القائمة والتحليل والمسودات.",
        "en": "Local SQLite demo mailbox — no credentials. Safe for testing list/analyze/send drafts.",
    },
    "settings.email_sandbox_try": {
        "ar": "جرّب في الدردشة: «اعرض بريدي» أو «حلّل رسالة اليانصيب».",
        "en": "Try in chat: “List my inbox” or “Analyze the lottery email”.",
    },
    "settings.email_save_gmail": {
        "ar": "حفظ Gmail",
        "en": "Save Gmail",
    },
    "settings.email_save_imap": {
        "ar": "حفظ IMAP",
        "en": "Save IMAP",
    },
    "settings.email_save_ms_client": {
        "ar": "حفظ معرّف التطبيق",
        "en": "Save app ID",
    },
    "settings.email_save_oauth_client": {
        "ar": "حفظ عميل OAuth",
        "en": "Save OAuth client",
    },
    "settings.email_save_pop": {
        "ar": "حفظ POP",
        "en": "Save POP",
    },
    "settings.email_subtitle": {
        "ar": "Gmail أو Microsoft 365/Outlook أو صندوق تجريبي (sandbox) للوكيل.",
        "en": "Gmail, Microsoft 365/Outlook, or sandbox demo mailbox for the agent.",
    },
    "settings.email_title": {
        "ar": "ربط البريد",
        "en": "Connect email",
    },
    "settings.email_waiting_auth": {
        "ar": "بانتظار تسجيل الدخول…",
        "en": "Waiting for sign-in…",
    },
    "settings.embedder_active_class": {
        "ar": "الواجهة النشطة",
        "en": "Active backend",
    },
    "settings.embedder_api_key_env": {
        "ar": "متغير بيئة مفتاح API",
        "en": "API key env var",
    },
    "settings.embedder_api_key_env_hint": {
        "ar": "يُقرأ المفتاح نفسه من متغير بيئة، ولا يُخزَّن أبدًا في الواجهة.",
        "en": "The key itself is read from an environment variable, never stored in the UI.",
    },
    "settings.embedder_base_url": {
        "ar": "الرابط الأساسي",
        "en": "Base URL",
    },
    "settings.embedder_config_title": {
        "ar": "الإعدادات",
        "en": "Configuration",
    },
    "settings.embedder_custom_model": {
        "ar": "اسم النموذج",
        "en": "Model name",
    },
    "settings.embedder_db_beliefs": {
        "ar": "المعتقدات",
        "en": "Beliefs",
    },
    "settings.embedder_db_empty": {
        "ar": "لا توجد صفوف ذاكرة بعد.",
        "en": "No memory rows yet.",
    },
    "settings.embedder_db_episodes": {
        "ar": "الأحداث",
        "en": "Episodes",
    },
    "settings.embedder_db_hint": {
        "ar": "تُجمَّع الصفوف حسب النموذج الذي ضمّنها. تعدد الإصدارات يضعف الاسترجاع — أعد البناء لتوحيدها.",
        "en": "Rows are grouped by the model that embedded them. Mixed versions degrade recall — rebuild to unify.",
    },
    "settings.embedder_db_title": {
        "ar": "تركيبة فضاء المتجهات في الذاكرة",
        "en": "Memory vector-space composition",
    },
    "settings.embedder_db_version": {
        "ar": "إصدار النموذج",
        "en": "Model version",
    },
    "settings.embedder_dim": {
        "ar": "البُعد",
        "en": "Dimension",
    },
    "settings.embedder_dim_hint": {
        "ar": "يجب أن يطابق حجم مخرجات النموذج. الإعدادات الجاهزة تملؤه تلقائيًا.",
        "en": "Must match the model's output size. Presets fill this automatically.",
    },
    "settings.embedder_hint": {
        "ar": "نموذج التضمين يشغّل استرجاع الذاكرة الدلالي. تُحفظ التغييرات هنا وتُطبَّق بعد إعادة تشغيل الخادم (يُحمَّل النموذج مرة واحدة عند الإقلاع).",
        "en": "The embedding model powers semantic memory recall. Changes are saved here and applied after a server restart (the model loads once at boot).",
    },
    "settings.embedder_manual_hint": {
        "ar": "فضّل الزر أعلاه. من جذر المستودع، يقوم سكربت CLI بنفس العمل (يتطلب إعادة تشغيل الخادم بعد ذلك):",
        "en": "Prefer the button above. From the repo root, the CLI script does the same (requires a server restart afterwards):",
    },
    "settings.embedder_manual_title": {
        "ar": "بديل يدوي",
        "en": "Manual fallback",
    },
    "settings.embedder_model": {
        "ar": "النموذج",
        "en": "Model",
    },
    "settings.embedder_model_custom": {
        "ar": "نموذج مخصص…",
        "en": "Custom model…",
    },
    "settings.embedder_model_hint": {
        "ar": "BAAI/bge-m3 هو الافتراضي متعدد اللغات الموصى به (1024 بُعدًا). تغيير النموذج يتطلب إعادة بناء حتى تبقى كل الصفوف في نفس فضاء المتجهات.",
        "en": "BAAI/bge-m3 is the recommended multilingual default (1024-dim). Switching models requires a rebuild so all rows live in the same vector space.",
    },
    "settings.embedder_provider": {
        "ar": "المزود",
        "en": "Provider",
    },
    "settings.embedder_provider_hint": {
        "ar": "المحلي يعمل دون اتصال على هذا الجهاز. البعيد يستدعي نقطة /embeddings (NVIDIA NIM, TEI, …).",
        "en": "Local runs fully offline on this machine. Remote calls an /embeddings endpoint (NVIDIA NIM, TEI, …).",
    },
    "settings.embedder_provider_local": {
        "ar": "محلي (sentence-transformers)",
        "en": "Local (sentence-transformers)",
    },
    "settings.embedder_provider_remote": {
        "ar": "واجهة برمجية متوافقة مع OpenAI",
        "en": "OpenAI-compatible API",
    },
    "settings.embedder_rebuild_btn": {
        "ar": "إعادة بناء التضمينات",
        "en": "Rebuild embeddings",
    },
    "settings.embedder_rebuild_done": {
        "ar": "اكتملت إعادة البناء",
        "en": "Rebuild complete",
    },
    "settings.embedder_rebuild_error": {
        "ar": "فشلت إعادة البناء",
        "en": "Rebuild failed",
    },
    "settings.embedder_rebuild_hint": {
        "ar": "إعادة البناء تعيد ترميز الصفوف غير الموجودة أصلاً في فضاء المتجهات الحالي فقط (بعد تبديل النموذج يعني ذلك كل الصفوف). يُنشأ نسخ احتياطي (memory_state.db.pre_reembed) تلقائيًا.",
        "en": "Rebuilding re-encodes only rows not already in the current vector space (after a model switch that is every row). A backup (memory_state.db.pre_reembed) is created automatically.",
    },
    "settings.embedder_rebuild_running": {
        "ar": "جارٍ إعادة البناء…",
        "en": "Rebuilding…",
    },
    "settings.embedder_restart_btn": {
        "ar": "إعادة تشغيل الخادم",
        "en": "Restart server",
    },
    "settings.embedder_restart_needed": {
        "ar": "إعادة تشغيل مطلوبة",
        "en": "Restart required",
    },
    "settings.embedder_restart_needed_hint": {
        "ar": "المُضمِّن الحالي يختلف عن الإعدادات المحفوظة. أعد تشغيل الخادم لتطبيق التغيير.",
        "en": "The running embedder differs from the saved config. Restart the server to apply the change.",
    },
    "settings.embedder_save": {
        "ar": "حفظ إعدادات المُضمِّن",
        "en": "Save embedder settings",
    },
    "settings.embedder_title": {
        "ar": "مُضمِّن الذاكرة",
        "en": "Memory Embedder",
    },
    "settings.enable": {
        "ar": "تفعيل",
        "en": "Enable",
    },
    "settings.enable_hitl": {
        "ar": "تفعيل المشاركة البشرية",
        "en": "Enable Human-in-the-Loop",
    },
    "settings.enabled_label": {
        "ar": "مفعّل",
        "en": "Enabled",
    },
    "settings.english": {
        "ar": "الإنجليزية",
        "en": "English",
    },
    "settings.env_vars_json": {
        "ar": "متغيرات البيئة (JSON)",
        "en": "Environment Variables (JSON)",
    },
    "settings.export_config": {
        "ar": "تصدير الإعدادات",
        "en": "Export Configuration",
    },
    "settings.fetch": {
        "ar": "جلب",
        "en": "Fetch",
    },
    "settings.font_size": {
        "ar": "حجم الخط",
        "en": "Font Size",
    },
    "settings.gateway_adapters": {
        "ar": "محولات البوابة",
        "en": "Gateway Adapters",
    },
    "settings.gateway_adapters_desc": {
        "ar": "تُطبّق تغييرات المحولات تلقائياً عند الحفظ. استخدم هذا الزر لإعادة تحميل جميع المحولات يدوياً دون إعادة تشغيل الخادم.",
        "en": "Connector changes are auto-applied on save. Use this button to manually reload all adapters without restarting the server.",
    },
    "settings.gateway_restart_required": {
        "ar": "مطلوب إعادة تشغيل البوابة بعد حفظ تغييرات الموصلات.",
        "en": "Gateway restart required after saving connector changes.",
    },
    "settings.go_to_providers_connectors": {
        "ar": "انتقل إلى المزودون والموصلات",
        "en": "Go to Providers & Connectors",
    },
    "settings.guild_id": {
        "ar": "معرّف السيرفر",
        "en": "Guild ID",
    },
    "settings.imap_host": {
        "ar": "خادم IMAP",
        "en": "IMAP Host",
    },
    "settings.import_config": {
        "ar": "استيراد الإعدادات",
        "en": "Import Configuration",
    },
    "settings.import_configuration": {
        "ar": "استيراد الإعدادات",
        "en": "Import Configuration",
    },
    "settings.incoming_webhook_url": {
        "ar": "رابط الويب هوك الوارد",
        "en": "Incoming Webhook URL",
    },
    "settings.inject_custom_css": {
        "ar": "إدراج CSS مخصص (متقدم)",
        "en": "Inject custom CSS (advanced)",
    },
    "settings.installed_skills": {
        "ar": "المهارات المثبتة",
        "en": "Installed Skills",
    },
    "settings.kb_action.clear_chat": {
        "ar": "مسح المحادثة",
        "en": "clear chat",
    },
    "settings.kb_action.close_modal": {
        "ar": "إغلاق النافذة",
        "en": "close modal",
    },
    "settings.kb_action.focus_input": {
        "ar": "تركيز الإدخال",
        "en": "focus input",
    },
    "settings.kb_action.go_to_chat": {
        "ar": "الذهاب إلى المحادثة",
        "en": "go to chat",
    },
    "settings.kb_action.go_to_mcp": {
        "ar": "الذهاب إلى MCP",
        "en": "go to MCP",
    },
    "settings.kb_action.go_to_settings": {
        "ar": "الذهاب إلى الإعدادات",
        "en": "go to settings",
    },
    "settings.kb_action.go_to_skills": {
        "ar": "الذهاب إلى المهارات",
        "en": "go to skills",
    },
    "settings.kb_action.go_to_swarm": {
        "ar": "الذهاب إلى السرب",
        "en": "go to swarm",
    },
    "settings.kb_action.new_chat": {
        "ar": "محادثة جديدة",
        "en": "new chat",
    },
    "settings.kb_action.new_line": {
        "ar": "سطر جديد",
        "en": "new line",
    },
    "settings.kb_action.search_chats": {
        "ar": "بحث في المحادثات",
        "en": "search chats",
    },
    "settings.kb_action.send_message": {
        "ar": "إرسال رسالة",
        "en": "send message",
    },
    "settings.kb_action.toggle_sidebar": {
        "ar": "تبديل الشريط الجانبي",
        "en": "toggle sidebar",
    },
    "settings.kb_action.toggle_theme": {
        "ar": "تبديل المظهر",
        "en": "toggle theme",
    },
    "settings.kb_and": {
        "ar": "و",
        "en": "and",
    },
    "settings.kb_both_use": {
        "ar": "كلاهما يستخدم",
        "en": "both use",
    },
    "settings.kb_click_press": {
        "ar": "انقر واضغط المفاتيح",
        "en": "Click & press keys",
    },
    "settings.kb_press_keys": {
        "ar": "اضغط المفاتيح...",
        "en": "Press keys...",
    },
    "settings.keyboard_shortcuts": {
        "ar": "اختصارات لوحة المفاتيح",
        "en": "Keyboard Shortcuts",
    },
    "settings.language": {
        "ar": "اللغة",
        "en": "Language",
    },
    "settings.latest_version": {
        "ar": "(الحالي:",
        "en": "(current:",
    },
    "settings.layout": {
        "ar": "التخطيط",
        "en": "Layout",
    },
    "settings.left": {
        "ar": "يسار",
        "en": "Left",
    },
    "settings.light": {
        "ar": "فاتح",
        "en": "Light",
    },
    "settings.lines": {
        "ar": "سطور",
        "en": "lines",
    },
    "settings.llm_providers": {
        "ar": "مزودو النماذج",
        "en": "LLM Providers",
    },
    "settings.llm_providers_models": {
        "ar": "مزودو النماذج والملفات",
        "en": "LLM Providers & Models",
    },
    "settings.load": {
        "ar": "تحميل",
        "en": "Load",
    },
    "settings.loading": {
        "ar": "جاري تحميل الإعدادات…",
        "en": "Loading settings…",
    },
    "settings.logging_format": {
        "ar": "تنسيق ملف السجل",
        "en": "File Log Format",
    },
    "settings.logging_format_json": {
        "ar": "JSON",
        "en": "JSON",
    },
    "settings.logging_format_text": {
        "ar": "نص",
        "en": "Text",
    },
    "settings.logging_level": {
        "ar": "مستوى السجل",
        "en": "Log Level",
    },
    "settings.logging_level_hint": {
        "ar": "يُطبّق فورًا. المستويات الأقل تسجّل تفاصيل أكثر.",
        "en": "Applies immediately. Lower levels log more detail.",
    },
    "settings.logging_restart_hint": {
        "ar": "تسري تغييرات التدوير والاحتفاظ بعد إعادة تشغيل الخادم. المستوى يُطبّق فورًا.",
        "en": "Rotation and retention changes take effect after a server restart. Level applies immediately.",
    },
    "settings.logging_retention": {
        "ar": "الاحتفاظ (أيام)",
        "en": "Retention (days)",
    },
    "settings.logging_retention_hint": {
        "ar": "يتدوير السجل يوميًا ويحذف تلقائيًا الملفات الأقدم من هذا عدد الأيام.",
        "en": "The log rotates daily and auto-deletes files older than this many days.",
    },
    "settings.logging_title": {
        "ar": "تسجيل الدخول",
        "en": "Logging",
    },
    "settings.long_task_default_on": {
        "ar": "استخدم ميزانيات المهام الطويلة لكل المحادثات (وليس فقط بعد /long on)",
        "en": "Use long-task budgets for all chats (not just after /long on)",
    },
    "settings.long_task_help": {
        "ar": "وضع الميزانية /long يرفع سقف الجولات الناعم (قد يتوقف جزئيًا). للتشغيل حتى الانتهاء: /long mission (جدار أمان ~500 جولة). لا يتجاوز HITL — استخدم /yolo. لكل محادثة: /long on · /long mission · /long off.",
        "en": "Budget /long raises soft tool-round ceilings (may still PARTIAL). For real run-until-done use /long mission (hard wall ~500 rounds, env-tunable). Does not skip HITL — use /yolo. Per-chat: /long on · /long mission · /long off.",
    },
    "settings.long_task_mode": {
        "ar": "وضع المهام الطويلة (افتراضي)",
        "en": "Long-task mode (default)",
    },
    "settings.masked_placeholder_hint": {
        "ar": "اترك الحقل فارغًا للإبقاء على المفتاح الحالي. الصق المفتاح كاملًا فقط عند استبداله.",
        "en": "Leave blank to keep the current key. Paste a full key only when replacing it.",
    },
    "settings.max_context_tokens": {
        "ar": "أقصى رموز للسياق",
        "en": "Max Context Tokens",
    },
    "settings.max_tokens": {
        "ar": "أقصى عدد من الرموز",
        "en": "Max Tokens",
    },
    "settings.max_tool_rounds": {
        "ar": "الحد الأقصى لجولات الأدوات",
        "en": "Max tool rounds",
    },
    "settings.max_tool_rounds_help": {
        "ar": "كم جولة أدوات (ReAct) مسموحة لكل رسالة قبل أن يجب على كاظمه الإجابة. الافتراضي 15 (محادثة). استخدم عميق (30) أو بحث (40) للمهام الطويلة. النطاق 5–100. القيم الأعلى تستهلك رموزًا ووقتًا أكثر. احفظ إعدادات الوكيل بعد اختيار الإعداد المسبق.",
        "en": "How many supervisor tool rounds (ReAct) are allowed per chat turn before Kazma must answer. Default 15 (Chat). Use Deep (30) or Research (40) for long audits/smoke. Range 5–100. Higher values use more tokens and time. Click Save Agent after choosing a preset.",
    },
    "settings.max_tool_rounds_presets": {
        "ar": "إعدادات مسبقة للعمق",
        "en": "Depth presets",
    },
    "settings.mcp_servers": {
        "ar": "خوادم MCP",
        "en": "MCP Servers",
    },
    "settings.memory_backends_hint": {
        "ar": "الافتراضي SQLite محلي + تضمينات محلية. بدّل إلى تضمينات بعيدة أو قواعد متجهات دون تعديل YAML.",
        "en": "Default is local SQLite + local embeddings. Switch to remote embedders or vector DBs without editing YAML.",
    },
    "settings.memory_backends_title": {
        "ar": "خلفيات الذاكرة",
        "en": "Memory backends",
    },
    "settings.memory_embedder_crosslink": {
        "ar": "لإعادة بناء النموذج وتركيبة فضاء المتجهات، افتح المُضمِّن.",
        "en": "For model rebuild and vector-space composition, open Embedder.",
    },
    "settings.memory_explain_recall": {
        "ar": "شرح الاستدعاء — وسم النتائج بقنوات الاسترجاع (fts5 / dense / ppr / session)",
        "en": "Explain recall — tag hits with retrieval channels (fts5 / dense / ppr / session)",
    },
    "settings.memory_failover": {
        "ar": "التراجع عند تعطل البعيد",
        "en": "Failover if remote down",
    },
    "settings.memory_failover_empty": {
        "ar": "نتائج فارغة",
        "en": "Empty results",
    },
    "settings.memory_failover_local": {
        "ar": "الرجوع للمحلي",
        "en": "Fall back to local",
    },
    "settings.memory_failover_raise": {
        "ar": "إظهار الخطأ",
        "en": "Surface error",
    },
    "settings.memory_graph_neo4j": {
        "ar": "Neo4j (bolt — كتابة مزدوجة + طوبولوجيا عند الاتصال)",
        "en": "Neo4j (bolt — dual-write + primary topology when online)",
    },
    "settings.memory_graph_provider": {
        "ar": "مخزن الرسم البياني",
        "en": "Graph store",
    },
    "settings.memory_graph_sqlite": {
        "ar": "SQLite محلي (افتراضي — طوبولوجيا V2)",
        "en": "Local SQLite (default — V2 Belief Topology)",
    },
    "settings.memory_isolation_title": {
        "ar": "عزل الذاكرة",
        "en": "Memory Isolation",
    },
    "settings.memory_kb_inject": {
        "ar": "حقن مكتبة المعرفة في كل دور محادثة (مع الذاكرة الشخصية)",
        "en": "Inject Knowledge Library into every chat turn (with personal memory)",
    },
    "settings.memory_kb_merge_hint": {
        "ar": "دمج مكتبة المعرفة في مسار المحادثة (حقن موسوم). المخازن تبقى منفصلة؛ النتائج مسيّجة كمستندات غير موثوقة.",
        "en": "Merge Knowledge Library into the chat path (labeled inject). Stores stay separate; hits are fenced as untrusted docs.",
    },
    "settings.memory_kb_merge_title": {
        "ar": "المعرفة + ذاكرة المحادثة",
        "en": "Knowledge + chat memory",
    },
    "settings.memory_kb_promote": {
        "ar": "ترقية أفضل نتائج المكتبة إلى الذاكرة العرضية (دمج مرن للاستدعاء لاحقًا)",
        "en": "Promote top KB hits into episodic memory (soft merge for later recall)",
    },
    "settings.memory_kb_smart_search": {
        "ar": "بحث معرفة ذكي — حقن من كل المكتبات النشطة عند الأسئلة التقنية",
        "en": "Smart Knowledge search — inject from all active libraries on technical questions",
    },
    "settings.memory_kb_smart_search_hint": {
        "ar": "البحث الذكي يوسّع الحقن خارج auto-inject لكل مكتبة عندما تبدو الرسالة وثائق/API. شرح الاستدعاء يوسِم المصادر (fts5/dense/ppr) لمسبار لوحة التحكم.",
        "en": "Smart search expands inject beyond per-library auto-inject when the message looks like docs/API. Explain-recall tags hits (fts5/dense/ppr) for the Dashboard probe and debug.",
    },
    "settings.memory_mode": {
        "ar": "الوضع",
        "en": "Mode",
    },
    "settings.memory_mode_hybrid": {
        "ar": "هجين",
        "en": "Hybrid",
    },
    "settings.memory_mode_local": {
        "ar": "محلي فقط",
        "en": "Local only",
    },
    "settings.memory_mode_remote": {
        "ar": "بعيد أولًا",
        "en": "Remote-first",
    },
    "settings.memory_moved_hint": {
        "ar": "عزل الذاكرة ودمج المعرفة والخلفيات (Neo4j وQdrant وPostgres) في تبويب الذاكرة.",
        "en": "Memory isolation, Knowledge merge, and backends (Neo4j, Qdrant, Postgres) live on the Memory tab.",
    },
    "settings.memory_neo4j_driver_hint": {
        "ar": "يتطلب حزمة Python في بيئة Kazma: pip install neo4j (تثبيت خادم Neo4j وحده لا يكفي).",
        "en": "Requires Python package in Kazma venv: pip install neo4j (installing the Neo4j Desktop/server alone is not enough).",
    },
    "settings.memory_neo4j_hint": {
        "ar": "الافتراضي SQLite (طوبولوجيا معتقدات V2). اختر Neo4j لكتابة مزدوجة واستخدامه في لوحة الرسم عند الاتصال. المعتقدات تبقى في SQLite.",
        "en": "Default is SQLite (V2 Belief Topology). Choose Neo4j to dual-write triples and use Neo4j for the Dashboard graph when online. Personal beliefs stay in SQLite.",
    },
    "settings.memory_neo4j_password": {
        "ar": "كلمة مرور Neo4j",
        "en": "Neo4j password",
    },
    "settings.memory_neo4j_step1": {
        "ar": "اختر Neo4j وأدخل رابط bolt وكلمة المرور",
        "en": "Select Neo4j in the dropdown, enter bolt URL + password",
    },
    "settings.memory_neo4j_step2": {
        "ar": "انقر حفظ الخلفيات (الحفظ إلزامي — كتابة الرابط وحده لا يكفي)",
        "en": "Click Save backends (must save — typing URL alone does nothing)",
    },
    "settings.memory_neo4j_step3": {
        "ar": "انقر اختبار Neo4j — يجب أن يظهر Connected",
        "en": "Click Test Neo4j — must say Connected",
    },
    "settings.memory_neo4j_step4": {
        "ar": "انقر مزامنة المعتقدات ثم افتح رسم اللوحة (نفس اللوحة، قد يظهر المصدر neo4j)",
        "en": "Click Sync beliefs → Neo4j, then open Dashboard graph (same canvas, source may say neo4j)",
    },
    "settings.memory_neo4j_title": {
        "ar": "رسم المعتقدات — Neo4j (اختياري)",
        "en": "Belief graph — Neo4j (optional)",
    },
    "settings.memory_neo4j_url": {
        "ar": "رابط Neo4j bolt",
        "en": "Neo4j bolt URL",
    },
    "settings.memory_neo4j_user": {
        "ar": "اسم مستخدم Neo4j",
        "en": "Neo4j username",
    },
    "settings.memory_rebuild": {
        "ar": "إعادة بناء التضمينات…",
        "en": "Rebuild embeddings…",
    },
    "settings.memory_reset_local": {
        "ar": "إعادة ضبط للمحلي",
        "en": "Reset to local",
    },
    "settings.memory_save_backends": {
        "ar": "حفظ الخلفيات",
        "en": "Save backends",
    },
    "settings.memory_state_provider": {
        "ar": "حالة مشتركة (مرآة متعددة النسخ)",
        "en": "Shared state (multi-replica mirror)",
    },
    "settings.memory_sync_neo4j": {
        "ar": "مزامنة المعتقدات → Neo4j",
        "en": "Sync beliefs → Neo4j",
    },
    "settings.memory_sync_postgres": {
        "ar": "مزامنة المعتقدات + الحلقات → Postgres",
        "en": "Sync beliefs + episodes → Postgres",
    },
    "settings.memory_tenant_hint": {
        "ar": "يتحكم بمشاركة أو عزل الذكريات. المشاركة مناسبة لمستخدم واحد. لكل مستخدم لمواقع متعددة. يسري من الدور التالي.",
        "en": "Controls whether memories are shared or isolated. Share everything is best for single-user Web+Telegram. Per user is for multi-user SaaS. Takes effect next turn.",
    },
    "settings.memory_tenant_mode": {
        "ar": "وضع المستأجر",
        "en": "Tenant Mode",
    },
    "settings.memory_tenant_platform": {
        "ar": "لكل منصة — عزل كل منصة (تيليجرام ≠ الويب)",
        "en": "Per platform — each platform isolates (Telegram ≠ Web)",
    },
    "settings.memory_tenant_shared": {
        "ar": "مشاركة الكل (مستخدم واحد) — كل المنصات تشترك في ذاكرة واحدة",
        "en": "Share everything (single-user) — all platforms share one memory pool",
    },
    "settings.memory_tenant_user": {
        "ar": "لكل مستخدم — ذاكرة معزولة لكل مرسل/جلسة",
        "en": "Per user — each sender/session gets fully isolated memory",
    },
    "settings.memory_test_embed": {
        "ar": "اختبار التضمين",
        "en": "Test embed",
    },
    "settings.memory_test_neo4j": {
        "ar": "اختبار Neo4j",
        "en": "Test Neo4j",
    },
    "settings.memory_test_vector": {
        "ar": "اختبار المتجه",
        "en": "Test vector",
    },
    "settings.memory_vector_provider": {
        "ar": "مزود المتجهات",
        "en": "Vector provider",
    },
    "settings.min_8_chars": {
        "ar": "8 أحرف على الأقل",
        "en": "Min 8 characters",
    },
    "settings.model_comparison": {
        "ar": "مقارنة النماذج",
        "en": "Model Comparison",
    },
    "settings.model_name": {
        "ar": "اسم النموذج",
        "en": "Model Name",
    },
    "settings.models_comma": {
        "ar": "النماذج (مفصولة بفواصل، أو اتركها فارغة لاكتشاف تلقائي)",
        "en": "Models (comma-separated, or leave empty to auto-discover)",
    },
    "settings.models_label": {
        "ar": "النماذج:",
        "en": "Models:",
    },
    "settings.models_to_compare": {
        "ar": "النماذج للمقارنة (مفصولة بفواصل)",
        "en": "Models to compare (comma-separated)",
    },
    "settings.name_id": {
        "ar": "الاسم (المعرّف)",
        "en": "Name (ID)",
    },
    "settings.new_password": {
        "ar": "كلمة المرور الجديدة",
        "en": "New Password",
    },
    "settings.no_active_sessions": {
        "ar": "لا توجد جلسات نشطة",
        "en": "No active sessions",
    },
    "settings.no_api_tokens": {
        "ar": "لا توجد رموز API",
        "en": "No API tokens",
    },
    "settings.no_connectors": {
        "ar": "لا توجد موصلات مُعدّة. انقر على \"إضافة موصل\" للبدء.",
        "en": "No connectors configured. Click \"Add Connector\" to get started.",
    },
    "settings.no_description": {
        "ar": "لا يوجد وصف",
        "en": "No description",
    },
    "settings.no_logs_available": {
        "ar": "لا توجد سجلات متاحة",
        "en": "No logs available",
    },
    "settings.no_mcp_servers": {
        "ar": "لا توجد خوادم MCP مُعدة. أضف واحداً لتوسيع قدرات أدوات كاظمه.",
        "en": "No MCP servers configured. Add one to extend Kazma's tool capabilities.",
    },
    "settings.no_platform_users": {
        "ar": "لا يوجد مستخدمون بعد. أضف مستخدماً أعلاه، أو استمر باستخدام سر الخادم.",
        "en": "No platform users yet. Add one above, or keep using the shared server secret.",
    },
    "settings.no_providers": {
        "ar": "لا يوجد مزودون مُعدّون. انقر على \"إضافة مزود\" للبدء.",
        "en": "No providers configured. Click \"Add Provider\" to get started.",
    },
    "settings.no_response": {
        "ar": "لا توجد استجابة",
        "en": "No response",
    },
    "settings.no_saved_profiles": {
        "ar": "لا توجد ملفات محفوظة. أدخل اسم ملف أعلاه وانقر على \"حفظ الملف\".",
        "en": "No saved profiles. Enter a profile name above and click \"Save Profile\".",
    },
    "settings.no_skills": {
        "ar": "لا توجد مهارات مثبتة. تصفح سوق المهارات لإضافة قدرات.",
        "en": "No skills installed. Browse the skill marketplace to add capabilities.",
    },
    "settings.no_tools": {
        "ar": "لا توجد أدوات مسجلة. تُضاف الأدوات عبر خوادم MCP أو تعريفات الأدوات المحلية.",
        "en": "No tools registered. Tools are added via MCP servers or local tool definitions.",
    },
    "settings.nonstop_backoff_base": {
        "ar": "أساس التراجع (ثانية)",
        "en": "Backoff Base (seconds)",
    },
    "settings.nonstop_backoff_max": {
        "ar": "أقصى تراجع (ثانية)",
        "en": "Backoff Max (seconds)",
    },
    "settings.nonstop_enabled": {
        "ar": "تفعيل الوضع المتواصل",
        "en": "Enable Non-Stop Mode",
    },
    "settings.nonstop_failover_chain": {
        "ar": "سلسلة التبديل الاحتياطي (معرّفات مفصولة بفواصل)",
        "en": "Failover Chain (comma-separated model ids)",
    },
    "settings.nonstop_failover_cooldown": {
        "ar": "فترة تهدئة التبديل (ثانية)",
        "en": "Failover Cooldown (seconds)",
    },
    "settings.nonstop_failover_enabled": {
        "ar": "تفعيل التبديل الاحتياطي للنموذج",
        "en": "Enable Model Failover",
    },
    "settings.nonstop_hint": {
        "ar": "تنفيذ خاضع للمراقبة: كشف التوقف، استرجاع نقاط الحفظ، استعادة تلقائية محدودة، وتبديل النموذج الاحتياطي. يعمل فورًا؛ معطّل افتراضيًا.",
        "en": "Supervised execution: stall detection, checkpoint rollback, bounded auto-recovery, and model failover. Applies live; off by default.",
    },
    "settings.nonstop_ledger": {
        "ar": "تسجيل سجل لكل استدعاء نموذج",
        "en": "Record per-call LLM ledger",
    },
    "settings.nonstop_max_recovery": {
        "ar": "أقصى محاولات استعادة",
        "en": "Max Recovery Attempts",
    },
    "settings.nonstop_stall": {
        "ar": "حد كشف التوقف (ثانية)",
        "en": "Stall Threshold (seconds)",
    },
    "settings.nonstop_title": {
        "ar": "التشغيل المتواصل والإصلاح الذاتي",
        "en": "Non-Stop & Self-Healing",
    },
    "settings.nonstop_tool_timeout": {
        "ar": "مهلة الأداة (ثانية، 0 = معطّل)",
        "en": "Per-Tool Timeout (seconds, 0 = disabled)",
    },
    "settings.offsite_desc": {
        "ar": "مزامنة كل نسخة احتياطية مع التخزين السحابي — Google Drive أو OneDrive أو WD MyCloud/NAS أو S3/B2. يحمي من فشل القرص: بدون هذا، البيانات والنسخ على قرص واحد.",
        "en": "Sync every backup to your cloud storage — Google Drive, OneDrive, WD MyCloud/NAS, or S3/B2. Protects against disk failure: without this, data and backups share one drive.",
    },
    "settings.offsite_enabled": {
        "ar": "تفعيل المزامنة الخارجية بعد كل نسخة احتياطية",
        "en": "Enable offsite sync after each backup",
    },
    "settings.offsite_heading": {
        "ar": "نسخ احتياطي خارجي (مزامنة سحابية)",
        "en": "Offsite Backup (Cloud Sync)",
    },
    "settings.offsite_provider": {
        "ar": "مزود الخدمة السحابية",
        "en": "Cloud Provider",
    },
    "settings.offsite_provider_select": {
        "ar": "اختر مزوداً",
        "en": "Select a provider",
    },
    "settings.offsite_remote": {
        "ar": "المسار البعيد",
        "en": "Remote Path",
    },
    "settings.offsite_remote_hint": {
        "ar": "التنسيق: اسم-البعيد:المجلد (مثال: kazma-backup:kazma-backups)",
        "en": "Format: remote-name:folder (e.g. kazma-backup:kazma-backups)",
    },
    "settings.offsite_save": {
        "ar": "حفظ",
        "en": "Save",
    },
    "settings.offsite_saved": {
        "ar": "تم حفظ إعدادات النسخ الاحتياطي الخارجي.",
        "en": "Offsite backup configuration saved.",
    },
    "settings.offsite_test": {
        "ar": "اختبار الاتصال",
        "en": "Test Connection",
    },
    "settings.oidc_enabled": {
        "ar": "OIDC مفعّل",
        "en": "OIDC enabled",
    },
    "settings.outgoing_webhook_url": {
        "ar": "رابط الويب هوك الصادر",
        "en": "Outgoing Webhook URL",
    },
    "settings.parameters": {
        "ar": "المعاملات:",
        "en": "Parameters:",
    },
    "settings.password": {
        "ar": "كلمة المرور",
        "en": "Password",
    },
    "settings.password_min8": {
        "ar": "كلمة المرور (٨ على الأقل)",
        "en": "password (min 8)",
    },
    "settings.paste_yaml_json": {
        "ar": "الصق إعدادات YAML أو JSON هنا…",
        "en": "Paste YAML or JSON configuration here…",
    },
    "settings.personality_templates": {
        "ar": "قوالب الشخصية",
        "en": "Personality Templates",
    },
    "settings.platform_connectors": {
        "ar": "موصلات المنصات",
        "en": "Platform Connectors",
    },
    "settings.platform_users": {
        "ar": "مستخدمو المنصة",
        "en": "Platform users",
    },
    "settings.platform_users_help": {
        "ar": "التحكم متعدد المستخدمين (عارض / مشغّل / مسؤول).",
        "en": "Multi-user access control (viewer / operator / admin).",
    },
    "settings.postgres_ready": {
        "ar": "Postgres جاهز للتكرار",
        "en": "Postgres multi-replica ready",
    },
    "settings.preset": {
        "ar": "القالب",
        "en": "Preset",
    },
    "settings.profile_name": {
        "ar": "اسم الملف (حفظ باسم)",
        "en": "Profile Name (save as)",
    },
    "settings.provider": {
        "ar": "المزود",
        "en": "Provider",
    },
    "settings.proxy_country": {
        "ar": "الدولة (اختياري)",
        "en": "Country (optional)",
    },
    "settings.proxy_exit_ip": {
        "ar": "IP الخروج",
        "en": "Exit IP",
    },
    "settings.proxy_hint": {
        "ar": "إضافة اختيارية. عند التفعيل، يمر كشط الويب عبر هذا البروكسي لمقاومة حظر IP. اتركه معطلاً للجلب المباشر.",
        "en": "Opt-in addon. When enabled, web scraping routes through this proxy for resilience to IP blocks. Leave disabled for direct fetching.",
    },
    "settings.proxy_host": {
        "ar": "المضيف",
        "en": "Host",
    },
    "settings.proxy_network": {
        "ar": "نوع الشبكة",
        "en": "Network type",
    },
    "settings.proxy_network_mixed": {
        "ar": "مختلط",
        "en": "Mixed",
    },
    "settings.proxy_network_mobile": {
        "ar": "محمول",
        "en": "Mobile",
    },
    "settings.proxy_network_residential": {
        "ar": "سكني",
        "en": "Residential",
    },
    "settings.proxy_none": {
        "ar": "لا شيء (مباشر)",
        "en": "None (direct)",
    },
    "settings.proxy_password": {
        "ar": "كلمة المرور",
        "en": "Password",
    },
    "settings.proxy_port": {
        "ar": "المنفذ",
        "en": "Port",
    },
    "settings.proxy_provider": {
        "ar": "المزود",
        "en": "Provider",
    },
    "settings.proxy_save": {
        "ar": "حفظ البروكسي",
        "en": "Save Proxy",
    },
    "settings.proxy_saved": {
        "ar": "تم حفظ إعدادات البروكسي",
        "en": "Proxy settings saved",
    },
    "settings.proxy_sticky": {
        "ar": "جلسة ثابتة (نفس IP عبر الطلبات)",
        "en": "Sticky session (same IP across requests)",
    },
    "settings.proxy_test": {
        "ar": "اختبار الاتصال",
        "en": "Test Connection",
    },
    "settings.proxy_title": {
        "ar": "مزود البروكسي",
        "en": "Proxy Provider",
    },
    "settings.proxy_username": {
        "ar": "اسم المستخدم",
        "en": "Username",
    },
    "settings.python": {
        "ar": "بايثون",
        "en": "Python",
    },
    "settings.refresh_gateway": {
        "ar": "تحديث البوابة",
        "en": "Refresh Gateway",
    },
    "settings.refreshing": {
        "ar": "جارٍ التحديث...",
        "en": "Refreshing...",
    },
    "settings.release_notes": {
        "ar": "ملاحظات الإصدار",
        "en": "Release notes",
    },
    "settings.remove_model": {
        "ar": "إزالة النموذج من القائمة",
        "en": "Remove model from list",
    },
    "settings.reset_to_defaults": {
        "ar": "إعادة التعيين للافتراضي",
        "en": "Reset to Defaults",
    },
    "settings.revoke": {
        "ar": "إلغاء",
        "en": "Revoke",
    },
    "settings.right": {
        "ar": "يمين",
        "en": "Right",
    },
    "settings.role": {
        "ar": "الدور",
        "en": "Role",
    },
    "settings.run_comparison": {
        "ar": "تشغيل المقارنة",
        "en": "Run Comparison",
    },
    "settings.running": {
        "ar": "جاري التشغيل…",
        "en": "Running…",
    },
    "settings.running_latest": {
        "ar": "تعمل بأحدث إصدار",
        "en": "Running the latest version",
    },
    "settings.safety_hitl": {
        "ar": "الأمان (الموافقة البشرية)",
        "en": "Safety (HITL)",
    },
    "settings.save": {
        "ar": "حفظ",
        "en": "Save",
    },
    "settings.save_agent": {
        "ar": "حفظ الوكيل",
        "en": "Save Agent",
    },
    "settings.save_appearance": {
        "ar": "حفظ المظهر",
        "en": "Save Appearance",
    },
    "settings.save_context": {
        "ar": "حفظ إعدادات السياق",
        "en": "Save Context Settings",
    },
    "settings.save_logging": {
        "ar": "حفظ إعدادات السجل",
        "en": "Save Logging",
    },
    "settings.save_nonstop": {
        "ar": "حفظ إعدادات التشغيل المتواصل",
        "en": "Save Non-Stop Settings",
    },
    "settings.save_profile": {
        "ar": "حفظ الملف",
        "en": "Save Profile",
    },
    "settings.save_safety": {
        "ar": "حفظ إعدادات الأمان",
        "en": "Save Safety Settings",
    },
    "settings.save_checkpoint_retention": {
        "ar": "حفظ مدة الاحتفاظ",
        "en": "Save Retention",
    },
    "settings.save_swarm_retention": {
        "ar": "حفظ مدة الاحتفاظ",
        "en": "Save Retention",
    },
    "settings.saved": {
        "ar": "تم الحفظ",
        "en": "Saved",
    },
    "settings.saved_profiles": {
        "ar": "ملفات النماذج المحفوظة",
        "en": "Saved Model Profiles",
    },
    "settings.saving": {
        "ar": "جاري الحفظ…",
        "en": "Saving…",
    },
    "settings.search_skills": {
        "ar": "ابحث في المهارات…",
        "en": "Search skills…",
    },
    "settings.search_tools": {
        "ar": "ابحث في الأدوات بالاسم أو الوصف أو الفئة…",
        "en": "Search tools by name, description, or category…",
    },
    "settings.select_connector": {
        "ar": "— اختر موصلاً —",
        "en": "— select connector —",
    },
    "settings.select_model": {
        "ar": "— اختر نموذجاً —",
        "en": "— select model —",
    },
    "settings.select_sections": {
        "ar": "اختر الأقسام للاستيراد",
        "en": "Select sections to import",
    },
    "settings.selected_n": {
        "ar": "({n} محدد)",
        "en": "({n} selected)",
    },
    "settings.selective_import": {
        "ar": "استيراد انتقائي",
        "en": "Selective Import",
    },
    "settings.set": {
        "ar": "تعيين",
        "en": "Set",
    },
    "settings.sidebar_position": {
        "ar": "موضع الشريط الجانبي",
        "en": "Sidebar Position",
    },
    "settings.slack": {
        "ar": "سلاك",
        "en": "Slack",
    },
    "settings.sliding_window": {
        "ar": "النافذة المنزلقة",
        "en": "Sliding Window",
    },
    "settings.smtp_host": {
        "ar": "خادم SMTP",
        "en": "SMTP Host",
    },
    "settings.smtp_port": {
        "ar": "منفذ SMTP",
        "en": "SMTP Port",
    },
    "settings.sse_http": {
        "ar": "SSE (HTTP)",
        "en": "SSE (HTTP)",
    },
    "settings.status_active": {
        "ar": "نشط",
        "en": "active",
    },
    "settings.status_disabled": {
        "ar": "معطّل",
        "en": "disabled",
    },
    "settings.stdio_local": {
        "ar": "stdio (عملية محلية)",
        "en": "stdio (local process)",
    },
    "settings.strategy": {
        "ar": "الاستراتيجية",
        "en": "Strategy",
    },
    "settings.summarization_threshold": {
        "ar": "حد التلخيص:",
        "en": "Summarization Threshold:",
    },
    "settings.summarize_old": {
        "ar": "تلخيص الرسائل القديمة",
        "en": "Summarize Old Messages",
    },
    "settings.checkpoint_retention_days": {
        "ar": "الاحتفاظ بكامل السجل بعد آخر رسالة (أيام)",
        "en": "Keep the full history after a chat's last message for (days)",
    },
    "settings.checkpoint_retention_env": {
        "ar": "هذه القيمة مضبوطة بالمتغير KAZMA_CHECKPOINT_RETENTION_DAYS في بيئة الخادم؛ غيّرها هناك.",
        "en": "Set by KAZMA_CHECKPOINT_RETENTION_DAYS in the server's environment; change it there.",
    },
    "settings.checkpoint_retention_hint": {
        "ar": "كل محادثة تحتفظ بآخر 200 خطوة من سجل خطواتها (ما يستخدمه التراجع والاستئناف)، والمحادثة الخاملة طوال هذه المدة تحتفظ بآخر 10 فقط. الرسائل نفسها والذاكرة لا تتأثر. تتم المراجعة كل 15 دقيقة. القيمة 0 تحتفظ بكل الخطوات.",
        "en": "Every chat keeps the newest 200 steps of its step history (what undo and resume use); a chat idle this long keeps its newest 10. The messages themselves and memory are not affected. The check runs every 15 minutes. 0 keeps every step.",
    },
    "settings.checkpoint_retention_saved": {
        "ar": "تم حفظ مدة الاحتفاظ بسجل الخطوات",
        "en": "Step history retention saved",
    },
    "settings.checkpoint_retention_title": {
        "ar": "سجل خطوات المحادثات",
        "en": "Chat step history",
    },
    "settings.swarm_retention_days": {
        "ar": "الاحتفاظ بالمهام المنتهية (أيام)",
        "en": "Keep finished tasks for (days)",
    },
    "settings.swarm_retention_hint": {
        "ar": "تُحذف مهام السرب المكتملة والفاشلة والملغاة والمنتهية المهلة الأقدم من هذه المدة، وتتم المراجعة كل 15 دقيقة. المهام المتوقفة والجارية لا تُحذف أبدًا. القيمة 0 تحتفظ بكل المهام.",
        "en": "Completed, failed, cancelled and timed-out swarm tasks older than this are deleted; the check runs every 15 minutes. Paused and running tasks are never deleted. 0 keeps every task.",
    },
    "settings.swarm_retention_saved": {
        "ar": "تم حفظ مدة الاحتفاظ بمهام السرب",
        "en": "Swarm task retention saved",
    },
    "settings.swarm_retention_title": {
        "ar": "سجل مهام السرب",
        "en": "Swarm task history",
    },
    "settings.system_diagnostics": {
        "ar": "تشخيص النظام",
        "en": "System Diagnostics",
    },
    "settings.system_logs": {
        "ar": "سجلات النظام",
        "en": "System Logs",
    },
    "settings.system_prompt": {
        "ar": "الموجه النظامي",
        "en": "System Prompt",
    },
    "settings.system_prompt_placeholder": {
        "ar": "أنت مساعد ذكاء اصطناعي مفيد...",
        "en": "You are a helpful AI assistant...",
    },
    "settings.tab_account": {
        "ar": "الحساب",
        "en": "Account",
    },
    "settings.tab_agent": {
        "ar": "الوكيل",
        "en": "Agent",
    },
    "settings.tab_appearance": {
        "ar": "المظهر",
        "en": "Appearance",
    },
    "settings.tab_backup": {
        "ar": "النسخ الاحتياطي",
        "en": "Backup",
    },
    "settings.tab_connectors": {
        "ar": "الموصلات",
        "en": "Connectors",
    },
    "settings.tab_documents": {
        "ar": "المستندات",
        "en": "Documents",
    },
    "settings.tab_email": {
        "ar": "البريد",
        "en": "Email",
    },
    "settings.tab_embedder": {
        "ar": "المُضمِّن",
        "en": "Embedder",
    },
    "settings.tab_import": {
        "ar": "استيراد/تصدير",
        "en": "Import/Export",
    },
    "settings.tab_mcp": {
        "ar": "MCP",
        "en": "MCP",
    },
    "settings.tab_memory": {
        "ar": "الذاكرة",
        "en": "Memory",
    },
    "settings.tab_models": {
        "ar": "النماذج",
        "en": "Models",
    },
    "settings.tab_packages": {
        "ar": "الحزم",
        "en": "Packages",
    },
    "settings.tab_providers_connectors": {
        "ar": "المزودون والموصلات",
        "en": "Providers & Connectors",
    },
    "settings.tab_services": {
        "ar": "الخدمات",
        "en": "Services",
    },
    "settings.tab_shortcuts": {
        "ar": "الاختصارات",
        "en": "Shortcuts",
    },
    "settings.tab_skills": {
        "ar": "المهارات",
        "en": "Skills",
    },
    "settings.tab_system": {
        "ar": "النظام",
        "en": "System",
    },
    "settings.tab_tools": {
        "ar": "الأدوات",
        "en": "Tools",
    },
    "settings.tab_voice": {
        "ar": "الصوت",
        "en": "Voice",
    },
    "settings.tab_x": {
        "ar": "إكس",
        "en": "X",
    },
    "settings.telegram": {
        "ar": "تيليجرام",
        "en": "Telegram",
    },
    "settings.temperature": {
        "ar": "درجة الحرارة:",
        "en": "Temperature:",
    },
    "settings.tenant_id": {
        "ar": "معرّف المستأجر",
        "en": "tenant-id",
    },
    "settings.tenants": {
        "ar": "المستأجرون",
        "en": "Tenants",
    },
    "settings.test": {
        "ar": "اختبار",
        "en": "Test",
    },
    "settings.test_arguments_json": {
        "ar": "وسائط الاختبار (JSON)",
        "en": "Test Arguments (JSON)",
    },
    "settings.test_before_save": {
        "ar": "اختبر الاتصال قبل الحفظ.",
        "en": "Test the connection before saving.",
    },
    "settings.test_connection": {
        "ar": "اختبار الاتصال",
        "en": "Test Connection",
    },
    "settings.test_prompt": {
        "ar": "نص الاختبار",
        "en": "Test Prompt",
    },
    "settings.test_prompt_placeholder": {
        "ar": "أدخل نصاً لتجربته عبر النماذج…",
        "en": "Enter a prompt to test across models…",
    },
    "settings.testing": {
        "ar": "جاري الاختبار…",
        "en": "Testing…",
    },
    "settings.theme": {
        "ar": "السمة",
        "en": "Theme",
    },
    "settings.time_travel_auto_maintain": {
        "ar": "تنظيف تلقائي (يوميًا)",
        "en": "Clean up automatically (daily)",
    },
    "settings.time_travel_auto_maintain_hint": {
        "ar": "ينفّذ التنظيف + VACUUM كل 24 ساعة بعد الإقلاع. أوقفه لتشغيله يدويًا فقط من لوحة التحكم.",
        "en": "Runs the prune + VACUUM every 24h on boot-cadence. Turn off to only run it manually from the Dashboard.",
    },
    "settings.time_travel_hint": {
        "ar": "تلتقط كاظمه لقطات من كل محادثة لتتيح أمرَي /replay N و /fork N. هذا الحد يتحكم بعدد اللقطات المحفوظة لكل محادثة — القيم الأعلى تسمح بتراجع أعمق لكنها تزيد حجم snapshots.db (ينمو لكل محادثة).",
        "en": "Kazma snapshots each conversation turn so /replay N and /fork N can rewind it. This cap controls how many snapshots are kept per thread — higher values allow deeper rewinds but grow snapshots.db (it accumulates per thread).",
    },
    "settings.time_travel_max_snapshots": {
        "ar": "عدد اللقطات لكل محادثة",
        "en": "Snapshots per thread",
    },
    "settings.time_travel_max_snapshots_hint": {
        "ar": "الافتراضي 50. كل لقطة تخزّن حالة المحادثة الكاملة عند تكرار مشرف واحد. القيم الأقل تصغّر قاعدة اللقطات؛ تُحذف لقطات المحادثات القديمة فقط عند التقاط جديد لنفس المحادثة.",
        "en": "Default 50. Each snapshot stores the full conversation state at one supervisor iteration. Lower values shrink the snapshot DB; old threads' snapshots are evicted only when that thread captures again.",
    },
    "settings.time_travel_restart_btn": {
        "ar": "إعادة تشغيل الخادم",
        "en": "Restart server",
    },
    "settings.time_travel_restart_message": {
        "ar": "سيعاد تشغيل الخادم لتطبيق حد اللقطات الجديد. ستُعاد الصفحة الاتصال تلقائيًا. جلسات المحادثة غير المحفوظة محفوظة.",
        "en": "The server will restart to apply the new snapshot cap. The page will reconnect automatically. Unsaved chat sessions are persisted.",
    },
    "settings.time_travel_restart_needed": {
        "ar": "إعادة تشغيل مطلوبة للتطبيق",
        "en": "Restart required to apply",
    },
    "settings.time_travel_restart_needed_hint": {
        "ar": "يُقرأ حد اللقطات عند إقلاع الخادم. ستُعاد الصفحة الاتصال تلقائيًا بعد إعادة التشغيل.",
        "en": "The snapshot cap is read when the server boots. The page will reconnect automatically after restart.",
    },
    "settings.time_travel_restart_noop": {
        "ar": "لا حاجة لإعادة التشغيل — الحد الحالي مطابق.",
        "en": "No restart needed — the running cap already matches.",
    },
    "settings.time_travel_restart_title": {
        "ar": "إعادة تشغيل الخادم؟",
        "en": "Restart server?",
    },
    "settings.time_travel_retention_days": {
        "ar": "الاحتفاظ (أيام)",
        "en": "Retention (days)",
    },
    "settings.time_travel_retention_days_hint": {
        "ar": "تُحذف اللقطات الأقدم من هذا العدد بواسطة مهمة الصيانة. يُطبَّق في التشغيل التالي — لا حاجة لإعادة التشغيل.",
        "en": "Snapshots older than this are deleted by the maintenance job. Applies to the next run — no restart needed.",
    },
    "settings.time_travel_save": {
        "ar": "حفظ إعدادات السفر عبر الزمن",
        "en": "Save time travel settings",
    },
    "settings.time_travel_title": {
        "ar": "السفر عبر الزمن (إعادة / تفريع)",
        "en": "Time travel (replay / fork)",
    },
    "settings.timeout_seconds": {
        "ar": "مهلة الانتظار (ثانية)",
        "en": "Timeout (seconds)",
    },
    "settings.title": {
        "ar": "الإعدادات",
        "en": "Settings",
    },
    "settings.token": {
        "ar": "الرمز / المفتاح",
        "en": "Token / Key",
    },
    "settings.token_name": {
        "ar": "اسم الرمز",
        "en": "Token name",
    },
    "settings.tool_registry": {
        "ar": "سجل الأدوات",
        "en": "Tool Registry",
    },
    "settings.tools_count": {
        "ar": "أدوات",
        "en": "tools",
    },
    "settings.tools_requiring_approval": {
        "ar": "الأدوات التي تتطلب موافقة (مفصولة بفواصل)",
        "en": "Tools Requiring Approval (comma-separated)",
    },
    "settings.transport": {
        "ar": "نوع النقل",
        "en": "Transport",
    },
    "settings.truncate_oldest": {
        "ar": "اقتطاع الأقدم",
        "en": "Truncate Oldest",
    },
    "settings.turn_notify_hint": {
        "ar": "إشعار سطح المكتب وعلامة في عنوان التبويب عند انتهاء مهمة الوكيل في تبويب بالخلفية. ينطبق على محادثة الويب.",
        "en": "Desktop notification + tab-title marker when an agent turn completes in a background tab. Applies to the web chat.",
    },
    "settings.turn_notify_hint2": {
        "ar": "سيطلب المتصفح إذن الإشعارات عند إرسال أول مهمة. رفض الإذن هناك يعطل هذه الميزة أيضاً.",
        "en": "The browser will ask for notification permission the first time you send a task. Denying it there also disables this feature.",
    },
    "settings.turn_notify_label": {
        "ar": "أشعرني عند انتهاء المهمة الجارية",
        "en": "Notify me when a running task finishes",
    },
    "settings.turn_notify_save": {
        "ar": "حفظ تفضيل الإشعارات",
        "en": "Save notification preference",
    },
    "settings.turn_notify_title": {
        "ar": "إشعارات المهام",
        "en": "Task notifications",
    },
    "settings.uninstall": {
        "ar": "إزالة",
        "en": "Uninstall",
    },
    "settings.unknown": {
        "ar": "غير معروف",
        "en": "unknown",
    },
    "settings.update_available": {
        "ar": "يتوفر تحديث:",
        "en": "Update available:",
    },
    "settings.update_check_failed": {
        "ar": "تعذّر التحقق من التحديثات:",
        "en": "Could not check for updates:",
    },
    "settings.upload_file": {
        "ar": "رفع ملف",
        "en": "Upload File",
    },
    "settings.upload_or_paste": {
        "ar": "ارفع ملف إعدادات أو الصقه (YAML أو JSON). سترى ما سيتغير قبل كتابة أي شيء، وتبقى المفاتيح التي لديك الآن.",
        "en": "Upload or paste a settings file (YAML or JSON). You see what will change before anything is written, and the keys you have now are kept.",
    },
    "settings.url": {
        "ar": "العنوان",
        "en": "URL",
    },
    "settings.username": {
        "ar": "اسم المستخدم",
        "en": "Username",
    },
    "settings.vector_status_checking": {
        "ar": "المتجهات: لم يُفحص المخزن بعد",
        "en": "Vector: not checked yet",
    },
    "settings.vector_status_full": {
        "ar": "المتجهات: كاملة (محلية)",
        "en": "Vector: full (local)",
    },
    "settings.vector_status_needs_setup": {
        "ar": "المتجهات: تحتاج إلى إعداد",
        "en": "Vector: needs setup",
    },
    "settings.vector_status_remote_ready": {
        "ar": "المتجهات: بحث وكتابة عن بُعد",
        "en": "Vector: remote search + write",
    },
    "settings.vector_status_unreachable": {
        "ar": "المتجهات: المخزن البعيد لا يستجيب",
        "en": "Vector: remote store not answering",
    },
    "settings.web": {
        "ar": "ويب",
        "en": "Web",
    },
    "settings.webhook_secret": {
        "ar": "سر الويب هوك",
        "en": "Webhook Secret",
    },
    "settings.webhooks": {
        "ar": "الويب هوك",
        "en": "Webhooks",
    },
    "settings.workspace": {
        "ar": "مساحة العمل",
        "en": "Workspace",
    },
    "settings.x_access_token": {
        "ar": "رمز الوصول",
        "en": "Access Token",
    },
    "settings.x_access_token_secret": {
        "ar": "سر رمز الوصول",
        "en": "Access Token Secret",
    },
    "settings.x_api_key": {
        "ar": "مفتاح API",
        "en": "API Key (consumer key)",
    },
    "settings.x_api_key_secret": {
        "ar": "سر مفتاح API",
        "en": "API Key Secret",
    },
    "settings.x_audit_action": {
        "ar": "العملية",
        "en": "Action",
    },
    "settings.x_audit_details": {
        "ar": "انقر لقراءة النص",
        "en": "Click a row to read the tweet",
    },
    "settings.x_audit_empty": {
        "ar": "لا يوجد نشاط مسجّل بعد — ستظهر العمليات هنا بعد أول استدعاء للواجهة.",
        "en": "No X activity recorded yet — entries appear here after the first API call.",
    },
    "settings.x_audit_hint": {
        "ar": "كل عملية من واجهة إكس — نشر، ردود، حذف، أخطاء — بالنص والوقت. انقر على صف لقراءة التغريدة.",
        "en": "Every X API call — posts, replies, deletes, errors — with the tweet text and timestamp. Click a row to read the tweet.",
    },
    "settings.x_audit_moved": {
        "ar": "كل طلب أرسله كازما إلى إكس مع النص الكامل — أصبح الآن في صفحة المهام المجدولة بجوار المنشورات التي أنتجته.",
        "en": "Every X call Kazma made, with full content — now on the Scheduled page, next to the posts that produced it.",
    },
    "settings.x_audit_open": {
        "ar": "فتح نشاط إكس",
        "en": "Open X activity",
    },
    "settings.x_audit_refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "settings.x_audit_status": {
        "ar": "الحالة",
        "en": "Status",
    },
    "settings.x_audit_text": {
        "ar": "نص المنشور",
        "en": "Post text",
    },
    "settings.x_audit_title": {
        "ar": "سجل التدقيق",
        "en": "Audit log",
    },
    "settings.x_audit_tweet": {
        "ar": "التغريدة",
        "en": "Tweet",
    },
    "settings.x_audit_when": {
        "ar": "الوقت",
        "en": "When",
    },
    "settings.x_configured": {
        "ar": "المفاتيح محفوظة",
        "en": "Keys stored",
    },
    "settings.x_disconnect": {
        "ar": "قطع الاتصال",
        "en": "Disconnect",
    },
    "settings.x_docs_hint": {
        "ar": "الدليل:",
        "en": "Guide:",
    },
    "settings.x_docs_link": {
        "ar": "ناشر إكس",
        "en": "X publisher",
    },
    "settings.x_enabled": {
        "ar": "تفعيل النشر (بعد حفظ المفاتيح)",
        "en": "Enable posting (after keys are saved)",
    },
    "settings.x_handle": {
        "ar": "المعرف (للعرض وتجاهل الإشارة الذاتية)",
        "en": "Handle (for display + mention skip)",
    },
    "settings.x_hitl_note": {
        "ar": "حماية: x_post وx_delete_post تتطلبان موافقة دائماً حتى في وضع YOLO. الواجهة الرسمية فقط — لا نشر عبر المتصفح.",
        "en": "Fail-safe: x_post / x_delete_post always interrupt for approval, even in YOLO. Official API only — no browser posting.",
    },
    "settings.x_howto": {
        "ar": "developer.x.com ← مشروع وتطبيق ← مصادقة المستخدم = قراءة وكتابة ← المفاتيح ← ولّد قيم OAuth 1.0a الأربع. رمز Bearer لا ينشر. ضع وسم الحساب الآلي في إعدادات إكس.",
        "en": "developer.x.com → Project + App → User authentication = Read and write → Keys and tokens → generate the four OAuth 1.0a values. The Bearer token cannot post. Label the account Automated in X settings.",
    },
    "settings.x_kill_switch": {
        "ar": "KAZMA_X_POST=0 مفعّل — النشر معطّل تماماً.",
        "en": "KAZMA_X_POST=0 is set — posting is hard-disabled.",
    },
    "settings.x_max_day": {
        "ar": "أقصى تغريدات في اليوم",
        "en": "Max posts per day (Kazma cap)",
    },
    "settings.x_max_month": {
        "ar": "أقصى تغريدات في 30 يوماً",
        "en": "Max posts per 30 days",
    },
    "settings.x_not_configured": {
        "ar": "غير مُعدّ",
        "en": "Not configured",
    },
    "settings.x_per_day": {
        "ar": "اليوم",
        "en": "today",
    },
    "settings.x_quota": {
        "ar": "حد كاظمه",
        "en": "Kazma cap",
    },
    "settings.x_refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "settings.x_save": {
        "ar": "حفظ المفاتيح",
        "en": "Save keys",
    },
    "settings.x_show_keys": {
        "ar": "إظهار القيم أثناء الكتابة",
        "en": "Show values while typing",
    },
    "settings.x_status": {
        "ar": "الحالة",
        "en": "Status",
    },
    "settings.x_subtitle": {
        "ar": "انشر باسمك عبر OAuth 1.0a من developer.x.com (قراءة وكتابة). المفاتيح في الخزنة لا في الدردشة. كل تغريدة تحتاج موافقتك.",
        "en": "Tweet as you via developer.x.com OAuth 1.0a (Read + Write). Keys go in the vault — never in chat. Each post still needs your approval.",
    },
    "settings.x_test": {
        "ar": "اختبار (users/me)",
        "en": "Test (users/me)",
    },
    "settings.x_title": {
        "ar": "إكس (واجهة رسمية)",
        "en": "X (official API)",
    },
    "settings.outbound_allowed_targets": {
        "ar": "أهداف الإرسال المسموحة",
        "en": "Outbound allowed targets",
    },
    "settings.outbound_allowed_targets_hint": {
        "ar": "فارغ = مسموح (HITL يتولى الأمر). قائمة مفصولة بفواصل تقيّد البريد والقنوات قبل الإرسال.",
        "en": "Empty = permissive (HITL still applies). A comma-separated list restricts email/channel targets before send.",
    },
    "settings.shortcut_updated": {
        "ar": "حُدّث اختصار \"{action}\"",
        "en": "Shortcut for \"{action}\" updated",
    },
    "settings.shortcuts_reset": {
        "ar": "أُعيدت الاختصارات",
        "en": "Shortcuts reset",
    },
    "settings.username_password_required": {
        "ar": "اسم المستخدم وكلمة المرور مطلوبان",
        "en": "Username and password required",
    },
    "settings.user_create_failed": {
        "ar": "فشل إنشاء المستخدم",
        "en": "Failed to create user",
    },
    "settings.user_created": {
        "ar": "أُنشئ المستخدم",
        "en": "User created",
    },
    "settings.failed_with_reason": {
        "ar": "فشل: {error}",
        "en": "Failed: {error}",
    },
    "settings.update_failed": {
        "ar": "فشل التحديث",
        "en": "Update failed",
    },
    "settings.user_updated": {
        "ar": "حُدّث المستخدم",
        "en": "User updated",
    },
    "settings.delete_failed": {
        "ar": "فشل الحذف",
        "en": "Delete failed",
    },
    "settings.user_deleted": {
        "ar": "حُذف المستخدم",
        "en": "User deleted",
    },
    "settings.tenant_id_required": {
        "ar": "معرّف المستأجر مطلوب",
        "en": "Tenant id required",
    },
    "settings.failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "settings.tenant_added": {
        "ar": "أُضيف المستأجر",
        "en": "Tenant added",
    },
    "settings.passwords_mismatch": {
        "ar": "كلمتا المرور غير متطابقتين",
        "en": "Passwords do not match",
    },
    "settings.password_min": {
        "ar": "يجب ألا تقل كلمة المرور عن 8 أحرف",
        "en": "Password must be at least 8 characters",
    },
    "settings.password_changed": {
        "ar": "غُيّرت كلمة المرور",
        "en": "Password changed",
    },
    "settings.token_name_required": {
        "ar": "اسم الرمز مطلوب",
        "en": "Token name required",
    },
    "settings.token_created_copy": {
        "ar": "أُنشئ الرمز — انسخه أدناه (يُعرض مرة واحدة)",
        "en": "Token created — copy it below (shown once)",
    },
    "settings.token_created": {
        "ar": "أُنشئ الرمز",
        "en": "Token created",
    },
    "settings.token_copied": {
        "ar": "نُسخ الرمز إلى الحافظة",
        "en": "Token copied to clipboard",
    },
    "settings.copy_failed_manual": {
        "ar": "فشل النسخ — حدّد الرمز وانسخه يدوياً",
        "en": "Copy failed — select the token and copy manually",
    },
    "settings.curl_copied": {
        "ar": "نُسخ مثال curl",
        "en": "curl example copied",
    },
    "settings.copy_failed": {
        "ar": "فشل النسخ",
        "en": "Copy failed",
    },
    "settings.missing_token_id": {
        "ar": "معرّف الرمز مفقود",
        "en": "Missing token id",
    },
    "settings.revoke_failed": {
        "ar": "فشل الإلغاء: {error}",
        "en": "Revoke failed: {error}",
    },
    "settings.token_revoked": {
        "ar": "أُلغي الرمز",
        "en": "Token revoked",
    },
    "settings.invalid_json_args": {
        "ar": "وسائط JSON غير صالحة",
        "en": "Invalid JSON arguments",
    },
    "settings.backup_downloaded": {
        "ar": "حُمّل النسخ الاحتياطي",
        "en": "Backup downloaded",
    },
    "settings.backup_failed_reason": {
        "ar": "فشل النسخ الاحتياطي: {error}",
        "en": "Backup failed: {error}",
    },
    "settings.settings_backup_hint": {
        "ar": "هذه إعداداتك فقط. تذكر النسخة الاحتياطية مفاتيحك بأسمائها (إشارات إلى خزنة هذا التثبيت) ولا تحفظ المفاتيح نفسها. النسخ الاحتياطية الكاملة للمحادثات والذاكرة والملفات في تبويب النسخ الاحتياطي.",
        "en": "These are your settings only. A backup names your keys (references to this install's vault) and never holds the keys themselves. Full backups of chats, memory and files are on the Backup tab.",
    },
    "settings.restore_backup": {
        "ar": "استعادة نسخة احتياطية من الإعدادات…",
        "en": "Restore settings backup…",
    },
    "settings.restoring": {
        "ar": "جارٍ الاستعادة…",
        "en": "Restoring…",
    },
    "settings.undo_restore": {
        "ar": "التراجع عن آخر استعادة",
        "en": "Undo last restore",
    },
    "settings.select_a_section": {
        "ar": "اختر قسمًا واحدًا على الأقل للاستيراد.",
        "en": "Pick at least one section to import.",
    },
    "settings.restore_source_pasted": {
        "ar": "النص أعلاه",
        "en": "the text above",
    },
    "settings.restore_too_large": {
        "ar": "الملف أكبر مما يمكن أن تكون عليه نسخة احتياطية للإعدادات (10 ميغابايت).",
        "en": "The file is larger than a settings backup can be (10 MB).",
    },
    "settings.restore_and_more": {
        "ar": "… و{count} غيرها",
        "en": "… and {count} more",
    },
    "settings.restore_from": {
        "ar": "من: {source}",
        "en": "From: {source}",
    },
    "settings.restore_backup_made": {
        "ar": "أُنشئت النسخة في {date}.",
        "en": "Backup made {date}.",
    },
    "settings.restore_backup_version": {
        "ar": "إصدار Kazma: {version}",
        "en": "Kazma version: {version}",
    },
    "settings.restore_will_change": {
        "ar": "الإعدادات التي ستتغير: {count}",
        "en": "Settings that will change: {count}",
    },
    "settings.restore_list_added": {
        "ar": "تعود إلى {list}: {names}",
        "en": "Back in {list}: {names}",
    },
    "settings.restore_list_updated": {
        "ar": "تُحدَّث في {list}: {names}",
        "en": "Updated in {list}: {names}",
    },
    "settings.restore_keys_back": {
        "ar": "مفاتيح تُستعاد من خزنة هذا التثبيت (دون إدخال شيء): {count}",
        "en": "Keys brought back from this install's vault (nothing to enter): {count}",
    },
    "settings.restore_keys_reenter": {
        "ar": "مفاتيح لم تعد في هذه الخزنة، أدخلها مجددًا من الإعدادات: {count}",
        "en": "Keys this vault no longer has, to enter again in Settings: {count}",
    },
    "settings.restore_keys_kept": {
        "ar": "المفاتيح التي لديك الآن تبقى كما هي: {count}",
        "en": "Keys you have now stay as they are: {count}",
    },
    "settings.restore_state_kept": {
        "ar": "تبقى كما هي: {count} من سجلات Kazma الخاصة وسجلات تسجيل الدخول.",
        "en": "Left as they are: {count} of Kazma's own records and sign-in records.",
    },
    "settings.restore_learned_kept": {
        "ar": "ما تعلّمه Kazma (الروح) يبقى كما هو الآن.",
        "en": "What Kazma learned (its Soul) stays as it is now.",
    },
    "settings.restore_retired_kept": {
        "ar": "القيم الافتراضية التي غيّرها Kazma بعد النسخة تبقى على قيمتها الجديدة: {count}",
        "en": "Defaults Kazma changed since the backup keep their new value: {count}",
    },
    "settings.restore_refused": {
        "ar": "لم تُستعد لأن القيمة غير مسموح بها: {count}",
        "en": "Not restored, the value is not allowed: {count}",
    },
    "settings.restore_unchanged": {
        "ar": "مطابقة للنسخة أصلًا: {count}",
        "en": "Already as in the backup: {count}",
    },
    "settings.restore_nothing_deleted": {
        "ar": "لا يُحذف شيء، ويمكنك التراجع عن هذه الاستعادة لاحقًا.",
        "en": "Nothing is deleted, and you can undo this restore afterwards.",
    },
    "settings.restore_restart_title": {
        "ar": "إعادة تشغيل Kazma الآن؟",
        "en": "Restart Kazma now?",
    },
    "settings.restore_restart_message": {
        "ar": "بعض الإعدادات تُقرأ عند بدء تشغيل Kazma: المزوّدون والنماذج وتطبيقات المحادثة. أعد التشغيل الآن ليستخدم كل جزء من Kazma الإعدادات المستعادة.",
        "en": "Some settings are read when Kazma starts: providers, models, chat apps. Restart now so every part of Kazma uses the restored ones.",
    },
    "settings.restore_nothing_to_do": {
        "ar": "إعداداتك مطابقة لهذه النسخة بالفعل؛ لا شيء لاستعادته.",
        "en": "Your settings already match this backup; nothing to restore.",
    },
    "settings.restore_preview_title": {
        "ar": "استعادة الإعدادات؟",
        "en": "Restore settings?",
    },
    "settings.restore_confirm": {
        "ar": "استعادة",
        "en": "Restore",
    },
    "settings.restore_done": {
        "ar": "الإعدادات المستعادة: {count}",
        "en": "Settings restored: {count}",
    },
    "settings.restore_changed_since": {
        "ar": "تغيّرت إعداداتك بعد المعاينة؛ هذه الخطة كما هي الآن.",
        "en": "Your settings changed since the preview; here is the plan as it is now.",
    },
    "settings.restore_failed": {
        "ar": "فشلت الاستعادة: {error}",
        "en": "Restore failed: {error}",
    },
    "settings.undo_nothing": {
        "ar": "لا توجد استعادة للتراجع عنها.",
        "en": "There is no restore to undo.",
    },
    "settings.undo_restored_at": {
        "ar": "تمت الاستعادة في {date}.",
        "en": "Restored {date}.",
    },
    "settings.undo_will_revert": {
        "ar": "إعدادات تعود إلى ما كانت عليه: {count}",
        "en": "Settings that go back to what they were: {count}",
    },
    "settings.undo_changed_since": {
        "ar": "تغيّرت مجددًا بعد الاستعادة، وتبقى كما هي الآن: {count}",
        "en": "Changed again since the restore, left as they are now: {count}",
    },
    "settings.undo_keys_kept": {
        "ar": "المفاتيح التي أعادتها الاستعادة تبقى: {count}",
        "en": "Keys the restore brought back stay: {count}",
    },
    "settings.undo_title": {
        "ar": "التراجع عن آخر استعادة؟",
        "en": "Undo the last restore?",
    },
    "settings.undo_confirm": {
        "ar": "تراجع",
        "en": "Undo",
    },
    "settings.undo_done": {
        "ar": "الإعدادات التي عادت إلى ما كانت عليه: {count}",
        "en": "Settings put back: {count}",
    },
    "settings.undo_failed": {
        "ar": "فشل التراجع: {error}",
        "en": "Undo failed: {error}",
    },
    "settings.config_exported": {
        "ar": "صُدّر الإعداد",
        "en": "Configuration exported",
    },
    "settings.export_failed": {
        "ar": "فشل التصدير: {error}",
        "en": "Export failed: {error}",
    },
    "settings.paste_or_upload": {
        "ar": "الصق أو ارفع بيانات الإعداد",
        "en": "Paste or upload config data",
    },
    "settings.google_drive_failed": {
        "ar": "اتصلت جوجل، لكن دريف فشل: {error}. اختبر البطاقة لخطوات الإصلاح.",
        "en": "Google connected, but Drive access failed: {error}. Test the card for the fix steps.",
    },
    "settings.offsite_connected": {
        "ar": "☁️ {provider} متصل — النسخ الخارجي نشط",
        "en": "☁️ {provider} connected — offsite backup active",
    },
    "settings.cloud_connect_incomplete": {
        "ar": "لم يكتمل الاتصال السحابي — افتح بطاقة المزود وحاول مجدداً",
        "en": "Cloud connect did not complete — open the provider card and try again",
    },
    "settings.retention_min": {
        "ar": "يجب أن يكون الاحتفاظ بنسخة واحدة على الأقل",
        "en": "Backup retention must be at least 1",
    },
    "settings.retention_saved": {
        "ar": "حُفظ الاحتفاظ — الإبقاء على أحدث {n} نسخ",
        "en": "Backup retention saved — keeping the newest {n} backups",
    },
    "settings.backup_complete": {
        "ar": "اكتمل النسخ: {dbs} قواعد، {mb} م.ب",
        "en": "Backup complete: {dbs} DBs, {mb} MB",
    },
    "settings.backup_deleted": {
        "ar": "حُذف النسخ الاحتياطي",
        "en": "Backup deleted",
    },
    "settings.archived_mb": {
        "ar": "أُرشف: {mb} م.ب. اضغط تحميل للحفظ.",
        "en": "Archived: {mb} MB. Click Download to save.",
    },
    "settings.archive_failed": {
        "ar": "فشل الأرشفة: {error}",
        "en": "Archive failed: {error}",
    },
    # ── Providers control plane ─────────────────────────────────────────
    "settings.providers_lede": {
        "ar": "المزود «قابل للوصول» عندما تستجيب قائمة نماذجه، و«يعمل» فقط عندما تعود استجابة حقيقية. هذان سؤالان مختلفان، وهذه الصفحة تسأل كليهما.",
        "en": "A provider is reachable when its model list answers, and working only when a real completion comes back. Those are different questions, so this page asks both.",
    },
    "settings.state_working": {"ar": "يعمل", "en": "Working"},
    "settings.state_chat_failing": {"ar": "المحادثة تفشل", "en": "Chat failing"},
    "settings.state_unreachable": {"ar": "لا يمكن الوصول", "en": "Unreachable"},
    "settings.state_untested": {"ar": "لم يُختبر", "en": "Not tested"},
    "settings.state_models_ok_chat_failing": {
        "ar": "النماذج تعمل · المحادثة تفشل",
        "en": "models ok · chat failing",
    },
    "settings.configured": {"ar": "مُهيّأ", "en": "Configured"},
    "settings.select_a_provider": {
        "ar": "اختر مزودًا من القائمة لعرض تفاصيله.",
        "en": "Select a provider from the list to see its details.",
    },
    "settings.api_style": {"ar": "نمط الواجهة", "en": "API style"},
    "settings.api_version": {"ar": "إصدار الواجهة", "en": "API version"},
    "settings.system_turn": {"ar": "دور النظام", "en": "System turn"},
    "settings.declared_not_inferred": {
        "ar": "معلن، غير مستنتج",
        "en": "declared, not inferred",
    },
    "settings.capabilities": {"ar": "القدرات", "en": "Capabilities"},
    "settings.not_verified": {"ar": "غير مُتحقق منه", "en": "Not verified"},
    "settings.not_verified_hint": {
        "ar": "لم يقس أحد هذه القدرة بعد. شغّل scripts/provider_conformance.py --live لتحويلها إلى قيمة مقيسة.",
        "en": "Nobody has measured this yet. Run scripts/provider_conformance.py --live to turn it into a measured value.",
    },
    "settings.checks": {"ar": "الفحوصات", "en": "Checks"},
    "settings.check_model_list": {"ar": "قائمة النماذج", "en": "Model list"},
    "settings.check_completion": {"ar": "استجابة حقيقية", "en": "Real completion"},
    "settings.not_run_yet": {"ar": "لم يُشغّل بعد", "en": "not run yet"},
    "settings.run_checks": {"ar": "شغّل الفحوصات", "en": "Run checks"},
    "settings.why_two_checks": {
        "ar": "لماذا فحصان",
        "en": "Why two checks",
    },
    "settings.why_two_checks_body": {
        "ar": "كان زر الاختبار يستعلم قائمة النماذج فقط. على مزود مدفوع حقيقي أعاد ذلك 200 بينما أعادت كل رسالة فعلية 404 — فظهر المزود أخضر ولم تصله رسالة واحدة. الفحص الذي لا يسلك المسار الذي يستخدمه المنتج ليس فحصًا.",
        "en": "The old Test button queried the model list only. On a real paid provider that returned 200 while every real message returned 404 — so the provider showed green and never answered a single chat. A check that does not exercise the path the product uses is not a check.",
    },
    "settings.selected_models_count": {
        "ar": "النماذج المختارة",
        "en": "Models selected",
    },
    "settings.key_not_decryptable": {
        "ar": "مخزّن، لكن تعذّر فك تشفيره",
        "en": "stored, but cannot be decrypted",
    },
    "settings.api_tokens_hint": {
        "ar": "استخدم هذه الرموز لاستدعاء الواجهات المحمية من السكربتات دون لصق <code>KAZMA_SECRET</code>. أرسلها بصيغة <code>Authorization: Bearer …</code> أو <code>X-Api-Token</code>.",
        "en": "Use these tokens to call protected APIs from scripts without pasting <code>KAZMA_SECRET</code>. Send as <code>Authorization: Bearer …</code> or <code>X-Api-Token</code>.",
    },
    "settings.graph_budget_auto": {
        "ar": "ميزانية خطوات المخطط (تلقائية): ~{n} (متوافقة مع جولات الأدوات)",
        "en": "Graph step budget (auto): ~{n} (aligned with tool rounds)",
    },
    "settings.email_client_secret_ph": {
        "ar": "سرّ العميل (GOCSPX-…)",
        "en": "Client secret (GOCSPX-…)",
    },
    "settings.email_azure_client_id_ph": {
        "ar": "معرّف تطبيق Azure (العميل)",
        "en": "Azure app (client) ID",
    },
    "settings.email_public_client_ph": {
        "ar": "اختياري للعملاء العامّين",
        "en": "Optional for public clients",
    },
    "settings.bk.working": {
        "ar": "جارٍ العمل…",
        "en": "Working…",
    },
    "settings.bk.phase": {
        "ar": "المرحلة",
        "en": "Phase",
    },
    "settings.bk.phase_starting": {
        "ar": "البدء",
        "en": "starting",
    },
    "settings.bk.phase_databases": {
        "ar": "قواعد البيانات",
        "en": "databases",
    },
    "settings.bk.phase_assets": {
        "ar": "الملفات",
        "en": "files",
    },
    "settings.bk.phase_postgres": {
        "ar": "Postgres",
        "en": "Postgres",
    },
    "settings.bk.phase_done": {
        "ar": "اكتمل",
        "en": "done",
    },
    "settings.bk.done_summary": {
        "ar": "{dbs} قاعدة بيانات، {mb} ميغابايت",
        "en": "{dbs} databases, {mb} MB",
    },
    "settings.bk.synced_to": {
        "ar": "☁ نُسخت إلى {remote}",
        "en": "☁ Synced to {remote}",
    },
    "settings.bk.cloud": {
        "ar": "السحابة",
        "en": "cloud",
    },
    "settings.bk.manual": {
        "ar": "يدوي",
        "en": "Manual",
    },
    "settings.bk.auto": {
        "ar": "تلقائي",
        "en": "Auto",
    },
    "settings.bk.synced_to_title": {
        "ar": "نُسخت إلى: {remote}",
        "en": "Synced to: {remote}",
    },
    "settings.bk.cloud_label": {
        "ar": "السحابة",
        "en": "Cloud",
    },
    "settings.bk.not_synced": {
        "ar": "لم تُنسخ إلى السحابة",
        "en": "Not synced to cloud",
    },
    "settings.bk.local_only": {
        "ar": "محلية فقط",
        "en": "local only",
    },
    "settings.bk.row_summary": {
        "ar": "{dbs} قاعدة بيانات · {mb} ميغابايت",
        "en": "{dbs} databases · {mb} MB",
    },
    "settings.bk.incomplete": {
        "ar": "غير مكتملة",
        "en": "incomplete",
    },
    "settings.bk.download": {
        "ar": "تنزيل",
        "en": "Download",
    },
    "settings.bk.archive": {
        "ar": "أرشفة",
        "en": "Archive",
    },
    "settings.bk.sync_active": {
        "ar": "النسخ الخارجي مفعّل",
        "en": "Offsite sync active",
    },
    "settings.bk.sync_disabled": {
        "ar": "النسخ الخارجي معطّل",
        "en": "Offsite sync disabled",
    },
    "settings.bk.pick_card": {
        "ar": "اضغط بطاقة مزوّد لتختاره وجهةً للنسخ الاحتياطي، ثم اختبر واحفظ.",
        "en": "Click a provider card to select it as the backup destination, then Test + Save.",
    },
    "settings.bk.drive_blocked_title": {
        "ar": "الوصول إلى Drive محجوب",
        "en": "Drive access blocked",
    },
    "settings.bk.drive_warning": {
        "ar": "⚠ Drive",
        "en": "⚠ Drive",
    },
    "settings.bk.active": {
        "ar": "نشط",
        "en": "active",
    },
    "settings.bk.drive_hint": {
        "ar": "يستخدم تفويض Gmail لديك — اضغط «اتصال» للسماح بالوصول إلى Drive.",
        "en": "Uses your Gmail OAuth — click Connect to authorize Drive access.",
    },
    "settings.bk.reconnect": {
        "ar": "⚠ أعد الاتصال",
        "en": "⚠ Reconnect",
    },
    "settings.bk.connected": {
        "ar": "✓ متصل",
        "en": "✓ Connected",
    },
    "settings.bk.connect": {
        "ar": "اتصال",
        "en": "Connect",
    },
    "settings.bk.drive_blocked": {
        "ar": "Drive محجوب: {reason}. اختبر البطاقة لمعرفة خطوات الإصلاح.",
        "en": "Drive blocked: {reason}. Test the card for the fix steps.",
    },
    "settings.bk.unknown_reason": {
        "ar": "سبب غير معروف",
        "en": "unknown reason",
    },
    "settings.bk.onedrive_hint": {
        "ar": "يستخدم حساب Microsoft لديك — اضغط «اتصال» للسماح بالوصول إلى الملفات.",
        "en": "Uses your Microsoft account — click Connect to authorize Files access.",
    },
    "settings.bk.webdav_hint": {
        "ar": "WebDAV — يعمل مع WD MyCloud OS5 وأي جهاز NAS.",
        "en": "WebDAV — works with WD MyCloud OS5 and any NAS.",
    },
    "settings.bk.ftp_path_ph": {
        "ar": "المسار الأساسي على الخادم (اختياري، مثل Backups/Kazma)",
        "en": "Base path on the server (optional, e.g. Backups/Kazma)",
    },
    "settings.bk.ftp_hint": {
        "ar": "FTP عادي — يعمل مع WD MyCloud OS3 (EX4100) وأي خادم FTP.",
        "en": "Plain FTP — works with WD MyCloud OS3 (EX4100) and any FTP server.",
    },
    "settings.bk.s3_key_ph": {
        "ar": "معرّف مفتاح الوصول",
        "en": "Access Key ID",
    },
    "settings.bk.s3_secret_ph": {
        "ar": "مفتاح الوصول السري",
        "en": "Secret Access Key",
    },
    "settings.bk.s3_bucket_ph": {
        "ar": "اسم الحاوية",
        "en": "Bucket name",
    },
    "settings.bk.s3_endpoint_ph": {
        "ar": "نقطة النهاية (اختيارية، لـ B2/MinIO)",
        "en": "Endpoint (optional, for B2/MinIO)",
    },
    "settings.bk.s3_hint": {
        "ar": "Amazon S3 أو أي تخزين متوافق مع S3. الأرخص للنسخ الاحتياطي.",
        "en": "Amazon S3 or any S3-compatible storage. Cheapest for backups.",
    },
    "settings.bk.pick_provider_first": {
        "ar": "اختر مزوّدًا أولًا لتفعيل النسخ الخارجي أو تعطيله",
        "en": "Select a provider first to enable/disable offsite sync",
    },
    "settings.bk.test_ok": {
        "ar": "متصل",
        "en": "Connected",
    },
    "settings.bk.test_failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "settings.docs.open_documents_page": {
        "ar": "افتح صفحة المستندات ←",
        "en": "Open Documents page →",
    },
    "settings.docs.enabled_durable_writes": {
        "ar": "مفعّل (كتابة دائمة)",
        "en": "Enabled (durable writes)",
    },
    "settings.docs.shadow_mode": {
        "ar": "وضع الظل",
        "en": "Shadow mode",
    },
    "settings.docs.default_authoritative": {
        "ar": "مرجعي افتراضيًا",
        "en": "Default authoritative",
    },
    "settings.docs.mode_live": {
        "ar": "الوضع (الحالي)",
        "en": "Mode (live)",
    },
    "settings.docs.max_file_bytes": {
        "ar": "أقصى حجم للملف (بايت)",
        "en": "Max file bytes",
    },
    "settings.docs.max_files_request": {
        "ar": "أقصى عدد ملفات في الطلب",
        "en": "Max files / request",
    },
    "settings.docs.worker_timeout_s": {
        "ar": "مهلة العامل (ثوانٍ)",
        "en": "Worker timeout (s)",
    },
    "settings.docs.worker_memory_mb": {
        "ar": "ذاكرة العامل (ميغابايت)",
        "en": "Worker memory (MB)",
    },
    "settings.docs.storage_free_floor_bytes": {
        "ar": "الحد الأدنى للمساحة الحرة (بايت)",
        "en": "Storage free floor (bytes)",
    },
    "settings.docs.malware_scan": {
        "ar": "فحص البرمجيات الخبيثة",
        "en": "Malware scan",
    },
    "settings.docs.malware_fail_closed": {
        "ar": "الرفض عند تعذّر فحص البرمجيات الخبيثة",
        "en": "Malware fail-closed",
    },
    "settings.docs.ocr_enabled": {
        "ar": "التعرّف الضوئي مفعّل",
        "en": "OCR enabled",
    },
    "settings.docs.gc_enabled": {
        "ar": "تنظيف المساحة مفعّل",
        "en": "GC enabled",
    },
    "settings.docs.indexing_enabled": {
        "ar": "الفهرسة مفعّلة",
        "en": "Indexing enabled",
    },
    "settings.docs.scanner": {
        "ar": "الفاحص:",
        "en": "Scanner:",
    },
    "settings.docs.scanner_available": {
        "ar": "{scanner} متاح",
        "en": "{scanner} available",
    },
    "settings.docs.scanner_missing": {
        "ar": "غير موجود في PATH (الوضع التلقائي يتخطّاه ما لم يُفعَّل الرفض)",
        "en": "not on PATH (auto skips unless fail-closed)",
    },
    "settings.docs.opt_auto": {
        "ar": "تلقائي",
        "en": "auto",
    },
    "settings.docs.opt_on": {
        "ar": "تشغيل",
        "en": "on",
    },
    "settings.docs.opt_off": {
        "ar": "إيقاف",
        "en": "off",
    },
    "settings.docs.mode_compatibility": {
        "ar": "التوافق",
        "en": "compatibility",
    },
    "settings.mem.leave_blank": {
        "ar": "اتركه فارغًا للإبقاء على الحالي",
        "en": "leave blank to keep",
    },
    "settings.mem.vector_url": {
        "ar": "عنوان اتصال المتجهات / DSN",
        "en": "Vector connection URL / DSN",
    },
    "settings.mem.vector_url_ph": {
        "ar": "postgresql://… أو http://qdrant:6333",
        "en": "postgresql://… or http://qdrant:6333",
    },
    "settings.mem.postgres_dsn": {
        "ar": "رابط اتصال Postgres (DSN)",
        "en": "Postgres DSN",
    },
    "settings.mem.mirror_hint": {
        "ar": "المرآة تنقل الكتابات الجديدة فقط — لا تُنسخ المعتقدات والحلقات الموجودة تلقائيًا. استخدم المزامنة مرة بعد التفعيل (ولإعادة المزامنة بعد التعديلات الكبيرة).",
        "en": "The mirror is write-forward only — existing beliefs/episodes aren't copied automatically. Use Sync once after enabling (and to re-sync after bulk edits).",
    },
    "settings.mem.opt_sqlite_vec": {
        "ar": "SQLite-vec (محلي)",
        "en": "SQLite-vec (local)",
    },
    "settings.mem.opt_sqlite_only": {
        "ar": "SQLite محلي فقط",
        "en": "Local SQLite only",
    },
    "settings.mem.opt_postgres_mirror": {
        "ar": "مرآة Postgres مزدوجة",
        "en": "Postgres dual-mirror",
    },
    "settings.mem.emb_multilingual_recommended": {
        "ar": "متعدد اللغات (موصى به)",
        "en": "multilingual (recommended)",
    },
    "settings.mem.emb_english": {
        "ar": "للإنجليزية",
        "en": "English",
    },
    "settings.mem.emb_lightweight_legacy": {
        "ar": "خفيف (قديم)",
        "en": "lightweight (legacy)",
    },
    "settings.cap_vector_local": {
        "ar": "sqlite-vec المحلي: بحث وكتابة",
        "en": "Local sqlite-vec: search + write",
    },
    "settings.cap_graph_local": {
        "ar": "رسم المعتقدات يُقدَّم من جدول المعتقدات في SQLite",
        "en": "Belief graph served from SQLite beliefs table",
    },
    "settings.cap_state_local": {
        "ar": "الحالة الأساسية في SQLite المحلي (memory_state.db)",
        "en": "Primary state is local SQLite (memory_state.db)",
    },
    "settings.prov_replied_in": {
        "ar": "ردّ خلال {ms}{model}",
        "en": "replied in {ms}{model}",
    },
    "settings.prov_chat_failing_detail": {
        "ar": "قائمة النماذج تستجيب، أما الرسالة الفعلية فلا",
        "en": "the model list answers, a real message does not",
    },
    "settings.prov_no_response": {
        "ar": "لا استجابة",
        "en": "no response",
    },
    "settings.cap_tools": {
        "ar": "الأدوات",
        "en": "Tools",
    },
    "settings.cap_streaming": {
        "ar": "البث المتدفق",
        "en": "Streaming",
    },
    "settings.cap_json_mode": {
        "ar": "وضع JSON",
        "en": "JSON mode",
    },
    "settings.cap_vision": {
        "ar": "الرؤية",
        "en": "Vision",
    },
    "settings.cap_title_yes": {
        "ar": "قيس مع هذا المزوّد وتأكّد.",
        "en": "Measured against this provider and confirmed.",
    },
    "settings.cap_title_no": {
        "ar": "قيس مع هذا المزوّد وتبيّن أنه غير مدعوم.",
        "en": "Measured against this provider and not supported.",
    },
    "settings.cap_title_unknown": {
        "ar": "لم يُتحقَّق منه. لم يقسه أحد بعد — شغّل scripts/provider_conformance.py --live.",
        "en": "Not verified. Nobody has measured this yet — run scripts/provider_conformance.py --live.",
    },
    "settings.fact_api": {
        "ar": "الواجهة",
        "en": "API",
    },
    "settings.fact_system_turn": {
        "ar": "دور النظام",
        "en": "System turn",
    },
    "settings.fact_context": {
        "ar": "السياق",
        "en": "Context",
    },
    "settings.hub.chat_failing_hint": {
        "ar": "قائمة النماذج استجابت، إذن المفتاح والمضيف سليمان. تحقّق من مقطع إصدار الواجهة في عنوان URL الأساسي ومن النموذج المختار — هذا الزوج هو ما يرفضه <code>/chat/completions</code>.",
        "en": "The model list answered, so the key and the host are fine. Check the base URL's API version segment and the selected model — that pair is what <code>/chat/completions</code> rejects.",
    },
    "settings.hub.discovered_n": {
        "ar": "المكتشفة ({n})",
        "en": "Discovered ({n})",
    },
    "settings.hub.selected_n": {
        "ar": "(المختارة: {n})",
        "en": "({n} selected)",
    },
    "settings.hub.search_models_n": {
        "ar": "ابحث في {n} نموذجًا…",
        "en": "Search {n} models…",
    },
    "settings.hub.no_models_match": {
        "ar": "لا توجد نماذج مطابقة لبحثك",
        "en": "No models match your search",
    },
    "settings.hub.platform_adapters": {
        "ar": "محوّلات المنصات",
        "en": "Platform Adapters",
    },
    "settings.hub.adapters_blurb": {
        "ar": "بيانات الاعتماد والوجهات والتوجيه لكل منصة توصيل — في مكان واحد. الرموز مشفّرة في الخزنة؛ اترك النقاط المقنّعة للإبقاء على القيمة المحفوظة.",
        "en": "Credentials, destinations and routing for every delivery platform — all in one place. Tokens are vault-encrypted; leave the masked dots to keep the saved value.",
    },
    "settings.hub.saving": {
        "ar": "جارٍ الحفظ…",
        "en": "Saving…",
    },
    "settings.hub.tg_main": {
        "ar": "Telegram — البوت الرئيسي",
        "en": "Telegram — Main bot",
    },
    "settings.hub.configured": {
        "ar": "مُعدّ",
        "en": "Configured",
    },
    "settings.hub.not_configured": {
        "ar": "غير مُعدّ",
        "en": "Not configured",
    },
    "settings.hub.enable_adapter": {
        "ar": "تفعيل محوّل هذه المنصة",
        "en": "Enable this platform adapter",
    },
    "settings.hub.testing": {
        "ar": "جارٍ الاختبار…",
        "en": "Testing…",
    },
    "settings.hub.hide": {
        "ar": "إخفاء",
        "en": "Hide",
    },
    "settings.hub.show": {
        "ar": "إظهار",
        "en": "Show",
    },
    "settings.hub.masked_hint": {
        "ar": "القيمة المحفوظة تظهر كنقاط — اكتب لاستبدالها.",
        "en": "Saved value shows as dots — type to replace it.",
    },
    "settings.hub.chat_id": {
        "ar": "معرّف المحادثة",
        "en": "Chat ID",
    },
    "settings.hub.delivery_destination": {
        "ar": "(وجهة التوصيل)",
        "en": "(delivery destination)",
    },
    "settings.hub.tg_chat_ph": {
        "ar": "123456789 (معرّف مستخدمك) أو -100…",
        "en": "123456789 (your user id) or -100…",
    },
    "settings.hub.tg_chat_hint": {
        "ar": "حيث تصل التنبيهات وتقارير السرب عند اختيار هذا المسار.",
        "en": "Where alerts and swarm reports land when this route is selected.",
    },
    "settings.hub.allowed_user_ids": {
        "ar": "معرّفات المستخدمين المسموح لهم",
        "en": "Allowed User IDs",
    },
    "settings.hub.tg_allowed_ph": {
        "ar": "123456789 — مفصولة بفواصل؛ الفراغ يسمح للجميع (الوضع غير الصارم)",
        "en": "123456789 — comma-separated; empty allows all (non-strict mode)",
    },
    "settings.hub.tg_group": {
        "ar": "Telegram — مجموعة",
        "en": "Telegram — Group",
    },
    "settings.hub.active": {
        "ar": "نشط",
        "en": "Active",
    },
    "settings.hub.off": {
        "ar": "متوقف",
        "en": "Off",
    },
    "settings.hub.enable_group": {
        "ar": "تفعيل مسار المجموعة",
        "en": "Enable the group route",
    },
    "settings.hub.group_hint": {
        "ar": "مسار ثانٍ اختياري — اترك الرمز المخصص فارغًا لاستخدام البوت الرئيسي",
        "en": "optional second route — leave the dedicated token empty to use the main bot",
    },
    "settings.hub.group_chat_id": {
        "ar": "معرّف محادثة المجموعة",
        "en": "Group Chat ID",
    },
    "settings.hub.group_chat_ph": {
        "ar": "-100… (يعلنه البوت عند إضافته إلى مجموعة)",
        "en": "-100… (the bot announces it when added to a group)",
    },
    "settings.hub.dedicated_token": {
        "ar": "رمز بوت مخصص",
        "en": "Dedicated Bot Token",
    },
    "settings.hub.optional": {
        "ar": "(اختياري)",
        "en": "(optional)",
    },
    "settings.hub.discord_token_ph": {
        "ar": "MTIzNDU2… (من Developer Portal ← Bot ← Reset Token)",
        "en": "MTIzNDU2… (Developer Portal → Bot → Reset Token)",
    },
    "settings.hub.channel_id": {
        "ar": "معرّف القناة",
        "en": "Channel ID",
    },
    "settings.hub.discord_channel_ph": {
        "ar": "معرّف القناة (Developer Mode ← انقر القناة بالزر الأيمن ← Copy ID)",
        "en": "channel id (Developer Mode → right-click channel → Copy ID)",
    },
    "settings.hub.discord_guild_ph": {
        "ar": "معرّف الخادم (Developer Mode ← انقر الخادم بالزر الأيمن ← Copy ID)",
        "en": "server id (Developer Mode → right-click server → Copy ID)",
    },
    "settings.hub.discord_allowed_ph": {
        "ar": "111, 222 — مفصولة بفواصل",
        "en": "111, 222 — comma-separated",
    },
    "settings.hub.slack_token_ph": {
        "ar": "xoxb-… (من تثبيت تطبيق Slack)",
        "en": "xoxb-… (from the Slack app install)",
    },
    "settings.hub.app_token": {
        "ar": "رمز التطبيق",
        "en": "App Token",
    },
    "settings.hub.app_token_hint": {
        "ar": "(اختياري، Socket Mode)",
        "en": "(optional, Socket Mode)",
    },
    "settings.hub.slack_workspace": {
        "ar": "مساحة العمل",
        "en": "Workspace",
    },
    "settings.hub.slack_channel_ph": {
        "ar": "C… (انقر القناة بالزر الأيمن ← View channel details)",
        "en": "C… (right-click channel → View channel details)",
    },
    "settings.hub.slack_allowed_ph": {
        "ar": "U0123ABCD — مفصولة بفواصل",
        "en": "U0123ABCD — comma-separated",
    },
    "settings.hub.adapters_routes": {
        "ar": "المحوّلات والمسارات",
        "en": "Adapters & Routes",
    },
    "settings.hub.routes_blurb": {
        "ar": "توجيه التوصيل — اختر المنصات التي تستقبل كل نوع من الرسائل. لا يوصل المسار إلا إذا ضُبطت وجهته أعلاه.",
        "en": "Delivery routing — pick which platforms receive each kind of message. A route only delivers when its destination above is set.",
    },
    "settings.hub.alerts_go_to": {
        "ar": "تذهب التنبيهات إلى",
        "en": "Alerts go to",
    },
    "settings.hub.alerts_hint": {
        "ar": "تنبيهات التشغيل وانقطاعات MCP وبدء الخادم وإيقافه. اترك الكل دون تحديد للتوصيل إلى كل منصة مُعدّة.",
        "en": "Ops pages, MCP outages, and server start/stop. Leave ALL unchecked to deliver to every configured platform.",
    },
    "settings.hub.test_alert": {
        "ar": "إرسال تنبيه تجريبي",
        "en": "Send a test alert",
    },
    "settings.hub.test_alert_sending": {
        "ar": "جارٍ الإرسال…",
        "en": "Sending…",
    },
    "settings.hub.test_alert_hint": {
        "ar": "يرسل رسالة واحدة عبر مسارات التنبيهات المحفوظة، بالطريقة نفسها التي يُرسَل بها أي تنبيه حقيقي. احفظ تغييراتك أولًا.",
        "en": "Sends one message along the saved alert routes, the way a real alert travels. Save your changes first.",
    },
    "settings.hub.test_alert_sent": {
        "ar": "وصل إلى {routes}.",
        "en": "Delivered to {routes}.",
    },
    "settings.hub.test_alert_failed": {
        "ar": "لم يصل إلى {routes}: زر «اختبار» الخاص به أعلاه يوضح السبب.",
        "en": "Not delivered to {routes}: its Test button above says why.",
    },
    "settings.hub.test_alert_nowhere": {
        "ar": "لا يرسل أي مسار تنبيهات إلى أي مكان. اختر قناة واحفظ، أو أعِدّ تطبيق محادثة أعلاه.",
        "en": "No alert route sends anywhere. Tick a channel and save, or set up a chat app above.",
    },
    "settings.hub.test_alert_error": {
        "ar": "لم يُرسَل التنبيه التجريبي: {error}",
        "en": "The test alert was not sent: {error}",
    },
    "settings.hub.swarm_goes_to": {
        "ar": "تذهب مخرجات السرب إلى",
        "en": "Swarm output goes to",
    },
    "settings.hub.swarm_hint": {
        "ar": "يُرسل تقرير السرب النهائي إلى كل مسار مختار. اترك الكل دون تحديد لإرساله إلى مجموعة Telegram فقط.",
        "en": "The final swarm report is mirrored to every selected route. Leave all unchecked for the single Telegram-group target only.",
    },
    "settings.hub.route_tg_group": {
        "ar": "Telegram (مجموعة)",
        "en": "Telegram (group)",
    },
    "settings.hub.restart_hint": {
        "ar": "أعد تشغيل محوّلات المنصات دون إعادة تشغيل الخادم. الحفظ يطبّق تغييرات بيانات الاعتماد تلقائيًا.",
        "en": "Restart the platform adapters without restarting the server. Saving applies credential changes automatically.",
    },
    "settings.hub.other_integrations": {
        "ar": "تكاملات أخرى",
        "en": "Other integrations",
    },
    "settings.hub.other_hint": {
        "ar": "موصلات البريد والـ Webhook. تُدار Telegram وDiscord وSlack في محوّلات المنصات أعلاه.",
        "en": "Email and webhook connectors. Telegram, Discord and Slack are managed in Platform Adapters above.",
    },
    "settings.connector_email": {
        "ar": "البريد الإلكتروني",
        "en": "Email",
    },
    "settings.connector_webhook": {
        "ar": "Webhook",
        "en": "Webhook",
    },
    "settings.connector_status_configured": {
        "ar": "مُعدّ",
        "en": "configured",
    },
    "settings.connector_status_missing": {
        "ar": "غير مُعدّ",
        "en": "missing",
    },
    "settings.hub.google_mode": {
        "ar": "منتج Google / طريقة المصادقة",
        "en": "Google Product / Authentication Mode",
    },
    "settings.hub.google_ai_studio": {
        "ar": "Google AI Studio (مفتاح API)",
        "en": "Google AI Studio (API Key)",
    },
    "settings.hub.keep_key": {
        "ar": "اتركه فارغًا للإبقاء على المفتاح الحالي",
        "en": "leave blank to keep current key",
    },
    "settings.hub.ai_studio_key_ph": {
        "ar": "مفتاح AI Studio (AIzaSy...)",
        "en": "AI Studio Key (AIzaSy...)",
    },
    "settings.hub.base_url_optional": {
        "ar": "عنوان URL الأساسي (اختياري)",
        "en": "Base URL (Optional)",
    },
    "settings.hub.gcp_project": {
        "ar": "معرّف مشروع GCP (اختياري — يُؤخذ من gcloud/ADC عند غيابه)",
        "en": "GCP Project ID (Optional - falls back to gcloud/ADC)",
    },
    "settings.hub.gcp_location": {
        "ar": "موقع / منطقة GCP",
        "en": "GCP Location / Region",
    },
    "settings.hub.adc_hint": {
        "ar": "يصادق بأمان عبر <strong>Application Default Credentials (ADC)</strong>. اترك معرّف المشروع فارغًا ليُحدَّد تلقائيًا من إعدادات gcloud المحلية.",
        "en": "Authenticates securely via <strong>Application Default Credentials (ADC)</strong>. Keep project ID empty to auto-resolve from local gcloud config.",
    },
    "settings.hub.token_ph": {
        "ar": "رمز / مفتاح",
        "en": "token / key",
    },
    "settings.hub.app_password_ph": {
        "ar": "كلمة مرور التطبيق",
        "en": "App password",
    },
    "settings.hub.shared_secret_ph": {
        "ar": "سرّ مشترك",
        "en": "Shared secret",
    },
    "settings.sys.secret_vault": {
        "ar": "خزنة الأسرار",
        "en": "Secret Vault",
    },
    "settings.sys.vault_enabled": {
        "ar": "الخزنة مفعّلة",
        "en": "Vault Enabled",
    },
    "settings.sys.vault_disabled": {
        "ar": "الخزنة معطّلة",
        "en": "Vault Disabled",
    },
    "settings.sys.secrets_stored": {
        "ar": "الأسرار المخزّنة: {n}",
        "en": "{n} secrets stored",
    },
    "settings.sys.vault_hint": {
        "ar": "اضبط KAZMA_VAULT_KEY في ‎.env أو أعد تشغيل الخادم ليُولَّد تلقائيًا.",
        "en": "Set KAZMA_VAULT_KEY in .env or restart the server to auto-generate one.",
    },
    "settings.sys.vault_tools_hint": {
        "ar": "يستطيع الوكيل تخزين الأسرار المشفّرة واسترجاعها (مفاتيح API والرموز وكلمات المرور) عبر الأداتين <code>vault_store</code> و<code>vault_retrieve</code>. الاسترجاع يتطلب موافقة.",
        "en": "The agent can store and retrieve encrypted secrets (API keys, tokens, passwords) using the <code>vault_store</code> and <code>vault_retrieve</code> tools. Retrieval requires HITL approval.",
    },
    "settings.sys.proxy_test_failed": {
        "ar": "فشل الاختبار",
        "en": "Test failed",
    },
    "settings.xt.handle_ph": {
        "ar": "@حسابك",
        "en": "@yourhandle",
    },
    "settings.xt.auto_reply": {
        "ar": "الرد التلقائي",
        "en": "Auto-reply",
    },
    "settings.xt.intro": {
        "ar": "أشر إلى حسابك ويردّ Kazma. لا يلزم تحديد موضوع — الرمز التعبيري في الإشارة يحدد النبرة (😂 سخرية، 🤬 غضب، 🙄 جفاف، ❤️ دعم). أضف موضوعًا فقط حين تريد رأيًا معلنًا في مسألة؛ فالمواضيع تتقدّم على الصوت الافتراضي.",
        "en": "Mention @yourhandle and Kazma replies. No subject is required — the emoji in the mention picks the tone (😂 roast, 🤬 angry, 🙄 dry, ❤️ supportive). Add a subject only when you want a declared view on a topic; those still win over the default voice.",
    },
    "settings.xt.live": {
        "ar": "يعمل",
        "en": "Live",
    },
    "settings.xt.not_live": {
        "ar": "لا يعمل",
        "en": "Not live",
    },
    "settings.xt.not_ready": {
        "ar": "موصل X غير جاهز للنشر بعد — احفظ بيانات الاعتماد أعلاه واختبرها أولًا.",
        "en": "The X connector is not posting-ready yet — save and test the credentials above first.",
    },
    "settings.xt.enable": {
        "ar": "تفعيل الرد التلقائي",
        "en": "Enable auto-reply",
    },
    "settings.xt.mode": {
        "ar": "الوضع",
        "en": "Mode",
    },
    "settings.xt.mode_off": {
        "ar": "إيقاف — لا ردود أبدًا",
        "en": "off — never reply",
    },
    "settings.xt.mode_draft": {
        "ar": "مسودة — اسألني قبل النشر",
        "en": "draft — ask me before posting",
    },
    "settings.xt.mode_auto": {
        "ar": "تلقائي — انشر دون سؤال",
        "en": "auto — post without asking",
    },
    "settings.xt.auto_warning": {
        "ar": "الوضع التلقائي ينشر نصًا لم تقرأه باسمك. ابدأ بالمسودة.",
        "en": "Auto publishes text you have not read, under your name. Start with draft.",
    },
    "settings.xt.who": {
        "ar": "من يمكنه استدعاؤه",
        "en": "Who can summon it",
    },
    "settings.xt.who_allowlist": {
        "ar": "الحسابات التي أحددها فقط",
        "en": "only the handles I list",
    },
    "settings.xt.who_anyone": {
        "ar": "أي شخص يشير إليّ",
        "en": "anyone who mentions me",
    },
    "settings.xt.anyone_warning": {
        "ar": "يستطيع أي غريب إطلاق رد. ومع ذلك تبقى مطابقة المواضيع والحدود وحد المتابعين الأدنى وفحص المحتوى سارية.",
        "en": "Any stranger can trigger a reply. Subject matching, the caps, the follower floor and the content screen all still apply.",
    },
    "settings.xt.trusted": {
        "ar": "الحسابات الموثوقة",
        "en": "Trusted handles",
    },
    "settings.xt.trusted_ph": {
        "ar": "حسابك, شريكك",
        "en": "yourhandle, cohost",
    },
    "settings.xt.trusted_hint": {
        "ar": "مفصولة بفواصل. الفراغ يعني لا أحد — خطأ في الإعداد يجب ألا يفتح الحساب للجميع.",
        "en": "Comma-separated. Empty means nobody — a config mistake must not open the account to the world.",
    },
    "settings.xt.anyone_hint": {
        "ar": "كل من يشير إلى الحساب يستطيع الاستدعاء. وتبقى الحدود والفحص ساريين.",
        "en": "Anyone who mentions the account can summon. Caps and the screen still apply.",
    },
    "settings.xt.open_marker": {
        "ar": "وسم فتح السلسلة (اختياري)",
        "en": "Open-thread marker (optional)",
    },
    "settings.xt.open_marker_hint": {
        "ar": "بدونه لا يحصل على رد إلا الحسابات الموثوقة. ضع الوسم في إشارتك <em>أنت</em> إن أردت أن يحصل كل من يرد على ذلك المنشور على Kazma أيضًا. يُطابَق الوسم كاملًا: <code>#Open</code> لا يطابق <code>#OpenAI</code>. اتركه فارغًا = أنت فقط.",
        "en": "Without this, only trusted handles get a reply. Put the hashtag in <em>your</em> mention if you want anyone who replies on that post to get Kazma too. Matched as a whole tag: <code>#Open</code> does not match <code>#OpenAI</code>. Leave empty = strictly you.",
    },
    "settings.xt.close_marker": {
        "ar": "وسم إغلاق السلسلة (اختياري)",
        "en": "Close-thread marker (optional)",
    },
    "settings.xt.close_marker_hint": {
        "ar": "أشر إلى Kazma بهذا الوسم لإيقاف الغرباء في تلك السلسلة. يبقى بإمكانك الحديث. مثال: <code>@KazmaAI #Close</code>.",
        "en": "Mention Kazma with this tag to stop strangers on that thread. You can still talk. Example: <code>@KazmaAI #Close</code>.",
    },
    "settings.xt.trigger": {
        "ar": "عبارة التشغيل (اختيارية)",
        "en": "Trigger phrase (optional)",
    },
    "settings.xt.trigger_ph": {
        "ar": "ما رأيك",
        "en": "what do you think",
    },
    "settings.xt.trigger_hint": {
        "ar": "عند ضبطها يجب أن تحتويها الإشارة لتُعدّ استدعاءً.",
        "en": "When set, a mention must contain this to count as a summon.",
    },
    "settings.xt.emoji_tone": {
        "ar": "دع الرمز التعبيري يحدد النبرة",
        "en": "Let an emoji set the tone",
    },
    "settings.xt.emoji_hint": {
        "ar": "«ما رأيك يا Kazma؟ 😂» تحصل على سخرية؛ والعبارة نفسها مع 🤬 تحصل على رد غاضب. النبرة فقط — لا يستطيع الرمز التعبيري المساس برأي الموضوع أو خطوطه الحمراء، ولا يضبطه إلا الحسابات الموثوقة.",
        "en": "“what do you think Kazma? 😂” gets a roast; the same line with 🤬 gets an angry one. Tone only — an emoji can never reach the subject’s view or its hard lines, and only trusted handles can set it.",
    },
    "settings.xt.stance": {
        "ar": "تحقّق أن المسودة تدافع عن رأيك",
        "en": "Check the draft argues your view",
    },
    "settings.xt.stance_hint": {
        "ar": "استدعاء قصير إضافي للنموذج لكل رد. فحص المحتوى يلتقط التهديدات والإطالة فقط — ولا يعرف موقفك، فمسودة تدافع بهدوء عن الطرف الآخر (أو تبدو متعاطفة معه) تجتازه. هذا يمنع التناقض والحياد وانقلابات «لا تهاجمهم».",
        "en": "One extra short model call per reply. The content screen only catches threats and over-length — it has no idea what your position is, so a draft that quietly argues the other side (or sounds sympathetic to it) passes it. This blocks contradictions, fence-sitting, and “don’t attack them” polarity flips.",
    },
    "settings.xt.stance_off_warning": {
        "ar": "مع إيقاف هذا والوضع تلقائي، لا شيء يتحقق من الرد قبل نشره.",
        "en": "With this off and mode set to auto, nothing verifies a reply before it publishes.",
    },
    "settings.xt.unmatched": {
        "ar": "إن لم يطابق أي موضوع",
        "en": "If no subject matches",
    },
    "settings.xt.unmatched_skip": {
        "ar": "التزم الصمت (صارم)",
        "en": "stay silent (strict)",
    },
    "settings.xt.unmatched_voice": {
        "ar": "ردّ على أي حال، والرمز التعبيري يحدد النبرة",
        "en": "reply anyway, emoji sets the tone",
    },
    "settings.xt.unmatched_hint": {
        "ar": "الوضع الصارم هو الافتراضي حين تكون لديك مواضيع. ومع ذلك تستطيع الإشارة تحديد الجانب دون بطاقة: 😂/🤬/🙄/👎 = انتقد هذا المنشور، ❤️/👍 = دافع عنه (أو الكلمتان against / support). والكلمة المفتاحية <code>*</code> تجيب على كل شيء.",
        "en": "Strict is the default once you have subjects. A mention can still set the side without a card: 😂/🤬/🙄/👎 = criticise this post, ❤️/👍 = defend it (or the words against / support). A <code>*</code> keyword still answers everything.",
    },
    "settings.xt.use_kb": {
        "ar": "استند في المسودات إلى المكتبة المعرفية",
        "en": "Ground drafts in the Knowledge Base",
    },
    "settings.xt.use_kb_hint": {
        "ar": "يسحب حتى ثلاثة مقتطفات محاطة إلى المسودة كحقائق. لا تتجاوز أبدًا جانب البطاقة (مع/ضد). معطّل افتراضيًا. أدخل مكتبة في <a href=\"/knowledge\">المكتبة المعرفية</a> أولًا، ثم <strong>جرّبه</strong> — تخبرك المعاينة بعدد المقتطفات المستخدمة.",
        "en": "Pulls up to three fenced snippets into the draft as facts. They never override the card’s against/support side. Off by default. Ingest a library on <a href=\"/knowledge\">Knowledge</a> first, then <strong>Try it</strong> — the preview says how many snippets landed.",
    },
    "settings.xt.library": {
        "ar": "المكتبة",
        "en": "Library",
    },
    "settings.xt.all_libraries": {
        "ar": "كل المكتبات التي تحوي مقاطع",
        "en": "All libraries with chunks",
    },
    "settings.xt.lib_chunks": {
        "ar": "{name} ({n} مقطعًا)",
        "en": "{name} ({n} chunks)",
    },
    "settings.xt.no_libraries": {
        "ar": "لا مكتبات بعد. أضف واحدة في صفحة المكتبة المعرفية ثم أعد تحميل هذا التبويب.",
        "en": "No libraries yet. Add one on the Knowledge page, then reload this tab.",
    },
    "settings.xt.max_day": {
        "ar": "أقصى ردود في اليوم",
        "en": "Max replies / day",
    },
    "settings.xt.max_account": {
        "ar": "أقصى ردود لكل حساب في اليوم",
        "en": "Max per account / day",
    },
    "settings.xt.cooldown": {
        "ar": "فترة تهدئة السلسلة (ث)",
        "en": "Thread cooldown (s)",
    },
    "settings.xt.min_followers": {
        "ar": "أدنى عدد متابعين",
        "en": "Min follower count",
    },
    "settings.xt.poll": {
        "ar": "فاصل الاستطلاع (ث)",
        "en": "Poll interval (s)",
    },
    "settings.xt.floor_hint": {
        "ar": "حد المتابعين الأدنى يمنع الردود غير المطلوبة على الحسابات الصغيرة. استطلاع الإشارات يتطلب خطة X مدفوعة ويستهلك حصة القراءة الشهرية.",
        "en": "The follower floor stops unsolicited replies at small accounts. Polling mentions needs a paid X plan and spends monthly read quota.",
    },
    "settings.xt.subjects": {
        "ar": "المواضيع",
        "en": "Subjects",
    },
    "settings.xt.add_subject": {
        "ar": "+ إضافة موضوع",
        "en": "+ Add subject",
    },
    "settings.xt.no_subjects": {
        "ar": "لا مواضيع — لا بأس. يبقى Kazma يرد؛ ورمز الاستدعاء يحدد النبرة. أضف موضوعًا حين تريد أن يدافع عن رأي كتبته، أو أعطِ موضوعًا الكلمة المفتاحية <code>*</code> لتكتب الصوت بنفسك.",
        "en": "No subjects — that is fine. Kazma still replies; the summon emoji picks the tone. Add a subject when you want it to argue a view you wrote, or give one the keyword <code>*</code> to write the voice yourself.",
    },
    "settings.xt.unnamed": {
        "ar": "(بلا اسم)",
        "en": "(unnamed)",
    },
    "settings.xt.id": {
        "ar": "المعرّف",
        "en": "Id",
    },
    "settings.xt.topic_ph": {
        "ar": "الموضوع",
        "en": "topic",
    },
    "settings.xt.side": {
        "ar": "الجانب — دائمًا هذا، والرمز التعبيري للنبرة فقط",
        "en": "Side — always this, emoji is tone only",
    },
    "settings.xt.side_against": {
        "ar": "ضد — انتقد دائمًا",
        "en": "against — always criticise",
    },
    "settings.xt.side_support": {
        "ar": "مع — دافع دائمًا",
        "en": "support — always defend",
    },
    "settings.xt.side_legacy": {
        "ar": "(قديم: استخدم نص الرأي)",
        "en": "(legacy: use the view text)",
    },
    "settings.xt.side_value_against": {
        "ar": "ضد",
        "en": "against",
    },
    "settings.xt.side_value_support": {
        "ar": "مع",
        "en": "support",
    },
    "settings.xt.fallback_mood": {
        "ar": "المزاج الاحتياطي (إن خلا الاستدعاء من رمز تعبيري)",
        "en": "Fallback mood (if the summon has no emoji)",
    },
    "settings.xt.keywords": {
        "ar": "الكلمات المفتاحية",
        "en": "Keywords",
    },
    "settings.xt.keywords_ph": {
        "ar": "اسم، اسم بديل، كلمة مفتاحية  —  أو * وحدها",
        "en": "name, alias, keyword  —  or just *",
    },
    "settings.xt.keywords_hint": {
        "ar": "تُطابَق كلمات كاملة، فلا تنطلق «var» على «variable». استخدم <code>*</code> وحدها للرد على <strong>كل</strong> منشور — ثم يحدد رمز الاستدعاء النبرة. تُجرَّب المواضيع المحددة أولًا، فلا يضعفها الموضوع العام.",
        "en": "Matched as whole words, so \"var\" will not fire on \"variable\". Use <code>*</code> on its own to answer <strong>every</strong> post — the summon emoji then picks the tone. Specific subjects are still tried first, so a catch-all never blunts them.",
    },
    "settings.xt.extra": {
        "ar": "إضافة اختيارية (من / لماذا — الجانب نفسه)",
        "en": "Optional extra (who / why — still the same side)",
    },
    "settings.xt.extra_ph": {
        "ar": "اختياري: من أو ما الذي ينطبق عليه هذا الجانب",
        "en": "optional: who or what this side applies to",
    },
    "settings.xt.extra_hint": {
        "ar": "ضد + 😂 = سخرية من ذلك الموضوع. مع + 🤬 = غضب <em>لأجله</em> (غضب على منتقديه، لا على الموضوع أبدًا).",
        "en": "Against + 😂 = roast that subject. Support + 🤬 = angry <em>for</em> it (anger at its critics, never at the subject).",
    },
    "settings.xt.register": {
        "ar": "اللهجة (اختيارية)",
        "en": "Register (optional)",
    },
    "settings.xt.register_ph": {
        "ar": "خليجية",
        "en": "gulf arabic",
    },
    "settings.xt.hard_lines": {
        "ar": "الخطوط الحمراء — واحد في كل سطر",
        "en": "Hard lines — one per line",
    },
    "settings.xt.hard_lines_ph": {
        "ar": "لا تمدح الطرف الآخر أبدًا",
        "en": "never praise the other side",
    },
    "settings.xt.hard_lines_hint": {
        "ar": "أشياء يجب ألا يقولها أبدًا، إضافةً إلى القواعد العامة (لا إهانات ولا تهديدات).",
        "en": "Things it must never say, on top of the universal rules (no slurs, no threats).",
    },
    "settings.xt.examples": {
        "ar": "أمثلة — واحد في كل سطر",
        "en": "Examples — one per line",
    },
    "settings.xt.examples_ph": {
        "ar": "ردّ كتبته فعلًا.",
        "en": "A reply you actually wrote.",
    },
    "settings.xt.examples_hint": {
        "ar": "أنفع من الرأي في ضبط الصوت. اثنان أو ثلاثة تكفي.",
        "en": "Worth more than the view for voice. Two or three is enough.",
    },
    "settings.xt.save": {
        "ar": "حفظ الرد التلقائي",
        "en": "Save auto-reply",
    },
    "settings.xt.recent": {
        "ar": "الاستدعاءات الأخيرة",
        "en": "Recent summons",
    },
    "settings.xt.try_it": {
        "ar": "جرّبه",
        "en": "Try it",
    },
    "settings.xt.try_hint": {
        "ar": "يصوغ ردًا على منشور تلصقه. لا يُنشر شيء ولا يُسجَّل. اختر نبرة أدناه لترى أثر الرمز التعبيري.",
        "en": "Drafts against a pasted post. Nothing is published or recorded. Pick a tone below to see how the emoji would land.",
    },
    "settings.xt.try_open": {
        "ar": "يستخدم بطاقة الموضوع المفتوحة، بما فيها التعديلات غير المحفوظة.",
        "en": "Uses the subject card you have open, including edits you have not saved.",
    },
    "settings.xt.try_voice_only": {
        "ar": "لا موضوع مفتوح — الصياغة بوضع الصوت فقط (منتقي الرمز/النبرة).",
        "en": "No subject open — this drafts in voice-only mode (emoji/tone picker).",
    },
    "settings.xt.post_text": {
        "ar": "نص المنشور",
        "en": "Post text",
    },
    "settings.xt.post_ph": {
        "ar": "الصق المنشور الذي سترد عليه.",
        "en": "Paste the post you would be replying to.",
    },
    "settings.xt.their_handle": {
        "ar": "حسابه (اختياري)",
        "en": "Their handle (optional)",
    },
    "settings.xt.tone": {
        "ar": "النبرة (اختيارية)",
        "en": "Tone (optional)",
    },
    "settings.xt.tone_default": {
        "ar": "نبرة الموضوع نفسه",
        "en": "the subject's own",
    },
    "settings.xt.force_subject": {
        "ar": "فرض موضوع (اختياري)",
        "en": "Force a subject (optional)",
    },
    "settings.xt.auto_detect": {
        "ar": "اكتشاف تلقائي",
        "en": "auto-detect",
    },
    "settings.xt.drafting": {
        "ar": "جارٍ الصياغة…",
        "en": "Drafting…",
    },
    "settings.xt.draft_reply": {
        "ar": "صُغ ردًا",
        "en": "Draft a reply",
    },
    "settings.xt.subject_label": {
        "ar": "الموضوع:",
        "en": "subject:",
    },
    "settings.xt.kb_hits": {
        "ar": "المكتبة: {n} مقتطف",
        "en": "KB: {n} snippet(s)",
    },
    "settings.xt.kb_from": {
        "ar": "من {libs}",
        "en": "from {libs}",
    },
    "settings.xt.kb_none": {
        "ar": "· المكتبة مفعّلة، ولم يطابق أي مقتطف",
        "en": "· KB on, no snippets matched",
    },
    "settings.xt.full_guide": {
        "ar": "الدليل الكامل:",
        "en": "Full guide:",
    },
    "settings.xt.guide_link": {
        "ar": "الرد التلقائي على X",
        "en": "X auto-reply",
    },
    "settings.xt.mood_roast": {
        "ar": "سخرية",
        "en": "roast",
    },
    "settings.xt.mood_angry": {
        "ar": "غضب",
        "en": "angry",
    },
    "settings.xt.mood_dry": {
        "ar": "جاف",
        "en": "dry",
    },
    "settings.xt.mood_supportive": {
        "ar": "داعم",
        "en": "supportive",
    },
    "settings.xt.mood_neutral": {
        "ar": "محايد",
        "en": "neutral",
    },
    "settings.xt.mood_playful": {
        "ar": "مرح",
        "en": "playful",
    },
    "settings.xt.mood_serious": {
        "ar": "جاد",
        "en": "serious",
    },
    "settings.xt.mood_sarcastic": {
        "ar": "تهكّمي",
        "en": "sarcastic",
    },
    "settings.category_swarm": {
        "ar": "السرب",
        "en": "swarm",
    },
    "settings.category_browser": {
        "ar": "المتصفح",
        "en": "browser",
    },
    "settings.category_knowledge": {
        "ar": "المعرفة",
        "en": "knowledge",
    },
    "settings.category_research": {
        "ar": "البحث",
        "en": "research",
    },
    "settings.category_mcp": {
        "ar": "MCP",
        "en": "MCP",
    },
    "settings.category_skills": {
        "ar": "المهارات",
        "en": "skills",
    },
    "settings.category_calendar": {
        "ar": "التقويم",
        "en": "calendar",
    },
    "settings.category_document": {
        "ar": "المستندات",
        "en": "document",
    },
    "settings.category_email": {
        "ar": "البريد",
        "en": "email",
    },
    "settings.category_security": {
        "ar": "الأمان",
        "en": "security",
    },
    "settings.category_social": {
        "ar": "التواصل الاجتماعي",
        "en": "social",
    },
    "settings.xt.mood_deadpan": {
        "ar": "جامد",
        "en": "deadpan",
    },
    "settings.int.gcal_connected": {
        "ar": "تم ربط Google Calendar",
        "en": "Google Calendar connected",
    },
    "settings.int.as_account": {
        "ar": " بالحساب {email}",
        "en": " as {email}",
    },
    "settings.int.protocol_failed": {
        "ar": "فشل {protocol} في {provider}: {error}",
        "en": "{provider} {protocol} failed: {error}",
    },
    "settings.int.x_api_ok": {
        "ar": "واجهة X تعمل{who}. رموز المستخدم للقراءة والكتابة تعمل.",
        "en": "X API ok{who}. Read + Write user tokens work.",
    },
    "settings.int.server_name_is_required": {
        "ar": "اسم الخادم مطلوب",
        "en": "Server name is required",
    },
    "settings.int.mcp_server_added": {
        "ar": "أُضيف خادم MCP",
        "en": "MCP server added",
    },
    "settings.int.failed_to_add_server": {
        "ar": "فشلت إضافة الخادم: ",
        "en": "Failed to add server: ",
    },
    "settings.int.remove_mcp_server": {
        "ar": "إزالة خادم MCP",
        "en": "Remove MCP server",
    },
    "settings.int.remove": {
        "ar": "إزالة",
        "en": "Remove",
    },
    "settings.int.server_removed": {
        "ar": "أُزيل الخادم",
        "en": "Server removed",
    },
    "settings.int.delete_failed_http": {
        "ar": "فشل الحذف (HTTP {status})",
        "en": "Delete failed (HTTP {status})",
    },
    "settings.int.delete_failed": {
        "ar": "فشل الحذف: ",
        "en": "Delete failed: ",
    },
    "settings.int.toggle_failed": {
        "ar": "فشل التبديل: ",
        "en": "Toggle failed: ",
    },
    "settings.int.uninstall_skill": {
        "ar": "إلغاء تثبيت المهارة",
        "en": "Uninstall skill",
    },
    "settings.int.uninstall": {
        "ar": "إلغاء التثبيت",
        "en": "Uninstall",
    },
    "settings.int.skill_uninstalled": {
        "ar": "أُلغي تثبيت المهارة",
        "en": "Skill uninstalled",
    },
    "settings.int.uninstall_failed": {
        "ar": "فشل إلغاء التثبيت: ",
        "en": "Uninstall failed: ",
    },
    "settings.int.voice_settings_saved": {
        "ar": "حُفظت إعدادات الصوت",
        "en": "Voice settings saved",
    },
    "settings.int.failed_to_save_voice_settings": {
        "ar": "فشل حفظ إعدادات الصوت: ",
        "en": "Failed to save voice settings: ",
    },
    "settings.int.oauth_failed": {
        "ar": "فشل OAuth: ",
        "en": "OAuth failed: ",
    },
    "settings.int.calendar_oauth_failed": {
        "ar": "فشل OAuth للتقويم: ",
        "en": "Calendar OAuth failed: ",
    },
    "settings.int.google_oauth_client_saved": {
        "ar": "حُفظ عميل Google OAuth",
        "en": "Google OAuth client saved",
    },
    "settings.int.save_failed": {
        "ar": "فشل الحفظ: ",
        "en": "Save failed: ",
    },
    "settings.int.could_not_start_google_oauth": {
        "ar": "تعذّر بدء Google OAuth (هل حُفظ معرّف العميل والسر؟)",
        "en": "Could not start Google OAuth (is Client ID/secret saved?)",
    },
    "settings.int.gmail_oauth_failed": {
        "ar": "فشل OAuth لـ Gmail: ",
        "en": "Gmail OAuth failed: ",
    },
    "settings.int.could_not_start_microsoft_oauth": {
        "ar": "تعذّر بدء Microsoft OAuth",
        "en": "Could not start Microsoft OAuth",
    },
    "settings.int.microsoft_oauth_failed": {
        "ar": "فشل OAuth لـ Microsoft: ",
        "en": "Microsoft OAuth failed: ",
    },
    "settings.int.gmail_connected": {
        "ar": "تم ربط Gmail",
        "en": "Gmail connected",
    },
    "settings.int.gmail_protocol_connected": {
        "ar": "تم ربط Gmail عبر {protocol}",
        "en": "Gmail {protocol} connected",
    },
    "settings.int.microsoft_protocol_connected": {
        "ar": "تم ربط Microsoft عبر {protocol}",
        "en": "Microsoft {protocol} connected",
    },
    "settings.int.gmail_connect_failed": {
        "ar": "فشل ربط Gmail: ",
        "en": "Gmail connect failed: ",
    },
    "settings.int.could_not_start_google_calendar": {
        "ar": "تعذّر بدء OAuth لـ Google Calendar",
        "en": "Could not start Google Calendar OAuth",
    },
    "settings.int.failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "settings.int.google_calendar_disconnected": {
        "ar": "فُصل Google Calendar",
        "en": "Google Calendar disconnected",
    },
    "settings.int.disconnect_failed": {
        "ar": "فشل الفصل: ",
        "en": "Disconnect failed: ",
    },
    "settings.int.gmail_disconnected": {
        "ar": "فُصل Gmail",
        "en": "Gmail disconnected",
    },
    "settings.int.microsoft_app_saved": {
        "ar": "حُفظ تطبيق Microsoft",
        "en": "Microsoft app saved",
    },
    "settings.int.microsoft_connect_failed": {
        "ar": "فشل ربط Microsoft: ",
        "en": "Microsoft connect failed: ",
    },
    "settings.int.microsoft_connected": {
        "ar": "تم ربط Microsoft",
        "en": "Microsoft connected",
    },
    "settings.int.authorization_failed": {
        "ar": "فشل التفويض",
        "en": "Authorization failed",
    },
    "settings.int.microsoft_disconnected": {
        "ar": "فُصل Microsoft",
        "en": "Microsoft disconnected",
    },
    "settings.int.failed_to_load_x_status": {
        "ar": "فشل تحميل حالة X: ",
        "en": "Failed to load X status: ",
    },
    "settings.int.save_failed_2": {
        "ar": "فشل الحفظ",
        "en": "Save failed",
    },
    "settings.int.x_credentials_saved_vaulted_test": {
        "ar": "حُفظت بيانات اعتماد X (في الخزنة). اختبر الاتصال بعدها.",
        "en": "X credentials saved (vaulted). Test the connection next.",
    },
    "settings.int.x_test_failed": {
        "ar": "فشل اختبار X",
        "en": "X test failed",
    },
    "settings.int.x_test_failed_2": {
        "ar": "فشل اختبار X: ",
        "en": "X test failed: ",
    },
    "settings.int.disconnect_x": {
        "ar": "فصل X",
        "en": "Disconnect X",
    },
    "settings.int.remove_the_four_oauth_keys": {
        "ar": "إزالة مفاتيح OAuth الأربعة من الخزنة وتعطيل النشر؟",
        "en": "Remove the four OAuth keys from the vault and disable posting?",
    },
    "settings.int.disconnect": {
        "ar": "فصل",
        "en": "Disconnect",
    },
    "settings.int.disconnect_failed_2": {
        "ar": "فشل الفصل",
        "en": "Disconnect failed",
    },
    "settings.int.x_connector_disconnected": {
        "ar": "فُصل موصّل X.",
        "en": "X connector disconnected.",
    },
    "settings.int.failed_to_load_auto_reply": {
        "ar": "فشل تحميل إعدادات الرد التلقائي: ",
        "en": "Failed to load auto-reply settings: ",
    },
    "settings.int.auto_reply_saved_mentions_poller": {
        "ar": "حُفظ الرد التلقائي. متابِع الإشارات يعمل.",
        "en": "Auto-reply saved. Mentions poller is live.",
    },
    "settings.int.saved_restart_kazma_to_start": {
        "ar": "حُفظ. أعد تشغيل Kazma لبدء متابِع الإشارات.",
        "en": "Saved. Restart Kazma to start the mentions poller.",
    },
    "settings.int.auto_reply_settings_saved": {
        "ar": "حُفظت إعدادات الرد التلقائي.",
        "en": "Auto-reply settings saved.",
    },
    "settings.int.paste_the_post_you_want": {
        "ar": "الصق المنشور الذي تريد الرد عليه.",
        "en": "Paste the post you want a reply to.",
    },
    "settings.int.preview_failed": {
        "ar": "فشلت المعاينة",
        "en": "Preview failed",
    },
    "settings.int.preview_failed_2": {
        "ar": "فشلت المعاينة: ",
        "en": "Preview failed: ",
    },
    "settings.int.remove_mcp_message": {
        "ar": "إزالة خادم MCP «{name}»؟ لا يمكن التراجع عن ذلك.",
        "en": "Remove MCP server \"{name}\"? This cannot be undone.",
    },
    "settings.int.mcp_tools_found": {
        "ar": "{name}: عُثر على {n} أدوات",
        "en": "{name}: {n} tools found",
    },
    "settings.int.test_failed": {
        "ar": "فشل الاختبار: {error}",
        "en": "Test failed: {error}",
    },
    "settings.int.uninstall_skill_message": {
        "ar": "إلغاء تثبيت المهارة «{name}»؟ لا يمكن التراجع عن ذلك.",
        "en": "Uninstall skill \"{name}\"? This cannot be undone.",
    },
    "settings.hub.default_set": {
        "ar": "صار الافتراضي لـ «{task}» هو {model}",
        "en": "Default for \"{task}\" set to {model}",
    },
    "settings.hub.delete_profile_message": {
        "ar": "حذف الملف الشخصي «{name}»؟ لا يمكن التراجع عن ذلك.",
        "en": "Delete profile \"{name}\"? This cannot be undone.",
    },
    "settings.hub.loaded_profile": {
        "ar": "حُمّل الملف الشخصي «{name}»",
        "en": "Loaded profile \"{name}\"",
    },
    "settings.hub.gateway_refreshed": {
        "ar": "حُدّثت البوابة — {n} محوّلات: {names}",
        "en": "Gateway refreshed — {n} adapter(s): {names}",
    },
    "settings.hub.test_failed_error": {
        "ar": "فشل الاختبار: {error}",
        "en": "Test failed: {error}",
    },
    "settings.hub.delete_provider_message": {
        "ar": "حذف المزوّد «{name}»؟ لا يمكن التراجع عن ذلك.",
        "en": "Delete provider \"{name}\"? This cannot be undone.",
    },
    "settings.hub.model_not_in_list": {
        "ar": "{model} ليس في القائمة",
        "en": "{model} is not in the list",
    },
    # Toasts settings_hub.js wrote in English in every language (2026-10-01).
    "settings.hub.models_found": {
        "ar": "النماذج التي عُثر عليها: {n}",
        "en": "Models found: {n}",
    },
    "settings.hub.profile_saved": {
        "ar": "حُفظ الملف الشخصي «{name}»",
        "en": "Profile \"{name}\" saved",
    },
    "settings.hub.profile_deleted": {
        "ar": "حُذف الملف الشخصي «{name}»",
        "en": "Profile \"{name}\" deleted",
    },
    "settings.hub.model_removed": {
        "ar": "أُزيل {model}",
        "en": "Removed {model}",
    },
    "settings.hub.clear_models_message": {
        "ar": "مسح النماذج المكتشفة لـ «{name}»؟ ستُمسح أيضًا النماذج التي اخترتها لهذا المزوّد.",
        "en": "Clear discovered models for \"{name}\"? Your selected models for this provider will also be cleared.",
    },
    "settings.hub.delete_connector_message": {
        "ar": "حذف الموصّل «{name}»؟ لا يمكن التراجع عن ذلك.",
        "en": "Delete connector \"{name}\"? This cannot be undone.",
    },
    "settings.hub.connection_test_ok": {
        "ar": "نجح اختبار الاتصال",
        "en": "Connection test succeeded",
    },
    "settings.hub.enter_a_base_url_first": {
        "ar": "أدخل عنوان URL الأساسي أولًا",
        "en": "Enter a base URL first",
    },
    "settings.hub.no_models_returned_check_your": {
        "ar": "لم تُرجع أي نماذج. تحقّق من مفتاح API.",
        "en": "No models returned. Check your API key.",
    },
    "settings.hub.fetch_failed": {
        "ar": "فشل الجلب: ",
        "en": "Fetch failed: ",
    },
    "settings.hub.model_settings_saved": {
        "ar": "حُفظت إعدادات النموذج",
        "en": "Model settings saved",
    },
    "settings.hub.save_failed": {
        "ar": "فشل الحفظ",
        "en": "Save failed",
    },
    "settings.hub.enter_a_profile_name": {
        "ar": "أدخل اسمًا للملف الشخصي",
        "en": "Enter a profile name",
    },
    "settings.hub.failed_to_save_profile": {
        "ar": "فشل حفظ الملف الشخصي: ",
        "en": "Failed to save profile: ",
    },
    "settings.hub.delete_profile": {
        "ar": "حذف الملف الشخصي",
        "en": "Delete profile",
    },
    "settings.hub.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "settings.hub.failed_to_delete_profile": {
        "ar": "فشل حذف الملف الشخصي: ",
        "en": "Failed to delete profile: ",
    },
    "settings.hub.enter_a_prompt_and_select": {
        "ar": "اكتب طلبًا واختر النماذج",
        "en": "Enter a prompt and select models",
    },
    "settings.hub.comparison_failed": {
        "ar": "فشلت المقارنة: ",
        "en": "Comparison failed: ",
    },
    "settings.hub.gateway_refresh_failed": {
        "ar": "فشل تحديث البوابة: ",
        "en": "Gateway refresh failed: ",
    },
    "settings.hub.nothing_to_save_no_changes": {
        "ar": "لا شيء للحفظ — لا تغييرات.",
        "en": "Nothing to save — no changes.",
    },
    "settings.hub.saved": {
        "ar": "حُفظ.",
        "en": "Saved.",
    },
    "settings.hub.save_failed_2": {
        "ar": "فشل الحفظ: ",
        "en": "Save failed: ",
    },
    "settings.hub.test_failed": {
        "ar": "فشل الاختبار: ",
        "en": "Test failed: ",
    },
    "settings.hub.name_and_base_url_are": {
        "ar": "الاسم وعنوان URL الأساسي مطلوبان",
        "en": "Name and Base URL are required",
    },
    "settings.hub.provider_saved": {
        "ar": "حُفظ المزوّد",
        "en": "Provider saved",
    },
    "settings.hub.failed_to_save_provider": {
        "ar": "فشل حفظ المزوّد: ",
        "en": "Failed to save provider: ",
    },
    "settings.hub.delete_provider": {
        "ar": "حذف المزوّد",
        "en": "Delete provider",
    },
    "settings.hub.provider_removed": {
        "ar": "أُزيل المزوّد",
        "en": "Provider removed",
    },
    "settings.hub.failed_to_delete_provider": {
        "ar": "فشل حذف المزوّد: ",
        "en": "Failed to delete provider: ",
    },
    "settings.hub.toggle_failed": {
        "ar": "فشل التبديل: ",
        "en": "Toggle failed: ",
    },
    "settings.hub.enter_a_provider_name_and": {
        "ar": "أدخل اسم المزوّد وعنوان URL الأساسي أولًا",
        "en": "Enter a provider name and base URL first",
    },
    "settings.hub.connection_test_succeeded": {
        "ar": "نجح اختبار الاتصال",
        "en": "Connection test succeeded",
    },
    "settings.hub.discover_failed": {
        "ar": "فشل الاكتشاف: ",
        "en": "Discover failed: ",
    },
    "settings.hub.failed_to_save_model_selection": {
        "ar": "فشل حفظ اختيار النماذج",
        "en": "Failed to save model selection",
    },
    "settings.hub.failed_to_remove_model": {
        "ar": "فشلت إزالة النموذج",
        "en": "Failed to remove model",
    },
    "settings.hub.failed_to_remove_model_2": {
        "ar": "فشلت إزالة النموذج: ",
        "en": "Failed to remove model: ",
    },
    "settings.hub.clear_discovered_models": {
        "ar": "مسح النماذج المكتشفة",
        "en": "Clear discovered models",
    },
    "settings.hub.clear": {
        "ar": "مسح",
        "en": "Clear",
    },
    "settings.hub.cleared_discovered_models": {
        "ar": "مُسحت النماذج المكتشفة",
        "en": "Cleared discovered models",
    },
    "settings.hub.failed_to_clear_models": {
        "ar": "فشل مسح النماذج",
        "en": "Failed to clear models",
    },
    "settings.hub.failed_to_clear_models_2": {
        "ar": "فشل مسح النماذج: ",
        "en": "Failed to clear models: ",
    },
    "settings.hub.connector_name_is_required": {
        "ar": "اسم الموصّل مطلوب",
        "en": "Connector name is required",
    },
    "settings.hub.connector_saved": {
        "ar": "حُفظ الموصّل",
        "en": "Connector saved",
    },
    "settings.hub.failed_to_save_connector": {
        "ar": "فشل حفظ الموصّل: ",
        "en": "Failed to save connector: ",
    },
    "settings.hub.delete_connector": {
        "ar": "حذف الموصّل",
        "en": "Delete connector",
    },
    "settings.hub.connector_removed": {
        "ar": "أُزيل الموصّل",
        "en": "Connector removed",
    },
    "settings.hub.failed_to_delete_connector": {
        "ar": "فشل حذف الموصّل: ",
        "en": "Failed to delete connector: ",
    },
    "settings.hub.select_a_connector_name_first": {
        "ar": "اختر اسم موصّل أولًا",
        "en": "Select a connector name first",
    },
    "settings.hub.profile_name_is_required": {
        "ar": "اسم الملف الشخصي مطلوب",
        "en": "Profile name is required",
    },
    "settings.agentjs.synced_neo4j": {
        "ar": "زُامنت {n} معتقدات إلى Neo4j",
        "en": "Synced {n} beliefs to Neo4j",
    },
    "settings.agentjs.rebuild_started_status": {
        "ar": "بدأت إعادة البناء (راجع الحالة في صفحة المُضمِّن)",
        "en": "Rebuild started (see status on Embedder page)",
    },
    "settings.agentjs.status_failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "settings.agentjs.save_failed": {
        "ar": "فشل الحفظ: ",
        "en": "Save failed: ",
    },
    "settings.agentjs.non_stop_settings_saved_applies": {
        "ar": "حُفظت إعدادات التشغيل المتواصل — تُطبّق فورًا",
        "en": "Non-stop settings saved — applies live",
    },
    "settings.agentjs.save_failed_2": {
        "ar": "فشل الحفظ",
        "en": "Save failed",
    },
    "settings.agentjs.memory_isolation_mode_saved_takes": {
        "ar": "حُفظ وضع عزل الذاكرة — يسري من الدور التالي",
        "en": "Memory isolation mode saved — takes effect next turn",
    },
    "settings.agentjs.memory_knowledge_settings_saved": {
        "ar": "حُفظت إعدادات الذاكرة / المعرفة",
        "en": "Memory / Knowledge settings saved",
    },
    "settings.agentjs.memory_backends_saved": {
        "ar": "حُفظت خلفيات الذاكرة",
        "en": "Memory backends saved",
    },
    "settings.agentjs.neo4j_connected": {
        "ar": "تم الاتصال بـ Neo4j",
        "en": "Neo4j connected",
    },
    "settings.agentjs.neo4j_test_failed": {
        "ar": "فشل اختبار Neo4j",
        "en": "Neo4j test failed",
    },
    "settings.agentjs.neo4j_sync_failed": {
        "ar": "فشلت المزامنة مع Neo4j",
        "en": "Neo4j sync failed",
    },
    "settings.agentjs.postgres_sync_failed": {
        "ar": "فشلت المزامنة مع Postgres",
        "en": "Postgres sync failed",
    },
    "settings.agentjs.memory_backends_reset_to_local": {
        "ar": "أُعيدت خلفيات الذاكرة إلى المحلية",
        "en": "Memory backends reset to local",
    },
    "settings.agentjs.reset_failed": {
        "ar": "فشلت إعادة الضبط",
        "en": "Reset failed",
    },
    "settings.agentjs.rebuild_embeddings": {
        "ar": "إعادة بناء التضمينات؟",
        "en": "Rebuild embeddings?",
    },
    "settings.agentjs.re_embed_episodes_beliefs_for": {
        "ar": "إعادة تضمين الحلقات والمعتقدات للنموذج الحالي. قد يستغرق دقائق.",
        "en": "Re-embed episodes/beliefs for the current model. May take minutes.",
    },
    "settings.agentjs.rebuild_failed": {
        "ar": "فشلت إعادة البناء",
        "en": "Rebuild failed",
    },
    "settings.agentjs.rebuild_started": {
        "ar": "بدأت إعادة البناء",
        "en": "Rebuild started",
    },
    "settings.agentjs.synced_rows_postgres": {
        "ar": "الصفوف المُزامنة إلى Postgres: {n}",
        "en": "Synced {n} rows to Postgres",
    },
    "settings.agentjs.logging_settings_saved_restart_for": {
        "ar": "حُفظت إعدادات السجلات (أعد التشغيل لتطبيق تغييرات التدوير)",
        "en": "Logging settings saved (restart for rotation changes)",
    },
    "settings.agentjs.document_settings_saved": {
        "ar": "حُفظت إعدادات المستندات",
        "en": "Document settings saved",
    },
    "settings.agentjs.document_settings_save_failed": {
        "ar": "فشل حفظ إعدادات المستندات",
        "en": "Document settings save failed",
    },
    "settings.agentjs.model_is_required": {
        "ar": "النموذج مطلوب",
        "en": "Model is required",
    },
    "settings.agentjs.embedder_settings_saved_restart_the": {
        "ar": "حُفظت إعدادات المُضمِّن. أعد تشغيل الخادم لتطبيقها.",
        "en": "Embedder settings saved. Restart the server to apply.",
    },
    "settings.agentjs.all_memory_rows_not_in": {
        "ar": "ستُعاد ترميز كل صفوف الذاكرة غير الموجودة في فضاء المتجهات الحالي بالنموذج النشط. يعمل ذلك في الخلفية وقد يطول للمخازن الكبيرة. تُنشأ نسخة احتياطية تلقائيًا أولًا.",
        "en": "All memory rows not in the current vector space will be re-encoded with the active model. This runs in the background and can take a while for large stores. A backup is created automatically first.",
    },
    "settings.agentjs.rebuild_started_in_the_background": {
        "ar": "بدأت إعادة البناء في الخلفية.",
        "en": "Rebuild started in the background.",
    },
    "settings.agentjs.a_rebuild_is_already_running": {
        "ar": "إعادة بناء تعمل بالفعل.",
        "en": "A rebuild is already running.",
    },
    "settings.agentjs.failed_to_start_rebuild": {
        "ar": "تعذّر بدء إعادة البناء",
        "en": "Failed to start rebuild",
    },
    "settings.agentjs.rebuild_failed_to_start": {
        "ar": "تعذّر بدء إعادة البناء: ",
        "en": "Rebuild failed to start: ",
    },
    "settings.agentjs.embedding_rebuild_complete": {
        "ar": "اكتملت إعادة بناء التضمينات.",
        "en": "Embedding rebuild complete.",
    },
    "settings.agentjs.embedding_rebuild_failed": {
        "ar": "فشلت إعادة بناء التضمينات: ",
        "en": "Embedding rebuild failed: ",
    },
    "settings.agentjs.unknown_error": {
        "ar": "خطأ غير معروف",
        "en": "unknown error",
    },
    "settings.agentjs.notification_preference_saved": {
        "ar": "حُفظ تفضيل الإشعارات.",
        "en": "Notification preference saved.",
    },
    "settings.agentjs.snapshots_per_thread_must_be": {
        "ar": "يجب ألا تقل اللقطات لكل محادثة عن 1",
        "en": "Snapshots per thread must be at least 1",
    },
    "settings.agentjs.retention_days_must_be_at": {
        "ar": "يجب ألا تقل أيام الاحتفاظ عن 1",
        "en": "Retention days must be at least 1",
    },
    "settings.agentjs.time_travel_settings_saved_restart": {
        "ar": "حُفظت إعدادات السفر عبر الزمن. أعد تشغيل الخادم لتطبيقها.",
        "en": "Time travel settings saved. Restart the server to apply.",
    },
    "settings.ops.install_extra_message": {
        "ar": "تثبيت الحزمة الإضافية «{name}» في بيئة Python هذه؟ يعمل ذلك عبر uv/pip في الخلفية.",
        "en": "Install the \"{name}\" extra into this Python environment? This runs uv/pip in the background.",
    },
    "settings.ops.install_extra_native": {
        "ar": "تثبيت الحزمة الإضافية الاختيارية «{name}»؟",
        "en": "Install optional extra \"{name}\"?",
    },
    "settings.ops.delete_user_confirm": {
        "ar": "حذف المستخدم {name}؟",
        "en": "Delete user {name}?",
    },
    "settings.ops.install_failed_to_start": {
        "ar": "تعذّر بدء التثبيت",
        "en": "Install failed to start",
    },
    "settings.ops.installing_extra": {
        "ar": "جارٍ تثبيت «{name}» في الخلفية… حدّث هذا التبويب بعد دقيقة.",
        "en": "Installing \"{name}\" in the background… Refresh this tab in a minute.",
    },
    "settings.ops.network_error": {
        "ar": "خطأ في الشبكة",
        "en": "Network error",
    },
    "settings.ops.installed_extra": {
        "ar": "ثُبّتت «{name}». جارٍ إعادة تحميل قائمة الحزم…",
        "en": "Installed \"{name}\". Reloading package list…",
    },
    "settings.ops.install_failed": {
        "ar": "فشل التثبيت: {error}",
        "en": "Install failed: {error}",
    },
    "settings.ops.see_server_logs": {
        "ar": "راجع سجلات الخادم",
        "en": "see server logs",
    },
    "settings.ops.delete_backup_message": {
        "ar": "حذف النسخة الاحتياطية {date}؟ لا يمكن التراجع عن ذلك.",
        "en": "Delete backup {date}? This cannot be undone.",
    },
    "settings.drive_run_test": {
        "ar": "شغّل «اختبار» للتشخيص",
        "en": "run Test to diagnose",
    },
    "settings.ops.shortcut_not_saved": {
        "ar": "لم يُحفظ الاختصار: ",
        "en": "Shortcut not saved: ",
    },
    "settings.ops.reset_shortcuts": {
        "ar": "إعادة ضبط الاختصارات",
        "en": "Reset shortcuts",
    },
    "settings.ops.reset_all_shortcuts_to_defaults": {
        "ar": "إعادة كل الاختصارات إلى الافتراضي؟",
        "en": "Reset all shortcuts to defaults?",
    },
    "settings.ops.reset": {
        "ar": "إعادة ضبط",
        "en": "Reset",
    },
    "settings.ops.reset_failed": {
        "ar": "فشلت إعادة الضبط: ",
        "en": "Reset failed: ",
    },
    "settings.ops.revoke_token": {
        "ar": "إلغاء الرمز",
        "en": "Revoke token",
    },
    "settings.ops.revoke_this_token_this_cannot": {
        "ar": "إلغاء هذا الرمز؟ لا يمكن التراجع عن ذلك. ستحصل البرامج التي تستخدمه على 401.",
        "en": "Revoke this token? This cannot be undone. Scripts using it will get 401.",
    },
    "settings.ops.revoke": {
        "ar": "إلغاء",
        "en": "Revoke",
    },
    "settings.ops.copied_to_clipboard": {
        "ar": "نُسخ إلى الحافظة",
        "en": "Copied to clipboard",
    },
    "settings.ops.install_optional_dependency": {
        "ar": "تثبيت حزمة اختيارية",
        "en": "Install optional dependency",
    },
    "settings.ops.install": {
        "ar": "تثبيت",
        "en": "Install",
    },
    "settings.ops.delete_backup": {
        "ar": "حذف النسخة الاحتياطية",
        "en": "Delete backup",
    },
    "settings.ops.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "settings.core.no_restart_needed_config_already": {
        "ar": "لا حاجة لإعادة التشغيل — الإعدادات تطابق الخادم العامل.",
        "en": "No restart needed — config already matches the running server.",
    },
    "settings.core.restart_failed": {
        "ar": "فشلت إعادة التشغيل",
        "en": "Restart failed",
    },
    "settings.core.restarting_server_the_page_will": {
        "ar": "جارٍ إعادة تشغيل الخادم… ستُعاد تحميل الصفحة بعد قليل.",
        "en": "Restarting server… the page will reload shortly.",
    },
    "settings.core.server_did_not_come_back": {
        "ar": "لم يعد الخادم — تحقّق من الطرفية.",
        "en": "Server did not come back — check the terminal.",
    },
    "settings.core.restart_request_failed": {
        "ar": "فشل طلب إعادة التشغيل: ",
        "en": "Restart request failed: ",
    },
    "settings.token_created_once": {
        "ar": "أُنشئ الرمز — انسخه الآن (يظهر مرة واحدة)",
        "en": "Token created — copy now (shown once)",
    },
    "settings.copy_token": {
        "ar": "نسخ الرمز",
        "en": "Copy token",
    },
    "settings.copy_curl": {
        "ar": "نسخ مثال curl",
        "en": "Copy curl example",
    },
    "settings.token_prefix": {
        "ar": "البادئة",
        "en": "Prefix",
    },
    "settings.last_active": {
        "ar": "آخر نشاط",
        "en": "Last active",
    },
    "settings.stt_api_key": {
        "ar": "مفتاح API لتحويل الكلام إلى نص",
        "en": "STT API key",
    },
    "settings.keep_stored_key": {
        "ar": "اتركه فارغًا للإبقاء على المفتاح المحفوظ",
        "en": "Leave blank to keep the stored key",
    },
    "settings.keep_stored_secret": {
        "ar": "اتركه فارغًا للإبقاء على السر المحفوظ",
        "en": "Leave blank to keep the stored secret",
    },
    "settings.stored_in_vault_hint": {
        "ar": "يُحفظ مشفّرًا في الخزنة. يظهر هكذا <code>********</code> عند تعيينه — تفريغ الحقل لا يحذفه.",
        "en": "Stored encrypted in the vault. Shown as <code>********</code> when one is set — clearing the box does not delete it.",
    },
    "settings.voice_auto_hint": {
        "ar": "يختار صوتًا لكل رد حسب نصه. أي خيار آخر هنا يثبّت صوتًا واحدًا لكل الردود، بكل اللغات.",
        "en": "Picks a voice per reply from the text. Anything else here pins one voice to every reply, in every language.",
    },
    "settings.english_voice": {
        "ar": "الصوت الإنجليزي",
        "en": "English voice",
    },
    "settings.arabic_voice": {
        "ar": "الصوت العربي",
        "en": "Arabic voice",
    },
    "settings.voice_default": {
        "ar": "الافتراضي ({voice})",
        "en": "Default ({voice})",
    },
    "settings.no_arabic_voices": {
        "ar": "لم يُبلغ هذا المزوّد عن أصوات عربية.",
        "en": "No Arabic voices reported by this provider.",
    },
    "settings.custom_voice_placeholder": {
        "ar": "أدخل معرّف صوت مخصص",
        "en": "Enter custom voice ID",
    },
    "settings.livekit_title": {
        "ar": "الصوت المباشر ثنائي الاتجاه (LiveKit)",
        "en": "Live duplex voice (LiveKit)",
    },
    "settings.livekit_hint": {
        "ar": "اختياري. مطلوب فقط للوضع المباشر الذي يمكن مقاطعته في أي وقت — التسجيل والقراءة بصوت عالٍ يعملان بدونه.",
        "en": "Optional. Only needed for the live, interrupt-anytime mode — recording and read-aloud work without it.",
    },
    "settings.livekit_url": {
        "ar": "رابط LiveKit",
        "en": "LiveKit URL",
    },
    "settings.livekit_api_key": {
        "ar": "مفتاح API لـ LiveKit",
        "en": "LiveKit API key",
    },
    "settings.livekit_api_secret": {
        "ar": "سر API لـ LiveKit",
        "en": "LiveKit API secret",
    },
    "settings.livekit_vault_hint": {
        "ar": "يُحفظ كلاهما مشفّرًا في الخزنة ويظهران هكذا <code>********</code> بعد التعيين.",
        "en": "Both are stored encrypted in the vault and shown as <code>********</code> once set.",
    },
    "settings.preset_deep": {
        "ar": "معمّق",
        "en": "Deep",
    },
    "settings.preset_research": {
        "ar": "بحث",
        "en": "Research",
    },
    "settings.preset_chat": {
        "ar": "محادثة",
        "en": "Chat",
    },
    "settings.role_viewer": {
        "ar": "مشاهد",
        "en": "viewer",
    },
    "settings.role_operator": {
        "ar": "مشغّل",
        "en": "operator",
    },
    "settings.role_admin": {
        "ar": "مشرف",
        "en": "admin",
    },
    "settings.tts_voice_auto": {
        "ar": "تلقائي — بلغة الرسالة",
        "en": "Auto — match the message language",
    },
    "settings.agentjs.st_saving": {
        "ar": "جارٍ الحفظ…",
        "en": "Saving…",
    },
    "settings.agentjs.st_saved_next": {
        "ar": "حُفظ. التالي: اختبر Neo4j، ثم زامن المعتقدات ← Neo4j.",
        "en": "Saved. Next: Test Neo4j, then Sync beliefs → Neo4j.",
    },
    "settings.agentjs.st_save_failed": {
        "ar": "فشل الحفظ",
        "en": "Save failed",
    },
    "settings.agentjs.st_testing_neo4j": {
        "ar": "جارٍ اختبار Neo4j…",
        "en": "Testing Neo4j…",
    },
    "settings.agentjs.st_neo4j_connected": {
        "ar": "متصل · {ms}ms — {detail}",
        "en": "Connected · {ms}ms — {detail}",
    },
    "settings.agentjs.st_test_error": {
        "ar": "خطأ في الاختبار: {error}",
        "en": "Test error: {error}",
    },
    "settings.agentjs.st_syncing_neo4j": {
        "ar": "جارٍ مزامنة المعتقدات إلى Neo4j…",
        "en": "Syncing beliefs to Neo4j…",
    },
    "settings.agentjs.st_synced_beliefs": {
        "ar": "زُامنت {n} معتقدات",
        "en": "Synced {n} beliefs",
    },
    "settings.agentjs.st_sync_failed": {
        "ar": "فشلت المزامنة",
        "en": "Sync failed",
    },
    "settings.agentjs.st_sync_error": {
        "ar": "خطأ في المزامنة: {error}",
        "en": "Sync error: {error}",
    },
    "settings.agentjs.st_syncing_postgres": {
        "ar": "جارٍ مزامنة المعتقدات والحلقات إلى Postgres…",
        "en": "Syncing beliefs + episodes to Postgres…",
    },
    "settings.agentjs.st_synced_rows": {
        "ar": "زُامنت {n} صفوف",
        "en": "Synced {n} rows",
    },
    "settings.agentjs.st_testing_embedder": {
        "ar": "جارٍ اختبار المُضمِّن…",
        "en": "Testing embedder…",
    },
    "settings.agentjs.st_embed_ok": {
        "ar": "التضمين يعمل · {ms}ms · البُعد {dim}",
        "en": "Embed OK · {ms}ms · dim {dim}",
    },
    "settings.agentjs.st_embed_failed": {
        "ar": "فشل التضمين: {error}",
        "en": "Embed failed: {error}",
    },
    "settings.agentjs.st_embed_test_error": {
        "ar": "خطأ في اختبار التضمين",
        "en": "Embed test error",
    },
    "settings.agentjs.st_testing_vector": {
        "ar": "جارٍ اختبار خلفية المتجهات…",
        "en": "Testing vector backend…",
    },
    "settings.agentjs.st_vector_ok": {
        "ar": "المتجهات تعمل · {provider} · {ms}ms",
        "en": "Vector OK · {provider} · {ms}ms",
    },
    "settings.agentjs.st_vector_failed": {
        "ar": "فشلت المتجهات: {error}",
        "en": "Vector failed: {error}",
    },
    "settings.agentjs.st_vector_test_error": {
        "ar": "خطأ في اختبار المتجهات",
        "en": "Vector test error",
    },
    "settings.agentjs.st_reset_local": {
        "ar": "أُعيد الضبط إلى الافتراضيات المحلية",
        "en": "Reset to local defaults",
    },
    "settings.agentjs.st_unknown": {
        "ar": "غير معروف",
        "en": "unknown",
    },
    "settings.agentjs.st_saved": {
        "ar": "حُفظ",
        "en": "Saved",
    },
    "settings.ops.backup_starting": {
        "ar": "جارٍ البدء…",
        "en": "Starting…",
    },
    "settings.calendar_google": {
        "ar": "تقويم Google",
        "en": "Google Calendar",
    },
    "settings.calendar_outlook": {
        "ar": "تقويم Outlook",
        "en": "Outlook Calendar",
    },
    "settings.calendar_connect_outlook": {
        "ar": "ربط تقويم Outlook",
        "en": "Connect Outlook Calendar",
    },
    "settings.calendar_disconnect_google": {
        "ar": "فصل تقويم Google",
        "en": "Disconnect Google Calendar",
    },
    "settings.calendar_disconnect_outlook": {
        "ar": "فصل تقويم Outlook",
        "en": "Disconnect Outlook Calendar",
    },
    "settings.calendar_disconnect_outlook_confirm": {
        "ar": "فصل تقويم Outlook؟ يتوقف Kazma عن قراءته وتعديله حتى تربطه من هنا مرة أخرى. يبقى بريد Microsoft مربوطًا.",
        "en": "Disconnect Outlook Calendar? Kazma stops reading and changing it until you connect it again here. Microsoft mail stays connected.",
    },
    "settings.email_account_signed_in": {
        "ar": "تم تسجيل الدخول",
        "en": "Signed in",
    },
    "settings.email_account_incomplete": {
        "ar": "تسجيل الدخول غير مكتمل",
        "en": "Sign-in incomplete",
    },
    "settings.int.outlook_calendar_connected": {
        "ar": "تم ربط تقويم Outlook",
        "en": "Outlook Calendar connected",
    },
    "settings.int.outlook_calendar_disconnected": {
        "ar": "فُصل تقويم Outlook",
        "en": "Outlook Calendar disconnected",
    },
    "settings.int.calendar_not_granted": {
        "ar": " — لم تُمنح صلاحية التقويم؛ استخدم زر ربط تقويم Google",
        "en": " — Calendar was not granted; use Connect Google Calendar",
    },
    "settings.calendar_outlook_code_fallback": {
        "ar": "ترفض Microsoft إعادة التوجيه؟ اربط برمز بدلًا من ذلك",
        "en": "Microsoft refuses the redirect? Connect with a code instead",
    },
    "settings.calendar_outlook_code_start": {
        "ar": "احصل على رمز لتقويم Outlook",
        "en": "Get a code for Outlook Calendar",
    },
    "settings.email_accounts_title": {
        "ar": "حسابات أخرى",
        "en": "Other accounts",
    },
    "settings.email_accounts_hint": {
        "ar": "صناديق بريد أخرى من Google أو Microsoft أو IMAP إلى جانب الحسابين الرئيسيين أعلاه. في المحادثة، اذكر اسم الحساب أو عنوانه («تحقق من بريد العمل»، «أرسل من you@example.com»)؛ وإن لم تذكر حسابًا يستخدم Kazma الحساب الرئيسي. ويأتي تقويم حساب Google أو Microsoft مع تسجيل دخوله.",
        "en": "More Google, Microsoft or IMAP mailboxes beside the main ones above. In chat, name an account or its address (“check my work inbox”, “send from you@example.com”); with none named, Kazma uses the main account. A Google or Microsoft account's calendar comes with its sign-in.",
    },
    "settings.email_accounts_none": {
        "ar": "لا توجد حسابات أخرى بعد.",
        "en": "No other accounts yet.",
    },
    "settings.email_account_add_google": {
        "ar": "إضافة حساب Google",
        "en": "Add Google account",
    },
    "settings.email_account_add_microsoft": {
        "ar": "إضافة حساب Microsoft",
        "en": "Add Microsoft account",
    },
    "settings.email_account_add_password": {
        "ar": "إضافة بكلمة مرور",
        "en": "Add with a password",
    },
    "settings.email_account_code_fallback": {
        "ar": "ترفض Microsoft إعادة التوجيه؟ أضف حساب Microsoft برمز",
        "en": "Microsoft refuses the redirect? Add a Microsoft account with a code",
    },
    "settings.email_account_code_start": {
        "ar": "احصل على رمز لحساب Microsoft جديد",
        "en": "Get a code for a new Microsoft account",
    },
    "settings.email_account_reconnect": {
        "ar": "إعادة الربط",
        "en": "Reconnect",
    },
    "settings.email_account_remove": {
        "ar": "إزالة",
        "en": "Remove",
    },
    "settings.email_account_calendar": {
        "ar": "التقويم",
        "en": "Calendar",
    },
    "settings.email_account_env_hint": {
        "ar": "مضبوط في ‎.env، ويُغيَّر هناك.",
        "en": "Set in .env; change it there.",
    },
    "settings.email_account_app_password": {
        "ar": "كلمة مرور",
        "en": "Password",
    },
    "settings.email_account_name_title_google": {
        "ar": "إضافة حساب Google",
        "en": "Add a Google account",
    },
    "settings.email_account_name_title_microsoft": {
        "ar": "إضافة حساب Microsoft",
        "en": "Add a Microsoft account",
    },
    "settings.email_account_name_prompt": {
        "ar": "اسم قصير لهذا الحساب تستخدمه في المحادثة (مثل: work أو personal). بأحرف a–z وأرقام وشرطات.",
        "en": "A short name for this account, used in chat (for example: work, personal). Letters a–z, digits and hyphens.",
    },
    "settings.email_account_continue": {
        "ar": "متابعة إلى تسجيل الدخول",
        "en": "Continue to sign in",
    },
    "settings.email_account_remove_title": {
        "ar": "إزالة الحساب",
        "en": "Remove account",
    },
    "settings.email_account_remove_confirm": {
        "ar": "إزالة «{name}» ({address})؟ ينسى Kazma تسجيل دخوله ويتوقف عن استخدامه، ولا يُمس صندوق البريد نفسه.",
        "en": "Remove “{name}” ({address})? Kazma forgets its sign-in and stops using it; the mailbox itself is not touched.",
    },
    "settings.email_account_form_name": {
        "ar": "الاسم المستخدم في المحادثة",
        "en": "Name used in chat",
    },
    "settings.email_account_form_type": {
        "ar": "المزوّد",
        "en": "Provider",
    },
    "settings.email_account_form_type_gmail": {
        "ar": "Gmail (كلمة مرور تطبيق)",
        "en": "Gmail (app password)",
    },
    "settings.email_account_form_type_microsoft": {
        "ar": "Microsoft (كلمة مرور)",
        "en": "Microsoft (password)",
    },
    "settings.email_account_form_type_imap": {
        "ar": "خادم بريد آخر (IMAP)",
        "en": "Other mail server (IMAP)",
    },
    "settings.email_account_form_type_pop": {
        "ar": "خادم بريد آخر (POP)",
        "en": "Other mail server (POP)",
    },
    "settings.email_account_form_address": {
        "ar": "عنوان البريد",
        "en": "Email address",
    },
    "settings.email_account_form_password": {
        "ar": "كلمة المرور (كلمة مرور تطبيق لـ Gmail)",
        "en": "Password (an app password for Gmail)",
    },
    "settings.email_account_form_host": {
        "ar": "خادم البريد (مضيف IMAP أو POP)",
        "en": "Mail server (IMAP or POP host)",
    },
    "settings.email_account_form_smtp_host": {
        "ar": "خادم الإرسال (مضيف SMTP)",
        "en": "Sending server (SMTP host)",
    },
    "settings.email_account_form_hint": {
        "ar": "يجرّب Kazma تسجيل الدخول قبل حفظ الحساب. وليس لحساب كلمة المرور تقويم.",
        "en": "Kazma tries the login before keeping the account. A password account has no calendar.",
    },
    "settings.email_account_form_submit": {
        "ar": "إضافة الحساب",
        "en": "Add account",
    },
    "settings.int.email_account_connected": {
        "ar": "تم ربط الحساب «{name}»",
        "en": "Account “{name}” connected",
    },
    "settings.int.email_account_added": {
        "ar": "أُضيف الحساب «{name}»",
        "en": "Account “{name}” added",
    },
    "settings.int.email_account_removed": {
        "ar": "أُزيل الحساب «{name}»",
        "en": "Account “{name}” removed",
    },
    "settings.int.email_account_failed": {
        "ar": "تعذّرت إضافة الحساب: {error}",
        "en": "Could not add the account: {error}",
    },
    "settings.int.email_account_bad_name": {
        "ar": "سمِّ الحساب بأحرف a–z وأرقام وشرطات.",
        "en": "Name the account with letters a–z, digits and hyphens.",
    },
    "settings.hub.check_token": {
        "ar": "رمز البوت",
        "en": "Bot token",
    },
    "settings.hub.check_message_text": {
        "ar": "نص الرسائل",
        "en": "Message text",
    },
    "settings.hub.check_servers": {
        "ar": "الخوادم",
        "en": "Servers",
    },
    "settings.hub.check_channel": {
        "ar": "قناة التسليم",
        "en": "Delivery channel",
    },
    "settings.hub.check_latest": {
        "ar": "آخر رسالة كتبتها",
        "en": "Your latest message",
    },
    "settings.hub.check_allowed": {
        "ar": "المستخدمون المسموح لهم",
        "en": "Allowed users",
    },
    "settings.hub.check_listening": {
        "ar": "Kazma يستمع",
        "en": "Kazma is listening",
    },
    "settings.hub.discord_test_title": {
        "ar": "يفحص رمز البوت وصلاحياته وخوادمه وقناة التسليم، وهل وصلت آخر رسالة كتبتها هناك إلى Kazma",
        "en": "Checks the bot token, its permissions and servers, the delivery channel, and whether your latest message there reached Kazma",
    },
    "settings.hub.check_direct_message": {
        "ar": "رسائلك المباشرة",
        "en": "Your direct messages",
    },
    "settings.hub.open_in_discord": {
        "ar": "افتح هذه المحادثة في Discord",
        "en": "Open this conversation in Discord",
    },
    "settings.hub.check_receiving": {
        "ar": "كيف تصل الرسائل",
        "en": "How messages arrive",
    },
    "settings.hub.check_groups": {
        "ar": "المجموعات",
        "en": "Groups",
    },
    "settings.hub.check_chat": {
        "ar": "محادثة التسليم",
        "en": "Delivery chat",
    },
    "settings.hub.check_group": {
        "ar": "مسار المجموعة",
        "en": "Group route",
    },
    "settings.hub.check_app_token": {
        "ar": "رمز Socket Mode",
        "en": "Socket Mode token",
    },
    "settings.hub.check_scopes": {
        "ar": "الصلاحيات",
        "en": "Permissions",
    },
    "settings.hub.open_in_slack": {
        "ar": "افتح هذه المحادثة في Slack",
        "en": "Open this conversation in Slack",
    },
    "settings.hub.telegram_test_title": {
        "ar": "يفحص رمز البوت وطريقة وصول الرسائل وخصوصية المجموعات ومحادثة التسليم ومسار المجموعة، وما حدث لآخر رسالة أرسلتها",
        "en": "Checks the bot token, how messages arrive, group privacy, the delivery chat and the group route, and what became of the last message you sent",
    },
    "settings.hub.slack_test_title": {
        "ar": "يفحص رمزي البوت والتطبيق والصلاحيات وقناة التسليم، وهل وصلت آخر رسائلك هناك وفي الرسائل المباشرة إلى Kazma",
        "en": "Checks the bot and app tokens, the permissions, the delivery channel, and whether your latest messages there and in direct messages reached Kazma",
    },
    "settings.hub.status_messages": {
        "ar": "رسائل حالة الخادم",
        "en": "Server status messages",
    },
    "settings.hub.status_messages_hint": {
        "ar": "تُرسل حيث تذهب التنبيهات. تكفي في الغالب بطاقة واحدة عند عودة Kazma: تذكر مدة التوقف وهل اتصل كل تطبيق محادثة.",
        "en": "Sent where alerts go. One card when Kazma is back up is enough for most: it says how long Kazma was down and whether each chat app connected.",
    },
    "settings.hub.status_started": {
        "ar": "عاد Kazma للعمل (مع حالة اتصال كل تطبيق محادثة)",
        "en": "Kazma is back up (with each chat app's connection)",
    },
    "settings.hub.status_startup_failed": {
        "ar": "تعذّر تشغيل Kazma",
        "en": "Kazma failed to start",
    },
    "settings.hub.status_starting": {
        "ar": "Kazma قيد التشغيل",
        "en": "Kazma is starting",
    },
    "settings.hub.status_shutting_down": {
        "ar": "Kazma يتوقف عن العمل",
        "en": "Kazma is shutting down",
    },
}
