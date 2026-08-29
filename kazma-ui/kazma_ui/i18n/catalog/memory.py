"""``memory`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "memory.archive_dead": {
        "ar": "أرشفة المعتقدات الملغاة",
        "en": "Archive invalidated beliefs",
    },
    "memory.confirm_delete": {
        "ar": "حذف هذا الكيان؟",
        "en": "Delete this entity shell?",
    },
    "memory.confirm_hygiene": {
        "ar": "تشغيل إجراءات التنظيف المحددة؟",
        "en": "Run the selected hygiene actions?",
    },
    "memory.confirm_invalidate": {
        "ar": "إبطال هذا المعتقد؟",
        "en": "Soft-invalidate this belief?",
    },
    "memory.confirm_merge": {
        "ar": "دمج المصدر في الهدف؟",
        "en": "Merge source into target? Beliefs will be rewired.",
    },
    "memory.dedupe_noted": {
        "ar": "إبطال ملاحظات مكررة",
        "en": "Invalidate near-dup noted",
    },
    "memory.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "memory.edit": {
        "ar": "تعديل",
        "en": "Edit",
    },
    "memory.empty_only": {
        "ar": "الفارغة فقط",
        "en": "Empty only",
    },
    "memory.hub_blurb": {
        "ar": "رسم المعتقدات، دمج/ربط الكيانات، التنظيف، والاستكشاف في صفحة الذاكرة.",
        "en": "Belief graph, entity merge/link, hygiene, probe, and backups live on the Memory page.",
    },
    "memory.intro": {
        "ar": "تصفح المعتقدات والكيانات، ادمج/اربط العقد المعزولة، نظّف الذاكرة، واستكشف الرسم البياني — كل عمليات الذاكرة في مكان واحد.",
        "en": "Browse beliefs and entities, merge/link isolated nodes, run hygiene, and explore the belief graph — one place for all memory ops.",
    },
    "memory.invalidate": {
        "ar": "إبطال",
        "en": "Invalidate",
    },
    "memory.isolated_hint": {
        "ar": "معزول = لديه معتقدات بلا روابط لكيانات أخرى. استخدم الربط أو الدمج.",
        "en": "Isolated = has beliefs but no edges to other entities. Use Link or Merge to connect them.",
    },
    "memory.isolated_only": {
        "ar": "المعزولة فقط",
        "en": "Isolated only",
    },
    "memory.link": {
        "ar": "ربط المصدر ← الهدف",
        "en": "Link source —pred→ target",
    },
    "memory.merge": {
        "ar": "دمج المصدر ← الهدف",
        "en": "Merge source → target",
    },
    "memory.no_rows": {
        "ar": "لا صفوف",
        "en": "No rows",
    },
    "memory.open_console": {
        "ar": "فتح وحدة الذاكرة",
        "en": "Open Memory console",
    },
    "memory.predicate": {
        "ar": "العلاقة (رابط)",
        "en": "Predicate (link)",
    },
    "memory.purge_empty": {
        "ar": "حذف كيانات فارغة",
        "en": "Purge empty entity shells",
    },
    "memory.refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "memory.rename": {
        "ar": "إعادة تسمية",
        "en": "Rename",
    },
    "memory.run_hygiene": {
        "ar": "تشغيل التنظيف",
        "en": "Run selected hygiene",
    },
    "memory.search": {
        "ar": "بحث",
        "en": "Search",
    },
    "memory.source": {
        "ar": "المصدر",
        "en": "Source",
    },
    "memory.tab_beliefs": {
        "ar": "المعتقدات",
        "en": "Beliefs",
    },
    "memory.tab_console": {
        "ar": "الرسم والصحة",
        "en": "Graph & health",
    },
    "memory.tab_entities": {
        "ar": "الكيانات",
        "en": "Entities",
    },
    "memory.tab_hygiene": {
        "ar": "التنظيف",
        "en": "Hygiene",
    },
    "memory.tab_merges": {
        "ar": "عمليات الدمج المعلقة",
        "en": "Pending merges",
    },
    "memory.target": {
        "ar": "الهدف",
        "en": "Target",
    },
    "memory.title": {
        "ar": "الذاكرة",
        "en": "Memory",
    },
    "memory.unlink": {
        "ar": "فك الربط",
        "en": "Unlink",
    },
}
