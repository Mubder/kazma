"""Ready-made automations: the Scheduled page's gallery (kazma_core/cron/recipes.py)."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "scheduled.recipes_title": {"en": "Ready-made automations", "ar": "أتمتة جاهزة"},
    "scheduled.recipes_hint": {
        "en": "Set one up in a click. Each one only reads (it never sends, changes or deletes anything) "
              "and answers you where your reminders go. Change the time, or cancel it, like any scheduled task.",
        "ar": "جهّز أيًّا منها بنقرة. كلٌّ منها يقرأ فقط (لا يرسل ولا يغيّر ولا يحذف شيئًا) ويجيبك حيث تصلك "
              "تذكيراتك. غيّر الوقت أو ألغِها كأي مهمة مجدولة.",
    },
    "scheduled.recipe.morning_briefing.title": {"en": "Morning briefing", "ar": "موجز الصباح"},
    "scheduled.recipe.morning_briefing.what": {
        "en": "Today's calendar, the important unread emails and today's reminders, in one short message.",
        "ar": "تقويم اليوم، والرسائل المهمة غير المقروءة، وتذكيرات اليوم، في رسالة قصيرة واحدة.",
    },
    "scheduled.recipe.inbox_triage.title": {"en": "Inbox triage", "ar": "فرز البريد"},
    "scheduled.recipe.inbox_triage.what": {
        "en": "Yesterday's unread email sorted into needs a reply, for information and can wait, "
              "with the one to answer first.",
        "ar": "بريد الأمس غير المقروء مرتبًا: يحتاج ردًّا، وللاطلاع، ويمكن أن ينتظر، مع الرسالة التي تُجاب أولًا.",
    },
    "scheduled.recipe.weekly_report.title": {"en": "Weekly report", "ar": "التقرير الأسبوعي"},
    "scheduled.recipe.weekly_report.what": {
        "en": "Your week in review: what you worked on, what got done, what is still open, what is next.",
        "ar": "مراجعة أسبوعك: ما عملت عليه، وما أُنجز، وما لا يزال مفتوحًا، وما هو قادم.",
    },
    "scheduled.recipe.news_digest.title": {"en": "News digest", "ar": "موجز الأخبار"},
    "scheduled.recipe.news_digest.what": {
        "en": "The most important news on a topic you choose, with the sources.",
        "ar": "أهم الأخبار عن موضوع تختاره، مع مصادرها.",
    },
    "scheduled.recipe_every_day": {"en": "Every day at", "ar": "كل يوم الساعة"},
    "scheduled.recipe_weekdays": {"en": "Weekdays at", "ar": "أيام العمل الساعة"},
    "scheduled.recipe_fridays": {"en": "Fridays at", "ar": "كل جمعة الساعة"},
    "scheduled.recipe_time": {"en": "Time", "ar": "الوقت"},
    "scheduled.recipe_topic": {"en": "Topic, for example: electric cars", "ar": "الموضوع، مثلًا: السيارات الكهربائية"},
    "scheduled.recipe_book": {"en": "Set it up", "ar": "جهّزها"},
    "scheduled.recipe_booking": {"en": "Setting up…", "ar": "جارٍ التجهيز…"},
    "scheduled.recipe_booked": {
        "en": "Set up: it is in your scheduled tasks.",
        "ar": "تم التجهيز: تجدها في مهامك المجدولة.",
    },
    "scheduled.recipe_failed": {"en": "Not set up: {why}", "ar": "لم تُجهَّز: {why}"},
}
