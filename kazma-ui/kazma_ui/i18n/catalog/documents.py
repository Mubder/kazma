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
    "documents.library_added.zero": {
        "ar": "لم يُضف أي مقطع إلى «{library}».",
        "en": "Added {n} passages to “{library}”.",
    },
    "documents.library_added.one": {
        "ar": "أُضيف مقطع واحد إلى «{library}».",
        "en": "Added 1 passage to “{library}”.",
    },
    "documents.library_added.two": {
        "ar": "أُضيف مقطعان إلى «{library}».",
        "en": "Added {n} passages to “{library}”.",
    },
    "documents.library_added.few": {
        "ar": "أُضيفت {n} مقاطع إلى «{library}».",
        "en": "Added {n} passages to “{library}”.",
    },
    "documents.library_added.many": {
        "ar": "أُضيف {n} مقطعًا إلى «{library}».",
        "en": "Added {n} passages to “{library}”.",
    },
    "documents.library_added.other": {
        "ar": "أُضيف {n} مقطع إلى «{library}».",
        "en": "Added {n} passages to “{library}”.",
    },
    "documents.library_failed": {
        "ar": "تعذّرت الإضافة إلى المكتبة: {error}",
        "en": "Could not add it to the library: {error}",
    },
    "documents.library_none": {
        "ar": "لا توجد مكتبات بعد — اختر «مكتبة جديدة…».",
        "en": "No libraries yet — choose “New library…”.",
    },
    "documents.intro": {
        "ar": "— ارفع مستندًا؛ يوضع في الحجر ويُتحقق منه ويُحلَّل في عملية منفصلة ثم يُتاح بمعرّف معتم. المعالجة دائمة وتصمد أمام إعادة التشغيل.",
        "en": "— upload a document; it is quarantined, validated, parsed out-of-process, and made available by opaque ID. Processing is durable and restart-safe.",
    },
    "documents.upload_region": {
        "ar": "رفع مستند",
        "en": "Upload document",
    },
    "documents.drop_hint": {
        "ar": "أفلت ملفًا هنا أو اضغط للاختيار",
        "en": "Drop a file here or click to choose",
    },
    "documents.uploading": {
        "ar": "جارٍ الرفع…",
        "en": "Uploading…",
    },
    "documents.force_ocr": {
        "ar": "فرض التعرّف الضوئي على هذا الرفع",
        "en": "Force OCR on this upload",
    },
    "documents.library": {
        "ar": "المكتبة",
        "en": "Library",
    },
    "documents.no_documents": {
        "ar": "لا توجد مستندات بعد.",
        "en": "No documents yet.",
    },
    "documents.delete_title": {
        "ar": "حذف هذا المستند / أرشفته",
        "en": "Delete / archive this document",
    },
    "documents.delete_hint": {
        "ar": "الحذف يؤرشف المستند (حذف مرن). تُزال النسخ المفهرسة، وتُستعاد مساحة الملف لاحقًا عند التنظيف.",
        "en": "Delete archives the document (soft-delete). Indexed copies are removed; file bytes are reclaimed later by GC.",
    },
    "documents.select_hint": {
        "ar": "اختر مستندًا لفحص محتواه وإصداراته ومهامه.",
        "en": "Select a document to inspect its content, versions, and jobs.",
    },
    "documents.delete_archive": {
        "ar": "حذف / أرشفة",
        "en": "Delete / Archive",
    },
    "documents.convert": {
        "ar": "تحويل",
        "en": "Convert",
    },
    "documents.no_renderer": {
        "ar": "لا يتوفر محرك عرض",
        "en": "No renderer engine available",
    },
    "documents.pdf_tools": {
        "ar": "أدوات PDF",
        "en": "PDF tools",
    },
    "documents.pdf_info": {
        "ar": "معلومات PDF",
        "en": "PDF info",
    },
    "documents.no_pdf_engine": {
        "ar": "لا يتوفر محرك PDF",
        "en": "No PDF engine available",
    },
    "documents.from": {
        "ar": "من",
        "en": "from",
    },
    "documents.to": {
        "ar": "إلى",
        "en": "to",
    },
    "documents.split": {
        "ar": "تقسيم",
        "en": "Split",
    },
    "documents.redact": {
        "ar": "تنقيح",
        "en": "Redact",
    },
    "documents.artifacts": {
        "ar": "المخرجات",
        "en": "Artifacts",
    },
    "documents.content_preview": {
        "ar": "معاينة المحتوى",
        "en": "Content preview",
    },
    "documents.pages_n.zero": {
        "ar": "لا صفحات",
        "en": "{n} pages",
    },
    "documents.pages_n.one": {
        "ar": "صفحة واحدة",
        "en": "1 page",
    },
    "documents.pages_n.two": {
        "ar": "صفحتان",
        "en": "{n} pages",
    },
    "documents.pages_n.few": {
        "ar": "{n} صفحات",
        "en": "{n} pages",
    },
    "documents.pages_n.many": {
        "ar": "{n} صفحة",
        "en": "{n} pages",
    },
    "documents.pages_n.other": {
        "ar": "{n} صفحة",
        "en": "{n} pages",
    },
    "documents.empty": {
        "ar": "(فارغ)",
        "en": "(empty)",
    },
    "documents.not_ready": {
        "ar": "غير جاهز — ما زالت المعالجة جارية.",
        "en": "Not ready — still processing.",
    },
    "documents.versions": {
        "ar": "الإصدارات",
        "en": "Versions",
    },
    "documents.jobs": {
        "ar": "المهام والتشخيص",
        "en": "Jobs & diagnostics",
    },
    "documents.events": {
        "ar": "الأحداث",
        "en": "Events",
    },
    "documents.capabilities": {
        "ar": "القدرات",
        "en": "Capabilities",
    },
    "documents.worker": {
        "ar": "العامل:",
        "en": "Worker:",
    },
    "documents.worker_running": {
        "ar": "يعمل",
        "en": "running",
    },
    "documents.worker_stopped": {
        "ar": "متوقف",
        "en": "stopped",
    },
    "documents.ocr": {
        "ar": "التعرّف الضوئي:",
        "en": "OCR:",
    },
    "documents.operations": {
        "ar": "العمليات",
        "en": "Operations",
    },
    "documents.status": {
        "ar": "الحالة:",
        "en": "status:",
    },
    "documents.queue_depth": {
        "ar": "عمق الطابور:",
        "en": "Queue depth:",
    },
    "documents.active_leases": {
        "ar": "الحجوزات النشطة:",
        "en": "Active leases:",
    },
    "documents.retry_waiting": {
        "ar": "بانتظار إعادة المحاولة:",
        "en": "Retry waiting:",
    },
    "documents.dead_letters": {
        "ar": "المتوقفة نهائيًا:",
        "en": "Dead letters:",
    },
    "documents.oldest_age": {
        "ar": "أقدم عمر (ث):",
        "en": "Oldest age (s):",
    },
    "documents.backlog": {
        "ar": "المتراكم:",
        "en": "Backlog:",
    },
    "documents.storage_logical": {
        "ar": "التخزين: المنطقي",
        "en": "Storage: logical",
    },
    "documents.storage_physical": {
        "ar": "· الفعلي",
        "en": "· physical",
    },
    "documents.storage_dedup": {
        "ar": "· إزالة التكرار",
        "en": "· dedup",
    },
    "documents.quota": {
        "ar": "الحصة:",
        "en": "Quota:",
    },
    "documents.storage_status": {
        "ar": "التخزين:",
        "en": "storage:",
    },
    "documents.jobs_backend": {
        "ar": "المهام: {backend}",
        "en": "jobs: {backend}",
    },
    "documents.multi_replica": {
        "ar": "متعدد النسخ",
        "en": "multi-replica",
    },
    "documents.single_node": {
        "ar": "عقدة واحدة",
        "en": "single-node",
    },
    "documents.run_gc": {
        "ar": "تشغيل تنظيف المساحة…",
        "en": "Run garbage collection…",
    },
    "documents.working": {
        "ar": "جارٍ العمل…",
        "en": "Working…",
    },
    "documents.reclaimed": {
        "ar": "استُعيد",
        "en": "reclaimed",
    },
    "documents.blobs": {
        "ar": "كتلة",
        "en": "blob(s)",
    },
    "documents.audit_history": {
        "ar": "سجل التدقيق",
        "en": "Audit history",
    },
    "documents.no_audit": {
        "ar": "لا أحداث تدقيق بعد.",
        "en": "No audit events yet.",
    },
    "documents.download": {
        "ar": "تنزيل",
        "en": "Download",
    },
    "documents.state_received": {
        "ar": "مُستلم",
        "en": "received",
    },
    "documents.state_quarantined": {
        "ar": "في الحجر",
        "en": "quarantined",
    },
    "documents.state_validating": {
        "ar": "قيد التحقق",
        "en": "validating",
    },
    "documents.state_ready_to_parse": {
        "ar": "جاهز للتحليل",
        "en": "ready to parse",
    },
    "documents.state_ocr_required": {
        "ar": "يحتاج تعرّفًا ضوئيًا",
        "en": "ocr required",
    },
    "documents.state_parsing": {
        "ar": "قيد التحليل",
        "en": "parsing",
    },
    "documents.state_ocr_running": {
        "ar": "تعرّف ضوئي جارٍ",
        "en": "ocr running",
    },
    "documents.state_normalizing": {
        "ar": "قيد التوحيد",
        "en": "normalizing",
    },
    "documents.state_indexing": {
        "ar": "قيد الفهرسة",
        "en": "indexing",
    },
    "documents.state_verifying": {
        "ar": "قيد التحقق النهائي",
        "en": "verifying",
    },
    "documents.state_ready": {
        "ar": "جاهز",
        "en": "ready",
    },
    "documents.state_retry_wait": {
        "ar": "بانتظار إعادة المحاولة",
        "en": "retry wait",
    },
    "documents.state_rejected": {
        "ar": "مرفوض",
        "en": "rejected",
    },
    "documents.state_cancelled": {
        "ar": "ملغى",
        "en": "cancelled",
    },
    "documents.state_dead_letter": {
        "ar": "متوقف نهائيًا",
        "en": "dead letter",
    },
    "documents.state_unknown": {
        "ar": "غير معروف",
        "en": "unknown",
    },
    "documents.cap_ready": {
        "ar": "جاهز",
        "en": "ready",
    },
    "documents.cap_degraded": {
        "ar": "متدهور",
        "en": "degraded",
    },
    "documents.cap_unavailable": {
        "ar": "غير متاح",
        "en": "unavailable",
    },
    "documents.cap_ok": {
        "ar": "سليم",
        "en": "ok",
    },
    "documents.cap_blocked": {
        "ar": "محجوب",
        "en": "blocked",
    },
    "documents.cap_critical": {
        "ar": "حرج",
        "en": "critical",
    },
    "documents.js.failed_to_load_documents": {
        "ar": "تعذّر تحميل المستندات",
        "en": "Failed to load documents",
    },
    "documents.js.admin_privileges_required_to_run": {
        "ar": "يتطلب تنظيف المساحة صلاحيات المسؤول",
        "en": "Admin privileges required to run garbage collection",
    },
    "documents.js.garbage_collection_dry_run_failed": {
        "ar": "فشلت التجربة المسبقة لتنظيف المساحة",
        "en": "Garbage-collection dry-run failed",
    },
    "documents.js.nothing_to_reclaim_the_store": {
        "ar": "لا شيء لاستعادته — المخزن نظيف بالفعل",
        "en": "Nothing to reclaim — the store is already clean",
    },
    "documents.js.run_garbage_collection": {
        "ar": "تشغيل تنظيف المساحة؟",
        "en": "Run garbage collection?",
    },
    "documents.js.run_gc": {
        "ar": "تشغيل التنظيف",
        "en": "Run GC",
    },
    "documents.js.garbage_collection_failed": {
        "ar": "فشل تنظيف المساحة",
        "en": "Garbage collection failed",
    },
    "documents.js.garbage_collection_error": {
        "ar": "خطأ في تنظيف المساحة",
        "en": "Garbage collection error",
    },
    "documents.js.uploaded_processing_started": {
        "ar": "رُفع — بدأت المعالجة",
        "en": "Uploaded — processing started",
    },
    "documents.js.failed_to_load_document": {
        "ar": "تعذّر تحميل المستند",
        "en": "Failed to load document",
    },
    "documents.js.failed_to_load_events": {
        "ar": "تعذّر تحميل الأحداث",
        "en": "Failed to load events",
    },
    "documents.js.cancellation_requested": {
        "ar": "طُلب الإلغاء",
        "en": "Cancellation requested",
    },
    "documents.js.cancel_failed": {
        "ar": "فشل الإلغاء",
        "en": "Cancel failed",
    },
    "documents.js.retry_enqueued": {
        "ar": "أُضيفت إعادة المحاولة إلى الطابور",
        "en": "Retry enqueued",
    },
    "documents.js.retry_failed": {
        "ar": "فشلت إعادة المحاولة",
        "en": "Retry failed",
    },
    "documents.js.choose_a_target_format": {
        "ar": "اختر صيغة الهدف",
        "en": "Choose a target format",
    },
    "documents.js.convert_failed": {
        "ar": "فشل التحويل",
        "en": "Convert failed",
    },
    "documents.js.pdf_info_failed": {
        "ar": "تعذّر جلب معلومات PDF",
        "en": "PDF info failed",
    },
    "documents.js.pdf_info_loaded": {
        "ar": "حُمّلت معلومات PDF",
        "en": "PDF info loaded",
    },
    "documents.js.split_failed": {
        "ar": "فشل التقسيم",
        "en": "Split failed",
    },
    "documents.js.redact_document": {
        "ar": "تنقيح المستند",
        "en": "Redact document",
    },
    "documents.js.enter_at_least_one_term": {
        "ar": "أدخل مصطلحًا واحدًا على الأقل",
        "en": "Enter at least one term",
    },
    "documents.js.confirm_redaction": {
        "ar": "تأكيد التنقيح",
        "en": "Confirm redaction",
    },
    "documents.js.redaction_failed": {
        "ar": "فشل التنقيح",
        "en": "Redaction failed",
    },
    "documents.js.delete_archive_document": {
        "ar": "حذف المستند / أرشفته؟",
        "en": "Delete / archive document?",
    },
    "documents.js.document_archived_soft_deleted": {
        "ar": "أُرشف المستند (حذف مرن)",
        "en": "Document archived (soft-deleted)",
    },
    "documents.js.delete_failed_network": {
        "ar": "فشل الحذف (الشبكة)",
        "en": "Delete failed (network)",
    },
    "documents.js.gc_confirm.zero": {
        "ar": "لم تجد التجربة المسبقة شيئًا للحذف (نحو {size} قابلة للاستعادة). لا يُزال أبدًا محتوى مُشار إليه ولا الإصدارات الحالية. هل تتابع؟",
        "en": "Dry-run found {n} items to delete (~{size} reclaimable). Referenced content and current versions are never removed. Proceed?",
    },
    "documents.js.gc_confirm.one": {
        "ar": "وجدت التجربة المسبقة عنصرًا واحدًا للحذف (نحو {size} قابلة للاستعادة). لا يُزال أبدًا محتوى مُشار إليه ولا الإصدارات الحالية. هل تتابع؟",
        "en": "Dry-run found 1 item to delete (~{size} reclaimable). Referenced content and current versions are never removed. Proceed?",
    },
    "documents.js.gc_confirm.two": {
        "ar": "وجدت التجربة المسبقة عنصرين للحذف (نحو {size} قابلة للاستعادة). لا يُزال أبدًا محتوى مُشار إليه ولا الإصدارات الحالية. هل تتابع؟",
        "en": "Dry-run found {n} items to delete (~{size} reclaimable). Referenced content and current versions are never removed. Proceed?",
    },
    "documents.js.gc_confirm.few": {
        "ar": "وجدت التجربة المسبقة {n} عناصر للحذف (نحو {size} قابلة للاستعادة). لا يُزال أبدًا محتوى مُشار إليه ولا الإصدارات الحالية. هل تتابع؟",
        "en": "Dry-run found {n} items to delete (~{size} reclaimable). Referenced content and current versions are never removed. Proceed?",
    },
    "documents.js.gc_confirm.many": {
        "ar": "وجدت التجربة المسبقة {n} عنصرًا للحذف (نحو {size} قابلة للاستعادة). لا يُزال أبدًا محتوى مُشار إليه ولا الإصدارات الحالية. هل تتابع؟",
        "en": "Dry-run found {n} items to delete (~{size} reclaimable). Referenced content and current versions are never removed. Proceed?",
    },
    "documents.js.gc_confirm.other": {
        "ar": "وجدت التجربة المسبقة {n} عنصر للحذف (نحو {size} قابلة للاستعادة). لا يُزال أبدًا محتوى مُشار إليه ولا الإصدارات الحالية. هل تتابع؟",
        "en": "Dry-run found {n} items to delete (~{size} reclaimable). Referenced content and current versions are never removed. Proceed?",
    },
    "documents.js.gc_done.zero": {
        "ar": "لم يستعد التنظيف أي كتلة، {size}",
        "en": "GC reclaimed {n} blobs, {size}",
    },
    "documents.js.gc_done.one": {
        "ar": "استعاد التنظيف كتلة واحدة، {size}",
        "en": "GC reclaimed 1 blob, {size}",
    },
    "documents.js.gc_done.two": {
        "ar": "استعاد التنظيف كتلتين، {size}",
        "en": "GC reclaimed {n} blobs, {size}",
    },
    "documents.js.gc_done.few": {
        "ar": "استعاد التنظيف {n} كتل، {size}",
        "en": "GC reclaimed {n} blobs, {size}",
    },
    "documents.js.gc_done.many": {
        "ar": "استعاد التنظيف {n} كتلة، {size}",
        "en": "GC reclaimed {n} blobs, {size}",
    },
    "documents.js.gc_done.other": {
        "ar": "استعاد التنظيف {n} كتلة، {size}",
        "en": "GC reclaimed {n} blobs, {size}",
    },
    "documents.js.upload_not_authed": {
        "ar": "فشل الرفع: غير مصادَق (أعد تسجيل الدخول / اضبط السر)",
        "en": "Upload failed: not authenticated (re-login / set secret)",
    },
    "documents.js.upload_http": {
        "ar": "فشل الرفع (HTTP {status})",
        "en": "Upload failed (HTTP {status})",
    },
    "documents.js.not_authed": {
        "ar": "غير مصادَق — أعد تسجيل الدخول أو تحقّق من KAZMA_SECRET",
        "en": "Not authenticated — re-login or check KAZMA_SECRET",
    },
    "documents.js.upload_failed_msg": {
        "ar": "فشل الرفع: {error}",
        "en": "Upload failed: {error}",
    },
    "documents.js.upload_failed_network": {
        "ar": "فشل الرفع (الشبكة)",
        "en": "Upload failed (network)",
    },
    "documents.js.artifact_ready": {
        "ar": "{label} — المخرج جاهز",
        "en": "{label} — artifact ready",
    },
    "documents.js.split_label": {
        "ar": "التقسيم",
        "en": "Split",
    },
    "documents.js.redaction_label": {
        "ar": "التنقيح",
        "en": "Redaction",
    },
    "documents.js.op_complete": {
        "ar": "اكتمل {label}",
        "en": "{label} complete",
    },
    "documents.js.redact_prompt": {
        "ar": "أدخل المصطلحات المراد تنقيحها (مفصولة بفواصل). ينشئ التنقيح مخرجًا جديدًا غير قابل للتعديل. ملفات PDF المختلطة (صور/متجهات) تُرفض احتياطًا.",
        "en": "Enter terms to redact (comma-separated). Redaction creates a new immutable artifact. Mixed image/vector PDFs fail closed and are refused.",
    },
    "documents.js.redact_ph": {
        "ar": "مثل رقم الحساب أو رقم الهوية",
        "en": "e.g. account number, SSN",
    },
    "documents.js.redact_confirm.zero": {
        "ar": "تنقيح المصطلحات فعليًا؟ ينتج هذا مخرجًا جديدًا متحققًا منه بشكل مستقل وغير قابل للتعديل، ولا يغيّر الأصل.",
        "en": "Physically redact {n} terms? This produces a new, independently-verified immutable artifact and cannot alter the original.",
    },
    "documents.js.redact_confirm.one": {
        "ar": "تنقيح مصطلح واحد فعليًا؟ ينتج هذا مخرجًا جديدًا متحققًا منه بشكل مستقل وغير قابل للتعديل، ولا يغيّر الأصل.",
        "en": "Physically redact 1 term? This produces a new, independently-verified immutable artifact and cannot alter the original.",
    },
    "documents.js.redact_confirm.two": {
        "ar": "تنقيح مصطلحين فعليًا؟ ينتج هذا مخرجًا جديدًا متحققًا منه بشكل مستقل وغير قابل للتعديل، ولا يغيّر الأصل.",
        "en": "Physically redact {n} terms? This produces a new, independently-verified immutable artifact and cannot alter the original.",
    },
    "documents.js.redact_confirm.few": {
        "ar": "تنقيح {n} مصطلحات فعليًا؟ ينتج هذا مخرجًا جديدًا متحققًا منه بشكل مستقل وغير قابل للتعديل، ولا يغيّر الأصل.",
        "en": "Physically redact {n} terms? This produces a new, independently-verified immutable artifact and cannot alter the original.",
    },
    "documents.js.redact_confirm.many": {
        "ar": "تنقيح {n} مصطلحًا فعليًا؟ ينتج هذا مخرجًا جديدًا متحققًا منه بشكل مستقل وغير قابل للتعديل، ولا يغيّر الأصل.",
        "en": "Physically redact {n} terms? This produces a new, independently-verified immutable artifact and cannot alter the original.",
    },
    "documents.js.redact_confirm.other": {
        "ar": "تنقيح {n} مصطلح فعليًا؟ ينتج هذا مخرجًا جديدًا متحققًا منه بشكل مستقل وغير قابل للتعديل، ولا يغيّر الأصل.",
        "en": "Physically redact {n} terms? This produces a new, independently-verified immutable artifact and cannot alter the original.",
    },
    "documents.js.archive_confirm": {
        "ar": "أرشفة «{label}»؟\n\nيغادر المستند مكتبتك (حذف مرن). تُزال مدخلات فهرس البحث. تبقى البيانات الأصلية حتى يستعيد التنظيف المساحة غير المُشار إليها — ولا يمكن التراجع عن ذلك من الواجهة.",
        "en": "Archive \"{label}\"?\n\nThe document leaves your library (soft-delete). Any search index entries are removed. Original bytes stay until garbage collection reclaims unreferenced storage — this cannot be undone from the UI.",
    },
    "documents.js.converted_to": {
        "ar": "حُوّل إلى {format}",
        "en": "Converted to {format}",
    },
    "documents.js.delete_failed_http": {
        "ar": "فشل الحذف (HTTP {status})",
        "en": "Delete failed (HTTP {status})",
    },
    "documents.cap_disabled": {
        "ar": "معطّل",
        "en": "Disabled",
    },
    "documents.in_libraries": {
        "ar": "في المكتبات:",
        "en": "In libraries:",
    },
    "documents.in_no_library": {
        "ar": "ليس في أي مكتبة",
        "en": "Not in any library",
    },
    "documents.library_remove_from": {
        "ar": "إزالة من «{library}»",
        "en": "Remove from “{library}”",
    },
    "documents.library_remove_title": {
        "ar": "إزالة من المكتبة؟",
        "en": "Remove from library?",
    },
    "documents.library_remove_confirm": {
        "ar": "إزالة «{title}» من «{library}»؟ تتوقف مقاطعه عن الإجابة من تلك المكتبة، ويبقى المستند نفسه هنا.",
        "en": "Remove “{title}” from “{library}”? Its passages stop answering from that library; the document itself stays here.",
    },
    "documents.library_remove": {
        "ar": "إزالة",
        "en": "Remove",
    },
    "documents.library_removed": {
        "ar": "أُزيل من «{library}».",
        "en": "Removed from “{library}”.",
    },
    "documents.library_remove_failed": {
        "ar": "تعذّرت إزالته من المكتبة: {error}",
        "en": "Could not remove it from the library: {error}",
    },
}
