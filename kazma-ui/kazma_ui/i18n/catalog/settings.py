"""``settings`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
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
        "ar": "تنزيل نسخة احتياطية",
        "en": "Download Backup",
    },
    "settings.download_complete_config": {
        "ar": "نزّل إعدادات كاظمه الكاملة.",
        "en": "Download your complete Kazma configuration.",
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
        "ar": "مسح عنوان Gmail وكلمة مرور التطبيق المحفوظة؟",
        "en": "Clear saved Gmail address and app password?",
    },
    "settings.email_disconnect_ms_confirm": {
        "ar": "مسح رموز Microsoft Graph من هذا الخادم؟",
        "en": "Clear Microsoft Graph tokens from this server?",
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
        "ar": "موصى به لـ Google Workspace: تسجيل الدخول عبر Google OAuth (بدون كلمة مرور تطبيق). يستخدم Gmail API.",
        "en": "Recommended for Google Workspace: sign in with Google OAuth (no app password). Uses Gmail API.",
    },
    "settings.email_gmail_oauth_help": {
        "ar": "Google Cloud Console ← عميل OAuth ويب. يجب أن يتضمن Redirect URI المسار /api/email/oauth/gmail/callback",
        "en": "Google Cloud Console → OAuth Web client. Authorized redirect URI must include /api/email/oauth/gmail/callback",
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
    "settings.email_multi_accounts": {
        "ar": "حسابات متعددة (env)",
        "en": "Multi-account aliases (env)",
    },
    "settings.email_multi_accounts_hint": {
        "ar": "تُضبط عبر متغيرات EMAIL_ACCOUNTS / EMAIL_ACCOUNT_*.",
        "en": "Configured via EMAIL_ACCOUNTS / EMAIL_ACCOUNT_* environment variables.",
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
    "settings.format": {
        "ar": "الصيغة",
        "en": "Format",
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
    "settings.importing": {
        "ar": "جاري الاستيراد…",
        "en": "Importing…",
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
        "ar": "اترك القيمة المقنعة كما هي للحفاظ على السر الموجود.",
        "en": "Leave the masked value unchanged to keep the existing secret.",
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
    "settings.remove_model": {
        "ar": "إزالة النموذج من القائمة",
        "en": "Remove model from list",
    },
    "settings.reset_all_settings": {
        "ar": "إعادة تعيين كل الإعدادات",
        "en": "Reset All Settings",
    },
    "settings.reset_description": {
        "ar": "امسح جميع الإعدادات المحفوظة وارجع للإعدادات الافتراضية. لا يمكن التراجع عن هذا الإجراء.",
        "en": "Clear all saved settings and revert to factory defaults. This cannot be undone.",
    },
    "settings.reset_to_defaults": {
        "ar": "إعادة التعيين للافتراضي",
        "en": "Reset to Defaults",
    },
    "settings.reset_to_defaults_section": {
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
    "settings.system_reset": {
        "ar": "إعادة تعيين النظام",
        "en": "System Reset",
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
    "settings.upload_file": {
        "ar": "رفع ملف",
        "en": "Upload File",
    },
    "settings.upload_or_paste": {
        "ar": "ارفع أو الصق ملف إعدادات لاستيراد البيانات.",
        "en": "Upload or paste a configuration file to import settings.",
    },
    "settings.url": {
        "ar": "العنوان",
        "en": "URL",
    },
    "settings.username": {
        "ar": "اسم المستخدم",
        "en": "Username",
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
}
