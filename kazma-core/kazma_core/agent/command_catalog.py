"""Transport capabilities and discovery for slash commands.

Registration and suggestions share this catalog. Web page commands link to
their UI; they never pretend that a gateway-only mutation ran on the web.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    description_ar: str
    web: bool = False
    page: str | None = None
    arguments: str = ""


COMMANDS: tuple[Command, ...] = (
    Command("help", "Show available commands", "عرض الأوامر المتاحة", web=True, page=None, arguments=""),
    Command(
        "sessions",
        "List every season (Web + Telegram + Discord + Slack)",
        "عرض جميع المواسم في منصات المحادثة",
        web=False,
        page=None,
        arguments="",
    ),
    Command(
        "seasons",
        "List every season (alias of /sessions)",
        "عرض المواسم (مرادف sessions)",
        web=False,
        page=None,
        arguments="",
    ),
    Command(
        "session",
        "Switch onto a season (#, id, or name)",
        "الانتقال إلى موسم",
        web=False,
        page=None,
        arguments="<id or name>",
    ),
    Command(
        "season",
        "Switch onto a season (alias of /session)",
        "الانتقال إلى موسم (مرادف session)",
        web=False,
        page=None,
        arguments="",
    ),
    Command("switch", "Take over a season (same as /session)", "استئناف موسم", web=False, page=None, arguments=""),
    Command("new", "Create a brand new session/season", "بدء جلسة جديدة", web=True, page=None, arguments=""),
    Command("reset", "Clear conversation history", "مسح سجل المحادثة", web=True, page=None, arguments=""),
    Command("compact", "Manually trigger context compaction", "تلخيص سياق المحادثة", web=True, page=None, arguments=""),
    Command(
        "research",
        "Deep research via the same agent",
        "بحث متعمق عبر الوكيل",
        web=True,
        page=None,
        arguments="deep <topic>",
    ),
    Command("swarm", "Swarm orchestration", "تنسيق مهام السرب", web=True, page=None, arguments="<task>"),
    Command(
        "ide",
        "IDE: files, git, coding skills",
        "ملفات مساحة العمل وGit ومهارات البرمجة",
        web=True,
        page="/ide",
        arguments="",
    ),
    Command(
        "skill",
        "Agent Skills: list / install / activate (agentskills.io)",
        "إدارة مهارات الوكيل",
        web=False,
        page=None,
        arguments="",
    ),
    Command(
        "documents",
        "Document Intelligence: list / read / search",
        "عرض المستندات وقراءتها والبحث فيها",
        web=True,
        page="/documents",
        arguments="",
    ),
    Command(
        "docs",
        "Documents (alias of /documents)",
        "المستندات (مرادف documents)",
        web=True,
        page="/documents",
        arguments="",
    ),
    Command(
        "kb",
        "Knowledge library: list / crawl / search",
        "مكتبات المعرفة والبحث",
        web=True,
        page="/knowledge",
        arguments="",
    ),
    Command(
        "long", "Long-task mode on/off (deep audits)", "تفعيل ميزانية المهام الطويلة", web=True, page=None, arguments=""
    ),
    Command(
        "mission",
        "Mission-length budget for this chat",
        "ميزانية المهمة لهذه المحادثة",
        web=True,
        page=None,
        arguments="",
    ),
    Command(
        "yolo",
        "Toggle session YOLO safety bypass",
        "تبديل تجاوز الموافقات لهذه الجلسة",
        web=True,
        page=None,
        arguments="",
    ),
    Command(
        "unrestricted",
        "Mission budget + YOLO for this chat",
        "ميزانية المهمة مع تجاوز الموافقات",
        web=True,
        page=None,
        arguments="",
    ),
    Command(
        "plan", "Plan mode: inspect and propose before acting", "التخطيط قبل التنفيذ", web=True, page=None, arguments=""
    ),
    Command(
        "steer",
        "Add context to the running task",
        "إضافة توجيه للمهمة الجارية",
        web=True,
        page=None,
        arguments="<text>",
    ),
    Command("abort", "Stop and abandon the running task", "إيقاف المهمة الجارية", web=True, page=None, arguments=""),
    Command("replay", "Time travel snapshots", "لقطات خطوات المحادثة", web=True, page=None, arguments=""),
    Command(
        "fork",
        "Fork from a snapshot into a new thread",
        "إنشاء محادثة من لقطة",
        web=True,
        page=None,
        arguments="<iteration>",
    ),
    Command("undo", "Undo last response", "التراجع عن آخر رد", web=False, page=None, arguments=""),
    Command("edit", "Edit last response", "تعديل آخر رد", web=False, page=None, arguments=""),
    Command("config", "Configuration wizard", "إعدادات النظام", web=False, page=None, arguments=""),
    Command("personality", "Agent personality", "شخصية الوكيل", web=False, page=None, arguments=""),
    Command("model", "Show / switch active model", "عرض النموذج وتبديله", web=True, page="/settings", arguments=""),
    Command(
        "models",
        "Show / switch active model (alias of /model)",
        "النموذج (مرادف model)",
        web=False,
        page=None,
        arguments="",
    ),
    Command("context", "Context window usage", "استخدام نافذة السياق", web=False, page=None, arguments=""),
    Command("status", "Gateway health overview", "حالة بوابة المحادثة", web=False, page=None, arguments=""),
    Command("memory", "Report memory usage", "استخدام الذاكرة", web=False, page=None, arguments=""),
    Command("cost", "Tokens and cost of this chat", "رموز وتكلفة هذه المحادثة", web=False, page=None, arguments=""),
    Command(
        "hitl",
        "Approve or deny a pending HITL tool",
        "الموافقة على أداة معلقة أو رفضها",
        web=False,
        page=None,
        arguments="",
    ),
    Command(
        "x",
        "X Studio: drafts, accounts and scheduled posts (/x help)",
        "استوديو X: المسودات والحسابات والمنشورات المجدولة",
        web=True,
        page="/x",
        arguments="",
    ),
)
BY_NAME = {command.name: command for command in COMMANDS}
INTERNAL_COMMANDS = frozenset({"_models_provider", "_models_select"})


def command_name(text: str) -> str | None:
    token = (text or "").split(maxsplit=1)
    if not token or not re.fullmatch(r"/[A-Za-z_][A-Za-z0-9_]*!?", token[0]):
        return None
    return token[0][1:].lower()


def normalize_telegram_command(text: str, bot_username: str) -> str | None:
    """Remove an address only when it names this bot; keep arguments exact.

    None means this message addresses another bot, or our identity is unknown.
    Bare commands work even before getMe has supplied the bot identity.
    """
    match = re.match(r"^(\s*/[A-Za-z0-9_!]+)@([^\s]+)(.*)$", text or "", re.S)
    if not match:
        return text
    if not bot_username or match[2].casefold() != bot_username.lstrip("@").casefold():
        return None
    return match[1] + match[3]


def native_command_text(name: str, arguments: str) -> str:
    """The native /kazma entry point shares ordinary slash dispatch."""
    if name == "kazma":
        text = arguments.strip()
        return ("/" + text.lstrip("/")) if text else "/help"
    return "/" + name + (" " + arguments if arguments else "")


def menu_commands(lang: str = "en") -> list[dict[str, str]]:
    return [{"command": c.name, "description": c.description_ar if lang == "ar" else c.description} for c in COMMANDS]


def web_commands() -> list[dict[str, object]]:
    return [
        {
            "name": c.name,
            "description": c.description,
            "description_ar": c.description_ar,
            "arguments": c.arguments,
            "page": c.page,
            "admin_options": c.name in {"model", "ide", "swarm", "yolo", "long", "mission", "unrestricted"},
        }
        for c in COMMANDS
        if c.web
    ]
