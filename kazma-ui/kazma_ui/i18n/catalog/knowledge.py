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
        "ar": "معرّف المكتبة (مثل whatsapp_api_docs)",
        "en": "Library ID (slug, e.g. whatsapp_api_docs)",
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
    "knowledge.phase_done": {"en": 'Done', "ar": 'اكتمل'},
    "knowledge.discovery_incomplete": {"en": 'Crawl finished with incomplete discovery; review the retained outcomes.', "ar": 'انتهى الزحف باكتشاف غير مكتمل؛ راجع النتائج المحفوظة.'},
    "knowledge.discovery_uncertain": {"en": 'Uncertain discovery requests; skipped on resume', "ar": 'طلبات اكتشاف غير مؤكدة؛ تُتخطى عند الاستئناف'},
    "knowledge.phase_error": {"en": 'Error', "ar": 'خطأ'},
    "knowledge.phase_interrupted": {"en": 'Interrupted', "ar": 'متوقف'},
    "knowledge.phase_running": {"en": 'Crawling…', "ar": 'جارٍ الزحف…'},
    "knowledge.resume": {"en": 'Resume pending pages', "ar": 'استئناف الصفحات المعلقة'},
    "knowledge.resume_failed": {"en": 'Could not resume crawl', "ar": 'تعذر استئناف الزحف'},
    "knowledge.status_unavailable": {"en": 'Crawl status unavailable; retrying', "ar": 'حالة الزحف غير متاحة؛ جارٍ إعادة المحاولة'},
    "knowledge.acquisition_pending": {"en": 'Pending pages', "ar": 'الصفحات المعلقة'},
    "knowledge.acquisition_ambiguous": {"en": 'Uncertain pages; fetched again on resume', "ar": 'صفحات غير مؤكدة؛ تُجلب مجددًا عند الاستئناف'},
    "knowledge.acquisition_backend": {"en": 'Acquisition service', "ar": 'خدمة الاستخراج'},
    "knowledge.acquisition_attempts": {"en": 'Attempts', "ar": 'المحاولات'},
    "knowledge.allowance_unit": {"en": 'Request reservations (upper bound); provider charges are separate', "ar": 'حجوزات الطلبات (حد أقصى)؛ رسوم المزوّد منفصلة'},
    "knowledge.allowance_job": {"en": 'Job allowance', "ar": 'حصة المهمة'},
    "knowledge.credits_unavailable": {"en": 'Credit balance unavailable', "ar": 'رصيد الاعتمادات غير متاح'},
    "knowledge.credits_refresh": {"en": 'Refresh Firecrawl credits', "ar": 'تحديث رصيد Firecrawl'},
    "knowledge.provider_dashboard": {"en": 'Provider dashboard', "ar": 'لوحة المزوّد'},
    "knowledge.credits_scope": {"en": 'Balances belong to the provider account; fresh for five minutes. Charges for these requests remain unavailable.', "ar": 'الأرصدة تخص حساب المزوّد؛ تُعد حديثة لخمس دقائق. رسوم هذه الطلبات لا تزال غير متاحة.'},
    "knowledge.allowance_day": {"en": 'Installation allowance (UTC day)', "ar": 'حصة التثبيت (اليوم بتوقيت UTC)'},
    "knowledge.acquisition_unknown": {"en": 'Charges for these requests: unavailable', "ar": 'رسوم هذه الطلبات: غير متاحة'},
    "knowledge.usage_observations": {"en": 'Observed service responses; usage does not establish charges or remaining balance', "ar": 'استجابات الخدمة المرصودة؛ الاستخدام لا يحدد الرسوم أو الرصيد المتبقي'},
    "knowledge.reported_token_usage": {"en": 'Provider-reported token usage', "ar": 'استخدام الرموز المبلّغ عنه من المزوّد'},
    # Knowledge audit 2026-10-13: the page's states and the routes' refusals,
    # each worded in the page's language (KB-S05, KB-S15, KB-S04).
    "knowledge.single_operator": {
        "en": "The Knowledge Library works on a single-operator install. This install has platform users, and libraries are not separated per user yet, so the Knowledge Library is off here.",
        "ar": "تعمل مكتبة المعرفة على تثبيت يديره مشغّل واحد. لهذا التثبيت مستخدمون متعددون، ولا تُفصل المكتبات لكل مستخدم بعد، لذا مكتبة المعرفة متوقفة هنا.",
    },
    "knowledge.phase_partial": {"en": 'Finished with failures', "ar": 'انتهى مع إخفاقات'},
    "knowledge.crawl_partial_toast": {
        "en": "Crawl finished with failures: {fetched}/{pages} · {chunks} · {failed} failed",
        "ar": "انتهى الزحف مع إخفاقات: {fetched}/{pages} · {chunks} · فشل {failed}",
    },
    "knowledge.meaning_rebuilding": {
        "en": "Meaning search: rebuilding, {v} of {n}",
        "ar": "البحث بالمعنى: قيد إعادة البناء، {v} من {n}",
    },
    "knowledge.meaning_unknown": {
        "en": "Meaning search: not checked yet",
        "ar": "البحث بالمعنى: لم يُفحص بعد",
    },
    "knowledge.err.kb_single_operator": {
        "en": "The Knowledge Library is off on an install with platform users.",
        "ar": "مكتبة المعرفة متوقفة على تثبيت له مستخدمون متعددون.",
    },
    "knowledge.err.invalid_request": {
        "en": "The request was refused: {reason}",
        "ar": "رُفض الطلب لأن قيمة فيه غير صالحة.",
    },
    "knowledge.err.invalid_json": {
        "en": "The request was not valid JSON.",
        "ar": "الطلب ليس JSON صالحًا.",
    },
    "knowledge.err.request_too_large": {
        "en": "The request is too large.",
        "ar": "الطلب أكبر من المسموح.",
    },
    "knowledge.err.not_found": {
        "en": "Not found.",
        "ar": "غير موجود.",
    },
    "knowledge.err.library_exists": {
        "en": "A library with this id already exists.",
        "ar": "توجد مكتبة بهذا المعرّف بالفعل.",
    },
    "knowledge.err.url_refused": {
        "en": "Only http and https addresses can be used: {reason}",
        "ar": "لا يُقبل إلا عنوان يبدأ بـ http أو https.",
    },
    "knowledge.err.kb_storage_full": {
        "en": "The Knowledge Library is at its storage quota. Delete libraries you no longer need to make room.",
        "ar": "بلغت مكتبة المعرفة حد التخزين. احذف المكتبات التي لم تعد تحتاجها لإفساح المجال.",
    },
    "knowledge.err.kb_library_limit": {
        "en": "Kazma holds as many libraries as it can. Delete a library you no longer need to add another.",
        "ar": "بلغ كازما الحد الأقصى لعدد المكتبات. احذف مكتبة لم تعد تحتاجها لتضيف أخرى.",
    },
    "knowledge.err.library_busy": {
        "en": "A crawl of this library is already running; wait for it to finish.",
        "ar": "يجري زحف لهذه المكتبة بالفعل؛ انتظر حتى ينتهي.",
    },
    "knowledge.err.crawl_owns_library": {
        "en": "A crawl of this library is running; delete it when the crawl finishes.",
        "ar": "يجري زحف لهذه المكتبة؛ احذفها بعد انتهاء الزحف.",
    },
    "knowledge.err.documents_unavailable": {
        "en": "This library holds documents, and the Documents platform could not be told it is going. Try again shortly.",
        "ar": "تحتوي هذه المكتبة على مستندات، وتعذر إبلاغ منصة المستندات بحذفها. أعد المحاولة بعد قليل.",
    },
    "knowledge.err.page_not_ingested": {
        "en": "The page was not stored: {reason}",
        "ar": "لم تُحفظ الصفحة: تعذر جلبها أو قراءتها أو حفظها.",
    },
    "knowledge.err.no_seed": {
        "en": "This library has no seed URL to refresh from.",
        "ar": "لا يوجد لهذه المكتبة عنوان بذرة لإعادة الزحف منه.",
    },
    "knowledge.err.resume_refused": {
        "en": "Only an interrupted crawl can resume.",
        "ar": "لا يُستأنف إلا زحف متوقف.",
    },
    "knowledge.err.internal_error": {
        "en": "Something went wrong on the server: {reason}",
        "ar": "حدث خطأ في الخادم.",
    },
    "knowledge.err.invalid_url": {
        "en": "The address was refused: {reason}",
        "ar": "رُفض العنوان لأنه غير صالح أو غير مسموح.",
    },
    "knowledge.err.policy_error": {
        "en": "The crawl was refused by its policy: {reason}",
        "ar": "رفضت سياسة الجلب هذا الزحف (تغيّر النطاق أو الحدود أو السياسة، أو لا يمكن استئنافه).",
    },
    "knowledge.err.proxy_routing_error": {
        "en": "The configured proxy route is not usable.",
        "ar": "مسار الوكيل المضبوط غير صالح للاستخدام.",
    },
    "knowledge.err.origin_throttled": {
        "en": "The site asked for fewer requests; retry later.",
        "ar": "طلب الموقع تقليل الطلبات؛ أعد المحاولة لاحقًا.",
    },
    "knowledge.err.robots_disallowed": {
        "en": "The site's robots rules do not allow this crawl.",
        "ar": "قواعد robots في الموقع لا تسمح بهذا الزحف.",
    },
    "knowledge.err.robots_unavailable": {
        "en": "The site's robots rules could not be read, so the crawl was refused.",
        "ar": "تعذرت قراءة قواعد robots في الموقع، لذا رُفض الزحف.",
    },
    "knowledge.err.security_blocked": {
        "en": "The address points to a private or blocked network.",
        "ar": "يشير العنوان إلى شبكة خاصة أو محظورة.",
    },
    "knowledge.err.scope_blocked": {
        "en": "The address is outside the crawl's scope.",
        "ar": "العنوان خارج نطاق الزحف.",
    },
    "knowledge.err.redirect_error": {
        "en": "The site redirected somewhere the crawl cannot follow.",
        "ar": "أعاد الموقع التوجيه إلى مكان لا يمكن للزحف متابعته.",
    },
    "knowledge.err.http_error": {
        "en": "The site answered with an error.",
        "ar": "ردّ الموقع بخطأ.",
    },
    "knowledge.err.connect_error": {
        "en": "The site could not be reached.",
        "ar": "تعذر الوصول إلى الموقع.",
    },
    "knowledge.err.read_error": {
        "en": "The connection to the site broke while reading.",
        "ar": "انقطع الاتصال بالموقع أثناء القراءة.",
    },
    "knowledge.err.timeout": {
        "en": "The site took too long to answer.",
        "ar": "استغرق الموقع وقتًا طويلًا للرد.",
    },
    "knowledge.err.acquisition_deadline": {
        "en": "The page could not be fetched within its time limit.",
        "ar": "تعذر جلب الصفحة ضمن المهلة المحددة.",
    },
    "knowledge.err.decoding_error": {
        "en": "The page's content could not be decoded.",
        "ar": "تعذر فك ترميز محتوى الصفحة.",
    },
    "knowledge.err.backend_unavailable": {
        "en": "The page reader is unavailable.",
        "ar": "قارئ الصفحات غير متاح.",
    },
    "knowledge.err.empty_extract": {
        "en": "The page had no readable content.",
        "ar": "لا تحتوي الصفحة على محتوى قابل للقراءة.",
    },
    "knowledge.err.challenge_page": {
        "en": "The site answered with a bot challenge instead of the page.",
        "ar": "ردّ الموقع بصفحة تحقق من الروبوتات بدلًا من الصفحة.",
    },
    "knowledge.err.login_required": {
        "en": "The page needs a sign-in.",
        "ar": "تتطلب الصفحة تسجيل الدخول.",
    },
    "knowledge.err.invalid_provider_response": {
        "en": "The page service answered with something it should not have.",
        "ar": "ردّت خدمة الصفحات بشيء غير متوقع.",
    },
    "knowledge.err.unsupported_content": {
        "en": "This kind of content cannot be read into a library.",
        "ar": "لا يمكن قراءة هذا النوع من المحتوى في مكتبة.",
    },
    "knowledge.err.resource_limit": {
        "en": "A limit was reached: {reason}",
        "ar": "تم بلوغ حد (عدد الزحف النشط أو سعة سجل الزحف أو ميزانية الطلبات)؛ أعد المحاولة بعد انتهاء زحف.",
    },
    "knowledge.err.storage_error": {
        "en": "The crawl journal is unavailable; check the database. Retry shortly.",
        "ar": "سجل الزحف غير متاح؛ تحقق من قاعدة البيانات وأعد المحاولة بعد قليل.",
    },
    "knowledge.err.workspace_path_error": {
        "en": "The output folder is not usable.",
        "ar": "مجلد المخرجات غير صالح للاستخدام.",
    },
    "knowledge.err.file_exists": {
        "en": "This crawl job already exists; resume it instead.",
        "ar": "مهمة الزحف هذه موجودة بالفعل؛ استأنفها بدلًا من ذلك.",
    },
    "knowledge.err.acquisition_error": {
        "en": "The page could not be fetched.",
        "ar": "تعذر جلب الصفحة.",
    },
    "knowledge.err.cancelled": {
        "en": "The crawl was cancelled.",
        "ar": "أُلغي الزحف.",
    },
    "knowledge.scope_label": {
        "en": 'Scope',
        "ar": 'النطاق',
    },
    "knowledge.scope_default": {
        "en": 'Usual scope',
        "ar": 'النطاق المعتاد',
    },
    "knowledge.scope_section": {
        "en": 'This section',
        "ar": 'هذا القسم',
    },
    "knowledge.scope_site": {
        "en": 'Whole site',
        "ar": 'الموقع كاملاً',
    },
    "knowledge.scope_page": {
        "en": 'Just this page',
        "ar": 'هذه الصفحة فقط',
    },
    "knowledge.preview": {
        "en": 'Preview',
        "ar": 'معاينة',
    },
    "knowledge.preview_title": {
        "en": 'What this crawl would fetch',
        "ar": 'ما سيجلبه هذا الزحف',
    },
    "knowledge.preview_failed": {
        "en": 'Preview failed: {error}',
        "ar": 'تعذرت المعاينة: {error}',
    },
    "knowledge.preview_source.sitemap": {
        "en": "Listed by the site's sitemap.",
        "ar": 'من خريطة الموقع.',
    },
    "knowledge.preview_source.llms_txt": {
        "en": "Listed by the site's llms.txt.",
        "ar": 'من ملف llms.txt الخاص بالموقع.',
    },
    "knowledge.preview_source.link_walk": {
        "en": "Found by following the page's links.",
        "ar": 'بتتبّع روابط الصفحة.',
    },
    "knowledge.preview_source.single_page": {
        "en": 'Just this page.',
        "ar": 'هذه الصفحة فقط.',
    },
    "knowledge.preview_source.seed_only": {
        "en": 'Only this page could be read; its links could not be followed.',
        "ar": 'أمكن قراءة هذه الصفحة فقط؛ تعذّر تتبّع روابطها.',
    },
    "knowledge.preview_unknown": {
        "en": 'known once crawled',
        "ar": 'يُعرف بعد الزحف',
    },
    "knowledge.preview_cap": {
        "en": 'That is more than one crawl takes: it fetches the first pages, up to its limit.',
        "ar": 'هذا أكثر مما يأخذه زحف واحد: يجلب الصفحات الأولى حتى حدّه.',
    },
    "knowledge.preview_bounded": {
        "en": 'The sitemap is very large and was read in part; the crawl may find more.',
        "ar": 'خريطة الموقع كبيرة جداً وقُرئت جزئياً؛ قد يجد الزحف المزيد.',
    },
    "knowledge.preview_upper": {
        "en": 'Pages that robots.txt or a sign-in keeps out are found during the crawl.',
        "ar": 'الصفحات التي يمنعها robots.txt أو تسجيل الدخول تُكتشف أثناء الزحف.',
    },
    "knowledge.verdict.success": {
        "en": 'Completed',
        "ar": 'اكتمل',
    },
    "knowledge.verdict.partial": {
        "en": 'Finished with failures',
        "ar": 'انتهى مع إخفاقات',
    },
    "knowledge.verdict.failed": {
        "en": 'Failed',
        "ar": 'فشل',
    },
    "knowledge.verdict.empty": {
        "en": 'Stored nothing',
        "ar": 'لم يُخزَّن شيء',
    },
    "knowledge.report_title": {
        "en": 'Pages not stored, and why',
        "ar": 'صفحات لم تُخزَّن، ولماذا',
    },
    "knowledge.outcome.ingested": {
        "en": 'Stored',
        "ar": 'خُزّنت',
    },
    "knowledge.outcome.unchanged": {
        "en": 'Unchanged',
        "ar": 'لم تتغير',
    },
    "knowledge.outcome.excluded": {
        "en": 'Kept out',
        "ar": 'مستبعدة',
    },
    "knowledge.outcome.failed": {
        "en": 'Failed',
        "ar": 'فشلت',
    },
    "knowledge.outcome.uncertain": {
        "en": 'Uncertain',
        "ar": 'غير مؤكدة',
    },
    "knowledge.outcome.pending": {
        "en": 'Not reached',
        "ar": 'لم يُوصل إليها',
    },
    "knowledge.note.excluded": {
        "en": 'Some pages were kept out by robots.txt, the scope, a sign-in, removal or their size.',
        "ar": 'استُبعدت صفحات بسبب robots.txt أو النطاق أو تسجيل الدخول أو حذفها أو حجمها.',
    },
    "knowledge.note.page_cap": {
        "en": 'The crawl stopped at its page limit.',
        "ar": 'توقف الزحف عند حد الصفحات.',
    },
    "knowledge.note.link_walk_bounded": {
        "en": 'Following links stopped at its depth or size limit.',
        "ar": 'توقف تتبع الروابط عند حد العمق أو الحجم.',
    },
    "knowledge.note.sitemap_bounded": {
        "en": 'The sitemap is very large and was read in part.',
        "ar": 'خريطة الموقع كبيرة جداً وقُرئت جزئياً.',
    },
    "knowledge.note.nothing_stored": {
        "en": 'Nothing was stored: every page was kept out.',
        "ar": 'لم يُخزَّن شيء: استُبعدت كل الصفحات.',
    },
    "knowledge.reason.robots": {
        "en": 'robots.txt asks crawlers to stay out',
        "ar": 'يطلب robots.txt من الزواحف عدم الدخول',
    },
    "knowledge.reason.out_of_scope": {
        "en": "outside the crawl's scope",
        "ar": 'خارج نطاق الزحف',
    },
    "knowledge.reason.login": {
        "en": 'behind a sign-in',
        "ar": 'خلف تسجيل دخول',
    },
    "knowledge.reason.gone": {
        "en": 'no longer on the site',
        "ar": 'لم تعد موجودة في الموقع',
    },
    "knowledge.reason.too_large": {
        "en": 'larger than Kazma reads',
        "ar": 'أكبر مما يقرؤه Kazma',
    },
    "knowledge.reason.server_error": {
        "en": 'the site had an error',
        "ar": 'حدث خطأ في الموقع',
    },
    "knowledge.reason.http_error": {
        "en": 'the site refused the page',
        "ar": 'رفض الموقع الصفحة',
    },
    "knowledge.reason.network": {
        "en": 'the connection failed',
        "ar": 'فشل الاتصال',
    },
    "knowledge.reason.blocked_address": {
        "en": 'the address is on a private network',
        "ar": 'العنوان على شبكة خاصة',
    },
    "knowledge.reason.bot_wall": {
        "en": 'a bot check stood in the way',
        "ar": 'اعترضه فحص للروبوتات',
    },
    "knowledge.reason.no_text": {
        "en": 'no readable text',
        "ar": 'لا يوجد نص مقروء',
    },
    "knowledge.reason.unreadable": {
        "en": 'a format Kazma cannot read',
        "ar": 'صيغة لا يقرؤها Kazma',
    },
    "knowledge.reason.incomplete": {
        "en": 'the page could not be read completely',
        "ar": 'تعذرت قراءة الصفحة كاملة',
    },
    "knowledge.reason.library_gone": {
        "en": 'the library was deleted',
        "ar": 'حُذفت المكتبة',
    },
    "knowledge.reason.storage_full": {
        "en": 'the Knowledge store is full',
        "ar": 'مخزن المعرفة ممتلئ',
    },
    "knowledge.reason.other": {
        "en": 'an unexpected problem',
        "ar": 'مشكلة غير متوقعة',
    },
    "knowledge.hint.robots": {
        "en": 'Nothing to fix: the site asked not to be crawled.',
        "ar": 'لا شيء لإصلاحه: طلب الموقع عدم الزحف إليه.',
    },
    "knowledge.hint.out_of_scope": {
        "en": 'Choose a wider scope (Whole site) to include it.',
        "ar": 'اختر نطاقاً أوسع (الموقع كاملاً) لتضمينها.',
    },
    "knowledge.hint.login": {
        "en": 'Kazma does not sign in to sites.',
        "ar": 'لا يسجل Kazma الدخول إلى المواقع.',
    },
    "knowledge.hint.gone": {
        "en": 'The link is stale; nothing to fix.',
        "ar": 'الرابط قديم؛ لا شيء لإصلاحه.',
    },
    "knowledge.hint.too_large": {
        "en": 'Pages are stored whole, up to a million characters; a part is never stored.',
        "ar": 'تُخزَّن الصفحات كاملة حتى مليون حرف؛ ولا يُخزَّن جزء منها أبداً.',
    },
    "knowledge.hint.server_error": {
        "en": 'Try again later with Refresh.',
        "ar": 'أعد المحاولة لاحقاً بالتحديث.',
    },
    "knowledge.hint.http_error": {
        "en": 'The site may refuse crawlers; open the page in a browser to check.',
        "ar": 'قد يرفض الموقع الزواحف؛ افتح الصفحة في المتصفح للتحقق.',
    },
    "knowledge.hint.network": {
        "en": 'Kazma tried again and the connection still failed; check the internet connection, then Refresh.',
        "ar": 'أعاد Kazma المحاولة وما زال الاتصال يفشل؛ تحقق من الاتصال بالإنترنت ثم حدّث.',
    },
    "knowledge.hint.blocked_address": {
        "en": 'Kazma never crawls private addresses; if the site is public, Refresh once the network is back.',
        "ar": 'لا يزحف Kazma إلى العناوين الخاصة أبداً؛ إن كان الموقع عاماً فحدّث عند عودة الشبكة.',
    },
    "knowledge.hint.bot_wall": {
        "en": 'Kazma does not solve bot checks.',
        "ar": 'لا يحل Kazma فحوص الروبوتات.',
    },
    "knowledge.hint.no_text": {
        "en": 'The page may be empty or built entirely by scripts.',
        "ar": 'قد تكون الصفحة فارغة أو مبنية بالكامل بالبرمجيات.',
    },
    "knowledge.hint.unreadable": {
        "en": 'Add the file through Documents instead.',
        "ar": 'أضف الملف من صفحة المستندات بدلاً من ذلك.',
    },
    "knowledge.hint.incomplete": {
        "en": "Check that Kazma's local browser (Chromium) works, then Refresh.",
        "ar": 'تحقق من عمل متصفح Kazma المحلي (Chromium) ثم حدّث.',
    },
    "knowledge.hint.library_gone": {
        "en": 'The crawl stopped with its library.',
        "ar": 'توقف الزحف مع مكتبته.',
    },
    "knowledge.hint.storage_full": {
        "en": 'Delete or archive a library to make room.',
        "ar": 'احذف مكتبة أو أرشفها لإفساح المجال.',
    },
    "knowledge.hint.other": {
        "en": 'The server log names the cause.',
        "ar": 'يذكر سجل الخادم السبب.',
    },
}
