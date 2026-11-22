"""My account → Chat apps (Spaces stage 3): a person's own Telegram,
Discord, Slack and WhatsApp accounts, linked to their Kazma account."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "chat_links.title": {"en": "Chat apps", "ar": "تطبيقات الدردشة"},
    "chat_links.intro": {
        "en": "Link your own Telegram, Discord, Slack or WhatsApp account to your Kazma account. "
              "What you send the bot from a linked account runs as you, in your space; nobody "
              "else's account can.",
        "ar": "اربط حسابك أنت في Telegram أو Discord أو Slack أو WhatsApp بحسابك في Kazma. "
              "ما ترسله إلى البوت من حساب مربوط يُنفَّذ باسمك، في مساحتك؛ ولا يقدر على ذلك حساب غيرك.",
    },
    "chat_links.load_failed": {
        "en": "Your chat apps could not be read. Try again.",
        "ar": "تعذّرت قراءة تطبيقات الدردشة الخاصة بك. حاول مرة أخرى.",
    },
    "chat_links.col_app": {"en": "Chat app", "ar": "التطبيق"},
    "chat_links.col_account": {"en": "Your account there", "ar": "حسابك فيه"},
    "chat_links.col_linked": {"en": "Linked", "ar": "رُبط في"},
    "chat_links.none": {
        "en": "No chat app is linked to your account yet.",
        "ar": "لا يوجد تطبيق دردشة مربوط بحسابك بعد.",
    },
    "chat_links.no_platforms": {
        "en": "This Kazma runs no chat-app bot yet. The admin sets one up in Settings → Adapters & Routes.",
        "ar": "لا يشغّل Kazma هذا أي بوت دردشة بعد. يُعدّه المشرف من الإعدادات ← المحوّلات والمسارات.",
    },
    "chat_links.link": {"en": "Link {platform}", "ar": "اربط {platform}"},
    "chat_links.send_hint": {
        "en": "Send this to the {platform} bot in a direct message:",
        "ar": "أرسل هذا إلى بوت {platform} في رسالة مباشرة:",
    },
    "chat_links.code_hint": {
        "en": "It works once. Kazma keeps only a fingerprint of it.",
        "ar": "يعمل مرة واحدة. لا يحفظ Kazma منه إلا بصمته.",
    },
    "chat_links.expires_in": {"en": "Works for {time} more", "ar": "يعمل لمدة {time} أخرى"},
    "chat_links.waiting": {
        "en": "Waiting for your message to the bot…",
        "ar": "بانتظار رسالتك إلى البوت…",
    },
    "chat_links.expired": {
        "en": "That code has run out. Get a new one.",
        "ar": "انتهت مدة ذلك الرمز. احصل على رمز جديد.",
    },
    "chat_links.done": {
        "en": "Linked: your {platform} account {id}.",
        "ar": "تمّ الربط: حسابك في {platform} {id}.",
    },
    "chat_links.copy": {"en": "Copy", "ar": "نسخ"},
    "chat_links.copied": {"en": "Copied", "ar": "نُسخ"},
    "chat_links.copy_failed": {
        "en": "Could not copy. Select the text instead.",
        "ar": "تعذّر النسخ. حدّد النص بدلًا من ذلك.",
    },
    "chat_links.open_bot": {"en": "Open the bot", "ar": "افتح البوت"},
    "chat_links.err_spaces_off": {
        "en": "Chat apps are linked once Spaces is on for this install.",
        "ar": "تُربط تطبيقات الدردشة بعد تشغيل المساحات في هذا التثبيت.",
    },
    "chat_links.err_unavailable": {
        "en": "Accounts cannot be read right now. Try again.",
        "ar": "تعذّرت قراءة الحسابات الآن. حاول مرة أخرى.",
    },
    "chat_links.allowlist_retired": {
        "en": "Spaces is on: this bot answers the people who linked their own account here "
              "(each in My account → Chat apps). Allowed IDs are no longer read.",
        "ar": "المساحات مفعّلة: يرد هذا البوت على من ربط حسابه هنا (كلٌّ من حسابي ← تطبيقات "
              "الدردشة). لم تعد المعرّفات المسموح لها تُقرأ.",
    },
    "chat_links.people_links": {"en": "Chat apps:", "ar": "تطبيقات الدردشة:"},
    "chat_links.people_no_links": {"en": "none linked", "ar": "لا شيء مربوط"},
    "chat_links.err_code": {
        "en": "Could not get a code. Try again.",
        "ar": "تعذّر الحصول على رمز. حاول مرة أخرى.",
    },
}
