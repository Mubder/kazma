"""``research`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "research.about_browse": {
        "ar": "<strong>تصفّح</strong> أوراق المسار والجلسات الحية ومخرجات بحث السرب.",
        "en": "<strong>Browse</strong> pipeline papers, live sessions, and swarm research outputs.",
    },
    "research.about_compare": {
        "ar": "<strong>قارن</strong> تشغيلتي سرب جنباً إلى جنب — التكلفة والرموز والمدة ونص المخرج.",
        "en": "<strong>Compare</strong> two swarm runs side-by-side — cost, tokens, duration, and output text.",
    },
    "research.about_desc": {
        "ar": "تشغيلات البحث العميق من هذه اللوحة (أو المحادثة / <code>/research deep</code>) تنتج تقارير متعددة المصادر. مهام بحث السرب تظهر هنا أيضاً بكامل قابلية التتبع:",
        "en": "Deep research runs from this panel (or chat / <code>/research deep</code>) produce multi-source reports. Swarm research tasks also land here with full traceability:",
    },
    "research.about_export": {
        "ar": "<strong>صدّر</strong> أي نتيجة إلى DOCX أو PDF أو Markdown للاستخدام الأكاديمي أو التقارير.",
        "en": "<strong>Export</strong> any result to DOCX, PDF, or Markdown for academic or reporting use.",
    },
    "research.about_note": {
        "ar": "الجلسات العميقة: <code>kazma-data/research_sessions.db</code> + التقارير تحت <code>research/reports/</code>. مهام السرب: <code>metadata.kind=research</code> في TaskStore.",
        "en": "Deep sessions: <code>kazma-data/research_sessions.db</code> + reports under <code>research/reports/</code>. Swarm tasks: <code>metadata.kind=research</code> in TaskStore.",
    },
    "research.about_title": {
        "ar": "نتائج الأبحاث",
        "en": "Research Results",
    },
    "research.archive": {
        "ar": "أرشفة",
        "en": "Archive",
    },
    "research.archived": {
        "ar": "المؤرشف",
        "en": "Archived",
    },
    "research.archived_msg": {
        "ar": "تمت أرشفة البحث",
        "en": "Research archived",
    },
    "research.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "research.cancelled": {
        "ar": "أُلغي البحث",
        "en": "Research cancelled",
    },
    "research.compare_desc": {
        "ar": "قارن تشغيلتين بحثيتين لرؤية كيف تختلف المخرجات والتكلفة والمدة.",
        "en": "Compare two research runs to see how outputs, cost, and duration differ.",
    },
    "research.comparing": {
        "ar": "جارٍ المقارنة…",
        "en": "Comparing…",
    },
    "research.delta": {
        "ar": "الفرق",
        "en": "Delta",
    },
    "research.depth_brief": {
        "ar": "مختصر",
        "en": "Brief",
    },
    "research.depth_deep": {
        "ar": "عميق",
        "en": "Deep",
    },
    "research.source_chat": {
        "ar": "محادثة",
        "en": "Chat",
    },
    "research.depth_label": {
        "ar": "العمق",
        "en": "Depth",
    },
    "research.identical": {
        "ar": "النتائج متطابقة.",
        "en": "Results are identical.",
    },
    "research.metric": {
        "ar": "المقياس",
        "en": "Metric",
    },
    "research.no_archived": {
        "ar": "لا توجد أبحاث مؤرشفة.",
        "en": "No archived research.",
    },
    "research.no_results": {
        "ar": "لا توجد نتائج أبحاث بعد. اطلب من الوكيل البحث عن شيء ما باستخدام السرب.",
        "en": "No research results yet. Ask the agent to research something using the Swarm.",
    },
    "research.open_md": {
        "ar": "فتح التقرير",
        "en": "Open report",
    },
    "research.refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "research.restore": {
        "ar": "استعادة",
        "en": "Restore",
    },
    "research.restored_msg": {
        "ar": "تمت استعادة البحث",
        "en": "Research restored",
    },
    "research.run_a": {
        "ar": "التشغيلة أ",
        "en": "Run A",
    },
    "research.run_b": {
        "ar": "التشغيلة ب",
        "en": "Run B",
    },
    "research.search_placeholder": {
        "ar": "ابحث في الأبحاث...",
        "en": "Search research...",
    },
    "research.sources_label": {
        "ar": "أقصى مصادر",
        "en": "Max sources",
    },
    "research.start_btn": {
        "ar": "ابدأ",
        "en": "Start",
    },
    "research.start_done": {
        "ar": "اكتمل البحث",
        "en": "Research complete",
    },
    "research.start_error": {
        "ar": "فشل البحث",
        "en": "Research failed",
    },
    "research.start_running": {
        "ar": "البحث جارٍ…",
        "en": "Research running…",
    },
    "research.start_title": {
        "ar": "بدء بحث عميق",
        "en": "Start deep research",
    },
    "research.tab_about": {
        "ar": "حول",
        "en": "About",
    },
    "research.tab_archived": {
        "ar": "المؤرشف",
        "en": "Archived",
    },
    "research.tab_compare": {
        "ar": "مقارنة",
        "en": "Compare",
    },
    "research.tab_results": {
        "ar": "النتائج",
        "en": "Results",
    },
    "research.text_diff": {
        "ar": "فروقات النص",
        "en": "Text Diff",
    },
    "research.title": {
        "ar": "نتائج الأبحاث",
        "en": "Research Results",
    },
    "research.topic_label": {
        "ar": "الموضوع",
        "en": "Topic",
    },
    "research.topic_placeholder": {
        "ar": "مثال: تطور GIL في بايثون والتحرير من الخيوط",
        "en": "e.g. Python GIL evolution and free-threading",
    },
    "research.view_report": {
        "ar": "عرض التقرير",
        "en": "View report",
    },
    "research.exporting_to": {
        "ar": "جارٍ التصدير إلى {format}…",
        "en": "Exporting to {format}…",
    },
    "research.export_failed_error": {
        "ar": "فشل التصدير: {error}",
        "en": "Export failed: {error}",
    },
    "research.exported": {
        "ar": "صُدّر: {name}",
        "en": "Exported: {name}",
    },
    "research.papers_list_error": {
        "ar": "قائمة الأوراق: {error}",
        "en": "Papers list: {error}",
    },
    "research.ui.no_running_session": {
        "ar": "لا جلسة تعمل",
        "en": "No running session",
    },
    "research.ui.cancel_failed": {
        "ar": "فشل الإلغاء",
        "en": "Cancel failed",
    },
    "research.ui.enter_a_research_topic": {
        "ar": "أدخل موضوع البحث",
        "en": "Enter a research topic",
    },
    "research.ui.could_not_start_research": {
        "ar": "تعذّر بدء البحث",
        "en": "Could not start research",
    },
    "research.ui.session_not_found": {
        "ar": "الجلسة غير موجودة",
        "en": "Session not found",
    },
    "research.ui.deep_research": {
        "ar": "بحث معمّق",
        "en": "Deep research",
    },
    "research.ui.could_not_load_session": {
        "ar": "تعذّر تحميل الجلسة",
        "en": "Could not load session",
    },
    "research.ui.paper_not_found": {
        "ar": "الورقة غير موجودة",
        "en": "Paper not found",
    },
    "research.ui.paper": {
        "ar": "ورقة",
        "en": "Paper",
    },
    "research.ui.loading": {
        "ar": "جارٍ التحميل…",
        "en": "Loading…",
    },
    "research.ui.could_not_load_report_file": {
        "ar": "تعذّر تحميل ملف التقرير.",
        "en": "Could not load report file.",
    },
    "research.ui.could_not_load_paper": {
        "ar": "تعذّر تحميل الورقة",
        "en": "Could not load paper",
    },
    "research.ui.could_not_load": {
        "ar": "تعذّر التحميل",
        "en": "Could not load",
    },
    "research.ui.research": {
        "ar": "بحث",
        "en": "Research",
    },
    "research.ui.select_a_research_result_first": {
        "ar": "اختر نتيجة بحث أولًا",
        "en": "Select a research result first",
    },
    "research.ui.export_request_failed": {
        "ar": "فشل طلب التصدير",
        "en": "Export request failed",
    },
    "research.ui.delete_failed": {
        "ar": "فشل الحذف: ",
        "en": "Delete failed: ",
    },
    "research.ui.deleted": {
        "ar": "حُذف",
        "en": "Deleted",
    },
    "research.ui.delete_failed_2": {
        "ar": "فشل الحذف",
        "en": "Delete failed",
    },
    "research.ui.archive_failed": {
        "ar": "فشلت الأرشفة: ",
        "en": "Archive failed: ",
    },
    "research.ui.archive_failed_2": {
        "ar": "فشلت الأرشفة",
        "en": "Archive failed",
    },
    "research.ui.restore_failed": {
        "ar": "فشل الاسترجاع: ",
        "en": "Restore failed: ",
    },
    "research.ui.restore_failed_2": {
        "ar": "فشل الاسترجاع",
        "en": "Restore failed",
    },
    "research.ui.pick_two_runs": {
        "ar": "اختر تشغيلتين",
        "en": "Pick two runs",
    },
    "research.ui.compare_failed": {
        "ar": "فشلت المقارنة",
        "en": "Compare failed",
    },
    "research.ui.no_prompt": {
        "ar": "(بلا طلب)",
        "en": "(no prompt)",
    },
    "research.ui.kind_session": {
        "ar": "جلسة",
        "en": "session",
    },
    "research.ui.kind_pipeline": {
        "ar": "خط المعالجة",
        "en": "pipeline",
    },
    "research.ui.sources_n": {
        "ar": "المصادر: {n}",
        "en": "{n} sources",
    },
    "research.ui.rubric": {
        "ar": "التقييم {n}",
        "en": "rubric {n}",
    },
    "research.ui.rubric_passed": {
        "ar": "التقييم {n} ناجح",
        "en": "rubric {n} passed",
    },
    "research.ui.rubric_failed": {
        "ar": "التقييم {n} راسب",
        "en": "rubric {n} failed",
    },
    "research.ui.paper_tag": {
        "ar": "ورقة",
        "en": "Paper",
    },
    "research.ui.state_done": {
        "ar": "تم",
        "en": "done",
    },
    "research.ui.state_complete": {
        "ar": "اكتمل",
        "en": "complete",
    },
    "research.ui.state_running": {
        "ar": "قيد التشغيل",
        "en": "running",
    },
    "research.ui.state_pending": {
        "ar": "قيد الانتظار",
        "en": "pending",
    },
    "research.ui.state_error": {
        "ar": "خطأ",
        "en": "error",
    },
    "research.ui.state_failed": {
        "ar": "فشل",
        "en": "failed",
    },
    "research.ui.state_cancelled": {
        "ar": "أُلغي",
        "en": "cancelled",
    },
    "research.ui.state_paper": {
        "ar": "ورقة",
        "en": "paper",
    },
    "research.ui.state_queued": {
        "ar": "في الطابور",
        "en": "queued",
    },
    "research.ui.state_start": {
        "ar": "البدء",
        "en": "start",
    },
    "research.ui.state_preflight": {
        "ar": "فحص مسبق",
        "en": "preflight",
    },
    "research.ui.state_plan": {
        "ar": "التخطيط",
        "en": "plan",
    },
    "research.ui.state_discover": {
        "ar": "الاستكشاف",
        "en": "discover",
    },
    "research.ui.state_acquire": {
        "ar": "جلب الصفحات",
        "en": "acquire",
    },
    "research.ui.state_map": {
        "ar": "تلخيص المصادر",
        "en": "map",
    },
    "research.ui.state_reduce": {
        "ar": "التوليف",
        "en": "reduce",
    },
    "research.ui.state_verify": {
        "ar": "التحقق",
        "en": "verify",
    },
    "research.ui.state_assemble": {
        "ar": "كتابة التقرير",
        "en": "assemble",
    },
    "research.ui.state_export": {
        "ar": "التصدير",
        "en": "export",
    },
    "research.ui.detail_session": {
        "ar": "جلسة",
        "en": "Session",
    },
    "research.ui.detail_stage": {
        "ar": "المرحلة: {stage}",
        "en": "Stage: {stage}",
    },
    "research.ui.detail_sources": {
        "ar": "المصادر: {n}",
        "en": "Sources: {n}",
    },
    "research.ui.detail_paper": {
        "ar": "ورقة من خط المعالجة",
        "en": "Pipeline paper",
    },
    "research.ui.no_output_yet": {
        "ar": "(لا مخرجات بعد)",
        "en": "(no output yet)",
    },
    "research.ui.archive": {
        "ar": "أرشفة",
        "en": "Archive",
    },
    "research.ui.restore": {
        "ar": "استعادة",
        "en": "Restore",
    },
    "research.ui.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "research.ui.delete_confirm": {
        "ar": "حذف نتيجة البحث هذه؟",
        "en": "Delete this research result?",
    },
}
