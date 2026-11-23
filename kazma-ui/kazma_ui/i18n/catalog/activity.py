"""The Activity page (Spaces S2-09): what was done in a space, under each
person's name."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "nav.activity": {"en": "Activity", "ar": "النشاط"},
    "activity.title": {"en": "Activity", "ar": "النشاط"},
    "activity.intro": {
        "en": "Every change made in this space, under the name of the person who made it. "
              "The members of this space see it; nobody else does. What was sent is never kept here.",
        "ar": "كل تغيير جرى في هذه المساحة، باسم من أجراه. يراه أعضاء هذه المساحة ولا يراه غيرهم. "
              "ولا يُحفظ هنا ما أُرسل.",
    },
    "activity.loading": {"en": "Reading the activity…", "ar": "جارٍ قراءة النشاط…"},
    "activity.failed": {
        "en": "The activity could not be read. Try again.",
        "ar": "تعذّرت قراءة النشاط. حاول مرة أخرى.",
    },
    "activity.empty": {
        "en": "Nothing has been changed in this space yet.",
        "ar": "لم يتغيّر شيء في هذه المساحة بعد.",
    },
    "activity.when": {"en": "When", "ar": "متى"},
    "activity.who": {"en": "Who", "ar": "من"},
    "activity.what": {"en": "What", "ar": "ماذا"},
    "activity.answer": {"en": "Outcome", "ar": "النتيجة"},
    "activity.done": {"en": "Done", "ar": "تمّ"},
    "activity.refused": {"en": "Refused", "ar": "رُفض"},
    "activity.failed_one": {"en": "Failed", "ar": "فشل"},
    "activity.more": {"en": "Show older", "ar": "اعرض الأقدم"},
}
