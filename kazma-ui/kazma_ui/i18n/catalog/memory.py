"""``memory`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "memory.archive_dead": {
        "ar": "أرشفة الحقائق المنتهية (الملغاة أو المستبدلة، حتى 2000 في كل تشغيل)",
        "en": "Archive ended facts (invalidated or superseded, up to 2000 a run)",
    },
    "memory.err.not_found": {
        "ar": "غير موجود: ربما تغيّر أو حُذف في الأثناء",
        "en": "Not found: it may have been changed or removed meanwhile",
    },
    "memory.err.protected": {
        "ar": "هذا الكيان محمي",
        "en": "This entity is protected",
    },
    "memory.page.hygiene_partial": {
        "ar": "فشلت بعض عمليات الترتيب",
        "en": "Some clean-ups failed",
    },
    "memory.page.hygiene_more_left": {
        "ar": "تم الترتيب، وبقيت حقائق منتهية للمرة القادمة",
        "en": "Clean-up done; more ended facts are left for the next run",
    },
    "memory.console.forgotten_remote_pending": {
        "ar": "نُسيت. تُزال النسخة الموجودة في فهرس المتجهات البعيد في جولة الصيانة التالية.",
        "en": "Forgotten. The copy in the remote vector index is removed at the next maintenance pass.",
    },
    "memory.mc.search_memories": {
        "ar": "ابحث في الذكريات بكلماتها",
        "en": "Search memories by their words",
    },
    "memory.mc.newer": {
        "ar": "الأحدث",
        "en": "Newer",
    },
    "memory.mc.older": {
        "ar": "الأقدم",
        "en": "Older",
    },
    "memory.console.page_range": {
        "ar": "{from}–{to} من {total}",
        "en": "{from}–{to} of {total}",
    },
    "memory.console.no_memory_matches": {
        "ar": "لا توجد ذكرى تحمل هذه الكلمات.",
        "en": "No memory holds these words.",
    },
    "memory.console.hit_summary": {
        "ar": "ملخص أسبوعي",
        "en": "WEEKLY SUMMARY",
    },
    "memory.console.probe_degraded": {
        "ar": "البحث بالمعنى غير متاح: مطابقات الكلمات فقط.",
        "en": "Meaning search was unavailable: keyword matches only.",
    },
    "memory.console.memory_search_failed": {
        "ar": "تعذّر البحث في الذاكرة: هذه ليست نتيجة فارغة.",
        "en": "Memory search failed: this is not an empty result.",
    },
    "memory.console.knowledge_search_failed": {
        "ar": "تعذّر البحث في المكتبة المعرفية: هذه ليست نتيجة فارغة.",
        "en": "Knowledge search failed: this is not an empty result.",
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
    "memory.mc.embeddings": {
        "ar": "التضمينات",
        "en": "Embeddings",
    },
    "memory.mc.vector_backend": {
        "ar": "خلفية المتجهات",
        "en": "vector backend",
    },
    "memory.mc.fts_index": {
        "ar": "فهرس النص الكامل",
        "en": "FTS index",
    },
    "memory.mc.episodes_working": {
        "ar": "الحلقات + العاملة",
        "en": "episodes + working",
    },
    "memory.mc.belief_graph": {
        "ar": "رسم المعتقدات",
        "en": "Belief graph",
    },
    "memory.mc.active_triples": {
        "ar": "الثلاثيات النشطة",
        "en": "active triples",
    },
    "memory.mc.expand_all": {
        "ar": "توسيع الكل",
        "en": "Expand all",
    },
    "memory.mc.expand_or_collapse_all_health": {
        "ar": "توسيع مجموعات الصحة كلها أو طيّها",
        "en": "Expand or collapse all health groups",
    },
    "memory.mc.v2_cognitive_engine": {
        "ar": "المحرك المعرفي V2",
        "en": "V2 Cognitive Engine",
    },
    "memory.mc.loading_v2_status": {
        "ar": "جارٍ تحميل حالة V2…",
        "en": "Loading V2 status…",
    },
    "memory.mc.active_beliefs": {
        "ar": "المعتقدات النشطة",
        "en": "Active Beliefs",
    },
    "memory.mc.recall_episodes": {
        "ar": "حلقات الاسترجاع",
        "en": "Recall Episodes",
    },
    "memory.mc.queue": {
        "ar": "الطابور",
        "en": "Queue",
    },
    "memory.mc.memory_needs_attention": {
        "ar": "الذاكرة تحتاج إلى انتباه.",
        "en": "Memory needs attention.",
    },
    "memory.mc.jump_to_queue": {
        "ar": "انتقل إلى الطابور ↓",
        "en": "Jump to queue ↓",
    },
    "memory.mc.post_turn": {
        "ar": "بعد الدور",
        "en": "Post-turn",
    },
    "memory.mc.last_error": {
        "ar": "آخر خطأ: –",
        "en": "last error: –",
    },
    "memory.mc.embedder": {
        "ar": "المُضمِّن: –",
        "en": "embedder: –",
    },
    "memory.mc.reconsol": {
        "ar": "إعادة الدمج: –",
        "en": "reconsol: –",
    },
    "memory.mc.graph": {
        "ar": "الرسم: –",
        "en": "graph: –",
    },
    "memory.mc.backends": {
        "ar": "الخلفيات: –",
        "en": "backends: –",
    },
    "memory.mc.memory_settings": {
        "ar": "إعدادات الذاكرة ←",
        "en": "Memory settings →",
    },
    "memory.mc.v2_belief_topology": {
        "ar": "طوبولوجيا معتقدات V2",
        "en": "V2 Belief Topology",
    },
    "memory.mc.filter": {
        "ar": "تصفية…",
        "en": "filter…",
    },
    "memory.mc.path_from_query": {
        "ar": "المسار من الاستعلام",
        "en": "Path from query",
    },
    "memory.mc.highlight_entities_from_last_probe": {
        "ar": "إبراز الكيانات من آخر فحص/بحث موحّد",
        "en": "Highlight entities from last probe/federated search",
    },
    "memory.mc.edges_accent_blue": {
        "ar": "الحواف · أزرق مميّز",
        "en": "edges · accent blue",
    },
    "memory.mc.ops": {
        "ar": "العمليات",
        "en": "Ops",
    },
    "memory.mc.src": {
        "ar": "المصدر: —",
        "en": "src: —",
    },
    "memory.mc.source_entity": {
        "ar": "الكيان المصدر",
        "en": "Source entity",
    },
    "memory.mc.tgt": {
        "ar": "الهدف: —",
        "en": "tgt: —",
    },
    "memory.mc.target_entity": {
        "ar": "الكيان الهدف",
        "en": "Target entity",
    },
    "memory.mc.predicate": {
        "ar": "المحمول",
        "en": "predicate",
    },
    "memory.mc.link_predicate": {
        "ar": "محمول الربط",
        "en": "Link predicate",
    },
    "memory.mc.link": {
        "ar": "ربط",
        "en": "Link",
    },
    "memory.mc.link_source_pred_target_or": {
        "ar": "اربط المصدر —المحمول→ الهدف (أو ادخل وضع الاختيار)",
        "en": "Link source —pred→ target (or enter pick mode)",
    },
    "memory.mc.merge": {
        "ar": "دمج",
        "en": "Merge",
    },
    "memory.mc.merge_source_into_target": {
        "ar": "ادمج المصدر في الهدف",
        "en": "Merge source into target",
    },
    "memory.mc.swap_source_target": {
        "ar": "بدّل المصدر ↔ الهدف",
        "en": "Swap source ↔ target",
    },
    "memory.mc.clear_source_target_and_cancel": {
        "ar": "امسح المصدر/الهدف وألغِ وضع الاختيار",
        "en": "Clear source/target and cancel pick mode",
    },
    "memory.mc.click_node_inspect_click_edge": {
        "ar": "انقر عقدة ← فحص. انقر حافة ← تعديل/فك الربط.",
        "en": "Click node → inspect. Click edge → edit/unlink.",
    },
    "memory.mc.pred": {
        "ar": "المحمول",
        "en": "pred",
    },
    "memory.mc.v2_belief_topology_graph_arrow": {
        "ar": "رسم طوبولوجيا معتقدات V2. الأسهم للتحريك، والزائد والناقص للتكبير، وHome لإعادة العرض. انقر الحواف لتعديل المعتقدات أو فك ربطها.",
        "en": "V2 belief topology graph. Arrow keys pan, plus minus zoom, Home resets view. Click edges to edit or unlink beliefs.",
    },
    "memory.mc.no_beliefs_yet_the_graph": {
        "ar": "لا معتقدات بعد — يمتلئ الرسم كلما تعلّم Kazma.",
        "en": "No beliefs yet — the graph populates as Kazma learns.",
    },
    "memory.mc.teach_me_a_fact": {
        "ar": "علّمني حقيقة ←",
        "en": "Teach me a fact →",
    },
    "memory.mc.remember_my_favorite_color_is": {
        "ar": "تذكّر أن لوني المفضل هو الأزرق المخضر.",
        "en": "Remember my favorite color is teal.",
    },
    "memory.mc.click_a_node_or_edge": {
        "ar": "انقر عقدة أو حافة لفحصها.",
        "en": "Click a node or edge to inspect.",
    },
    "memory.mc.pan_zoom_edge_click_edit": {
        "ar": "←↑↓→ تحريك · +/− تكبير · نقر الحافة = تعديل · Esc إلغاء · Home إعادة",
        "en": "←↑↓→ pan · +/− zoom · edge click = edit · Esc cancel · Home reset",
    },
    "memory.mc.predicates": {
        "ar": "المحمولات",
        "en": "Predicates",
    },
    "memory.mc.tiers_procedural": {
        "ar": "المستويات · الإجرائية",
        "en": "Tiers · procedural",
    },
    "memory.mc.bi_temporal_scrub": {
        "ar": "تمرير زمني ثنائي",
        "en": "Bi-temporal scrub",
    },
    "memory.mc.play_scrub": {
        "ar": "تشغيل التمرير",
        "en": "Play scrub",
    },
    "memory.mc.known_beliefs": {
        "ar": "المعتقدات المعروفة",
        "en": "Known beliefs",
    },
    "memory.mc.currently_valid_click_to_focus": {
        "ar": "السارية حاليًا · انقر للتركيز على الرسم",
        "en": "currently valid · click to focus on graph",
    },
    "memory.mc.filter_2": {
        "ar": "تصفية…",
        "en": "filter…",
    },
    "memory.mc.no_beliefs_yet_teach_kazma": {
        "ar": "لا معتقدات بعد — علّم Kazma حقيقة.",
        "en": "No beliefs yet — teach Kazma a fact.",
    },
    "memory.mc.teach_me_a_fact_2": {
        "ar": "علّمني حقيقة ←",
        "en": "Teach me a fact →",
    },
    "memory.mc.belief_detail": {
        "ar": "تفاصيل المعتقد",
        "en": "Belief detail",
    },
    "memory.mc.unlink_belief": {
        "ar": "فك ربط المعتقد",
        "en": "Unlink belief",
    },
    "memory.mc.what_color_do_i_like": {
        "ar": "ما لوني المفضل؟ / كيف تعمل مصادقة الواجهة؟",
        "en": "What color do I like? / How does the API auth work?",
    },
    "memory.mc.memory_only": {
        "ar": "الذاكرة فقط",
        "en": "Memory only",
    },
    "memory.mc.memory_knowledge_library": {
        "ar": "الذاكرة + المكتبة المعرفية",
        "en": "Memory + Knowledge Library",
    },
    "memory.mc.task_queue": {
        "ar": "طابور المهام",
        "en": "Task queue",
    },
    "memory.mc.clear_failed": {
        "ar": "مسح الفاشلة",
        "en": "Clear failed",
    },
    "memory.mc.delete_failed_dead_letter_tasks": {
        "ar": "حذف المهام الفاشلة المتوقفة نهائيًا",
        "en": "Delete failed dead-letter tasks",
    },
    "memory.mc.run_reconsolidation": {
        "ar": "تشغيل إعادة الدمج",
        "en": "Run reconsolidation",
    },
    "memory.mc.pending_entity_merges": {
        "ar": "دمج الكيانات المعلّق",
        "en": "Pending entity merges",
    },
    "memory.mc.procedural_skills": {
        "ar": "المهارات الإجرائية",
        "en": "Procedural skills",
    },
    "memory.mc.memory_quality": {
        "ar": "جودة الذاكرة",
        "en": "Memory quality",
    },
    "memory.mc.memories_of_conversations": {
        "ar": "ذكريات المحادثات",
        "en": "Memories of conversations",
    },
    "memory.mc.download_everything_kazma_remembers_as": {
        "ar": "نزّل ما يتذكره Kazma بصيغة JSON، والملف يذكر ما لا يتضمنه",
        "en": "Download what Kazma remembers, as JSON; the file names anything it leaves out",
    },
    "memory.mc.weekly_summaries": {
        "ar": "الملخصات الأسبوعية",
        "en": "Weekly summaries",
    },
    "memory.mc.changes_to_your_facts": {
        "ar": "تغييرات على ما أخبرت به Kazma",
        "en": "Changes to what you told Kazma",
    },
    "memory.mc.changes_hint": {
        "ar": "عندما يحلّ كلامك محلّ معلومة أخبرت بها Kazma، يظهر ذلك هنا ويمكنك التراجع عنه. وعندما لا يتأكد Kazma أن التغيير يخصّك، يُبقي المعلومة السابقة ويسألك هنا.",
        "en": "When something you said replaces a fact you told Kazma, it shows here and you can undo it. When Kazma is not sure a change is yours, it keeps the earlier fact and asks you here.",
    },
    "memory.mc.refresh_changes": {
        "ar": "تحديث التغييرات",
        "en": "Refresh changes",
    },
    "memory.mc.suggested_for_about_me": {
        "ar": "مقترح لـ«عني»",
        "en": "Suggested for About me",
    },
    "memory.mc.suggested_hint": {
        "ar": "أسطر يقترحها Kazma من معلومات قلتها بنفسك. لا يُضاف شيء إلى «عني» إلا إذا ضغطت «أضف»، ويمكنك تعديل «عني» في الإعدادات ← الذاكرة.",
        "en": "Lines Kazma suggests from facts you said yourself. Nothing joins About me unless you press Add; you can edit About me in Settings → Memory.",
    },
    "memory.mc.refresh_suggestions": {
        "ar": "تحديث الاقتراحات",
        "en": "Refresh suggestions",
    },
    "memory.mc.legend_you": {
        "ar": "أنت",
        "en": "You",
    },
    "memory.mc.legend_entity": {
        "ar": "كيان",
        "en": "entity",
    },
    "memory.pg.ops": {
        "ar": "العمليات",
        "en": "Ops",
    },
    "memory.pg.browse_entities_beliefs_merges_hygiene": {
        "ar": "تصفّح الكيانات والمعتقدات والدمج والتنظيف",
        "en": "Browse entities, beliefs, merges, hygiene",
    },
    "memory.pg.graph": {
        "ar": "الرسم ↑",
        "en": "Graph ↑",
    },
    "memory.pg.active_beliefs_select_to_invalidate": {
        "ar": "المعتقدات النشطة — اختر لإبطالها، أو استخدم إجراءات الصف للتركيز أو التعديل أو فك الربط.",
        "en": "Active beliefs — select to invalidate, or use row actions to focus, edit, or unlink.",
    },
    "memory.pg.select_all_beliefs": {
        "ar": "تحديد كل المعتقدات",
        "en": "Select all beliefs",
    },
    "memory.pg.subject": {
        "ar": "الموضوع",
        "en": "Subject",
    },
    "memory.pg.predicate": {
        "ar": "المحمول",
        "en": "Predicate",
    },
    "memory.pg.object": {
        "ar": "القيمة",
        "en": "Object",
    },
    "memory.pg.graph_2": {
        "ar": "الرسم",
        "en": "Graph",
    },
    "memory.pg.focus_on_graph": {
        "ar": "التركيز على الرسم",
        "en": "Focus on graph",
    },
    "memory.pg.edit_subject_predicate_object": {
        "ar": "تعديل الموضوع / المحمول / القيمة",
        "en": "Edit subject / predicate / object",
    },
    "memory.pg.load_more": {
        "ar": "تحميل المزيد",
        "en": "Load more",
    },
    "memory.pg.entities_click_a_row_to": {
        "ar": "الكيانات — انقر صفًا للتركيز عليه في الرسم؛ استخدم مصدر/هدف ثم شريط عمليات الرسم للربط أو الدمج.",
        "en": "Entities — click a row to focus it on the graph; use Src/Tgt then the graph Ops bar to link or merge.",
    },
    "memory.pg.links": {
        "ar": "الروابط",
        "en": "Links",
    },
    "memory.pg.flags": {
        "ar": "العلامات",
        "en": "Flags",
    },
    "memory.pg.empty": {
        "ar": "فارغ",
        "en": "empty",
    },
    "memory.pg.isolated": {
        "ar": "معزول",
        "en": "isolated",
    },
    "memory.pg.protected": {
        "ar": "محمي",
        "en": "protected",
    },
    "memory.pg.merged": {
        "ar": "مدمج",
        "en": "merged",
    },
    "memory.pg.merged_into": {
        "ar": "مدمج في",
        "en": "merged into",
    },
    "memory.pg.merged_into_hint": {
        "ar": "اسم دُمج في كيان آخر: يُبقي هذا الصف الاسم القديم مرتبطًا بالكيان الجديد، فلا تحذفه تنظيفات الذاكرة.",
        "en": "A name merged into another entity: this row keeps the old name reaching the new one, so memory clean-ups never remove it.",
    },
    "memory.pg.graph_3": {
        "ar": "الرسم",
        "en": "Graph",
    },
    "memory.pg.focus_on_graph_2": {
        "ar": "التركيز على الرسم",
        "en": "Focus on graph",
    },
    "memory.pg.change_display_name_id_stays": {
        "ar": "تغيير الاسم المعروض (يبقى المعرّف كما هو)",
        "en": "Change display name (id stays the same)",
    },
    "memory.pg.src_2": {
        "ar": "مصدر",
        "en": "Src",
    },
    "memory.pg.set_as_link_merge_source": {
        "ar": "اجعله مصدر الربط/الدمج (يتزامن مع الرسم)",
        "en": "Set as link/merge source (syncs to graph)",
    },
    "memory.pg.tgt_2": {
        "ar": "هدف",
        "en": "Tgt",
    },
    "memory.pg.set_as_link_merge_target": {
        "ar": "اجعله هدف الربط/الدمج (يتزامن مع الرسم)",
        "en": "Set as link/merge target (syncs to graph)",
    },
    "memory.pg.load_more_2": {
        "ar": "تحميل المزيد",
        "en": "Load more",
    },
    "memory.pg.tier": {
        "ar": "المستوى",
        "en": "Tier",
    },
    "memory.pg.conf": {
        "ar": "الثقة",
        "en": "Conf",
    },
    "memory.pg.load_more_3": {
        "ar": "تحميل المزيد",
        "en": "Load more",
    },
    "memory.pg.isolated_entities": {
        "ar": "الكيانات المعزولة",
        "en": "Isolated entities",
    },
    "memory.pg.link_merge_hint": {
        "ar": "للربط أو الدمج: انقر زر <strong>مصدر</strong>/<strong>هدف</strong> في الصف (أو Shift+نقر على عقد الرسم)، ثم استخدم <strong>شريط عمليات الرسم</strong> <a href=\"#console\" style=\"text-decoration:none;\">↑</a>. انقر <em>حافة</em> لتعديل معتقد أو فك ربطه.",
        "en": "To link or merge: click a row's <strong>Src</strong>/<strong>Tgt</strong> (or Shift-click nodes on the graph), then use the <strong>graph Ops bar</strong> <a href=\"#console\" style=\"text-decoration:none;\">↑</a>. Click an <em>edge</em> to edit or unlink a belief.",
    },
    "memory.pg.source_target_title": {
        "ar": "المصدر ← الهدف (نفّذ من شريط عمليات الرسم)",
        "en": "Source → target (actuate from the graph Ops bar)",
    },
    "memory.console.expand_all": {
        "ar": "توسيع الكل",
        "en": "Expand all",
    },
    "memory.console.collapse_all": {
        "ar": "طي الكل",
        "en": "Collapse all",
    },
    "memory.console.group_other": {
        "ar": "أخرى",
        "en": "Other",
    },
    "memory.console.problem_fix": {
        "ar": "ما العمل:",
        "en": "What to do:",
    },
    "memory.console.problem_open": {
        "ar": "افتح",
        "en": "Open",
    },
    "memory.console.group_ok": {
        "ar": "{ok}/{n} سليمة",
        "en": "{ok}/{n} OK",
    },
    "memory.console.group_warn.zero": {
        "ar": "لا تحذيرات",
        "en": "{n} warnings",
    },
    "memory.console.group_warn.one": {
        "ar": "تحذير واحد",
        "en": "1 warning",
    },
    "memory.console.group_warn.two": {
        "ar": "تحذيران",
        "en": "{n} warnings",
    },
    "memory.console.group_warn.few": {
        "ar": "{n} تحذيرات",
        "en": "{n} warnings",
    },
    "memory.console.group_warn.many": {
        "ar": "{n} تحذيرًا",
        "en": "{n} warnings",
    },
    "memory.console.group_warn.other": {
        "ar": "{n} تحذير",
        "en": "{n} warnings",
    },
    "memory.console.group_err.zero": {
        "ar": "لا أخطاء",
        "en": "{n} errors",
    },
    "memory.console.group_err.one": {
        "ar": "خطأ واحد",
        "en": "1 error",
    },
    "memory.console.group_err.two": {
        "ar": "خطآن",
        "en": "{n} errors",
    },
    "memory.console.group_err.few": {
        "ar": "{n} أخطاء",
        "en": "{n} errors",
    },
    "memory.console.group_err.many": {
        "ar": "{n} خطأً",
        "en": "{n} errors",
    },
    "memory.console.group_err.other": {
        "ar": "{n} خطأ",
        "en": "{n} errors",
    },
    "memory.console.group_off": {
        "ar": "{n} متوقفة",
        "en": "{n} off",
    },
    "memory.console.components_healthy.zero": {
        "ar": "المكوّنات السليمة: {ok} من {n}",
        "en": "{ok}/{n} components healthy",
    },
    "memory.console.components_healthy.one": {
        "ar": "المكوّنات السليمة: {ok} من {n}",
        "en": "{ok}/1 component healthy",
    },
    "memory.console.components_healthy.two": {
        "ar": "المكوّنات السليمة: {ok} من {n}",
        "en": "{ok}/{n} components healthy",
    },
    "memory.console.components_healthy.few": {
        "ar": "المكوّنات السليمة: {ok} من {n}",
        "en": "{ok}/{n} components healthy",
    },
    "memory.console.components_healthy.many": {
        "ar": "المكوّنات السليمة: {ok} من {n}",
        "en": "{ok}/{n} components healthy",
    },
    "memory.console.components_healthy.other": {
        "ar": "المكوّنات السليمة: {ok} من {n}",
        "en": "{ok}/{n} components healthy",
    },
    "memory.console.superseded_n": {
        "ar": "{n} مستبدَلة",
        "en": "{n} superseded",
    },
    "memory.console.working_n": {
        "ar": "{n} عاملة",
        "en": "{n} working",
    },
    "memory.console.total_entities": {
        "ar": "إجمالي الكيانات",
        "en": "total entities",
    },
    "memory.console.beliefs_n.zero": {
        "ar": "لا معتقدات",
        "en": "{n} beliefs",
    },
    "memory.console.beliefs_n.one": {
        "ar": "معتقد واحد",
        "en": "1 belief",
    },
    "memory.console.beliefs_n.two": {
        "ar": "معتقدان",
        "en": "{n} beliefs",
    },
    "memory.console.beliefs_n.few": {
        "ar": "{n} معتقدات",
        "en": "{n} beliefs",
    },
    "memory.console.beliefs_n.many": {
        "ar": "{n} معتقدًا",
        "en": "{n} beliefs",
    },
    "memory.console.beliefs_n.other": {
        "ar": "{n} معتقد",
        "en": "{n} beliefs",
    },
    "memory.console.nodes_n.zero": {
        "ar": "لا عقد",
        "en": "{n} nodes",
    },
    "memory.console.nodes_n.one": {
        "ar": "عقدة واحدة",
        "en": "1 node",
    },
    "memory.console.nodes_n.two": {
        "ar": "عقدتان",
        "en": "{n} nodes",
    },
    "memory.console.nodes_n.few": {
        "ar": "{n} عقد",
        "en": "{n} nodes",
    },
    "memory.console.nodes_n.many": {
        "ar": "{n} عقدة",
        "en": "{n} nodes",
    },
    "memory.console.nodes_n.other": {
        "ar": "{n} عقدة",
        "en": "{n} nodes",
    },
    "memory.console.edges_n.zero": {
        "ar": "لا حواف",
        "en": "{n} edges",
    },
    "memory.console.edges_n.one": {
        "ar": "حافة واحدة",
        "en": "1 edge",
    },
    "memory.console.edges_n.two": {
        "ar": "حافتان",
        "en": "{n} edges",
    },
    "memory.console.edges_n.few": {
        "ar": "{n} حواف",
        "en": "{n} edges",
    },
    "memory.console.edges_n.many": {
        "ar": "{n} حافة",
        "en": "{n} edges",
    },
    "memory.console.edges_n.other": {
        "ar": "{n} حافة",
        "en": "{n} edges",
    },
    "memory.console.entities_n.zero": {
        "ar": "لا كيانات",
        "en": "{n} entities",
    },
    "memory.console.entities_n.one": {
        "ar": "كيان واحد",
        "en": "1 entity",
    },
    "memory.console.entities_n.two": {
        "ar": "كيانان",
        "en": "{n} entities",
    },
    "memory.console.entities_n.few": {
        "ar": "{n} كيانات",
        "en": "{n} entities",
    },
    "memory.console.entities_n.many": {
        "ar": "{n} كيانًا",
        "en": "{n} entities",
    },
    "memory.console.entities_n.other": {
        "ar": "{n} كيان",
        "en": "{n} entities",
    },
    "memory.console.headline": {
        "ar": "التخزين: {config} (الإعدادات/الجلسات/السرب)؛ نقاط الحفظ: {checkpoints}. {engine}.",
        "en": "Persistence: {config} (config/sessions/swarm); checkpoints: {checkpoints}. {engine}.",
    },
    "memory.console.engine_ok": {
        "ar": "محرك الذاكرة يعمل",
        "en": "memory engine working",
    },
    "memory.console.engine_degraded": {
        "ar": "محرك الذاكرة به مشكلة أو متوقف",
        "en": "memory engine has a problem or is off",
    },
    "memory.console.demo_desc": {
        "ar": "عرض تجريبي: الذاكرة متوقفة هنا. Kazma المثبَّت يتذكر محادثاتك.",
        "en": "Demo: memory is off here. An installed Kazma remembers your conversations.",
    },
    "memory.console.demo_badge": {
        "ar": "تجريبي",
        "en": "DEMO",
    },
    "memory.console.install_failed_title": {
        "ar": "فشل التثبيت",
        "en": "Install failed",
    },
    "memory.console.chip_active": {
        "ar": "نشط",
        "en": "Active",
    },
    "memory.console.chip_dual_write": {
        "ar": "كتابة مزدوجة",
        "en": "Dual-write",
    },
    "memory.console.chip_degraded": {
        "ar": "متدهور",
        "en": "Degraded",
    },
    "memory.console.chip_off": {
        "ar": "متوقف",
        "en": "Off",
    },
    "memory.console.chip_live": {
        "ar": "مباشر",
        "en": "LIVE",
    },
    "memory.console.v2_no_db": {
        "ar": "لا قاعدة بيانات للذاكرة بعد. تبدأ مع أول محادثة.",
        "en": "Memory has no database yet. It starts with the first conversation.",
    },
    "memory.console.v2_live_desc": {
        "ar": "الذاكرة تعمل: كل رد يستدعي الحقائق وذكريات المحادثات.",
        "en": "Memory is on: each reply recalls facts and conversation memories.",
    },
    "memory.console.v2_off_desc": {
        "ar": "الذاكرة متوقفة في الإعدادات: لا تستدعي الردود شيئًا ولا يُحفظ شيء جديد.",
        "en": "Memory is off in Settings: replies recall nothing and nothing new is kept.",
    },
    "memory.console.beliefs_meta": {
        "ar": "{superseded} مستبدَلة · {archived} مؤرشفة",
        "en": "{superseded} superseded · {archived} archived",
    },
    "memory.console.episodes_meta": {
        "ar": "{episodic} حلقية · {archived} مؤرشفة",
        "en": "{episodic} episodic · {archived} archived",
    },
    "memory.console.procedural_meta": {
        "ar": "{active} نشطة · {quarantined} معزولة",
        "en": "{active} active · {quarantined} quarantined",
    },
    "memory.console.queue_meta.zero": {
        "ar": "{failed} فاشلة · لا تدقيقات/24 ساعة",
        "en": "{failed} failed · {n} audits/24h",
    },
    "memory.console.queue_meta.one": {
        "ar": "{failed} فاشلة · تدقيق واحد/24 ساعة",
        "en": "{failed} failed · 1 audit/24h",
    },
    "memory.console.queue_meta.two": {
        "ar": "{failed} فاشلة · تدقيقان/24 ساعة",
        "en": "{failed} failed · {n} audits/24h",
    },
    "memory.console.queue_meta.few": {
        "ar": "{failed} فاشلة · {n} تدقيقات/24 ساعة",
        "en": "{failed} failed · {n} audits/24h",
    },
    "memory.console.queue_meta.many": {
        "ar": "{failed} فاشلة · {n} تدقيقًا/24 ساعة",
        "en": "{failed} failed · {n} audits/24h",
    },
    "memory.console.queue_meta.other": {
        "ar": "{failed} فاشلة · {n} تدقيق/24 ساعة",
        "en": "{failed} failed · {n} audits/24h",
    },
    "memory.console.queue_admins_only": {
        "ar": "طابور التثبيت: للمشرفين فقط",
        "en": "install queue: admins only",
    },
    "memory.console.post_turn_none": {
        "ar": "بعد الدور: –",
        "en": "post-turn: –",
    },
    "memory.console.post_turn_ok": {
        "ar": "ناجحة: {ok} · فاشلة م/س/ع/ط: {m}/{e}/{d}/{q}",
        "en": "ok: {ok} · fail m/e/d/q: {m}/{e}/{d}/{q}",
    },
    "memory.console.post_turn_ok_title": {
        "ar": "خطوات ما بعد الدور الفاشلة: م = الكتابة إلى المرآة، س = استخراج الحقائق، "
              "ع = استخراج النموذج العميق (يُعاد لاحقًا)، ط = الطابور",
        "en": "Failed post-turn steps: m = memory write, e = fact extraction, "
              "d = the model's deep pass (retried later), q = queue",
    },
    "memory.console.last_error": {
        "ar": "آخر خطأ: {error}",
        "en": "last error: {error}",
    },
    "memory.console.last_error_none": {
        "ar": "آخر خطأ: لا يوجد",
        "en": "last error: none",
    },
    "memory.console.embedder_ready": {
        "ar": "المُضمِّن: جاهز",
        "en": "embedder: ready",
    },
    "memory.console.embedder_unavailable": {
        "ar": "المُضمِّن: غير متاح",
        "en": "embedder: unavailable",
    },
    "memory.console.embedder_not_loaded": {
        "ar": "المُضمِّن: يُحمَّل عند أول بحث",
        "en": "embedder: loads at the first search",
    },
    "memory.console.reconsol_none": {
        "ar": "إعادة الدمج: –",
        "en": "reconsol: –",
    },
    "memory.console.reconsol_done": {
        "ar": "إعادة الدمج: دُمج {merged} · تضمين {embedded}",
        "en": "reconsol: merged {merged} · emb {embedded}",
    },
    "memory.console.reconsol_never": {
        "ar": "إعادة الدمج: لم تُشغَّل بعد",
        "en": "reconsol: never",
    },
    "memory.console.graph_neo4j": {
        "ar": "الرسم: neo4j بكتابة مزدوجة · {state} · العرض من sqlite",
        "en": "graph: neo4j dual-write · {state} · paint sqlite",
    },
    "memory.console.graph_online": {
        "ar": "متصل",
        "en": "online",
    },
    "memory.console.graph_offline": {
        "ar": "غير متصل ← الرجوع إلى sqlite",
        "en": "offline→sqlite fallback",
    },
    "memory.console.graph_sqlite": {
        "ar": "الرسم: sqlite",
        "en": "graph: sqlite",
    },
    "memory.console.backends": {
        "ar": "الخلفيات: {mode}",
        "en": "backends: {mode}",
    },
    "memory.console.backends_vector": {
        "ar": " · المتجهات {provider}",
        "en": " · vector {provider}",
    },
    "memory.console.banner_failed.zero": {
        "ar": "لا مهام فاشلة في الطابور",
        "en": "{n} failed queue tasks",
    },
    "memory.console.banner_failed.one": {
        "ar": "مهمة فاشلة واحدة في الطابور",
        "en": "1 failed queue task",
    },
    "memory.console.banner_failed.two": {
        "ar": "مهمتان فاشلتان في الطابور",
        "en": "{n} failed queue tasks",
    },
    "memory.console.banner_failed.few": {
        "ar": "{n} مهام فاشلة في الطابور",
        "en": "{n} failed queue tasks",
    },
    "memory.console.banner_failed.many": {
        "ar": "{n} مهمة فاشلة في الطابور",
        "en": "{n} failed queue tasks",
    },
    "memory.console.banner_failed.other": {
        "ar": "{n} مهمة فاشلة في الطابور",
        "en": "{n} failed queue tasks",
    },
    "memory.console.banner_last_error": {
        "ar": "لم تُحفظ ذاكرة أحد الردود",
        "en": "a reply's memory was not saved",
    },
    "memory.console.banner_degraded": {
        "ar": "الذاكرة لا تعمل بالكامل",
        "en": "memory is not fully working",
    },
    "memory.console.banner_check": {
        "ar": "راجع الصحة والصيانة.",
        "en": "See Health and maintenance.",
    },
    "memory.console.tier_working": {
        "ar": "جديدة",
        "en": "New",
    },
    "memory.console.tier_episodic": {
        "ar": "محفوظة",
        "en": "Kept",
    },
    "memory.console.tier_recall": {
        "ar": "طُلب تذكّرها",
        "en": "Asked to remember",
    },
    "memory.console.tier_archived": {
        "ar": "مؤرشفة",
        "en": "Archived",
    },
    "memory.console.tier_short_working": {
        "ar": "جديدة",
        "en": "New",
    },
    "memory.console.tier_short_episodic": {
        "ar": "محفوظة",
        "en": "Kept",
    },
    "memory.console.tier_short_recall": {
        "ar": "مطلوبة",
        "en": "Asked",
    },
    "memory.console.tier_short_archived": {
        "ar": "مؤرشفة",
        "en": "Archived",
    },
    "memory.console.tier_title": {
        "ar": "{name}: {count} ({pct}%)",
        "en": "{name}: {count} ({pct}%)",
    },
    "memory.console.procedural_breakdown": {
        "ar": "المهارات: {active} نشطة · {quarantined} معزولة",
        "en": "Skills: {active} active · {quarantined} quarantined",
    },
    "memory.console.enter_query": {
        "ar": "اكتب استعلامًا.",
        "en": "Enter a query.",
    },
    "memory.console.probing": {
        "ar": "جارٍ الفحص…",
        "en": "Probing…",
    },
    "memory.console.probe_failed": {
        "ar": "فشل الفحص",
        "en": "Probe failed",
    },
    "memory.console.probe_error": {
        "ar": "خطأ في الفحص: {error}",
        "en": "Probe error: {error}",
    },
    "memory.console.hit_belief": {
        "ar": "معتقد",
        "en": "BELIEF",
    },
    "memory.console.hit_episode": {
        "ar": "حلقة",
        "en": "EPISODE",
    },
    "memory.console.score": {
        "ar": "الدرجة {n}",
        "en": "score {n}",
    },
    "memory.console.no_hits": {
        "ar": "لا نتائج.",
        "en": "No hits.",
    },
    "memory.console.channels_hint": {
        "ar": "القنوات: fts5 · dense · belief_ppr · session_boost (فعّل «شرح الاسترجاع» من الإعدادات ← الذاكرة)",
        "en": "Channels: fts5 · dense · belief_ppr · session_boost (enable Explain recall in Settings → Memory)",
    },
    "memory.console.show_path": {
        "ar": "اعرض المسار على الرسم ←",
        "en": "Show path on graph →",
    },
    "memory.console.run_golden_eval": {
        "ar": "فحص سريع للاسترجاع",
        "en": "Quick recall check",
    },
    "memory.console.loading": {
        "ar": "جارٍ التحميل…",
        "en": "Loading…",
    },
    "memory.console.not_found": {
        "ar": "غير موجود",
        "en": "Not found",
    },
    "memory.console.never": {
        "ar": "أبدًا",
        "en": "never",
    },
    "memory.console.from": {
        "ar": "من",
        "en": "from",
    },
    "memory.console.session": {
        "ar": "الجلسة",
        "en": "session",
    },
    "memory.console.recall_trail": {
        "ar": "استُدعيت {count} · آخر مرة {last} · المصدر {method}",
        "en": "Recalled {count} · last {last} · source {method}",
    },
    "memory.console.probe_from_belief": {
        "ar": "ما الذي سيستدعيه Kazma مع هذا؟",
        "en": "What would Kazma recall with this?",
    },
    "memory.console.belief_meta": {
        "ar": "الثقة {conf}% · {recalled}",
        "en": "Confidence {conf}% · {recalled}",
    },
    "memory.console.supersedes_chain": {
        "ar": "استبدلت ما سبقها: {chain}",
        "en": "Replaced earlier: {chain}",
    },
    "memory.console.load_failed": {
        "ar": "فشل التحميل",
        "en": "Load failed",
    },
    "memory.console.unlink_title": {
        "ar": "إزالة هذه الحقيقة؟",
        "en": "Remove this fact?",
    },
    "memory.console.unlink_message": {
        "ar": "يتوقف Kazma عن استخدامها. وتبقى في سجل الحقائق السابقة.",
        "en": "Kazma stops using it. It stays in the record of past facts.",
    },
    "memory.console.unlink_native": {
        "ar": "إزالة هذه الحقيقة؟",
        "en": "Remove this fact?",
    },
    "memory.console.unlink_failed": {
        "ar": "تعذّرت إزالة الحقيقة",
        "en": "Removing the fact failed",
    },
    "memory.console.queue_admins": {
        "ar": "طابور المهام خاص بالتثبيت: للمشرفين فقط.",
        "en": "The task queue is the install's: admins only.",
    },
    "memory.console.queue_empty": {
        "ar": "الطابور فارغ.",
        "en": "Queue empty.",
    },
    "memory.console.retry": {
        "ar": "إعادة المحاولة",
        "en": "retry",
    },
    "memory.console.attempts.zero": {
        "ar": "لا محاولات",
        "en": "{n} attempts",
    },
    "memory.console.attempts.one": {
        "ar": "محاولة واحدة",
        "en": "1 attempt",
    },
    "memory.console.attempts.two": {
        "ar": "محاولتان",
        "en": "{n} attempts",
    },
    "memory.console.attempts.few": {
        "ar": "{n} محاولات",
        "en": "{n} attempts",
    },
    "memory.console.attempts.many": {
        "ar": "{n} محاولة",
        "en": "{n} attempts",
    },
    "memory.console.attempts.other": {
        "ar": "{n} محاولة",
        "en": "{n} attempts",
    },
    # A memory queue task's status (the queue's pending/processing/completed/failed).
    "memory.console.queue_pending": {"ar": "بانتظار التنفيذ", "en": "pending"},
    "memory.console.link_mode_hint": {
        "ar": "الربط: انقر الشيء الأول، ثم الثاني",
        "en": "Connect: click the first thing, then the second",
    },
    "memory.console.merge_mode_hint": {
        "ar": "الدمج: انقر الذي سيختفي، ثم الذي سيبقى",
        "en": "Join: click the one that goes, then the one to keep",
    },
    "memory.console.queue_processing": {"ar": "قيد التنفيذ", "en": "running"},
    "memory.console.queue_completed": {"ar": "اكتمل", "en": "completed"},
    "memory.console.queue_failed": {"ar": "فشل", "en": "failed"},
    "memory.console.retry_failed": {
        "ar": "فشلت إعادة المحاولة",
        "en": "Retry failed",
    },
    "memory.console.queue_load_failed": {
        "ar": "فشل تحميل الطابور",
        "en": "Queue load failed",
    },
    "memory.console.clear_failed_title": {
        "ar": "مسح المهام الفاشلة؟",
        "en": "Clear failed tasks?",
    },
    "memory.console.clear_failed_message": {
        "ar": "حذف صفوف المهام المتوقفة نهائيًا من الطابور. تبقى المهمة الفاشلة هنا حتى تعيد "
              "محاولتها أو تمسحها: لم تعد تُحذف بعد أسبوع.",
        "en": "Permanently delete dead-letter queue rows. A failed task stays here until you retry "
              "or clear it: it is no longer deleted after a week.",
    },
    "memory.console.clear_failed_native": {
        "ar": "مسح كل المهام الفاشلة في الطابور؟",
        "en": "Clear all failed queue tasks?",
    },
    "memory.console.cleared_failed.zero": {
        "ar": "لم تُمسح أي مهمة فاشلة",
        "en": "Cleared {n} failed tasks",
    },
    "memory.console.cleared_failed.one": {
        "ar": "مُسحت مهمة فاشلة واحدة",
        "en": "Cleared 1 failed task",
    },
    "memory.console.cleared_failed.two": {
        "ar": "مُسحت مهمتان فاشلتان",
        "en": "Cleared {n} failed tasks",
    },
    "memory.console.cleared_failed.few": {
        "ar": "مُسحت {n} مهام فاشلة",
        "en": "Cleared {n} failed tasks",
    },
    "memory.console.cleared_failed.many": {
        "ar": "مُسحت {n} مهمة فاشلة",
        "en": "Cleared {n} failed tasks",
    },
    "memory.console.cleared_failed.other": {
        "ar": "مُسحت {n} مهمة فاشلة",
        "en": "Cleared {n} failed tasks",
    },
    "memory.console.failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "memory.console.no_pending_merges": {
        "ar": "لا عمليات دمج معلّقة.",
        "en": "No pending merges.",
    },
    "memory.console.approve": {
        "ar": "موافقة",
        "en": "Approve",
    },
    "memory.console.reject": {
        "ar": "رفض",
        "en": "Reject",
    },
    "memory.console.merge_action_failed": {
        "ar": "فشل إجراء الدمج",
        "en": "Merge action failed",
    },
    "memory.console.merges_load_failed": {
        "ar": "فشل تحميل عمليات الدمج",
        "en": "Merges load failed",
    },
    "memory.console.searching": {
        "ar": "جارٍ البحث في الذاكرة والمعرفة…",
        "en": "Searching memory + knowledge…",
    },
    "memory.console.search_failed": {
        "ar": "فشل البحث",
        "en": "Search failed",
    },
    "memory.console.fed_summary": {
        "ar": "الذاكرة: {memory} · المعرفة: {knowledge} · الإجمالي: {total}  (المخزنان منفصلان — التسميات فقط للتمييز)",
        "en": "memory: {memory} · knowledge: {knowledge} · total: {total}  (stores stay separate — labels only)",
    },
    "memory.console.no_hits_either": {
        "ar": "لا نتائج في أي من المخزنين.",
        "en": "No hits in either store.",
    },
    "memory.console.fed_error": {
        "ar": "خطأ في البحث الموحّد: {error}",
        "en": "Federated search error: {error}",
    },
    "memory.console.running_eval": {
        "ar": "جارٍ الفحص السريع للاسترجاع…",
        "en": "Running the quick recall check…",
    },
    "memory.console.eval_failed_http": {
        "ar": "فشل التقييم: HTTP {status}",
        "en": "Eval failed: HTTP {status}",
    },
    "memory.console.eval_failed": {
        "ar": "فشل التقييم",
        "en": "Eval failed",
    },
    "memory.console.eval_summary": {
        "ar": "<strong>الفحص السريع للاسترجاع</strong> — نجح {passed}/{total} (النسبة {rate})",
        "en": "<strong>Quick recall check</strong> — pass {passed}/{total} (rate {rate})",
    },
    "memory.console.eval_unrelated_n.zero": {
        "en": "each case among {n} unrelated memories", "ar": "كل حالة بلا ذكريات لا علاقة لها بها"},
    "memory.console.eval_unrelated_n.one": {
        "en": "each case among 1 unrelated memory", "ar": "كل حالة مع ذكرى واحدة لا علاقة لها بها"},
    "memory.console.eval_unrelated_n.two": {
        "en": "each case among {n} unrelated memories", "ar": "كل حالة بين ذكريين لا علاقة لهما بها"},
    "memory.console.eval_unrelated_n.few": {
        "en": "each case among {n} unrelated memories", "ar": "كل حالة بين {n} ذكريات لا علاقة لها بها"},
    "memory.console.eval_unrelated_n.many": {
        "en": "each case among {n} unrelated memories", "ar": "كل حالة بين {n} ذكرى لا علاقة لها بها"},
    "memory.console.eval_unrelated_n.other": {
        "en": "each case among {n} unrelated memories", "ar": "كل حالة بين {n} ذكرى لا علاقة لها بها"},
    "memory.console.eval_shown_wrongly": {
        "ar": "عُرض ما لا علاقة له: {words}",
        "en": "showed unrelated: {words}",
    },
    "memory.console.eval_skipped": {
        "ar": "تُخطّي {n}",
        "en": "skipped {n}",
    },
    "memory.console.case_pass": {
        "ar": "نجح",
        "en": "PASS",
    },
    "memory.console.case_fail": {
        "ar": "فشل",
        "en": "FAIL",
    },
    "memory.console.case_skipped": {
        "ar": "تُخطّي",
        "en": "SKIPPED",
    },
    "memory.console.eval_error": {
        "ar": "خطأ في التقييم: {error}",
        "en": "Eval error: {error}",
    },
    "memory.console.reconsol_enqueued": {
        "ar": "يجري دمج الحقائق المكرّرة في الخلفية",
        "en": "Merging duplicate facts in the background",
    },
    "memory.console.no_active_skills": {
        "ar": "لا مهارات نشطة بعد.",
        "en": "No active skills yet.",
    },
    "memory.console.skills_load_failed": {
        "ar": "فشل تحميل المهارات",
        "en": "Skills load failed",
    },
    "memory.console.quality_checks.zero": {
        "ar": "الفحوص الناجحة: {passed} من {n}",
        "en": "{passed}/{n} checks",
    },
    "memory.console.quality_checks.one": {
        "ar": "الفحوص الناجحة: {passed} من {n}",
        "en": "{passed}/1 check",
    },
    "memory.console.quality_checks.two": {
        "ar": "الفحوص الناجحة: {passed} من {n}",
        "en": "{passed}/{n} checks",
    },
    "memory.console.quality_checks.few": {
        "ar": "الفحوص الناجحة: {passed} من {n}",
        "en": "{passed}/{n} checks",
    },
    "memory.console.quality_checks.many": {
        "ar": "الفحوص الناجحة: {passed} من {n}",
        "en": "{passed}/{n} checks",
    },
    "memory.console.quality_checks.other": {
        "ar": "الفحوص الناجحة: {passed} من {n}",
        "en": "{passed}/{n} checks",
    },
    "memory.console.all_green": {
        "ar": "كلها سليمة",
        "en": "all green",
    },
    "memory.console.no_memories": {
        "ar": "لا ذكريات محادثات بعد.",
        "en": "No memories of conversations yet.",
    },
    "memory.console.chat": {
        "ar": "المحادثة",
        "en": "chat",
    },
    "memory.console.forget": {
        "ar": "انسَ",
        "en": "Forget",
    },
    "memory.console.forget_memory_title": {
        "ar": "نسيان هذه الذكرى؟",
        "en": "Forget this memory?",
    },
    "memory.console.forget_memory_message": {
        "ar": "يتوقف Kazma عن تذكّر هذا الجزء من المحادثة والحقائق التي تعلّمها منه، أينما حُفظت نسخة منها. تبقى المحادثة نفسها كما هي. النسخ الاحتياطية التي سبقت هذا تحتفظ به حتى تُستبدل (حتى سنة للنسخ الخارجية)، وكذلك النسخ التي تحفظها أدوات الإصلاح قبل تعديل قاعدة الذاكرة، حتى تُحذف.",
        "en": "Kazma stops remembering this part of the conversation and the facts it learned from it, wherever a copy was kept. The chat itself stays as it is. Backups made before now keep it until they are replaced (up to a year for offsite backups), and so do the copies Kazma's repair scripts keep before changing the memory database, until they are removed.",
    },
    "memory.console.forgotten": {
        "ar": "نُسيت",
        "en": "Forgotten",
    },
    "memory.console.forgotten_with_facts.zero": {
        "ar": "نُسيت",
        "en": "Forgotten (and {n} facts)",
    },
    "memory.console.forgotten_with_facts.one": {
        "ar": "نُسيت (ومعها حقيقة واحدة)",
        "en": "Forgotten (and 1 fact)",
    },
    "memory.console.forgotten_with_facts.two": {
        "ar": "نُسيت (ومعها حقيقتان)",
        "en": "Forgotten (and {n} facts)",
    },
    "memory.console.forgotten_with_facts.few": {
        "ar": "نُسيت (ومعها {n} حقائق)",
        "en": "Forgotten (and {n} facts)",
    },
    "memory.console.forgotten_with_facts.many": {
        "ar": "نُسيت (ومعها {n} حقيقة)",
        "en": "Forgotten (and {n} facts)",
    },
    "memory.console.forgotten_with_facts.other": {
        "ar": "نُسيت (ومعها {n} حقيقة)",
        "en": "Forgotten (and {n} facts)",
    },
    "memory.console.forget_failed": {
        "ar": "فشل النسيان",
        "en": "Forget failed",
    },
    "memory.console.memories_load_failed": {
        "ar": "فشل تحميل الذكريات",
        "en": "Memories load failed",
    },
    "memory.console.weeks_done.zero": {
        "ar": "الأسابيع المُلخَّصة: {n}",
        "en": "{n} weeks summarized",
    },
    "memory.console.weeks_done.one": {
        "ar": "الأسابيع المُلخَّصة: {n}",
        "en": "1 week summarized",
    },
    "memory.console.weeks_done.two": {
        "ar": "الأسابيع المُلخَّصة: {n}",
        "en": "{n} weeks summarized",
    },
    "memory.console.weeks_done.few": {
        "ar": "الأسابيع المُلخَّصة: {n}",
        "en": "{n} weeks summarized",
    },
    "memory.console.weeks_done.many": {
        "ar": "الأسابيع المُلخَّصة: {n}",
        "en": "{n} weeks summarized",
    },
    "memory.console.weeks_done.other": {
        "ar": "الأسابيع المُلخَّصة: {n}",
        "en": "{n} weeks summarized",
    },
    "memory.console.weeks_queued": {
        "ar": "قيد التنفيذ: {n}",
        "en": "{n} in progress",
    },
    "memory.console.weeks_failed": {
        "ar": "فاشلة: {n}",
        "en": "{n} failed",
    },
    "memory.console.no_summaries": {
        "ar": "لا ملخصات أسبوعية بعد. يكتبها Kazma بعد يوم من نهاية كل أسبوع.",
        "en": "No weekly summaries yet. Kazma writes them a day after each week ends.",
    },
    "memory.console.summary_turns.zero": {
        "ar": "لا أدوار",
        "en": "{n} turns",
    },
    "memory.console.summary_turns.one": {
        "ar": "دور واحد",
        "en": "1 turn",
    },
    "memory.console.summary_turns.two": {
        "ar": "دوران",
        "en": "{n} turns",
    },
    "memory.console.summary_turns.few": {
        "ar": "{n} أدوار",
        "en": "{n} turns",
    },
    "memory.console.summary_turns.many": {
        "ar": "{n} دورًا",
        "en": "{n} turns",
    },
    "memory.console.summary_turns.other": {
        "ar": "{n} دور",
        "en": "{n} turns",
    },
    "memory.console.summary_chats.zero": {
        "ar": " في لا محادثات",
        "en": " in {n} chats",
    },
    "memory.console.summary_chats.one": {
        "ar": " في محادثة واحدة",
        "en": " in 1 chat",
    },
    "memory.console.summary_chats.two": {
        "ar": " في محادثتين",
        "en": " in {n} chats",
    },
    "memory.console.summary_chats.few": {
        "ar": " في {n} محادثات",
        "en": " in {n} chats",
    },
    "memory.console.summary_chats.many": {
        "ar": " في {n} محادثة",
        "en": " in {n} chats",
    },
    "memory.console.summary_chats.other": {
        "ar": " في {n} محادثة",
        "en": " in {n} chats",
    },
    "memory.console.summary_rebuilding": {
        "ar": "يُعاد كتابته بدون محادثة منسية",
        "en": "Being rewritten without a forgotten conversation",
    },
    "memory.console.week_of": {
        "ar": "أسبوع {date}",
        "en": "Week of {date}",
    },
    "memory.console.month_of": {
        "ar": "شهر {month}",
        "en": "Month of {month}",
    },
    "memory.console.project_span": {
        "ar": "على مدى عدة أسابيع، من {from} إلى {to}",
        "en": "Over several weeks, {from} to {to}",
    },
    "memory.console.forget_summary_title": {
        "ar": "نسيان هذا الملخص؟",
        "en": "Forget this summary?",
    },
    "memory.console.forget_summary_message": {
        "ar": "يتوقف Kazma عن استخدام هذا الملخص ولن يكتبه مرة أخرى. تبقى المحادثات التي كُتب منها كما هي.",
        "en": "Kazma stops using this summary and never writes it again. The conversations it was written from stay as they are.",
    },
    "memory.console.summary_forgotten": {
        "ar": "نُسي الملخص",
        "en": "Summary forgotten",
    },
    "memory.console.summaries_load_failed": {
        "ar": "فشل تحميل الملخصات الأسبوعية",
        "en": "Weekly summaries load failed",
    },
    "memory.console.change_update_title": {
        "ar": "تغيّرت بناءً على كلامك",
        "en": "Changed from what you said",
    },
    "memory.console.change_pending_title": {
        "ar": "هل هذا تغيير؟ أبقى Kazma المعلومة السابقة",
        "en": "Is this a change? Kazma kept the earlier fact",
    },
    "memory.console.change_was": {
        "ar": "قبل: {value}",
        "en": "Before: {value}",
    },
    "memory.console.change_now": {
        "ar": "الآن: {value}",
        "en": "Now: {value}",
    },
    "memory.console.change_kept": {
        "ar": "المحفوظ: {value}",
        "en": "Kept: {value}",
    },
    "memory.console.change_heard": {
        "ar": "ربما قلت: {value}",
        "en": "You may have said: {value}",
    },
    "memory.console.change_ended_title": {
        "ar": "انتهت حسب كلامك",
        "en": "Ended from what you said",
    },
    "memory.console.change_no_longer": {
        "ar": "لم تعد: {value}",
        "en": "No longer: {value}",
    },
    "memory.console.change_pending_end_title": {
        "ar": "هل انتهت هذه؟ أبقى Kazma المعلومة",
        "en": "Has this ended? Kazma kept the fact",
    },
    "memory.console.change_heard_end": {
        "ar": "ربما قلت إنها لم تعد صحيحة",
        "en": "You may have said it is no longer true",
    },
    "memory.console.change_confirm_end": {
        "ar": "نعم، انتهت",
        "en": "Yes, it ended",
    },
    "memory.console.change_undo": {
        "ar": "تراجع",
        "en": "Undo",
    },
    "memory.console.change_confirm": {
        "ar": "نعم، غيّرها",
        "en": "Yes, change it",
    },
    "memory.console.change_dismiss": {
        "ar": "لا، أبقِها",
        "en": "No, keep it",
    },
    "memory.console.change_undone": {
        "ar": "تم التراجع عن التغيير",
        "en": "Change undone",
    },
    "memory.console.change_confirmed": {
        "ar": "تم التغيير",
        "en": "Changed",
    },
    "memory.console.change_dismissed": {
        "ar": "بقيت كما كانت",
        "en": "Kept as it was",
    },
    "memory.console.change_failed": {
        "ar": "تعذّر إجراء هذا التغيير",
        "en": "That change could not be made",
    },
    "memory.console.change_reading_title": {
        "ar": "قراءة Kazma لرسالتك",
        "en": "Kazma's reading of your message",
    },
    "memory.console.change_read": {
        "ar": "قرأ Kazma: {value}",
        "en": "Kazma read: {value}",
    },
    "memory.console.change_reading_right": {
        "ar": "هذا صحيح",
        "en": "This is right",
    },
    "memory.console.change_reading_not_me": {
        "ar": "ليس عني",
        "en": "Not me",
    },
    "memory.console.change_reading_kept": {
        "ar": "حُفظت على أنها منك",
        "en": "Kept as yours",
    },
    "memory.console.change_reading_removed": {
        "ar": "أُزيلت، ولن يحفظها Kazma مرة أخرى",
        "en": "Removed. Kazma will not file it again",
    },
    "memory.console.no_suggestions": {
        "ar": "لا يوجد ما يُقترح لـ«عني» الآن.",
        "en": "Nothing to suggest for About me right now.",
    },
    "memory.console.suggestions_load_failed": {
        "ar": "تعذّر تحميل الاقتراحات",
        "en": "Suggestions load failed",
    },
    "memory.console.suggestion_add": {
        "ar": "أضف إلى «عني»",
        "en": "Add to About me",
    },
    "memory.console.suggestion_dismiss": {
        "ar": "تجاهل",
        "en": "Dismiss",
    },
    "memory.console.suggestion_added": {
        "ar": "أُضيف إلى «عني»",
        "en": "Added to About me",
    },
    "memory.console.suggestion_dismissed": {
        "ar": "تم التجاهل. لن يقترحه Kazma مرة أخرى",
        "en": "Dismissed. Kazma will not suggest it again",
    },
    "memory.console.suggestion_failed": {
        "ar": "تعذّر ذلك",
        "en": "That could not be done",
    },
    "memory.console.no_changes": {
        "ar": "لم يتغيّر شيء في المعلومات التي أخبرت بها Kazma، ولا شيء ينتظر ردّك.",
        "en": "Nothing has changed in the facts you told Kazma, and nothing is waiting for your answer.",
    },
    "memory.console.changes_load_failed": {
        "ar": "فشل تحميل التغييرات",
        "en": "Changes load failed",
    },
    "memory.err.about_too_long": {
        "ar": "سيتجاوز «عني» حدّه: اختصره في الإعدادات أولاً",
        "en": "About me would pass its limit: shorten it in Settings first",
    },
    "memory.err.no_account": {
        "ar": "سجّل الدخول لتضيف إلى «عني» الخاص بك",
        "en": "Sign in to add to your own About me",
    },
    "memory.err.not_yours": {
        "ar": "هذا لعضو آخر؛ لا ينساه إلا هو أو مالك المساحة",
        "en": "This is another member's; only they or the space's owner can forget it",
    },
    # Stage 3 review, L13: a change to another member's fact in a shared space.
    "memory.err.not_theirs": {
        "ar": "هذه معلومة لعضو آخر؛ لا يقرر فيها إلا هو أو مالك المساحة",
        "en": "This is another member's fact; only they or the space's owner decides on it",
    },
    "memory.shared_notice": {
        "ar": "الذاكرة هنا مشتركة مع {names}.",
        "en": "Memory here is shared with {names}.",
    },
    "memory.page.others_message_stays": {
        "ar": "نُسيت. رسالة {author} ما زالت تحمل هذه الكلمات ويمكن استرجاعها؛ يستطيع {author} أو مالك المساحة نسيانها.",
        "en": "Forgotten. {author}'s message still holds these words and can still be recalled; {author} or the space's owner can forget it.",
    },
    "memory.err.not_open": {
        "ar": "حُسم هذا من قبل",
        "en": "This was already decided",
    },
    "memory.err.replaced_since": {
        "ar": "تغيّرت هذه المعلومة مرة أخرى منذ ذلك؛ تراجع عن التغيير الأحدث أولًا",
        "en": "This fact has changed again since; undo the newer change first",
    },
    "memory.err.forgotten": {
        "ar": "نُسيت القيمة السابقة، فلا يمكن إعادتها",
        "en": "The earlier value was forgotten, so it cannot come back",
    },
    "memory.err.not_written": {
        "ar": "تعذّر حفظ القيمة",
        "en": "The value could not be saved",
    },
    "memory.console.no_active_beliefs": {
        "ar": "لا معتقدات نشطة بعد.",
        "en": "No active beliefs yet.",
    },
    # The user's own facts by Kazma's names (memory/fact_names.py KINDS), as
    # a change card names them (the third review of F1, item 6).
    "memory.console.fact_lives_in": {
        "ar": "مكان السكن",
        "en": "Home",
    },
    "memory.console.fact_name_is": {
        "ar": "الاسم",
        "en": "Name",
    },
    "memory.console.fact_preferred_name": {
        "ar": "الاسم الذي تُنادى به",
        "en": "Name you go by",
    },
    "memory.console.fact_works_at": {
        "ar": "جهة العمل",
        "en": "Employer",
    },
    "memory.console.fact_current_role": {
        "ar": "المسمى الوظيفي",
        "en": "Role",
    },
    "memory.console.ptype_functional": {
        "ar": "قيمة واحدة",
        "en": "one value",
    },
    "memory.console.ptype_set": {
        "ar": "قائمة",
        "en": "a list",
    },
    "memory.console.ptype_state": {
        "ar": "حالة",
        "en": "a state",
    },
    "memory.console.etype_person": {
        "ar": "شخص",
        "en": "person",
    },
    "memory.console.etype_tool": {
        "ar": "أداة",
        "en": "tool",
    },
    "memory.console.etype_concept": {
        "ar": "فكرة",
        "en": "idea",
    },
    "memory.console.etype_location": {
        "ar": "مكان",
        "en": "location",
    },
    "memory.console.etype_project": {
        "ar": "مشروع",
        "en": "project",
    },
    "memory.console.etype_entity": {
        "ar": "شيء",
        "en": "thing",
    },
    "memory.console.etype_organization": {
        "ar": "منظمة",
        "en": "organization",
    },
    "memory.console.etype_event": {
        "ar": "حدث",
        "en": "event",
    },
    "memory.console.ops_src": {
        "ar": "المصدر: {id}",
        "en": "src: {id}",
    },
    "memory.console.ops_tgt": {
        "ar": "الهدف: {id}",
        "en": "tgt: {id}",
    },
    "memory.console.hint_link_target": {
        "ar": "الربط: انقر الشيء الثاني…",
        "en": "Connect: click the second thing…",
    },
    "memory.console.hint_link_source": {
        "ar": "الربط: انقر الشيء الأول…",
        "en": "Connect: click the first thing…",
    },
    "memory.console.hint_merge_target": {
        "ar": "الدمج: انقر الشيء الذي يبقى…",
        "en": "Join: click the thing to keep…",
    },
    "memory.console.hint_merge_source": {
        "ar": "الدمج: انقر الشيء الذي سيُدمج في غيره…",
        "en": "Join: click the thing to join into another…",
    },
    "memory.console.hint_move": {
        "ar": "النقل: انقر الطرف الجديد لهذه الحقيقة… (امسح للإيقاف)",
        "en": "Move: click the new end of this fact… (Clear to stop)",
    },
    "memory.console.hint_group": {
        "ar": "التجميع: انقر الشيء الذي تضعه تحته… (امسح للإيقاف)",
        "en": "Group: click the thing to place it under… (Clear to stop)",
    },
    "memory.console.hint_ready": {
        "ar": "جاهز: اضغط «اربط» أو «ادمجهما في واحد».",
        "en": "Ready: press Connect, or Join into one.",
    },
    "memory.console.hint_idle": {
        "ar": "انقر نقطة لتراها. انقر خطًا لتغيير حقيقته أو إزالتها.",
        "en": "Click a dot to see it. Click a line to change or remove its fact.",
    },
    "memory.console.linking": {
        "ar": "جارٍ الربط…",
        "en": "Linking…",
    },
    "memory.console.merging": {
        "ar": "جارٍ الدمج…",
        "en": "Merging…",
    },
    "memory.console.need_slots": {
        "ar": "عيّن المصدر والهدف أولًا",
        "en": "Set source and target first",
    },
    "memory.console.slots_differ": {
        "ar": "يجب أن يختلف المصدر عن الهدف",
        "en": "Source and target must differ",
    },
    "memory.console.link_failed": {
        "ar": "فشل الربط",
        "en": "Link failed",
    },
    "memory.console.link_failed_error": {
        "ar": "فشل الربط: {error}",
        "en": "Link failed: {error}",
    },
    "memory.console.linked": {
        "ar": "رُبط {subject} —{predicate}← {object}",
        "en": "Linked {subject} —{predicate}→ {object}",
    },
    "memory.console.already_linked": {
        "ar": "مربوط مسبقًا · {subject} —{predicate}← {object}",
        "en": "Already linked · {subject} —{predicate}→ {object}",
    },
    "memory.console.merge_title": {
        "ar": "دمج في واحد",
        "en": "Join into one",
    },
    "memory.console.merge_message": {
        "ar": "دمج {source} في {target}؟\nتنتقل كل الحقائق عن {source} إلى {target}، ويختفي {source}. لا يمكن التراجع عن هذا.",
        "en": "Join {source} into {target}?\nEvery fact about {source} moves to {target}, and {source} goes. This cannot be undone.",
    },
    "memory.console.merged": {
        "ar": "دُمج {source} ← {target}",
        "en": "Merged {source} → {target}",
    },
    "memory.console.merge_failed": {
        "ar": "فشل الدمج",
        "en": "Merge failed",
    },
    "memory.console.cut_missing": {
        "ar": "تعذّر القطع — لا يوجد معرّف معتقد ولا ثلاثية حافة",
        "en": "Cannot cut — missing belief id and edge triple",
    },
    "memory.console.cut_title": {
        "ar": "إزالة الحقيقة",
        "en": "Remove fact",
    },
    "memory.console.cut_message": {
        "ar": "إزالة هذه الحقيقة مما يعرفه Kazma؟",
        "en": "Remove this fact from what Kazma knows?",
    },
    "memory.console.cut_message_soft": {
        "ar": "تبقى في سجل الحقائق السابقة.",
        "en": "It stays in the record of past facts.",
    },
    "memory.console.cut": {
        "ar": "إزالة",
        "en": "Remove",
    },
    "memory.console.cut_failed": {
        "ar": "فشل القطع",
        "en": "Cut failed",
    },
    "memory.console.cut_failed_error": {
        "ar": "فشل القطع: {error}",
        "en": "Cut failed: {error}",
    },
    "memory.console.already_cut": {
        "ar": "مقطوع مسبقًا",
        "en": "Already cut",
    },
    "memory.console.connection_cut": {
        "ar": "قُطع الاتصال",
        "en": "Connection cut",
    },
    "memory.console.no_edges_to_cut": {
        "ar": "لا حواف للقطع",
        "en": "No edges to cut",
    },
    "memory.console.cut_n_message.zero": {
        "ar": "قطع الاتصالات من الذاكرة النشطة؟",
        "en": "Cut {n} connections from active memory?",
    },
    "memory.console.cut_n_message.one": {
        "ar": "قطع اتصال واحد من الذاكرة النشطة؟",
        "en": "Cut 1 connection from active memory?",
    },
    "memory.console.cut_n_message.two": {
        "ar": "قطع اتصالين من الذاكرة النشطة؟",
        "en": "Cut {n} connections from active memory?",
    },
    "memory.console.cut_n_message.few": {
        "ar": "قطع {n} اتصالات من الذاكرة النشطة؟",
        "en": "Cut {n} connections from active memory?",
    },
    "memory.console.cut_n_message.many": {
        "ar": "قطع {n} اتصالًا من الذاكرة النشطة؟",
        "en": "Cut {n} connections from active memory?",
    },
    "memory.console.cut_n_message.other": {
        "ar": "قطع {n} اتصال من الذاكرة النشطة؟",
        "en": "Cut {n} connections from active memory?",
    },
    "memory.console.cut_connections_title": {
        "ar": "إزالة الحقائق",
        "en": "Remove facts",
    },
    "memory.console.cut_n": {
        "ar": "قطع {n}",
        "en": "Cut {n}",
    },
    "memory.console.cut_done.zero": {
        "ar": "لم يُقطع أي اتصال",
        "en": "Cut {n} connections",
    },
    "memory.console.cut_done.one": {
        "ar": "قُطع اتصال واحد",
        "en": "Cut 1 connection",
    },
    "memory.console.cut_done.two": {
        "ar": "قُطع اتصالان",
        "en": "Cut {n} connections",
    },
    "memory.console.cut_done.few": {
        "ar": "قُطعت {n} اتصالات",
        "en": "Cut {n} connections",
    },
    "memory.console.cut_done.many": {
        "ar": "قُطع {n} اتصالًا",
        "en": "Cut {n} connections",
    },
    "memory.console.cut_done.other": {
        "ar": "قُطع {n} اتصال",
        "en": "Cut {n} connections",
    },
    "memory.console.no_edges_cut": {
        "ar": "لم تُقطع أي حافة",
        "en": "No edges were cut",
    },
    "memory.console.no_hub_link": {
        "ar": "لا رابط مباشر بالمحور في هذه العقدة",
        "en": "No direct hub link on this node",
    },
    "memory.console.hub_keeps": {
        "ar": "تبقى الروابط إلى: {names}",
        "en": "Keeps links to: {names}",
    },
    "memory.console.hub_chain_hint": {
        "ar": "مفيد عندما يجب أن تكون السلسلة فرع ← أصل ← المحور (لا فرع ← المحور).",
        "en": "Useful when the chain should be leaf → parent → hub (not leaf → hub).",
    },
    "memory.console.cut_hub_title": {
        "ar": "إزالة الرابط المباشر بك",
        "en": "Remove direct link to you",
    },
    "memory.console.cut_hub_message.zero": {
        "ar": "إزالة الروابط المباشرة بالمحور (أنت)؟",
        "en": "Remove {n} direct links to the hub (you)?",
    },
    "memory.console.cut_hub_message.one": {
        "ar": "إزالة رابط مباشر واحد بالمحور (أنت)؟",
        "en": "Remove 1 direct link to the hub (you)?",
    },
    "memory.console.cut_hub_message.two": {
        "ar": "إزالة رابطين مباشرين بالمحور (أنت)؟",
        "en": "Remove {n} direct links to the hub (you)?",
    },
    "memory.console.cut_hub_message.few": {
        "ar": "إزالة {n} روابط مباشرة بالمحور (أنت)؟",
        "en": "Remove {n} direct links to the hub (you)?",
    },
    "memory.console.cut_hub_message.many": {
        "ar": "إزالة {n} رابطًا مباشرًا بالمحور (أنت)؟",
        "en": "Remove {n} direct links to the hub (you)?",
    },
    "memory.console.cut_hub_message.other": {
        "ar": "إزالة {n} رابط مباشر بالمحور (أنت)؟",
        "en": "Remove {n} direct links to the hub (you)?",
    },
    "memory.console.cut_hub_link": {
        "ar": "أزل الرابط المباشر",
        "en": "Remove direct link",
    },
    "memory.console.cut_hub_links": {
        "ar": "أزل الروابط المباشرة",
        "en": "Remove direct links",
    },
    "memory.console.no_belief_id_edit": {
        "ar": "لا معرّف للمعتقد — تعذّر التعديل",
        "en": "No belief id — cannot edit",
    },
    "memory.console.edit_object_title": {
        "ar": "تعديل المعتقد — القيمة",
        "en": "Edit belief — object",
    },
    "memory.console.edit_object_message": {
        "ar": "نص الحقيقة / القيمة. الإلغاء يوقف التعديل.",
        "en": "Fact / object text. Cancel aborts.",
    },
    "memory.console.edit_object_ph": {
        "ar": "مثال: باريس",
        "en": "e.g. Paris",
    },
    "memory.console.next": {
        "ar": "التالي",
        "en": "Next",
    },
    "memory.console.edit_predicate_title": {
        "ar": "تعديل المعتقد — المحمول",
        "en": "Edit belief — predicate",
    },
    "memory.console.edit_predicate_message": {
        "ar": "اسم العلاقة (يمكن بصيغة snake_case).",
        "en": "Relation name (snake_case ok).",
    },
    "memory.console.edit_subject_title": {
        "ar": "تعديل المعتقد — الموضوع",
        "en": "Edit belief — subject",
    },
    "memory.console.edit_subject_message": {
        "ar": "معرّف الكيان الموضوع.",
        "en": "Subject entity id.",
    },
    "memory.console.save": {
        "ar": "حفظ",
        "en": "Save",
    },
    "memory.console.spo_required": {
        "ar": "الموضوع والمحمول والقيمة مطلوبة",
        "en": "Subject, predicate, and object are required",
    },
    "memory.console.belief_updated": {
        "ar": "حُدّث المعتقد",
        "en": "Belief updated",
    },
    "memory.console.edit_failed": {
        "ar": "فشل التعديل",
        "en": "Edit failed",
    },
    "memory.console.no_belief_id_move": {
        "ar": "لا معرّف للمعتقد — تعذّر النقل",
        "en": "No belief id — cannot move",
    },
    "memory.console.move_title": {
        "ar": "نقل أي طرف من هذه الحقيقة؟",
        "en": "Move which end of this fact?",
    },
    "memory.console.move_message": {
        "ar": "البداية = {subject} · النهاية = {object}. اختر الطرف الذي تربطه بشيء آخر.",
        "en": "Start = {subject} · end = {object}. Pick the end to attach to another thing.",
    },
    "memory.console.move_subject": {
        "ar": "انقل البداية",
        "en": "Move the start",
    },
    "memory.console.move_object": {
        "ar": "انقل النهاية",
        "en": "Move the end",
    },
    "memory.console.click_new_endpoint": {
        "ar": "انقر عقدة الطرف الجديد…",
        "en": "Click the new endpoint node…",
    },
    "memory.console.move_failed": {
        "ar": "فشل النقل",
        "en": "Move failed",
    },
    "memory.console.moved_edge": {
        "ar": "نُقلت الحقيقة إلى {id}",
        "en": "Moved the fact to {id}",
    },
    "memory.console.moved_undo": {
        "ar": " · تراجع",
        "en": " · Undo",
    },
    "memory.console.moved_stranded": {
        "ar": " · بلا روابط: {ids}",
        "en": " · stranded: {ids}",
    },
    "memory.console.toggle_failed": {
        "ar": "فشل التبديل",
        "en": "Toggle failed",
    },
    "memory.console.marked_major": {
        "ar": "عُلّمت {id} كرئيسية",
        "en": "Marked {id} as major",
    },
    "memory.console.unmarked_major": {
        "ar": "أُزيلت علامة {id}",
        "en": "Unmarked {id}",
    },
    "memory.console.pick_node_first": {
        "ar": "اختر عقدة أولًا",
        "en": "Pick a node first",
    },
    "memory.console.hub_not_groupable": {
        "ar": "المحور هو الجذر الأعلى — لا يمكن تجميعه",
        "en": "The hub is the top-level root — not groupable",
    },
    "memory.console.group_pick_parent": {
        "ar": "انقر الشيء الذي تضع «{id}» تحته…",
        "en": "Click the thing to place “{id}” under…",
    },
    "memory.console.group_failed": {
        "ar": "فشل التجميع",
        "en": "Group failed",
    },
    "memory.console.grouped": {
        "ar": "وُضع {member} تحت {root} في الخريطة (المستوى {tier}). لا تتغيّر الذاكرة.",
        "en": "Placed {member} under {root} on the map (level {tier}). Memory is unchanged.",
    },
    "memory.console.not_grouped": {
        "ar": "غير مُجمّعة",
        "en": "Not grouped",
    },
    "memory.console.ungroup_failed": {
        "ar": "فشل فك التجميع",
        "en": "Ungroup failed",
    },
    "memory.console.ungrouped": {
        "ar": "فُكّ تجميع {id} · للعرض فقط، الذاكرة لم تتغير",
        "en": "Ungrouped {id} · view-only, memory untouched",
    },
    "memory.console.moved_group": {
        "ar": "نُقلت {member} تحت {root} (المستوى {tier}، وأُعيد ترتيب {n} تحتها) · للعرض فقط، الذاكرة لم تتغير",
        "en": "Moved {member} under {root} (tier {tier}; {n} below re-tiered) · view-only, memory untouched",
    },
    "memory.console.already_in_group": {
        "ar": "{member} مُجمّعة تحت {root} أصلًا",
        "en": "{member} is already grouped under {root}",
    },
    "memory.console.act_set_tier": {
        "ar": "المستوى…",
        "en": "Level…",
    },
    "memory.console.act_set_tier_title": {
        "ar": "حدد مستوى هذه العقدة في التجميع (0 = المحور … 4)",
        "en": "Set this node's tier in its group (0 = hub … 4)",
    },
    "memory.console.tier_prompt_title": {
        "ar": "المستوى في الخريطة",
        "en": "Level on the map",
    },
    "memory.console.tier_prompt_message": {
        "ar": "مستوى {id} في الخريطة، من 0 إلى 4. يحتفظ ما تحته بمستوياته.",
        "en": "Level of {id} on the map, 0 to 4. Things under it keep theirs.",
    },
    "memory.console.tier_invalid": {
        "ar": "اكتب عددًا صحيحًا من 0 إلى 4",
        "en": "Enter a whole number from 0 to 4",
    },
    "memory.console.tier_set": {
        "ar": "صار {id} في المستوى {tier} في الخريطة. لا تتغيّر الذاكرة.",
        "en": "{id} is now level {tier} on the map. Memory is unchanged.",
    },
    "memory.console.tier_failed": {
        "ar": "تعذّر تغيير المستوى",
        "en": "Setting the tier failed",
    },
    "memory.console.cannot_delete_hub": {
        "ar": "لا يمكن حذف المحور المحمي",
        "en": "Cannot delete protected hub",
    },
    "memory.console.delete_title": {
        "ar": "حذف الاسم",
        "en": "Delete name",
    },
    "memory.console.delete_message": {
        "ar": "حذف الاسم «{id}»؟ تبقى الحقائق عنه؛ يُحذف الاسم فقط. لا يمكن حذف اسم محمي.",
        "en": "Delete the name “{id}”? Facts about it stay; only the name goes. A protected name cannot be deleted.",
    },
    "memory.console.deleted": {
        "ar": "حُذف {id}",
        "en": "Deleted {id}",
    },
    "memory.console.delete_failed": {
        "ar": "فشل الحذف",
        "en": "Delete failed",
    },
    "memory.console.pick_different_node": {
        "ar": "اختر عقدة مختلفة",
        "en": "Pick a different node",
    },
    "memory.console.pick_different_parent": {
        "ar": "اختر عقدة أم مختلفة",
        "en": "Pick a different parent node",
    },
    "memory.console.pick_different_target": {
        "ar": "اختر عقدة مختلفة هدفًا",
        "en": "Pick a different node as target",
    },
    "memory.console.close": {
        "ar": "إغلاق",
        "en": "Close",
    },
    "memory.console.edge_belief": {
        "ar": "حقيقة",
        "en": "Fact",
    },
    "memory.console.edge_id": {
        "ar": "المعرّف: {id}",
        "en": "id: {id}",
    },
    "memory.console.edge_id_missing": {
        "ar": "(بلا معرّف: قد تفشل الإزالة)",
        "en": "(no id: removing may fail)",
    },
    "memory.console.edge_conf": {
        "ar": " · الثقة {n}%",
        "en": " · confidence {n}%",
    },
    "memory.console.superseded": {
        "ar": " · استُبدلت",
        "en": " · replaced",
    },
    "memory.console.cut_this_edge": {
        "ar": "أزل هذه الحقيقة",
        "en": "Remove this fact",
    },
    "memory.console.edit": {
        "ar": "تعديل",
        "en": "Edit",
    },
    "memory.console.src_from_a": {
        "ar": "مصدر←أ",
        "en": "Src←A",
    },
    "memory.console.tgt_to_b": {
        "ar": "هدف→ب",
        "en": "Tgt→B",
    },
    "memory.console.in_list": {
        "ar": "اعرض في القائمة",
        "en": "Show in list",
    },
    "memory.console.insp_you_hub": {
        "ar": "أنت: كل شيء يرتبط بك",
        "en": "You: everything connects to you",
    },
    "memory.console.insp_type": {
        "ar": "النوع: {type}",
        "en": "type: {type}",
    },
    "memory.console.insp_id": {
        "ar": "المعرّف:",
        "en": "id:",
    },
    "memory.console.high_stakes": {
        "ar": "حسّاس",
        "en": "sensitive",
    },
    "memory.console.fact_node": {
        "ar": " · قيمة",
        "en": " · a value",
    },
    "memory.console.act_src": {
        "ar": "من",
        "en": "From",
    },
    "memory.console.act_src_title": {
        "ar": "استخدمه أولَ شيئين للربط أو الدمج",
        "en": "Use as the first of two things to connect or join",
    },
    "memory.console.act_tgt": {
        "ar": "إلى",
        "en": "To",
    },
    "memory.console.act_tgt_title": {
        "ar": "استخدمه ثانيَ شيئين للربط أو الدمج",
        "en": "Use as the second of two things to connect or join",
    },
    "memory.console.act_merge_from": {
        "ar": "ادمج في…",
        "en": "Join into…",
    },
    "memory.console.act_merge_from_title": {
        "ar": "هذا هو الشيء نفسه كشيء آخر: انقر ذاك بعد ذلك؛ يختفي هذا",
        "en": "This is the same thing as another: click that one next; this one goes",
    },
    "memory.console.act_group_under": {
        "ar": "ضعه تحت…",
        "en": "Place under…",
    },
    "memory.console.act_group_under_title": {
        "ar": "ارسم هذا تحت شيء آخر في الخريطة. لا تتغيّر الذاكرة.",
        "en": "Draw this under another thing on the map. Memory is unchanged.",
    },
    "memory.console.act_ungroup": {
        "ar": "ألغِ الوضع",
        "en": "Unplace",
    },
    "memory.console.act_ungroup_title": {
        "ar": "توقف عن رسم هذا تحت شيء آخر. لا تتغيّر الذاكرة.",
        "en": "Stop drawing this under another thing. Memory is unchanged.",
    },
    "memory.console.act_cut_all": {
        "ar": "أزل كل حقائقه ({n})",
        "en": "Remove all its facts ({n})",
    },
    "memory.console.act_cut_all_title": {
        "ar": "أزل كل حقيقة على هذه النقطة",
        "en": "Remove every fact on this dot",
    },
    "memory.console.act_major_title": {
        "ar": "ارسمه أكبر وبلونه الخاص",
        "en": "Draw this bigger, in its own colour",
    },
    "memory.console.act_major": {
        "ar": "أبرِزه",
        "en": "Highlight",
    },
    "memory.console.act_rename": {
        "ar": "إعادة تسمية",
        "en": "Rename",
    },
    "memory.console.act_rename_title": {
        "ar": "غيّر الاسم المعروض",
        "en": "Change display name",
    },
    "memory.console.act_in_list_title": {
        "ar": "اعرض في قائمة الأشياء",
        "en": "Show in the Things list",
    },
    "memory.console.act_delete": {
        "ar": "احذف الاسم",
        "en": "Delete name",
    },
    "memory.console.act_delete_title": {
        "ar": "احذف هذا الاسم. تبقى الحقائق عنه.",
        "en": "Delete this name. Facts about it stay.",
    },
    "memory.console.act_link_from": {
        "ar": "اربط…",
        "en": "Connect…",
    },
    "memory.console.act_link_from_title": {
        "ar": "اربط هذا بشيء آخر: انقر ذاك بعد ذلك",
        "en": "Connect this to another thing: click that one next",
    },
    "memory.console.act_cut_hub": {
        "ar": "أزل الرابط المباشر بك",
        "en": "Remove direct link to you",
    },
    "memory.console.act_cut_hub_title": {
        "ar": "أزل الحقائق التي تربط هذا بك مباشرة",
        "en": "Remove the facts joining this straight to you",
    },
    "memory.console.more": {
        "ar": "المزيد",
        "en": "More",
    },
    "memory.console.less": {
        "ar": "أقل",
        "en": "Less",
    },
    "memory.console.hub_shortcut": {
        "ar": "<strong>رابط مباشر بك</strong> مع أنه مرتبط أيضًا عبر {parents}.",
        "en": "<strong>Direct link to you</strong> while also linked through {parents}.",
    },
    "memory.console.hub_shortcut_hint": {
        "ar": "يكون أوضح عادة هكذا: هذا ← أصله ← أنت. استخدم <b>أزل الرابط المباشر بك</b> لإسقاط الاختصار.",
        "en": "Usually clearer as this → its parent → you. Use <b>Remove direct link to you</b> to drop the shortcut.",
    },
    "memory.console.hub_only": {
        "ar": "مرتبط بك فقط. استخدم <b>إزالة</b> في صف الحقيقة لفصله.",
        "en": "Linked only to you. Use <b>Remove</b> on a fact row to detach it.",
    },
    "memory.console.contents": {
        "ar": "المحتوى",
        "en": "Contents",
    },
    "memory.console.connections": {
        "ar": "الاتصالات ({n})",
        "en": "Connections ({n})",
    },
    "memory.console.hub_badge": {
        "ar": "أنت",
        "en": "you",
    },
    "memory.console.cut_this_edge_only": {
        "ar": "أزل هذه الحقيقة فقط",
        "en": "Remove only this fact",
    },
    "memory.console.move": {
        "ar": "نقل",
        "en": "Move",
    },
    "memory.console.move_title_btn": {
        "ar": "اربط هذه الحقيقة بشيء آخر",
        "en": "Attach this fact to another thing",
    },
    "memory.console.more_edges": {
        "ar": "+{n} أخرى",
        "en": "+{n} more",
    },
    "memory.console.no_direct_beliefs": {
        "ar": "لا حقائق خاصة به. استخدم «اربط…» لوصله بشيء.",
        "en": "No facts of its own. Use Connect… to join it to something.",
    },
    "memory.console.toast_source": {
        "ar": "المصدر = {id}",
        "en": "Source = {id}",
    },
    "memory.console.toast_target": {
        "ar": "الهدف = {id}",
        "en": "Target = {id}",
    },
    "memory.console.toast_link_from": {
        "ar": "الربط من {id} — انقر الهدف على الرسم",
        "en": "Link from {id} — click target on graph",
    },
    "memory.console.toast_merge_from": {
        "ar": "الدمج من {id} — انقر الهدف الباقي",
        "en": "Merge from {id} — click survivor target",
    },
    "memory.console.cut_all_title": {
        "ar": "إزالة كل حقائقه",
        "en": "Remove all its facts",
    },
    "memory.console.cut_all_message.zero": {
        "ar": "فصل «{name}» عن جيرانها؟ يبقى غلاف العقدة.",
        "en": "Detach “{name}” from all {n} neighbors? Node shell stays.",
    },
    "memory.console.cut_all_message.one": {
        "ar": "فصل «{name}» عن جارها الوحيد؟ يبقى غلاف العقدة.",
        "en": "Detach “{name}” from its only neighbor? Node shell stays.",
    },
    "memory.console.cut_all_message.two": {
        "ar": "فصل «{name}» عن جاريها كليهما؟ يبقى غلاف العقدة.",
        "en": "Detach “{name}” from all {n} neighbors? Node shell stays.",
    },
    "memory.console.cut_all_message.few": {
        "ar": "فصل «{name}» عن جيرانها كلها ({n})؟ يبقى غلاف العقدة.",
        "en": "Detach “{name}” from all {n} neighbors? Node shell stays.",
    },
    "memory.console.cut_all_message.many": {
        "ar": "فصل «{name}» عن جيرانها كلها ({n})؟ يبقى غلاف العقدة.",
        "en": "Detach “{name}” from all {n} neighbors? Node shell stays.",
    },
    "memory.console.cut_all_message.other": {
        "ar": "فصل «{name}» عن جيرانها كلها ({n})؟ يبقى غلاف العقدة.",
        "en": "Detach “{name}” from all {n} neighbors? Node shell stays.",
    },
    "memory.console.cut_all": {
        "ar": "اقطع الكل",
        "en": "Cut all",
    },
    "memory.console.inspect_error": {
        "ar": "خطأ في الفحص: {error}",
        "en": "Inspect error: {error}",
    },
    "memory.console.rename_message": {
        "ar": "الاسم المعروض لهذه العقدة. يبقى المعرّف «{id}» كما هو لتبقى المعتقدات مرتبطة بشكل صحيح.",
        "en": "Display name for this node. The id stays \"{id}\" so all beliefs keep linking correctly.",
    },
    "memory.console.rename_title": {
        "ar": "إعادة تسمية العقدة",
        "en": "Rename node",
    },
    "memory.console.rename": {
        "ar": "إعادة تسمية",
        "en": "Rename",
    },
    "memory.console.rename_ph_user": {
        "ar": "مثال: اسمك",
        "en": "e.g. your name",
    },
    "memory.console.rename_ph": {
        "ar": "مثال: اسم مشروع",
        "en": "e.g. a project name",
    },
    "memory.console.name_empty": {
        "ar": "لا يمكن أن يكون الاسم فارغًا",
        "en": "Name cannot be empty",
    },
    "memory.console.rename_failed": {
        "ar": "فشلت إعادة التسمية",
        "en": "Rename failed",
    },
    "memory.console.renamed_to": {
        "ar": "أُعيدت التسمية إلى «{name}»",
        "en": "Renamed to “{name}”",
    },
    "memory.console.site_accent": {
        "ar": "· لون الموقع",
        "en": "· site accent",
    },
    "memory.console.aria_showing": {
        "ar": "المعروض حاليًا: {nodes} و{links}.",
        "en": "Currently showing {nodes} and {links}.",
    },
    "memory.console.aria_focused": {
        "ar": "التركيز على {id}.",
        "en": "Focused on {id}.",
    },
    "memory.console.stats_isolated": {
        "ar": "{n} معزولة",
        "en": "{n} isolated",
    },
    "memory.console.stats_paint": {
        "ar": "العرض من {source}",
        "en": "paint {source}",
    },
    "memory.console.stats_neo4j_online": {
        "ar": "neo4j بكتابة مزدوجة متصل",
        "en": "neo4j dual-write online",
    },
    "memory.console.stats_neo4j_offline": {
        "ar": "neo4j غير متصل",
        "en": "neo4j offline",
    },
    "memory.console.stats_filtered": {
        "ar": "مُصفّاة من {n}",
        "en": "filtered from {n}",
    },
    "memory.console.filter_entity": {
        "ar": "النوع: {type}",
        "en": "Kind: {type}",
    },
    "memory.console.filter_pred": {
        "ar": "الحقيقة: {type}",
        "en": "Fact: {type}",
    },
    "memory.console.reset_all": {
        "ar": "إعادة ضبط الكل",
        "en": "Reset all",
    },
    "memory.console.reuse_predicate": {
        "ar": "يُعاد استخدام محمول موجود.",
        "en": "Reusing existing predicate.",
    },
    "memory.console.similar_predicate": {
        "ar": "مشابه لـ {name}؟ انقر لإعادة استخدامه.",
        "en": "Similar to {name}? Click to reuse.",
    },
    "memory.console.png_downloaded": {
        "ar": "نُزّلت صورة PNG",
        "en": "PNG downloaded",
    },
    "memory.console.png_export_failed": {
        "ar": "فشل تصدير PNG",
        "en": "PNG export failed",
    },
    "memory.console.svg_downloaded": {
        "ar": "نُزّل ملف SVG",
        "en": "SVG downloaded",
    },
    "memory.console.link_mode_cancelled": {
        "ar": "أُلغي الربط",
        "en": "Connecting cancelled",
    },
    "memory.console.merge_mode_cancelled": {
        "ar": "أُلغي الدمج",
        "en": "Joining cancelled",
    },
    "memory.console.slots_cleared": {
        "ar": "مُسح المصدر والهدف",
        "en": "Slots cleared",
    },
    "memory.console.confirm_q": {
        "ar": "تأكيد؟",
        "en": "Confirm?",
    },
    "memory.page.undo_available": {
        "ar": " (يمكن التراجع)",
        "en": " (undo available)",
    },
    "memory.page.undo": {
        "ar": "تراجع",
        "en": "Undo",
    },
    "memory.page.undone": {
        "ar": "تم التراجع: {what}",
        "en": "Undone: {what}",
    },
    "memory.page.action": {
        "ar": "الإجراء",
        "en": "action",
    },
    "memory.page.undo_failed": {
        "ar": "فشل التراجع",
        "en": "Undo failed",
    },
    "memory.page.undo_failed_error": {
        "ar": "فشل التراجع: {error}",
        "en": "Undo failed: {error}",
    },
    "memory.page.chip_beliefs": {
        "ar": "الحقائق",
        "en": "Facts",
    },
    "memory.page.chip_invalidated": {
        "ar": "حقائق مُزالة",
        "en": "Removed facts",
    },
    "memory.page.chip_entities": {
        "ar": "الأشياء",
        "en": "Things",
    },
    "memory.page.chip_empty": {
        "ar": "بلا حقائق",
        "en": "With no facts",
    },
    "memory.page.chip_isolated": {
        "ar": "غير مرتبطة",
        "en": "Unconnected",
    },
    "memory.page.chip_episodes": {
        "ar": "ذكريات المحادثات",
        "en": "Conversation memories",
    },
    "memory.page.showing": {
        "ar": "عرض {start}–{end} من {total}",
        "en": "Showing {start}–{end} of {total}",
    },
    "memory.page.node_not_on_graph": {
        "ar": "العقدة ليست على الرسم (مُصفّاة أو بلا معتقدات)",
        "en": "Node not on graph (filtered out or no beliefs)",
    },
    "memory.page.belief_not_on_graph": {
        "ar": "طرفا المعتقد ليسا على الرسم (جرّب التحديث)",
        "en": "Belief endpoints not on graph (try refresh)",
    },
    "memory.page.invalidated_one": {
        "ar": "أُبطل {id}",
        "en": "Invalidated {id}",
    },
    "memory.page.invalidated_n.zero": {
        "ar": "لم يُبطَل أي معتقد.",
        "en": "Invalidated {n} beliefs.",
    },
    "memory.page.invalidated_n.one": {
        "ar": "أُبطل معتقد واحد.",
        "en": "Invalidated 1 belief.",
    },
    "memory.page.invalidated_n.two": {
        "ar": "أُبطل معتقدان.",
        "en": "Invalidated {n} beliefs.",
    },
    "memory.page.invalidated_n.few": {
        "ar": "أُبطلت {n} معتقدات.",
        "en": "Invalidated {n} beliefs.",
    },
    "memory.page.invalidated_n.many": {
        "ar": "أُبطل {n} معتقدًا.",
        "en": "Invalidated {n} beliefs.",
    },
    "memory.page.invalidated_n.other": {
        "ar": "أُبطل {n} معتقد.",
        "en": "Invalidated {n} beliefs.",
    },
    "memory.page.prompt_object": {
        "ar": "القيمة (نص الحقيقة)",
        "en": "Object (fact text)",
    },
    "memory.page.prompt_predicate": {
        "ar": "المحمول",
        "en": "Predicate",
    },
    "memory.page.prompt_subject": {
        "ar": "الموضوع",
        "en": "Subject",
    },
    "memory.page.edit_title": {
        "ar": "تعديل المعتقد",
        "en": "Edit belief",
    },
    "memory.page.label_subject": {
        "ar": "الموضوع (معرّف الكيان)",
        "en": "Subject (entity id)",
    },
    "memory.page.label_object_ph": {
        "ar": "مثال: ما تقوله الحقيقة…",
        "en": "e.g. what the fact says…",
    },
    "memory.page.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "memory.page.belief_updated": {
        "ar": "حُدّث المعتقد.",
        "en": "Belief updated.",
    },
    "memory.page.deleted_entity": {
        "ar": "حُذف الكيان {id}.",
        "en": "Deleted entity {id}.",
    },
    "memory.page.rename_title": {
        "ar": "إعادة تسمية الكيان",
        "en": "Rename entity",
    },
    "memory.page.rename_hint": {
        "ar": "الاسم المعروض فقط — يبقى المعرّف كما هو لتبقى المعتقدات مرتبطة.",
        "en": "Display name only — id stays the same so beliefs keep linking.",
    },
    "memory.page.id_line": {
        "ar": "المعرّف: {id}",
        "en": "id: {id}",
    },
    "memory.page.rename_native": {
        "ar": "الاسم المعروض الجديد لـ {id}",
        "en": "New display name for {id}",
    },
    "memory.page.merge_need_slots": {
        "ar": "عيّن معرّفي الكيانين المصدر والهدف (أو اخترهما على الرسم)",
        "en": "Set source and target entity ids (or pick on the graph)",
    },
    "memory.page.merged_n.zero": {
        "ar": "دُمج {source} ← {target}: لم يُنقل أي معتقد.",
        "en": "Merged {source} → {target}: {n} beliefs rewired.",
    },
    "memory.page.merged_n.one": {
        "ar": "دُمج {source} ← {target}: نُقل معتقد واحد.",
        "en": "Merged {source} → {target}: 1 belief rewired.",
    },
    "memory.page.merged_n.two": {
        "ar": "دُمج {source} ← {target}: نُقل معتقدان.",
        "en": "Merged {source} → {target}: {n} beliefs rewired.",
    },
    "memory.page.merged_n.few": {
        "ar": "دُمج {source} ← {target}: نُقلت {n} معتقدات.",
        "en": "Merged {source} → {target}: {n} beliefs rewired.",
    },
    "memory.page.merged_n.many": {
        "ar": "دُمج {source} ← {target}: نُقل {n} معتقدًا.",
        "en": "Merged {source} → {target}: {n} beliefs rewired.",
    },
    "memory.page.merged_n.other": {
        "ar": "دُمج {source} ← {target}: نُقل {n} معتقد.",
        "en": "Merged {source} → {target}: {n} beliefs rewired.",
    },
    "memory.page.link_need_slots": {
        "ar": "عيّن المصدر والهدف للربط (أو اخترهما على الرسم)",
        "en": "Set source and target for link (or pick on the graph)",
    },
    "memory.page.linked": {
        "ar": "رُبط {source} —{predicate}← {target}.",
        "en": "Linked {source} —{predicate}→ {target}.",
    },
    "memory.page.linked_already": {
        "ar": "رُبط {source} —{predicate}← {target} (مربوط مسبقًا).",
        "en": "Linked {source} —{predicate}→ {target} (already linked).",
    },
    "memory.page.merge_approved": {
        "ar": "تمت الموافقة على الدمج",
        "en": "Merge approved",
    },
    "memory.page.merge_rejected": {
        "ar": "رُفض الدمج",
        "en": "Merge rejected",
    },
    "memory.page.hygiene_complete": {
        "ar": "تم الترتيب",
        "en": "Clean-up done",
    },
    "memory.page.hygiene_failed": {
        "ar": "فشل الترتيب",
        "en": "The clean-up failed",
    },
    "memory.console.none": {
        "ar": "لا شيء",
        "en": "None",
    },
    "memory.console.fts_docs.zero": {
        "ar": "{size} (لا مستندات)",
        "en": "{size} ({n} docs)",
    },
    "memory.console.fts_docs.one": {
        "ar": "{size} (مستند واحد)",
        "en": "{size} (1 doc)",
    },
    "memory.console.fts_docs.two": {
        "ar": "{size} (مستندان)",
        "en": "{size} ({n} docs)",
    },
    "memory.console.fts_docs.few": {
        "ar": "{size} ({n} مستندات)",
        "en": "{size} ({n} docs)",
    },
    "memory.console.fts_docs.many": {
        "ar": "{size} ({n} مستندًا)",
        "en": "{size} ({n} docs)",
    },
    "memory.console.fts_docs.other": {
        "ar": "{size} ({n} مستند)",
        "en": "{size} ({n} docs)",
    },
    "memory.console.restore_title": {
        "ar": "استعادة نسخة احتياطية",
        "en": "Restore backup",
    },
    "memory.console.restore": {
        "ar": "استعادة",
        "en": "Restore",
    },
    "memory.console.restore_complete": {
        "ar": "اكتملت الاستعادة",
        "en": "Restore complete",
    },
    "memory.console.error": {
        "ar": "خطأ",
        "en": "Error",
    },
    "memory.console.restoration_error": {
        "ar": "خطأ في الاستعادة: {error}",
        "en": "Restoration error: {error}",
    },
    "memory.console.unknown_error": {
        "ar": "خطأ غير معروف",
        "en": "unknown error",
    },
    "memory.console.restoration_failed": {
        "ar": "فشلت الاستعادة: {error}",
        "en": "Restoration failed: {error}",
    },
    "memory.console.backups_load_failed": {
        "ar": "فشل تحميل النسخ الاحتياطية.",
        "en": "Failed to load backups.",
    },
    "memory.console.backup_complete": {
        "ar": "اكتمل النسخ الاحتياطي",
        "en": "Backup complete",
    },
    "memory.console.optimize_fts": {
        "ar": "• حُسّن فهرس الكلمات FTS5 (اكتمل VACUUM وANALYZE).\n  المساحة المستعادة: {size}\n\n",
        "en": "• FTS5 keyword index optimized (VACUUM & ANALYZE completed).\n  Reclaimed space: {size}\n\n",
    },
    "memory.console.optimize_vector": {
        "ar": "• حُسّن فهرس المتجهات.\n  المساحة المستعادة: {size}\n",
        "en": "• Vector index optimized.\n  Reclaimed space: {size}\n",
    },
    "memory.console.optimize_complete": {
        "ar": "اكتمل التحسين",
        "en": "Optimization complete",
    },
    "memory.console.clean_up": {
        "ar": "تنظيف",
        "en": "Clean up",
    },
    # The graph's truncation notice and query-path toast (2026-10-01): written
    # in English in every language until then.
    "memory.console.hidden_connections": {
        "ar": "روابط مخفية: {n}",
        "en": "connections hidden: {n}",
    },
    "memory.console.graph_trunc_notice": {
        "ar": "{summary} — استخدم التصفية لتضييق العرض.",
        "en": "{summary} — filter to narrow the view.",
    },
    "memory.console.path_highlighted.zero": {
        "ar": "عُقد المسار المُبرزة: {n}",
        "en": "Path: {n} nodes highlighted",
    },
    "memory.console.path_highlighted.one": {
        "ar": "عُقد المسار المُبرزة: {n}",
        "en": "Path: 1 node highlighted",
    },
    "memory.console.path_highlighted.two": {
        "ar": "عُقد المسار المُبرزة: {n}",
        "en": "Path: {n} nodes highlighted",
    },
    "memory.console.path_highlighted.few": {
        "ar": "عُقد المسار المُبرزة: {n}",
        "en": "Path: {n} nodes highlighted",
    },
    "memory.console.path_highlighted.many": {
        "ar": "عُقد المسار المُبرزة: {n}",
        "en": "Path: {n} nodes highlighted",
    },
    "memory.console.path_highlighted.other": {
        "ar": "عُقد المسار المُبرزة: {n}",
        "en": "Path: {n} nodes highlighted",
    },
    "memory.console.path_no_match": {
        "ar": "لا عُقد تطابق مسار الاستعلام",
        "en": "No matching nodes for the query path",
    },
    # The graph's hover tips (2026-10-01).
    "memory.console.tip_link_pick": {
        "ar": "انقر لاختياره للربط",
        "en": "click to choose it for Connect",
    },
    "memory.console.tip_merge_pick": {
        "ar": "انقر لاختياره للدمج",
        "en": "click to choose it for Join",
    },
    "memory.console.tip_you": {
        "ar": "أنت · مركز الذاكرة",
        "en": "you · center of memory",
    },
    "memory.console.tip_type": {
        "ar": "النوع: {type}",
        "en": "type: {type}",
    },
    "memory.console.tip_high_stakes": {
        "ar": "لا يُدمج تلقائيًا مع اسم مشابه",
        "en": "never joined automatically with a similar name",
    },
    "memory.console.tip_fact": {
        "ar": "حقيقة",
        "en": "fact",
    },
    "memory.console.tip_edge": {
        "ar": "رابط",
        "en": "edge",
    },
    "memory.console.tip_edge_click": {
        "ar": "انقر للتعديل أو فك الربط",
        "en": "click to edit / unlink",
    },
    "memory.console.snapshots_deleted.zero": {
        "ar": "لا لقطات أقدم من {days}",
        "en": "{n} snapshots older than {days}",
    },
    "memory.console.snapshots_deleted.one": {
        "ar": "لقطة واحدة أقدم من {days}",
        "en": "1 snapshot older than {days}",
    },
    "memory.console.snapshots_deleted.two": {
        "ar": "لقطتان أقدم من {days}",
        "en": "{n} snapshots older than {days}",
    },
    "memory.console.snapshots_deleted.few": {
        "ar": "{n} لقطات أقدم من {days}",
        "en": "{n} snapshots older than {days}",
    },
    "memory.console.snapshots_deleted.many": {
        "ar": "{n} لقطة أقدم من {days}",
        "en": "{n} snapshots older than {days}",
    },
    "memory.console.snapshots_deleted.other": {
        "ar": "{n} لقطة أقدم من {days}",
        "en": "{n} snapshots older than {days}",
    },
    "memory.console.snapshots_reclaimed": {
        "ar": "استُعيد {size}",
        "en": "{size} reclaimed",
    },
    "memory.console.snapshots_prune": {
        "ar": "التقليم: {state}",
        "en": "prune: {state}",
    },
    "memory.console.snapshots_vacuum": {
        "ar": "الضغط: {state}",
        "en": "vacuum: {state}",
    },
    "memory.console.snapshots_done_title": {
        "ar": "اكتمل تنظيف اللقطات",
        "en": "Snapshot cleanup complete",
    },
    "memory.console.snapshots_nothing": {
        "ar": "لا شيء للتنظيف — لا لقطات خارج نافذة الاحتفاظ.",
        "en": "Nothing to clean — no snapshots outside the retention window.",
    },
    "memory.console.auto_maintain_on": {
        "ar": "\n\nالصيانة التلقائية مفعّلة (يوميًا).",
        "en": "\n\nAuto-maintenance is ON (daily).",
    },
    "memory.console.auto_maintain_off": {
        "ar": "\n\nالصيانة التلقائية متوقفة — شغّلها يدويًا من هنا.",
        "en": "\n\nAuto-maintenance is OFF — run manually here.",
    },
    "memory.console.cleanup_failed": {
        "ar": "فشل التنظيف: {error}",
        "en": "Cleanup failed: {error}",
    },
    "memory.console.bytes": {
        "ar": "بايت",
        "en": "Bytes",
    },
    "memory.pg.show_on_graph": {
        "ar": "اعرض على الرسم",
        "en": "Show on graph",
    },
    "memory.pg.memory_hub_on_graph": {
        "ar": "محور الذاكرة على الرسم",
        "en": "Memory hub on graph",
    },
    "memory.pg.protect": {
        "ar": "حماية",
        "en": "Protect",
    },
    "memory.pg.unprotect": {
        "ar": "إلغاء الحماية",
        "en": "Unprotect",
    },
    "memory.pg.protect_hint": {
        "ar": "منع حذفه أو دمجه في كيان آخر",
        "en": "Keep it from being deleted or merged into another entity",
    },
    "memory.pg.unprotect_hint": {
        "ar": "السماح بحذفه ودمجه مرة أخرى",
        "en": "Allow deleting and merging it again",
    },
    "memory.pg.core_always_protected": {
        "ar": "محمي دائمًا: أحد الكيانات الأساسية (أنت، Kazma)",
        "en": "Always protected: one of the core entities (you, Kazma)",
    },
    "memory.pg.unprotect_first": {
        "ar": "محمي: ألغِ الحماية لتتمكن من حذفه",
        "en": "Protected: unprotect it to delete it",
    },
    "memory.page.protected_entity": {
        "ar": "حُمي {id}: لا يمكن حذفه أو دمجه.",
        "en": "Protected {id}: it cannot be deleted or merged away.",
    },
    "memory.page.unprotected_entity": {
        "ar": "لم يعد {id} محميًا.",
        "en": "{id} is no longer protected.",
    },
    "memory.pg.search_beliefs": {
        "ar": "البحث في المعتقدات",
        "en": "Search beliefs",
    },
    "memory.pg.search_entities": {
        "ar": "البحث في الكيانات",
        "en": "Search entities",
    },
    "memory.pg.select_belief": {
        "ar": "تحديد المعتقد {name}",
        "en": "Select belief {name}",
    },
    "memory.mc.filter_graph": {
        "ar": "تصفية مخطط المعتقدات",
        "en": "Filter the belief graph",
    },
    "memory.mc.filter_beliefs": {
        "ar": "تصفية المعتقدات المعروفة",
        "en": "Filter known beliefs",
    },
    # A memory kept whole, shown bounded (2026-11-19, memory/limits.py).
    "memory.console.whole_memory": {
        "ar": "الذاكرة كاملة",
        "en": "The whole memory",
    },
    "memory.console.more_chars.zero": {
        "ar": "لا أحرف أخرى",
        "en": "{n} more characters",
    },
    "memory.console.more_chars.one": {
        "ar": "حرف واحد آخر",
        "en": "1 more character",
    },
    "memory.console.more_chars.two": {
        "ar": "حرفان آخران",
        "en": "{n} more characters",
    },
    "memory.console.more_chars.few": {
        "ar": "{n} أحرف أخرى",
        "en": "{n} more characters",
    },
    "memory.console.more_chars.many": {
        "ar": "{n} حرفًا آخر",
        "en": "{n} more characters",
    },
    "memory.console.more_chars.other": {
        "ar": "{n} حرف آخر",
        "en": "{n} more characters",
    },
    "memory.console.whole_next": {
        "ar": "اعرض الجزء التالي",
        "en": "Show the next part",
    },
    "memory.console.whole_failed": {
        "ar": "تعذّرت قراءة الذاكرة. لم تُحذف.",
        "en": "The memory could not be read. It is not gone.",
    },
    "memory.console.whole_range": {
        "ar": "الأحرف من {start} إلى {end} من أصل {total}",
        "en": "Characters {start}-{end} of {total}",
    },
}
