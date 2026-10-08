"""Web command discovery; unsupported controls never become model work."""

from __future__ import annotations

from kazma_core.agent.command_catalog import BY_NAME, command_name, web_commands
from kazma_core.agent.slash_turns import is_control_slash


def discovery_reply(text: str, lang: str = "en") -> str | None:
    """Return help/navigation feedback, or None for an implemented turn control."""
    name = command_name(text)
    if name is None:
        return None
    entry = BY_NAME.get(name.rstrip("!"))
    ar = lang == "ar"
    if entry is None or not entry.web:
        return (
            "هذا الأمر غير متاح في محادثة الويب. استخدم /help لعرض الأوامر المتاحة."
            if ar
            else "This command is not available in web chat. Use /help for supported commands."
        )
    if name == "help":
        lines = ["الأوامر المتاحة:" if ar else "Available web commands:"]
        for command in web_commands():
            description = command["description_ar"] if ar else command["description"]
            lines.append(f"• `/{command['name']}` — {description}")
        return "\n".join(lines)
    if name in ("new", "steer", "steer!", "abort"):
        return (
            "استخدم هذا الأمر من مربع كتابة المحادثة لتطبيقه على المحادثة أو المهمة الجارية."
            if ar else "Use this command in the chat composer to apply it to the current chat or running task."
        )
    if name == "swarm" and is_control_slash(text):
        return (
            "[إدارة السرب](/swarm). لبدء مهمة استخدم `/swarm <task>`."
            if ar else "[Manage the swarm](/swarm). To start work use `/swarm <task>`."
        )
    if entry.page:
        description = entry.description_ar if ar else entry.description
        note = (
            "استخدم الصفحة لإدارة هذه الميزة؛ أوامرها الفرعية متاحة عبر منصات المحادثة."
            if ar
            else "Manage this feature on its page; subcommands are available in the chat adapters."
        )
        return f"[{description}]({entry.page})\n\n{note}"
    return None
