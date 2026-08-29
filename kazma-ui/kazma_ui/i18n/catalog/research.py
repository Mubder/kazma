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
}
