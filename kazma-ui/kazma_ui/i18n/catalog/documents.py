"""``documents`` UI strings (the /documents page).

One slice of the translation catalog; ``kazma_ui.i18n`` merges every slice
into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "documents.library_pick": {
        "ar": "اختر مكتبة…",
        "en": "Choose a library…",
    },
    "documents.library_new": {
        "ar": "مكتبة جديدة…",
        "en": "New library…",
    },
    "documents.add_to_library": {
        "ar": "أضف إلى المكتبة",
        "en": "Add to library",
    },
    "documents.library_new_title": {
        "ar": "مكتبة جديدة",
        "en": "New library",
    },
    "documents.library_new_prompt": {
        "ar": "اسم المكتبة التي سيُضاف إليها هذا المستند:",
        "en": "Name of the library to add this document to:",
    },
    "documents.library_added": {
        "ar": "أُضيف {n} مقطع إلى «{library}».",
        "en": "Added {n} passage(s) to “{library}”.",
    },
    "documents.library_failed": {
        "ar": "تعذّرت الإضافة إلى المكتبة: {error}",
        "en": "Could not add it to the library: {error}",
    },
    "documents.library_none": {
        "ar": "لا توجد مكتبات بعد — اختر «مكتبة جديدة…».",
        "en": "No libraries yet — choose “New library…”.",
    },
}
