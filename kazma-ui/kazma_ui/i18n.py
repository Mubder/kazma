"""Lightweight internationalization (i18n) system for Kazma UI.

Provides a ``t()`` translation function and a ``TRANSLATIONS`` dict keyed by
language code.  Exposed to Jinja2 templates so that ``{{ t('chat.send') }}``
renders the string in the currently configured language.

Only ``en`` and ``ar`` are shipped by default, but the structure supports
adding more languages by extending ``TRANSLATIONS``.

Usage in templates (after the global is registered)::

    {{ t('nav.dashboard') }}
    {{ t('chat.placeholder') }}
"""

from __future__ import annotations

import logging
from typing import Any

# ---------------------------------------------------------------------------
# Translation dictionaries
# ---------------------------------------------------------------------------
#
# Keys are dotted strings organised by section (nav, chat, dashboard, …).
# Every key must have an ``en`` entry; ``ar`` entries are provided for the
# Arabic-first experience.  Missing keys fall back to English, and if the
# English key is also missing the dotted key itself is returned.
# ---------------------------------------------------------------------------

TRANSLATIONS: dict[str, dict[str, str]] = {
    # ── Navigation / Sidebar ──────────────────────────────────────────
    "nav.primary": {"en": "Primary", "ar": "الرئيسية"},
    "nav.tools": {"en": "Tools", "ar": "الأدوات"},
    "nav.system": {"en": "System", "ar": "النظام"},
    "nav.workspace": {"en": "Workspace", "ar": "مساحة العمل"},
    "nav.chat": {"en": "Chat", "ar": "المحادثة"},
    "nav.dashboard": {"en": "Dashboard", "ar": "لوحة التحكم"},
    "nav.skills": {"en": "Skills", "ar": "المهارات"},
    "nav.mcp": {"en": "MCP Servers", "ar": "خوادم MCP"},
    "nav.swarm": {"en": "Swarm", "ar": "السرب"},
    "nav.agents": {"en": "Agents", "ar": "الوكلاء"},
    "nav.settings": {"en": "Settings", "ar": "الإعدادات"},

    # ── Header ────────────────────────────────────────────────────────
    "header.new_chat": {"en": "New Chat", "ar": "محادثة جديدة"},
    "header.home": {"en": "Home", "ar": "الرئيسية"},
    "header.settings": {"en": "Settings", "ar": "الإعدادات"},
    "header.dashboard": {"en": "Dashboard", "ar": "لوحة التحكم"},
    "header.health_status": {"en": "Health Status", "ar": "حالة النظام"},
    "header.logout": {"en": "Logout", "ar": "تسجيل الخروج"},

    # ── Chat ──────────────────────────────────────────────────────────
    "chat.title": {"en": "Chat", "ar": "المحادثة"},
    "chat.sessions": {"en": "Sessions", "ar": "الجلسات"},
    "chat.new_session": {"en": "+ New", "ar": "+ جديد"},
    "chat.search_sessions": {"en": "Search sessions…", "ar": "ابحث في الجلسات…"},
    "chat.loading_sessions": {"en": "Loading sessions…", "ar": "جاري تحميل الجلسات…"},
    "chat.search": {"en": "Search", "ar": "بحث"},
    "chat.new": {"en": "New", "ar": "جديد"},
    "chat.welcome_title": {"en": "Kazma", "ar": "كاظمة"},
    "chat.welcome_subtitle": {"en": "How can I help you today?", "ar": "كيف يمكنني مساعدتك اليوم؟"},
    "chat.thinking": {"en": "Kazma is thinking…", "ar": "كاظمة تفكر…"},
    "chat.placeholder": {
        "en": "Type your message… (Enter to send, Shift+Enter for newline)",
        "ar": "اكتب رسالتك… (Enter للإرسال، Shift+Enter لسطر جديد)",
    },
    "chat.send": {"en": "Send", "ar": "إرسال"},
    "chat.attach_file": {"en": "Attach file", "ar": "إرفاق ملف"},
    "chat.tokens": {"en": "tokens", "ar": "رمز"},
    "chat.send_shortcut": {"en": "send", "ar": "إرسال"},
    "chat.newline_shortcut": {"en": "newline", "ar": "سطر جديد"},

    # ── Dashboard ─────────────────────────────────────────────────────
    "dashboard.title": {"en": "Dashboard", "ar": "لوحة التحكم"},
    "dashboard.observability": {"en": "Observability Dashboard", "ar": "لوحة المراقبة"},
    "dashboard.refresh": {"en": "Refresh", "ar": "تحديث"},
    "dashboard.connecting": {"en": "Connecting…", "ar": "جاري الاتصال…"},
    "dashboard.pending_approvals": {"en": "Pending Approvals", "ar": "الموافقات المعلقة"},
    "dashboard.hitl_description": {"en": "Human-in-the-Loop tool execution review", "ar": "مراجعة تنفيذ الأدوات بمشاركة الإنسان"},
    "dashboard.no_pending_approvals": {"en": "No pending approvals", "ar": "لا توجد موافقات معلقة"},
    "dashboard.no_pending_approvals_hint": {"en": "Tools requiring human approval will appear here", "ar": "الأدوات التي تتطلب موافقة بشرية ستظهر هنا"},
    "dashboard.total_cost": {"en": "Total Cost", "ar": "التكلفة الإجمالية"},
    "dashboard.total_tokens": {"en": "Total Tokens", "ar": "إجمالي الرموز"},
    "dashboard.headroom": {"en": "headroom", "ar": "هامش متاح"},
    "dashboard.range_hour": {"en": "1H", "ar": "ساعة"},
    "dashboard.range_day": {"en": "24H", "ar": "يوم"},
    "dashboard.range_week": {"en": "7D", "ar": "أسبوع"},
    "dashboard.llm_calls": {"en": "LLM calls", "ar": "استدعاءات النماذج"},
    "dashboard.tool_calls": {"en": "Tool Calls", "ar": "استدعاءات الأدوات"},
    "dashboard.traces_count": {"en": "traces", "ar": "تتبع"},
    "dashboard.circuit_breaker": {"en": "Circuit Breaker", "ar": "قاطع الدائرة"},
    "dashboard.backend": {"en": "backend", "ar": "الخادم"},
    "dashboard.uptime": {"en": "Uptime", "ar": "وقت التشغيل"},
    "dashboard.tokens_over_time": {"en": "Tokens Over Time", "ar": "الرموز عبر الوقت"},
    "dashboard.cost_over_time": {"en": "Cost Over Time", "ar": "التكلفة عبر الوقت"},
    "dashboard.system_resources": {"en": "System Resources", "ar": "موارد النظام"},
    "dashboard.cpu": {"en": "CPU", "ar": "المعالج"},
    "dashboard.memory": {"en": "Memory", "ar": "الذاكرة"},
    "dashboard.session_management": {"en": "Session Management", "ar": "إدارة الجلسات"},
    "dashboard.clear_all": {"en": "Clear All", "ar": "مسح الكل"},
    "dashboard.no_active_sessions": {"en": "No active sessions", "ar": "لا توجد جلسات نشطة"},
    "dashboard.sessions_hint": {"en": "Sessions appear here when users start chatting", "ar": "تظهر الجلسات هنا عندما يبدأ المستخدمون بالمحادثة"},
    "dashboard.col_thread_id": {"en": "Thread ID", "ar": "معرف الجلسة"},
    "dashboard.col_platform": {"en": "Platform", "ar": "المنصة"},
    "dashboard.col_user": {"en": "User", "ar": "المستخدم"},
    "dashboard.col_messages": {"en": "Messages", "ar": "الرسائل"},
    "dashboard.col_tokens": {"en": "Tokens", "ar": "الرموز"},
    "dashboard.col_created": {"en": "Created", "ar": "تاريخ الإنشاء"},
    "dashboard.col_actions": {"en": "Actions", "ar": "إجراءات"},
    "dashboard.delete": {"en": "Delete", "ar": "حذف"},
    "dashboard.confirm_delete_session": {"en": "Delete session {thread_id}?", "ar": "حذف الجلسة {thread_id}؟"},
    "dashboard.confirm_clear_all": {"en": "Delete ALL sessions? This cannot be undone.", "ar": "حذف جميع الجلسات؟ لا يمكن التراجع عن هذا الإجراء."},
    "dashboard.error_deleting": {"en": "Error deleting session", "ar": "خطأ في حذف الجلسة"},
    "dashboard.error_clearing": {"en": "Error clearing sessions", "ar": "خطأ في مسح الجلسات"},
    "dashboard.recent_traces": {"en": "Recent Traces", "ar": "أحدث التتبعات"},
    "dashboard.col_time": {"en": "Time", "ar": "الوقت"},
    "dashboard.col_type": {"en": "Type", "ar": "النوع"},
    "dashboard.col_label": {"en": "Label", "ar": "الوصف"},
    "dashboard.col_status": {"en": "Status", "ar": "الحالة"},
    "dashboard.col_duration": {"en": "Duration", "ar": "المدة"},
    "dashboard.col_cost": {"en": "Cost", "ar": "التكلفة"},
    "dashboard.no_traces": {"en": "No traces yet", "ar": "لا توجد تتبعات بعد"},
    "dashboard.no_traces_hint": {"en": "Traces appear when Kazma processes requests", "ar": "تظهر التتبعات عندما تعالج كاظمة الطلبات"},
    "dashboard.na": {"en": "N/A", "ar": "غير متوفر"},

    # ── Settings ──────────────────────────────────────────────────────
    "settings.title": {"en": "Settings", "ar": "الإعدادات"},
    "settings.save": {"en": "Save", "ar": "حفظ"},
    "settings.saved": {"en": "Saved", "ar": "تم الحفظ"},
    "settings.cancel": {"en": "Cancel", "ar": "إلغاء"},
    "settings.loading": {"en": "Loading settings…", "ar": "جاري تحميل الإعدادات…"},

    # Settings — Tab labels
    "settings.tab_services": {"en": "Services", "ar": "الخدمات"},
    "settings.tab_models": {"en": "Models", "ar": "النماذج"},
    "settings.tab_agent": {"en": "Agent", "ar": "الوكيل"},
    "settings.tab_connectors": {"en": "Connectors", "ar": "الموصلات"},
    "settings.tab_mcp": {"en": "MCP", "ar": "MCP"},
    "settings.tab_skills": {"en": "Skills", "ar": "المهارات"},
    "settings.tab_appearance": {"en": "Appearance", "ar": "المظهر"},
    "settings.tab_shortcuts": {"en": "Shortcuts", "ar": "الاختصارات"},
    "settings.tab_account": {"en": "Account", "ar": "الحساب"},
    "settings.tab_tools": {"en": "Tools", "ar": "الأدوات"},
    "settings.tab_system": {"en": "System", "ar": "النظام"},
    "settings.tab_import": {"en": "Import/Export", "ar": "استيراد/تصدير"},

    # Settings — Providers tab
    "settings.llm_providers": {"en": "LLM Providers", "ar": "مزودو النماذج"},
    "settings.add_provider": {"en": "Add Provider", "ar": "إضافة مزود"},
    "settings.enabled_label": {"en": "Enabled", "ar": "مفعّل"},
    "settings.disabled_label": {"en": "Disabled", "ar": "معطّل"},
    "settings.unknown": {"en": "unknown", "ar": "غير معروف"},
    "settings.models_label": {"en": "Models:", "ar": "النماذج:"},
    "settings.test": {"en": "Test", "ar": "اختبار"},
    "settings.no_providers": {"en": "No providers configured. Click \"Add Provider\" to get started.", "ar": "لا يوجد مزودون مُعدّون. انقر على \"إضافة مزود\" للبدء."},
    "settings.connected_latency": {"en": "✓ Connected! Latency:", "ar": "✓ تم الاتصال! زمن الاستجابة:"},
    "settings.preset": {"en": "Preset", "ar": "القالب"},
    "settings.custom": {"en": "— Custom —", "ar": "— مخصص —"},
    "settings.name_id": {"en": "Name (ID)", "ar": "الاسم (المعرّف)"},
    "settings.display_name": {"en": "Display Name", "ar": "اسم العرض"},
    "settings.base_url": {"en": "Base URL", "ar": "عنوان الخادم"},
    "settings.api_key": {"en": "API Key", "ar": "مفتاح API"},
    "settings.models_comma": {"en": "Models (comma-separated, or leave empty to auto-discover)", "ar": "النماذج (مفصولة بفواصل، أو اتركها فارغة لاكتشاف تلقائي)"},
    "settings.adding": {"en": "Adding…", "ar": "جاري الإضافة…"},

    # Settings — Models tab
    "settings.active_model": {"en": "Active Model", "ar": "النموذج النشط"},
    "settings.provider": {"en": "Provider", "ar": "المزود"},
    "settings.model_name": {"en": "Model Name", "ar": "اسم النموذج"},
    "settings.fetch": {"en": "Fetch", "ar": "جلب"},
    "settings.select_model": {"en": "— select model —", "ar": "— اختر نموذجاً —"},
    "settings.max_tokens": {"en": "Max Tokens", "ar": "أقصى عدد من الرموز"},
    "settings.temperature": {"en": "Temperature:", "ar": "درجة الحرارة:"},
    "settings.timeout_seconds": {"en": "Timeout (seconds)", "ar": "مهلة الانتظار (ثانية)"},
    "settings.saving": {"en": "Saving…", "ar": "جاري الحفظ…"},
    "settings.test_connection": {"en": "Test Connection", "ar": "اختبار الاتصال"},
    "settings.testing": {"en": "Testing…", "ar": "جاري الاختبار…"},
    "settings.connected_model": {"en": "✓ Connected!", "ar": "✓ تم الاتصال!"},
    "settings.default_models_per_task": {"en": "Default Models per Task", "ar": "النماذج الافتراضية حسب المهمة"},
    "settings.set": {"en": "Set", "ar": "تعيين"},
    "settings.model_comparison": {"en": "Model Comparison", "ar": "مقارنة النماذج"},
    "settings.test_prompt": {"en": "Test Prompt", "ar": "نص الاختبار"},
    "settings.test_prompt_placeholder": {"en": "Enter a prompt to test across models…", "ar": "أدخل نصاً لتجربته عبر النماذج…"},
    "settings.models_to_compare": {"en": "Models to compare (comma-separated)", "ar": "النماذج للمقارنة (مفصولة بفواصل)"},
    "settings.run_comparison": {"en": "Run Comparison", "ar": "تشغيل المقارنة"},
    "settings.running": {"en": "Running…", "ar": "جاري التشغيل…"},
    "settings.no_response": {"en": "No response", "ar": "لا توجد استجابة"},

    # Settings — Agent tab
    "settings.agent_config": {"en": "Agent Configuration", "ar": "إعدادات الوكيل"},
    "settings.agent_name": {"en": "Agent Name", "ar": "اسم الوكيل"},
    "settings.language": {"en": "Language", "ar": "اللغة"},
    "settings.arabic": {"en": "Arabic", "ar": "العربية"},
    "settings.english": {"en": "English", "ar": "الإنجليزية"},
    "settings.system_prompt": {"en": "System Prompt", "ar": "الموجه النظامي"},
    "settings.save_agent": {"en": "Save Agent", "ar": "حفظ الوكيل"},
    "settings.personality_templates": {"en": "Personality Templates", "ar": "قوالب الشخصية"},
    "settings.safety_hitl": {"en": "Safety (HITL)", "ar": "الأمان (الموافقة البشرية)"},
    "settings.enable_hitl": {"en": "Enable Human-in-the-Loop", "ar": "تفعيل المشاركة البشرية"},
    "settings.tools_requiring_approval": {"en": "Tools Requiring Approval (comma-separated)", "ar": "الأدوات التي تتطلب موافقة (مفصولة بفواصل)"},
    "settings.approval_timeout_seconds": {"en": "Approval Timeout (seconds)", "ar": "مهلة الموافقة (ثانية)"},
    "settings.auto_deny_on_timeout": {"en": "Auto-deny on Timeout", "ar": "رفض تلقائي عند انتهاء المهلة"},
    "settings.save_safety": {"en": "Save Safety Settings", "ar": "حفظ إعدادات الأمان"},
    "settings.context_window": {"en": "Context Window", "ar": "نافذة السياق"},
    "settings.max_context_tokens": {"en": "Max Context Tokens", "ar": "أقصى رموز للسياق"},
    "settings.strategy": {"en": "Strategy", "ar": "الاستراتيجية"},
    "settings.sliding_window": {"en": "Sliding Window", "ar": "النافذة المنزلقة"},
    "settings.summarize_old": {"en": "Summarize Old Messages", "ar": "تلخيص الرسائل القديمة"},
    "settings.truncate_oldest": {"en": "Truncate Oldest", "ar": "اقتطاع الأقدم"},
    "settings.summarization_threshold": {"en": "Summarization Threshold:", "ar": "حد التلخيص:"},
    "settings.save_context": {"en": "Save Context Settings", "ar": "حفظ إعدادات السياق"},

    # Settings — Connectors tab
    "settings.telegram": {"en": "💬 Telegram", "ar": "💬 تيليجرام"},
    "settings.bot_token": {"en": "Bot Token", "ar": "رمز البوت"},
    "settings.allowed_user_ids": {"en": "Allowed User IDs (comma-separated)", "ar": "معرّفات المستخدمين المسموح لهم (مفصولة بفواصل)"},
    "settings.discord": {"en": "🎮 Discord", "ar": "🎮 ديسكورد"},
    "settings.slack": {"en": "📱 Slack", "ar": "📱 سلاك"},
    "settings.bot_token_xoxb": {"en": "Bot Token (xoxb-...)", "ar": "رمز البوت (xoxb-...)"},
    "settings.app_token_xapp": {"en": "App Token (xapp-...)", "ar": "رمز التطبيق (xapp-...)"},
    "settings.email": {"en": "📧 Email", "ar": "📧 البريد الإلكتروني"},
    "settings.smtp_host": {"en": "SMTP Host", "ar": "خادم SMTP"},
    "settings.smtp_port": {"en": "SMTP Port", "ar": "منفذ SMTP"},
    "settings.username": {"en": "Username", "ar": "اسم المستخدم"},
    "settings.password": {"en": "Password", "ar": "كلمة المرور"},
    "settings.imap_host": {"en": "IMAP Host", "ar": "خادم IMAP"},
    "settings.webhooks": {"en": "🔗 Webhooks", "ar": "🔗 الويب هوك"},
    "settings.incoming_webhook_url": {"en": "Incoming Webhook URL", "ar": "رابط الويب هوك الوارد"},
    "settings.outgoing_webhook_url": {"en": "Outgoing Webhook URL", "ar": "رابط الويب هوك الصادر"},
    "settings.webhook_secret": {"en": "Webhook Secret", "ar": "سر الويب هوك"},
    "settings.gateway_restart_required": {"en": "Gateway restart required after saving connector changes.", "ar": "مطلوب إعادة تشغيل البوابة بعد حفظ تغييرات الموصلات."},

    # Settings — MCP tab
    "settings.mcp_servers": {"en": "MCP Servers", "ar": "خوادم MCP"},
    "settings.add_server": {"en": "Add Server", "ar": "إضافة خادم"},
    "settings.transport": {"en": "Transport", "ar": "نوع النقل"},
    "settings.stdio_local": {"en": "stdio (local process)", "ar": "stdio (عملية محلية)"},
    "settings.sse_http": {"en": "SSE (HTTP)", "ar": "SSE (HTTP)"},
    "settings.command_space_separated": {"en": "Command (space-separated)", "ar": "الأمر (مفصول بمسافات)"},
    "settings.url": {"en": "URL", "ar": "العنوان"},
    "settings.env_vars_json": {"en": "Environment Variables (JSON)", "ar": "متغيرات البيئة (JSON)"},
    "settings.no_mcp_servers": {"en": "No MCP servers configured. Add one to extend Kazma's tool capabilities.", "ar": "لا توجد خوادم MCP مُعدة. أضف واحداً لتوسيع قدرات أدوات كاظمة."},
    "settings.tools_count": {"en": "tools", "ar": "أدوات"},

    # Settings — Skills tab
    "settings.installed_skills": {"en": "Installed Skills", "ar": "المهارات المثبتة"},
    "settings.search_skills": {"en": "Search skills…", "ar": "ابحث في المهارات…"},
    "settings.no_description": {"en": "No description", "ar": "لا يوجد وصف"},
    "settings.no_skills": {"en": "No skills installed. Browse the skill marketplace to add capabilities.", "ar": "لا توجد مهارات مثبتة. تصفح سوق المهارات لإضافة قدرات."},
    "settings.uninstall": {"en": "Uninstall", "ar": "إزالة"},
    "settings.by": {"en": "by", "ar": "بواسطة"},

    # Settings — Appearance tab
    "settings.theme": {"en": "Theme", "ar": "السمة"},
    "settings.dark": {"en": "🌙 Dark", "ar": "🌙 داكن"},
    "settings.light": {"en": "☀️ Light", "ar": "☀️ فاتح"},
    "settings.auto": {"en": "🔄 Auto", "ar": "🔄 تلقائي"},
    "settings.accent_color": {"en": "Accent Color", "ar": "لون التمييز"},
    "settings.font_size": {"en": "Font Size", "ar": "حجم الخط"},
    "settings.base_font_size": {"en": "Base Font Size:", "ar": "حجم الخط الأساسي:"},
    "settings.layout": {"en": "Layout", "ar": "التخطيط"},
    "settings.sidebar_position": {"en": "Sidebar Position", "ar": "موضع الشريط الجانبي"},
    "settings.left": {"en": "Left", "ar": "يسار"},
    "settings.right": {"en": "Right", "ar": "يمين"},
    "settings.custom_css": {"en": "Custom CSS", "ar": "CSS مخصص"},
    "settings.inject_custom_css": {"en": "Inject custom CSS (advanced)", "ar": "إدراج CSS مخصص (متقدم)"},
    "settings.save_appearance": {"en": "Save Appearance", "ar": "حفظ المظهر"},

    # Settings — Shortcuts tab
    "settings.keyboard_shortcuts": {"en": "Keyboard Shortcuts", "ar": "اختصارات لوحة المفاتيح"},
    "settings.reset_to_defaults": {"en": "Reset to Defaults", "ar": "إعادة التعيين للافتراضي"},
    "settings.conflicts_detected": {"en": "⚠️ Conflicts Detected", "ar": "⚠️ تم اكتشاف تعارضات"},

    # Settings — Account tab
    "settings.change_password": {"en": "Change Password", "ar": "تغيير كلمة المرور"},
    "settings.current_password": {"en": "Current Password", "ar": "كلمة المرور الحالية"},
    "settings.new_password": {"en": "New Password", "ar": "كلمة المرور الجديدة"},
    "settings.confirm_password": {"en": "Confirm Password", "ar": "تأكيد كلمة المرور"},
    "settings.min_8_chars": {"en": "Min 8 characters", "ar": "8 أحرف على الأقل"},
    "settings.api_tokens": {"en": "API Tokens", "ar": "رموز API"},
    "settings.token_name": {"en": "Token name", "ar": "اسم الرمز"},
    "settings.create_token": {"en": "Create Token", "ar": "إنشاء رمز"},
    "settings.revoke": {"en": "Revoke", "ar": "إلغاء"},
    "settings.no_api_tokens": {"en": "No API tokens", "ar": "لا توجد رموز API"},
    "settings.active_sessions": {"en": "Active Sessions", "ar": "الجلسات النشطة"},
    "settings.current": {"en": "Current", "ar": "الحالي"},
    "settings.no_active_sessions": {"en": "No active sessions", "ar": "لا توجد جلسات نشطة"},

    # Settings — Tools tab
    "settings.tool_registry": {"en": "Tool Registry", "ar": "سجل الأدوات"},
    "settings.search_tools": {"en": "Search tools by name, description, or category…", "ar": "ابحث في الأدوات بالاسم أو الوصف أو الفئة…"},
    "settings.no_tools": {"en": "No tools registered. Tools are added via MCP servers or local tool definitions.", "ar": "لا توجد أدوات مسجلة. تُضاف الأدوات عبر خوادم MCP أو تعريفات الأدوات المحلية."},
    "settings.test_arguments_json": {"en": "Test Arguments (JSON)", "ar": "وسائط الاختبار (JSON)"},
    "settings.parameters": {"en": "Parameters:", "ar": "المعاملات:"},

    # Settings — System tab
    "settings.system_diagnostics": {"en": "System Diagnostics", "ar": "تشخيص النظام"},
    "settings.python": {"en": "Python", "ar": "بايثون"},
    "settings.disk_free": {"en": "Disk Free", "ar": "مساحة فارغة"},
    "settings.system_logs": {"en": "System Logs", "ar": "سجلات النظام"},
    "settings.no_logs_available": {"en": "No logs available", "ar": "لا توجد سجلات متاحة"},
    "settings.lines": {"en": "lines", "ar": "سطور"},
    "settings.backup_maintenance": {"en": "Backup & Maintenance", "ar": "النسخ الاحتياطي والصيانة"},
    "settings.download_backup": {"en": "📦 Download Backup", "ar": "📦 تنزيل نسخة احتياطية"},
    "settings.check_updates": {"en": "🔄 Check for Updates", "ar": "🔄 التحقق من التحديثات"},
    "settings.system_reset": {"en": "⚠️ System Reset", "ar": "⚠️ إعادة تعيين النظام"},
    "settings.update_available": {"en": "Update available:", "ar": "يتوفر تحديث:"},
    "settings.latest_version": {"en": "(current:", "ar": "(الحالي:"},
    "settings.running_latest": {"en": "✓ Running the latest version", "ar": "✓ تعمل بأحدث إصدار"},

    # Settings — Import/Export tab
    "settings.export_config": {"en": "Export Configuration", "ar": "تصدير الإعدادات"},
    "settings.download_complete_config": {"en": "Download your complete Kazma configuration.", "ar": "نزّل إعدادات كاظمة الكاملة."},
    "settings.download_config": {"en": "Download Config", "ar": "تنزيل الإعدادات"},
    "settings.import_config": {"en": "Import Configuration", "ar": "استيراد الإعدادات"},
    "settings.upload_or_paste": {"en": "Upload or paste a configuration file to import settings.", "ar": "ارفع أو الصق ملف إعدادات لاستيراد البيانات."},
    "settings.format": {"en": "Format", "ar": "الصيغة"},
    "settings.config_data": {"en": "Configuration Data", "ar": "بيانات الإعدادات"},
    "settings.paste_yaml_json": {"en": "Paste YAML or JSON configuration here…", "ar": "الصق إعدادات YAML أو JSON هنا…"},
    "settings.upload_file": {"en": "📁 Upload File", "ar": "📁 رفع ملف"},
    "settings.selective_import": {"en": "Selective Import", "ar": "استيراد انتقائي"},
    "settings.select_sections": {"en": "Select sections to import", "ar": "اختر الأقسام للاستيراد"},
    "settings.importing": {"en": "Importing…", "ar": "جاري الاستيراد…"},
    "settings.import_configuration": {"en": "Import Configuration", "ar": "استيراد الإعدادات"},
    "settings.reset_to_defaults_section": {"en": "Reset to Defaults", "ar": "إعادة التعيين للافتراضي"},
    "settings.reset_description": {"en": "Clear all saved settings and revert to factory defaults. This cannot be undone.", "ar": "امسح جميع الإعدادات المحفوظة وارجع للإعدادات الافتراضية. لا يمكن التراجع عن هذا الإجراء."},
    "settings.reset_all_settings": {"en": "Reset All Settings", "ar": "إعادة تعيين كل الإعدادات"},

    # ── Language toggle ───────────────────────────────────────────────
    "lang.toggle_to_english": {"en": "EN", "ar": "EN"},
    "lang.toggle_to_arabic": {"en": "ع", "ar": "ع"},

    # ── Swarm ─────────────────────────────────────────────────────────
    "swarm.title": {"en": "Swarm Management", "ar": "إدارة السرب"},
    "swarm.workers": {"en": "Workers", "ar": "العمال"},
    "swarm.status": {"en": "Status", "ar": "الحالة"},
    "swarm.running": {"en": "Running", "ar": "قيد التشغيل"},
    "swarm.stopped": {"en": "Stopped", "ar": "متوقف"},
    "swarm.start_all": {"en": "Start All", "ar": "تشغيل الكل"},
    "swarm.stop_all": {"en": "Stop All", "ar": "إيقاف الكل"},
    "swarm.busy": {"en": "busy", "ar": "نشط"},

    # ── Agents ────────────────────────────────────────────────────────
    "agents.title": {"en": "Agents", "ar": "الوكلاء"},
    "agents.status": {"en": "Status", "ar": "الحالة"},
    "agents.model": {"en": "Model", "ar": "النموذج"},
    "agents.sessions": {"en": "Sessions", "ar": "الجلسات"},

    # ── Skills ────────────────────────────────────────────────────────
    "skills.title": {"en": "Skills", "ar": "المهارات"},

    # ── MCP ───────────────────────────────────────────────────────────
    "mcp.title": {"en": "MCP Servers", "ar": "خوادم MCP"},

    # ── Common / Generic ──────────────────────────────────────────────
    "common.loading": {"en": "Loading…", "ar": "جاري التحميل…"},
    "common.error": {"en": "Error", "ar": "خطأ"},
    "common.close": {"en": "Close", "ar": "إغلاق"},
    "common.delete": {"en": "Delete", "ar": "حذف"},
    "common.confirm": {"en": "Confirm", "ar": "تأكيد"},
    "common.yes": {"en": "Yes", "ar": "نعم"},
    "common.no": {"en": "No", "ar": "لا"},
    "common.search": {"en": "Search", "ar": "بحث"},
    "common.actions": {"en": "Actions", "ar": "إجراءات"},
    "common.name": {"en": "Name", "ar": "الاسم"},
    "common.type": {"en": "Type", "ar": "النوع"},
    "common.enabled": {"en": "Enabled", "ar": "مفعّل"},
    "common.disabled": {"en": "Disabled", "ar": "معطّل"},
}


def t(key: str, lang: str = "en", **kwargs: Any) -> str:
    """Translate *key* into *lang*.

    Falls back to English if the language is missing, and to the key itself
    if the key is entirely unknown (so templates never break).

    Optional ``kwargs`` provide ``str.format`` interpolation::

        t('common.welcome', lang='ar', name='أحمد')
    """
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get("en") or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass  # leave unfilled placeholders intact rather than crashing
    return text


def make_translator(lang: str = "en"):
    """Return a closure bound to *lang* for use as a Jinja2 global."""

    def _t(key: str, **kwargs: Any) -> str:
        return t(key, lang=lang, **kwargs)

    return _t


# Supported language codes (for validation / UI toggles)
SUPPORTED_LANGUAGES = sorted({lang for entry in TRANSLATIONS.values() for lang in entry})


# ---------------------------------------------------------------------------
# Jinja2Templates — always-available i18n globals
# ---------------------------------------------------------------------------
#
# ``create_app()`` (production) sets the language-specific translator on the
# Jinja2 env.  However, tests and lightweight code paths sometimes construct
# ``Jinja2Templates`` directly without going through ``create_app()``, which
# previously caused ``UndefinedError: 't' is undefined`` whenever a template
# used ``{{ t('...') }}``.
#
# To guarantee the ``t`` global is *always* present (defaulting to English),
# we wrap ``Jinja2Templates.__init__`` so every instance starts with sensible
# defaults.  Production code can still override these globals afterwards.
# ---------------------------------------------------------------------------

def _patch_jinja2_templates() -> None:
    """Patch ``Jinja2Templates.__init__`` to inject default i18n globals."""
    try:
        from fastapi.templating import Jinja2Templates as _Templates
    except Exception as exc:  # pragma: no cover — FastAPI always installed
        logging.getLogger(__name__).debug("Cannot patch Jinja2Templates: %s", exc)
        return

    # Guard against double-patching
    if getattr(_Templates.__init__, "_kazma_i18n_patched", False):
        return

    _original_init = _Templates.__init__

    def _patched_init(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = _original_init(self, *args, **kwargs)
        # Inject default i18n globals (English fallback).
        # ``create_app()`` may override these with the configured language.
        try:
            env = self.env
            env.globals.setdefault("t", make_translator("en"))
            env.globals.setdefault("lang", "en")
            env.globals.setdefault("dir", "ltr")
        except Exception:
            pass
        return result

    _patched_init._kazma_i18n_patched = True  # type: ignore[attr-defined]
    _Templates.__init__ = _patched_init  # type: ignore[method-assign]


_patch_jinja2_templates()
