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
    "knowledge.pages_count.zero": {
        "ar": "لا صفحات",
        "en": "{n} pages",
    },
    "knowledge.pages_count.one": {
        "ar": "صفحة واحدة",
        "en": "1 page",
    },
    "knowledge.pages_count.two": {
        "ar": "صفحتان",
        "en": "{n} pages",
    },
    "knowledge.pages_count.few": {
        "ar": "{n} صفحات",
        "en": "{n} pages",
    },
    "knowledge.pages_count.many": {
        "ar": "{n} صفحة",
        "en": "{n} pages",
    },
    "knowledge.pages_count.other": {
        "ar": "{n} صفحة",
        "en": "{n} pages",
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
        "ar": "اكتمل الزحف: {fetched}/{pages} · {chunks}",
        "en": "Crawl finished: {fetched}/{pages} · {chunks}",
    },
    "knowledge.crawl_finished_partial": {
        "ar": "اكتمل الزحف: {fetched}/{pages} · {chunks} · فشل {failed}",
        "en": "Crawl finished: {fetched}/{pages} · {chunks} · {failed} failed",
    },
    "knowledge.crawl_unchanged": {
        "ar": "دون تغيير: {n}",
        "en": "{n} unchanged",
    },
    "knowledge.crawl_pruned": {
        "ar": "المُزالة: {n}",
        "en": "{n} pruned",
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
        "ar": "أشر إلى موقع توثيق (مثل واجهة Meta WhatsApp) ليقوم كاظمه بابتلاع شجرة الصفحات دفعة واحدة. بعدها يستند الوكيل إلى المحتوى ويستشهد بمصادره عند طرح أسئلتك.",
        "en": "Point Kazma at a documentation site (e.g. the Meta WhatsApp Cloud API) and it ingests the whole tree once. The agent then reasons over the corpus and cites sources when you ask questions.",
    },
    "knowledge.keyword_only": {
        "ar": "بحث بالكلمات فقط",
        "en": "Keyword search only",
    },
    "knowledge.libraries": {
        "ar": "المكتبات",
        "en": "Libraries",
    },
    "knowledge.meaning_search": {
        "ar": "البحث بالمعنى: {v} من {n}",
        "en": "Meaning search: {v} of {n}",
    },
    "knowledge.name_placeholder": {
        "ar": "الاسم المعروض",
        "en": "Display name",
    },
    "knowledge.page_ingested.zero": {
        "ar": "تم ابتلاع صفحة واحدة — لا مقاطع جديدة.",
        "en": "Ingested 1 page — {n} new chunks.",
    },
    "knowledge.page_ingested.one": {
        "ar": "تم ابتلاع صفحة واحدة — مقطع جديد واحد.",
        "en": "Ingested 1 page — 1 new chunk.",
    },
    "knowledge.page_ingested.two": {
        "ar": "تم ابتلاع صفحة واحدة — مقطعان جديدان.",
        "en": "Ingested 1 page — {n} new chunks.",
    },
    "knowledge.page_ingested.few": {
        "ar": "تم ابتلاع صفحة واحدة — {n} مقاطع جديدة.",
        "en": "Ingested 1 page — {n} new chunks.",
    },
    "knowledge.page_ingested.many": {
        "ar": "تم ابتلاع صفحة واحدة — {n} مقطعًا جديدًا.",
        "en": "Ingested 1 page — {n} new chunks.",
    },
    "knowledge.page_ingested.other": {
        "ar": "تم ابتلاع صفحة واحدة — {n} مقطع جديد.",
        "en": "Ingested 1 page — {n} new chunks.",
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
    "knowledge.auto_inject_on": {
        "ar": "الحقن التلقائي مفعّل: تُدمج أفضل المقاطع في كل موجّه.",
        "en": "Auto-inject ON: top chunks are folded into every prompt.",
    },
    "knowledge.auto_inject_off": {
        "ar": "الحقن التلقائي معطّل: على الوكيل استدعاء knowledge_search صراحةً.",
        "en": "Auto-inject OFF: the agent must call knowledge_search explicitly.",
    },
    "knowledge.auto_on_msg": {
        "ar": "الحقن التلقائي مفعّل — ستُدمج مقاطع هذه المكتبة في كل موجّه.",
        "en": "Auto-inject ON — chunks from this library will be folded into every prompt.",
    },
    "knowledge.auto_off_msg": {
        "ar": "الحقن التلقائي معطّل.",
        "en": "Auto-inject OFF.",
    },
    "knowledge.library_deleted": {
        "ar": "حُذفت المكتبة.",
        "en": "Library deleted.",
    },
    "knowledge.library_not_found": {
        "ar": "المكتبة غير موجودة.",
        "en": "Library not found.",
    },
    "knowledge.ingest_failed": {
        "ar": "فشل الإدخال.",
        "en": "Ingest failed.",
    },
    "knowledge.no_seed": {
        "ar": "لا تملك المكتبة seed_url للتحديث منه.",
        "en": "Library has no seed_url to refresh from.",
    },
    "knowledge.ui.load_failed": {
        "ar": "فشل تحميل المكتبات: {error}",
        "en": "Failed to load libraries: {error}",
    },
    "knowledge.ui.create_failed": {
        "ar": "فشل الإنشاء",
        "en": "Create failed",
    },
    "knowledge.ui.ingest_failed": {
        "ar": "فشل الاستيعاب",
        "en": "Ingest failed",
    },
    "knowledge.ui.archive_failed": {
        "ar": "فشلت الأرشفة",
        "en": "Archive failed",
    },
    "knowledge.ui.unarchive_failed": {
        "ar": "فشل إلغاء الأرشفة",
        "en": "Unarchive failed",
    },
    "knowledge.ui.search_failed": {
        "ar": "فشل البحث",
        "en": "Search failed",
    },
    "knowledge.ui.browse_failed": {
        "ar": "فشل التصفح",
        "en": "Browse failed",
    },
    "knowledge.ui.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "knowledge.ui.refresh_failed": {
        "ar": "فشل التحديث",
        "en": "Refresh failed",
    },
    "knowledge.ui.update_failed": {
        "ar": "فشل التحديث",
        "en": "Update failed",
    },
    "knowledge.ui.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "knowledge.ui.delete_failed": {
        "ar": "فشل الحذف",
        "en": "Delete failed",
    },
    "knowledge.job_discovered": {
        "ar": "المكتشفة",
        "en": "discovered",
    },
    "knowledge.job_fetched": {
        "ar": "المجلوبة",
        "en": "fetched",
    },
    "knowledge.job_ingested": {
        "ar": "المستوعبة",
        "en": "ingested",
    },
    "knowledge.job_skipped": {
        "ar": "المتخطاة",
        "en": "skipped",
    },
    "knowledge.job_unchanged": {
        "ar": "دون تغيير",
        "en": "unchanged",
    },
    "knowledge.job_pruned": {
        "ar": "المحذوفة",
        "en": "pruned",
    },
    "knowledge.job_failed": {
        "ar": "الفاشلة",
        "en": "failed",
    },
    "knowledge.job_current": {
        "ar": "الحالي:",
        "en": "current:",
    },
    "knowledge.job_failures": {
        "ar": "الإخفاقات:",
        "en": "Failures:",
    },
    "knowledge.loading": {
        "ar": "جارٍ التحميل…",
        "en": "Loading…",
    },
    "knowledge.seed_label": {
        "ar": "المصدر:",
        "en": "seed:",
    },
    "knowledge.hits_count.zero": {
        "ar": "لا نتائج",
        "en": "{n} hits",
    },
    "knowledge.hits_count.one": {
        "ar": "نتيجة واحدة",
        "en": "1 hit",
    },
    "knowledge.hits_count.two": {
        "ar": "نتيجتان",
        "en": "{n} hits",
    },
    "knowledge.hits_count.few": {
        "ar": "{n} نتائج",
        "en": "{n} hits",
    },
    "knowledge.hits_count.many": {
        "ar": "{n} نتيجة",
        "en": "{n} hits",
    },
    "knowledge.hits_count.other": {
        "ar": "{n} نتيجة",
        "en": "{n} hits",
    },
    "knowledge.hit_score": {
        "ar": "الدرجة",
        "en": "score",
    },
    "knowledge.chunks_of": {
        "ar": "{shown} من {total}",
        "en": "{shown} of {total}",
    },
    "knowledge.untitled": {
        "ar": "(بلا عنوان)",
        "en": "(untitled)",
    },
}
