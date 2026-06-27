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

    # ── Settings ──────────────────────────────────────────────────────
    "settings.title": {"en": "Settings", "ar": "الإعدادات"},
    "settings.save": {"en": "Save", "ar": "حفظ"},
    "settings.saved": {"en": "Saved", "ar": "تم الحفظ"},
    "settings.cancel": {"en": "Cancel", "ar": "إلغاء"},

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
