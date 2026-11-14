"""Bring your history: the Memory page's card for a ChatGPT or Claude export."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "memory.import.title": {"en": "Bring your history", "ar": "أحضر سجلّك"},
    "memory.import.intro": {
        "en": "Import your ChatGPT or Claude conversations into Kazma's memory, so it knows what you talked "
              "about before. Each turn keeps its own date, and Forget forgets it like any memory.",
        "ar": "استورد محادثاتك في ChatGPT أو Claude إلى ذاكرة Kazma، ليعرف ما تحدثت عنه من قبل. "
              "يحتفظ كل دور بتاريخه، ويُنسى بالنسيان كأي ذكرى.",
    },
    "memory.import.file": {"en": "The export", "ar": "ملف التصدير"},
    "memory.import.file_hint": {
        "en": "ChatGPT: Settings → Data controls → Export data. Claude: Settings → Privacy → Export data. "
              "Choose the .zip you were sent, or the conversations.json inside it (smaller, if the zip is large).",
        "ar": "ChatGPT: الإعدادات ← عناصر التحكم في البيانات ← تصدير البيانات. Claude: الإعدادات ← الخصوصية ← تصدير البيانات. "
              "اختر ملف ‎.zip الذي وصلك، أو conversations.json الذي بداخله (أصغر، إن كان الملف كبيرًا).",
    },
    "memory.import.read": {"en": "Read it", "ar": "اقرأه"},
    "memory.import.reading": {"en": "Reading…", "ar": "جارٍ القراءة…"},
    "memory.import.start": {"en": "Import", "ar": "استيراد"},
    "memory.import.stop": {"en": "Stop", "ar": "إيقاف"},
    "memory.import.holds": {
        "en": "A {name} export: {chats}, {turns}, from {first} to {last}.",
        "ar": "تصدير من {name}: {chats}، {turns}، من {first} إلى {last}.",
    },
    "memory.import.chats_n.zero": {"en": "no conversations", "ar": "لا محادثات"},
    "memory.import.chats_n.one": {"en": "1 conversation", "ar": "محادثة واحدة"},
    "memory.import.chats_n.two": {"en": "2 conversations", "ar": "محادثتان"},
    "memory.import.chats_n.few": {"en": "{n} conversations", "ar": "{n} محادثات"},
    "memory.import.chats_n.many": {"en": "{n} conversations", "ar": "{n} محادثة"},
    "memory.import.chats_n.other": {"en": "{n} conversations", "ar": "{n} محادثة"},
    "memory.import.turns_n.zero": {"en": "no turns", "ar": "لا أدوار"},
    "memory.import.turns_n.one": {"en": "1 turn", "ar": "دور واحد"},
    "memory.import.turns_n.two": {"en": "2 turns", "ar": "دوران"},
    "memory.import.turns_n.few": {"en": "{n} turns", "ar": "{n} أدوار"},
    "memory.import.turns_n.many": {"en": "{n} turns", "ar": "{n} دورًا"},
    "memory.import.turns_n.other": {"en": "{n} turns", "ar": "{n} دور"},
    "memory.import.progress": {"en": "Turns: {done} / {total}", "ar": "الأدوار: {done} / {total}"},
    "memory.import.done": {
        "en": "Imported. Remembered: {written} · Already there: {already} · Kept out: {kept} · Failed: {failed}. "
              "Weekly summaries of those weeks are written over the next hours.",
        "ar": "اكتمل الاستيراد. حُفظ: {written} · موجود من قبل: {already} · مستبعد: {kept} · تعذّر: {failed}. "
              "تُكتب ملخصات تلك الأسابيع خلال الساعات القادمة.",
    },
    "memory.import.stopped": {
        "en": "Stopped. Remembered: {written} · Already there: {already} · Kept out: {kept} · Failed: {failed}. "
              "Import it again to go on: what is there is skipped.",
        "ar": "توقف. حُفظ: {written} · موجود من قبل: {already} · مستبعد: {kept} · تعذّر: {failed}. "
              "استورده مرة أخرى لتكمل: ما هو موجود يُتخطى.",
    },
    "memory.import.not_read": {"en": "The file could not be read: {why}", "ar": "تعذّرت قراءة الملف: {why}"},
    "memory.import.not_started": {"en": "The import did not start: {why}", "ar": "لم يبدأ الاستيراد: {why}"},
    "memory.import.failed": {"en": "Kazma did not answer. Try again.", "ar": "لم يُجب Kazma. حاول مرة أخرى."},
    "memory.import.job_failed": {
        "en": "The import stopped with an error ({why}).",
        "ar": "توقف الاستيراد بسبب خطأ ({why}).",
    },
}
