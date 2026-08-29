"""``agents`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "agents.acting": {
        "ar": "ينفذ...",
        "en": "Acting...",
    },
    "agents.active_model": {
        "ar": "النموذج النشط",
        "en": "Active model",
    },
    "agents.agent_settings_link": {
        "ar": "الوكيل",
        "en": "Agent",
    },
    "agents.available": {
        "ar": "متاح",
        "en": "available",
    },
    "agents.base_url": {
        "ar": "عنوان الخدمة",
        "en": "Base URL",
    },
    "agents.calls": {
        "ar": "استدعاء",
        "en": "calls",
    },
    "agents.config_title": {
        "ar": "نظرة على التشغيل",
        "en": "Runtime Overview",
    },
    "agents.edit_in_settings": {
        "ar": "تعديل في الإعدادات",
        "en": "Edit in Settings",
    },
    "agents.executions": {
        "ar": "تنفيذ",
        "en": "executions",
    },
    "agents.executions_hint": {
        "ar": "تظهر استدعاءات الأدوات هنا عند تشغيل الوكيل",
        "en": "Tool calls appear here when the agent runs",
    },
    "agents.idle": {
        "ar": "خامل",
        "en": "idle",
    },
    "agents.inferences": {
        "ar": "استدلال",
        "en": "inferences",
    },
    "agents.language": {
        "ar": "اللغة",
        "en": "Language",
    },
    "agents.last_activity_label": {
        "ar": "آخر نشاط",
        "en": "Last activity",
    },
    "agents.llm_calls": {
        "ar": "استدعاءات النموذج",
        "en": "LLM Calls",
    },
    "agents.max_tokens": {
        "ar": "الحد الأقصى للرموز",
        "en": "Max Tokens",
    },
    "agents.mcp_servers": {
        "ar": "خوادم MCP",
        "en": "MCP servers",
    },
    "agents.model": {
        "ar": "النموذج",
        "en": "Model",
    },
    "agents.name": {
        "ar": "الاسم",
        "en": "Name",
    },
    "agents.no_executions": {
        "ar": "لا توجد عمليات تنفيذ بعد",
        "en": "No tool executions yet",
    },
    "agents.no_tools": {
        "ar": "لا توجد أدوات مسجّلة",
        "en": "No tools registered",
    },
    "agents.no_traces": {
        "ar": "لا توجد آثار تفكير بعد",
        "en": "No reasoning traces yet",
    },
    "agents.providers_link": {
        "ar": "المزوّدون",
        "en": "Providers",
    },
    "agents.ready": {
        "ar": "جاهز — بانتظار الرسائل",
        "en": "Ready — waiting for messages",
    },
    "agents.reasoning_steps": {
        "ar": "خطوات التفكير",
        "en": "Reasoning Steps",
    },
    "agents.refresh": {
        "ar": "تحديث الآن",
        "en": "Refresh now",
    },
    "agents.registered_tools": {
        "ar": "الأدوات المسجّلة",
        "en": "Registered Tools",
    },
    "agents.running_badge": {
        "ar": "يعمل",
        "en": "running",
    },
    "agents.runtime_hint": {
        "ar": "هذه الصفحة تراقب نشاط الوكيل. غيّر النموذج والمزوّد والشخصية من الإعدادات:",
        "en": "This page monitors live agent activity. Change model, provider, and personality in Settings:",
    },
    "agents.runtime_title": {
        "ar": "نظرة على التشغيل",
        "en": "Runtime Overview",
    },
    "agents.sessions": {
        "ar": "الجلسات",
        "en": "Sessions",
    },
    "agents.start": {
        "ar": "تشغيل",
        "en": "Start",
    },
    "agents.state_hint": {
        "ar": "خامل / يفكر / ينفذ",
        "en": "idle / thinking / acting",
    },
    "agents.state_label": {
        "ar": "الحالة",
        "en": "State",
    },
    "agents.status": {
        "ar": "الحالة",
        "en": "Status",
    },
    "agents.steps": {
        "ar": "خطوة",
        "en": "steps",
    },
    "agents.stop": {
        "ar": "إيقاف",
        "en": "Stop",
    },
    "agents.stopped": {
        "ar": "متوقف",
        "en": "Stopped",
    },
    "agents.temperature": {
        "ar": "درجة الحرارة",
        "en": "Temperature",
    },
    "agents.thinking": {
        "ar": "يفكر...",
        "en": "Thinking...",
    },
    "agents.title": {
        "ar": "الوكلاء",
        "en": "Agents",
    },
    "agents.tokens": {
        "ar": "رمز",
        "en": "tokens",
    },
    "agents.tool_calls": {
        "ar": "استدعاءات الأدوات",
        "en": "Tool Calls",
    },
    "agents.tool_executions": {
        "ar": "سجل تنفيذ الأدوات",
        "en": "Tool Execution History",
    },
    "agents.tools_registered": {
        "ar": "الأدوات",
        "en": "Tools",
    },
    "agents.total_cost": {
        "ar": "التكلفة الإجمالية",
        "en": "Total cost",
    },
    "agents.total_tokens": {
        "ar": "إجمالي الرموز",
        "en": "Total tokens",
    },
    "agents.trace_entries": {
        "ar": "مدخلات التتبع",
        "en": "trace entries",
    },
    "agents.traces_hint": {
        "ar": "تظهر خطوات تفكير النموذج من LangGraph هنا",
        "en": "LLM reasoning steps from LangGraph appear here",
    },
    "agents.version": {
        "ar": "الإصدار",
        "en": "Version",
    },
    "agents.waiting": {
        "ar": "بانتظار الرسائل",
        "en": "waiting for messages",
    },
}
