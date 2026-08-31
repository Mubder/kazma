"""``packages`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "packages.col_description": {
        "ar": "الوصف",
        "en": "Description",
    },
    "packages.col_package": {
        "ar": "الحزمة",
        "en": "Package",
    },
    "packages.col_version": {
        "ar": "الإصدار",
        "en": "Version",
    },
    "packages.complete_install": {
        "ar": "إكمال التثبيت",
        "en": "Complete install",
    },
    "packages.core_deps": {
        "ar": "التبعيات الأساسية",
        "en": "Core Dependencies",
    },
    "packages.db_backend": {
        "ar": "خلفية قاعدة البيانات النشطة",
        "en": "Active DB backend",
    },
    "packages.db_postgres": {
        "ar": "Postgres (متعدد النسخ)",
        "en": "Postgres (multi-replica)",
    },
    "packages.db_sqlite": {
        "ar": "SQLite (عقدة محلية واحدة)",
        "en": "SQLite (local single-node)",
    },
    "packages.extra.convert.desc": {
        "ar": "تحويل WeasyPrint. يحتاج خطوط النظام.",
        "en": "WeasyPrint conversion. Needs OS fonts.",
    },
    "packages.extra.convert.title": {
        "ar": "HTML/Markdown ← PDF",
        "en": "HTML/Markdown → PDF",
    },
    "packages.extra.database.desc": {
        "ar": "مشغّلات MySQL/Mongo لمهارة عميل قاعدة البيانات.",
        "en": "MySQL/Mongo drivers for database_client skill.",
    },
    "packages.extra.database.title": {
        "ar": "مشغّلات قواعد بيانات إضافية",
        "en": "Extra DB drivers",
    },
    "packages.extra.dev.desc": {
        "ar": "أدوات التطوير — اختبار (pytest) وفحص (ruff) وأنواع (mypy) وتحميل (locust) وخطافات Git (pre-commit).",
        "en": "Development tools — testing (pytest), linting (ruff), type checking (mypy), load testing (locust), git hooks (pre-commit).",
    },
    "packages.extra.dev.title": {
        "ar": "التطوير",
        "en": "Development",
    },
    "packages.extra.docling.desc": {
        "ar": "استخراج Docling المحلي لملفات PDF الصعبة بعد PyMuPDF. اختياري.",
        "en": "Local Docling extract for hard PDFs after PyMuPDF. Optional; skip if unused.",
    },
    "packages.extra.docling.title": {
        "ar": "إنقاذ PDF عبر Docling",
        "en": "Docling PDF salvage",
    },
    "packages.extra.document.desc": {
        "ar": "توليد PDF/DOCX/XLSX لمهارة توليد المستندات.",
        "en": "PDF/DOCX/XLSX generation for document_generator skill.",
    },
    "packages.extra.document.title": {
        "ar": "توليد المستندات",
        "en": "Document generation",
    },
    "packages.extra.document_platform.desc": {
        "ar": "تحليل وتنقيح وعرض (PyMuPDF + PDFium) مع إضافات المستند/OCR/التحويل.",
        "en": "Parse/redact/render (PyMuPDF + PDFium) plus document/ocr/convert extras.",
    },
    "packages.extra.document_platform.title": {
        "ar": "محركات ذكاء المستندات",
        "en": "Document Intelligence engines",
    },
    "packages.extra.durable.desc": {
        "ar": "إرسال السرب عبر Temporal (استئناف بعد الانهيار). يحتاج KAZMA_TEMPORAL_HOST. الافتراضي داخل العملية.",
        "en": "Temporal-wrapped swarm dispatch (crash-resume). Needs KAZMA_TEMPORAL_HOST. Default is in-process.",
    },
    "packages.extra.durable.title": {
        "ar": "سرب Temporal الدائم",
        "en": "Temporal durable swarm",
    },
    "packages.extra.index.desc": {
        "ar": "قواعد tree-sitter لـ codebase_search. البحث بالتعبير النمطي يعمل بدون هذه الإضافة.",
        "en": "tree-sitter grammars for codebase_search. Regex fallback works without this extra.",
    },
    "packages.extra.index.title": {
        "ar": "فهرس الشيفرة",
        "en": "Codebase index",
    },
    "packages.extra.observability.desc": {
        "ar": "تصدير مقاييس Prometheus لمراقبة الإنتاج.",
        "en": "Prometheus metrics export for production monitoring.",
    },
    "packages.extra.observability.title": {
        "ar": "المراقبة",
        "en": "Observability",
    },
    "packages.extra.ocr.desc": {
        "ar": "Tesseract للمستندات الممسوحة. ثبّت tesseract-ocr على النظام أيضاً.",
        "en": "Tesseract OCR for scanned documents. Also install system tesseract-ocr.",
    },
    "packages.extra.ocr.title": {
        "ar": "التعرّف الضوئي",
        "en": "OCR",
    },
    "packages.extra.postgres.desc": {
        "ar": "حالة مشتركة متعددة النسخ: ConfigStore والجلسات ومهام السرب ونقاط تحقق LangGraph على Postgres.",
        "en": "Multi-replica shared state: ConfigStore, sessions, swarm tasks, LangGraph checkpoints on Postgres.",
    },
    "packages.extra.postgres.title": {
        "ar": "Postgres (متعدد النسخ)",
        "en": "Postgres (multi-replica)",
    },
    "packages.extra.push.desc": {
        "ar": "pywebpush لإشعارات اكتمال الدورة. تتعطل الميزة تلقائياً إن غابت الحزمة.",
        "en": "pywebpush for turn-complete notifications. Feature self-disables if missing.",
    },
    "packages.extra.push.title": {
        "ar": "إشعارات الويب",
        "en": "Web Push",
    },
    "packages.extra.rag.desc": {
        "ar": "ذاكرة V2: تضمينات sentence-transformers + متجهات sqlite-vec محلية. chromadb اختياري قديم وليس مطلوباً لـ V2.",
        "en": "V2 cognitive memory: sentence-transformers embeddings + sqlite-vec local vectors. chromadb is optional legacy, not required for V2.",
    },
    "packages.extra.rag.title": {
        "ar": "الذاكرة وRAG",
        "en": "Memory & RAG",
    },
    "packages.extra.sandbox.desc": {
        "ar": "تنفيذ بايثون في Firecracker عبر E2B. يحتاج E2B_API_KEY. الافتراضي يبقى التنفيذ المحلي.",
        "en": "Firecracker python_exec via E2B. Needs E2B_API_KEY. Default remains local exec.",
    },
    "packages.extra.sandbox.title": {
        "ar": "عزل E2B",
        "en": "E2B sandbox",
    },
    "packages.extra.test.desc": {
        "ar": "تبعيات الاختبار (أخف من dev). تشمل pytest و fakeredis.",
        "en": "Test-specific dependencies (lighter than dev). Includes pytest + fakeredis.",
    },
    "packages.extra.test.title": {
        "ar": "اختبار",
        "en": "Test",
    },
    "packages.extra.tui.desc": {
        "ar": "واجهة الطرفية (Textual) مع نص ثنائي الاتجاه (python-bidi).",
        "en": "Terminal dashboard UI (Textual) with RTL/bidirectional text (python-bidi).",
    },
    "packages.extra.tui.title": {
        "ar": "لوحة TUI",
        "en": "TUI dashboard",
    },
    "packages.extra.web.desc": {
        "ar": "أتمتة المتصفح عبر Playwright لصفحات JavaScript.",
        "en": "Browser automation via Playwright for JS-heavy pages.",
    },
    "packages.extra.web.title": {
        "ar": "أتمتة المتصفح",
        "en": "Browser automation",
    },
    "packages.extras_active": {
        "ar": "إضافات نشطة",
        "en": "Extras Active",
    },
    "packages.extras_missing": {
        "ar": "مفقودة",
        "en": "Missing",
    },
    "packages.install_all_desc": {
        "ar": "لتثبيت جميع التبعيات الاختيارية دفعة واحدة:",
        "en": "To install all optional dependencies at once:",
    },
    "packages.install_all_title": {
        "ar": "تثبيت الكل",
        "en": "Install Everything",
    },
    "packages.install_btn": {
        "ar": "تثبيت",
        "en": "Install",
    },
    "packages.installed": {
        "ar": "حزمة مثبتة",
        "en": "packages installed",
    },
    "packages.installed_badge": {
        "ar": "مثبتة",
        "en": "Installed",
    },
    "packages.installing": {
        "ar": "جارٍ التثبيت…",
        "en": "Installing…",
    },
    "packages.layer.auto_store": {
        "ar": "تخزين تلقائي",
        "en": "Auto-store",
    },
    "packages.layer.consolidation": {
        "ar": "الموحّد",
        "en": "Consolidator",
    },
    "packages.layer.embedder": {
        "ar": "المضمّن",
        "en": "Embedder",
    },
    "packages.layer.layer_l1": {
        "ar": "L1 كروما",
        "en": "L1 Chroma",
    },
    "packages.layer.layer_l2": {
        "ar": "L2 الرسم",
        "en": "L2 Graph",
    },
    "packages.layer.layer_l3": {
        "ar": "L3 FTS5",
        "en": "L3 FTS5",
    },
    "packages.layer.layer_l4": {
        "ar": "L4 sqlite-vec",
        "en": "L4 sqlite-vec",
    },
    "packages.layer.per_turn_retrieval": {
        "ar": "RAG لكل دورة",
        "en": "Per-turn RAG",
    },
    "packages.layer.vector_memory": {
        "ar": "VectorMemory",
        "en": "VectorMemory",
    },
    "packages.memory_hint": {
        "ar": "لاسترجاع المتجهات بالكامل ثبّت إضافة rag:",
        "en": "For full vector recall install the rag extra:",
    },
    "packages.memory_issues": {
        "ar": "مشاكل الذاكرة",
        "en": "Memory issues",
    },
    "packages.memory_stack": {
        "ar": "مكوّن الذاكرة (مباشر)",
        "en": "Memory stack (live)",
    },
    "packages.missing_badge": {
        "ar": "مفقودة",
        "en": "Missing",
    },
    "packages.no_match": {
        "ar": "لا توجد حزم مطابقة",
        "en": "No matching packages",
    },
    "packages.optional_deps": {
        "ar": "التبعيات الاختيارية",
        "en": "Optional Dependencies",
    },
    "packages.optional_deps_hint": {
        "ar": "الإضافات مرتبة حسب الأولوية. Memory & RAG أولاً — ثبّتها لمتجهات L1/L4.",
        "en": "Extras are sorted by priority. Memory & RAG is first — install it for L1/L4 vectors.",
    },
    "packages.partial_badge": {
        "ar": "جزئي",
        "en": "Partial",
    },
    "packages.search_ph": {
        "ar": "ابحث في الحزم…",
        "en": "Search packages…",
    },
    "packages.title": {
        "ar": "الحزم والتبعيات",
        "en": "Packages & Dependencies",
    },
}
