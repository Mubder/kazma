"""``knowledge`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "knowledge.add_hint": {
        "ar": "الصفحة الواحدة فورية. أمّا الزحف فيكتشف كل صفحة تحت البذرة (عبر sitemap.xml أو تتبّع الروابط) ويعمل في الخلفية — تابع تقدّم المهمة بالأسفل.",
        "en": "Single page is instant. Crawl discovers every page under the seed (via sitemap.xml or link-walk) and runs in the background — watch the progress of the job below.",
    },
    "knowledge.add_title": {
        "ar": "إضافة مكتبة",
        "en": "Add a library",
    },
    "knowledge.archive": {
        "ar": "أرشفة",
        "en": "Archive",
    },
    "knowledge.archived_empty": {
        "ar": "لا توجد مكتبات مؤرشفة.",
        "en": "No archived libraries.",
    },
    "knowledge.archived_msg": {
        "ar": "تمت أرشفة المكتبة.",
        "en": "Library archived.",
    },
    "knowledge.auto_inject": {
        "ar": "حقن تلقائي",
        "en": "auto-inject",
    },
    "knowledge.browse": {
        "ar": "استعراض",
        "en": "Browse",
    },
    "knowledge.chunks": {
        "ar": "مقطع",
        "en": "chunks",
    },
    "knowledge.chunks_count.few": {
        "ar": "{n} مقاطع",
        "en": "{n} chunks",
    },
    "knowledge.chunks_count.many": {
        "ar": "{n} مقطعاً",
        "en": "{n} chunks",
    },
    "knowledge.chunks_count.one": {
        "ar": "مقطع واحد",
        "en": "1 chunk",
    },
    "knowledge.chunks_count.other": {
        "ar": "{n} مقطع",
        "en": "{n} chunks",
    },
    "knowledge.chunks_count.two": {
        "ar": "مقطعان",
        "en": "2 chunks",
    },
    "knowledge.chunks_count.zero": {
        "ar": "لا توجد مقاطع",
        "en": "no chunks",
    },
    "knowledge.crawl_finished_empty": {
        "ar": "اكتمل الزحف دون ابتلاع أي صفحة. تحقق من قائمة الفشل.",
        "en": "Crawl finished but no pages were ingested. Check the failures list.",
    },
    "knowledge.crawl_finished_ok": {
        "ar": "اكتمل الزحف: {fetched}/{discovered} صفحة · {ingested} مقطع",
        "en": "Crawl finished: {fetched}/{discovered} pages · {ingested} chunks",
    },
    "knowledge.crawl_finished_partial": {
        "ar": "اكتمل الزحف: {fetched}/{discovered} صفحة · {ingested} مقطع · فشل {failed}",
        "en": "Crawl finished: {fetched}/{discovered} pages · {ingested} chunks · {failed} failed",
    },
    "knowledge.crawl_started": {
        "ar": "بدأ الزحف — تابع التقدّم بالأسفل.",
        "en": "Crawl started — watch progress below.",
    },
    "knowledge.crawl_tree": {
        "ar": "زحف لشجرة التوثيق كاملة",
        "en": "Crawl whole doc tree",
    },
    "knowledge.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "knowledge.delete_confirm_msg": {
        "ar": "يحذف هذا المكتبة وكل مقاطعها البالغة {n}. لا يمكن التراجع.",
        "en": "This removes the library and all {n} of its chunks. Cannot be undone.",
    },
    "knowledge.delete_confirm_title": {
        "ar": "حذف \"{name}\"؟",
        "en": "Delete \"{name}\"?",
    },
    "knowledge.empty": {
        "ar": "لا توجد مكتبات بعد. أضف واحدة بالأعلى، أو من المحادثة عبر /kb crawl <id> <url>.",
        "en": "No libraries yet. Add one above, or from chat with /kb crawl <id> <url>.",
    },
    "knowledge.id_placeholder": {
        "ar": "معرّف المكتبة (مثل shipx_whatsapp_api)",
        "en": "Library ID (slug, e.g. shipx_whatsapp_api)",
    },
    "knowledge.ingest_page": {
        "ar": "ابتلاع صفحة واحدة",
        "en": "Ingest single page",
    },
    "knowledge.intro": {
        "ar": "أشر إلى موقع توثيق (مثل واجهة Meta WhatsApp) ليقوم كاظمة بابتلاع شجرة الصفحات دفعة واحدة. بعدها يستند الوكيل إلى المحتوى ويستشهد بمصادره عند طرح أسئلتك.",
        "en": "Point Kazma at a documentation site (e.g. the Meta WhatsApp Cloud API) and it ingests the whole tree once. The agent then reasons over the corpus and cites sources when you ask questions.",
    },
    "knowledge.libraries": {
        "ar": "المكتبات",
        "en": "Libraries",
    },
    "knowledge.name_placeholder": {
        "ar": "الاسم المعروض",
        "en": "Display name",
    },
    "knowledge.page_ingested": {
        "ar": "تم ابتلاع صفحة واحدة — {chunks} مقطع جديد.",
        "en": "Ingested 1 page — {chunks} new chunks.",
    },
    "knowledge.page_ingested_failed": {
        "ar": "فشل الابتلاع: {error}",
        "en": "Ingest failed: {error}",
    },
    "knowledge.refresh": {
        "ar": "↻ تحديث",
        "en": "↻ Refresh",
    },
    "knowledge.refresh_confirm_msg": {
        "ar": "سيتم إعادة زحف البذرة. فقط الصفحات المتغيرة تُعاد فهرستها (إزالة تكرار بتجزئة المحتوى).",
        "en": "This will re-crawl the seed. Only changed pages are re-indexed (content-hash dedup).",
    },
    "knowledge.refresh_confirm_title": {
        "ar": "إعادة ابتلاع المكتبة؟",
        "en": "Re-ingest library?",
    },
    "knowledge.refresh_lib": {
        "ar": "↻ تحديث",
        "en": "↻ Refresh",
    },
    "knowledge.refresh_started": {
        "ar": "بدأ التحديث.",
        "en": "Refresh started.",
    },
    "knowledge.restored_msg": {
        "ar": "تمت استعادة المكتبة.",
        "en": "Library restored.",
    },
    "knowledge.search_btn": {
        "ar": "بحث",
        "en": "Search",
    },
    "knowledge.search_placeholder": {
        "ar": "اسأل شيئًا عن هذا المحتوى…",
        "en": "Ask something about this corpus…",
    },
    "knowledge.searching": {
        "ar": "يبحث…",
        "en": "searching…",
    },
    "knowledge.seed_placeholder": {
        "ar": "رابط البذرة (جذر التوثيق أو صفحة)",
        "en": "Seed URL (doc root or page)",
    },
    "knowledge.tab_active": {
        "ar": "نشطة",
        "en": "Active",
    },
    "knowledge.tab_archived": {
        "ar": "مؤرشفة",
        "en": "Archived",
    },
    "knowledge.test": {
        "ar": "اختبار",
        "en": "Test",
    },
    "knowledge.title": {
        "ar": "المكتبة المعرفية",
        "en": "Knowledge Library",
    },
    "knowledge.unarchive": {
        "ar": "استعادة",
        "en": "Restore",
    },
}
