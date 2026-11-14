"""The problem report: the ``?`` beside the bell and "Report this"."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "support.report_title": {"en": "Report a problem", "ar": "الإبلاغ عن مشكلة"},
    "support.report_intro": {
        "en": "Tell us what happened. Kazma adds what helps us understand it and nothing private: no chats, "
              "memories, documents, settings or keys, and home folders, emails and IP addresses are taken out. "
              "You see all of it before it is sent.",
        "ar": "أخبرنا بما حدث. يضيف Kazma ما يساعدنا على فهمه ولا شيء خاصًّا: لا محادثات ولا ذكريات ولا مستندات "
              "ولا إعدادات ولا مفاتيح، وتُحذف مجلدات المستخدم والبريد الإلكتروني وعناوين IP. "
              "ترى كل ذلك قبل الإرسال.",
    },
    "support.note_label": {"en": "What happened?", "ar": "ماذا حدث؟"},
    "support.note_placeholder": {
        "en": "What you did, what you expected, what happened instead.",
        "ar": "ما الذي فعلته، وما الذي توقعته، وما الذي حدث بدلًا منه.",
    },
    "support.show": {"en": "Show what will be sent", "ar": "اعرض ما سيُرسل"},
    "support.preparing": {"en": "Preparing the report…", "ar": "جارٍ إعداد التقرير…"},
    "support.preview_label": {"en": "This is the whole report", "ar": "هذا هو التقرير كاملًا"},
    "support.send": {"en": "Send", "ar": "إرسال"},
    "support.sending": {"en": "Sending…", "ar": "جارٍ الإرسال…"},
    "support.sent": {
        "en": "Sent. Your report's number is {id}: mention it if you write to us.",
        "ar": "أُرسل. رقم تقريرك {id}: اذكره إن راسلتنا.",
    },
    "support.saved": {
        "en": "Not sent ({why}). It is saved at {path}: you can attach it to a GitHub issue.",
        "ar": "لم يُرسل ({why}). حُفظ في {path}: يمكنك إرفاقه في بلاغ على GitHub.",
    },
    "support.failed": {
        "en": "The report could not be made. Try again, or run kazma report in a terminal.",
        "ar": "تعذّر إعداد التقرير. حاول مرة أخرى، أو شغّل kazma report في الطرفية.",
    },
    "support.report_this": {"en": "Report this", "ar": "أبلغ عن هذا"},
    "support.report_footer": {"en": "Something wrong? Report it", "ar": "هناك خطأ؟ أبلغ عنه"},
    "settings.report_url_title": {"en": "Problem reports", "ar": "بلاغات المشكلات"},
    "settings.report_url_label": {"en": "Where problem reports go", "ar": "وجهة بلاغات المشكلات"},
    "settings.report_url_hint": {
        "en": "Kazma's own private inbox by default. Empty: reports are only saved on this computer "
              "(in the data folder's problem_reports), for you to attach to a GitHub issue.",
        "ar": "صندوق Kazma الخاص افتراضيًا. إذا تُرك فارغًا: تُحفظ البلاغات على هذا الجهاز فقط "
              "(في المجلد problem_reports داخل مجلد البيانات)، لترفقها في بلاغ على GitHub.",
    },
    "settings.report_url_saved": {"en": "Saved: where problem reports go", "ar": "حُفظت وجهة بلاغات المشكلات"},
}
