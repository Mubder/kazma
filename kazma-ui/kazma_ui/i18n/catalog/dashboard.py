"""``dashboard`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "dashboard.active_capabilities": {
        "ar": "القدرات النشطة",
        "en": "Active Capabilities",
    },
    "dashboard.available_backups": {
        "ar": "النسخ الاحتياطية المتاحة",
        "en": "Available Backups",
    },
    "dashboard.backend": {
        "ar": "الخادم",
        "en": "backend",
    },
    "dashboard.backup_failed": {
        "ar": "فشل النسخ الاحتياطي",
        "en": "Backup failed",
    },
    "dashboard.backup_maintenance": {
        "ar": "النسخ الاحتياطي وصيانة قاعدة البيانات",
        "en": "Backup & Database Maintenance",
    },
    "dashboard.backup_success": {
        "ar": "اكتملت النسخة الاحتياطية بنجاح",
        "en": "Backup completed successfully",
    },
    "dashboard.cap.cua": {
        "ar": "استخدام الحاسوب",
        "en": "Computer use",
    },
    "dashboard.cap.cua_desc": {
        "ar": "حلقة لقطة→إجراء؛ CUA من Anthropic أو Gemini عندما يكون النموذج نشطاً",
        "en": "Screenshot→action loop; Anthropic CUA / Gemini when that model is active",
    },
    "dashboard.cap.culture": {
        "ar": "العربية وRTL",
        "en": "Arabic & RTL",
    },
    "dashboard.cap.culture_desc": {
        "ar": "لهجة خليجية وواجهة RTL وبروتوكول المجلس وترجمة",
        "en": "Khaleeji dialect, RTL UI, Majlis protocol, i18n",
    },
    "dashboard.cap.doc": {
        "ar": "ذكاء المستندات",
        "en": "Document Intelligence",
    },
    "dashboard.cap.doc_desc": {
        "ar": "استلام وتعرّف ضوئي وفهرسة وتنقيح وتوليد PDF/DOCX — مهام دائمة وليست محللاً لمرة واحدة",
        "en": "Intake, OCR, index, redact, generate PDF/DOCX — durable jobs, not a one-shot parser",
    },
    "dashboard.cap.email": {
        "ar": "البريد",
        "en": "Email",
    },
    "dashboard.cap.email_desc": {
        "ar": "مصادقة Gmail وMicrosoft وصندوق حماية",
        "en": "Gmail / Microsoft OAuth + sandbox mailbox",
    },
    "dashboard.cap.hitl": {
        "ar": "HITL والالتزام",
        "en": "HITL + commitment",
    },
    "dashboard.cap.hitl_desc": {
        "ar": "ثلاث بوابات موافقة وتأكيد دلالي وHITL لعيّنات MCP",
        "en": "Three approval gates, semantic confirm, MCP sampling HITL",
    },
    "dashboard.cap.ide": {
        "ar": "IDE وفهرس الشيفرة",
        "en": "IDE + code index",
    },
    "dashboard.cap.ide_desc": {
        "ar": "Monaco وترقيع الملفات وcodebase_search (tree-sitter أو regex)",
        "en": "Monaco, apply-patch, codebase_search (tree-sitter extra or regex)",
    },
    "dashboard.cap.mcp": {
        "ar": "MCP والمهارات",
        "en": "MCP + skills",
    },
    "dashboard.cap.mcp_desc": {
        "ar": "أدوات وموارد MCP؛ عيّنات HITL؛ تثبيت agentskills.io",
        "en": "MCP tools/resources/prompts; sampling HITL; agentskills.io install",
    },
    "dashboard.cap.memory": {
        "ar": "الذاكرة المعرفية",
        "en": "Cognitive memory",
    },
    "dashboard.cap.memory_desc": {
        "ar": "معتقدات V2 وحلقات ورسم PPR واسترجاع sqlite-vec / pgvector",
        "en": "V2 beliefs, episodes, PPR graph, sqlite-vec / pgvector recall",
    },
    "dashboard.cap.swarm": {
        "ar": "محرك السرب",
        "en": "Swarm engine",
    },
    "dashboard.cap.swarm_desc": {
        "ar": "توسيع تلقائي وقواطع دوائر وإرسال Temporal اختياري",
        "en": "Autoscaler, circuit breakers, optional Temporal durable dispatch",
    },
    "dashboard.cap.time": {
        "ar": "السفر عبر الزمن",
        "en": "Time travel",
    },
    "dashboard.cap.time_desc": {
        "ar": "إعادة تشغيل اللقطات وتفريع حالة المحادثة",
        "en": "Snapshot replay and fork of conversation state",
    },
    "dashboard.cap.voice": {
        "ar": "صوت ثنائي الاتجاه",
        "en": "Voice duplex",
    },
    "dashboard.cap.voice_desc": {
        "ar": "LiveKit مع إلغاء صدى ومقاطعة على الويب؛ STT/TTS عبر الرسم البياني",
        "en": "LiveKit AEC + barge-in on web; STT/TTS still through the graph",
    },
    "dashboard.cap.web": {
        "ar": "بحث الويب",
        "en": "Web research",
    },
    "dashboard.cap.web_desc": {
        "ar": "بحث وقراءة وزحف مع Firecrawl/Jina وبروكسي اختياري",
        "en": "web_search, read_url, crawl + Firecrawl/Jina salvage and optional proxy",
    },
    "dashboard.cap.x": {
        "ar": "ناشر إكس",
        "en": "X publisher",
    },
    "dashboard.cap.x_desc": {
        "ar": "تغريدات واجهة إكس الرسمية — OAuth 1.0a وموافقة دائمة وحدود شروط الاستخدام",
        "en": "Official X API v2 tweets — OAuth 1.0a, always-on HITL, ToU caps",
    },
    "dashboard.circuit_breaker": {
        "ar": "قاطع الدائرة",
        "en": "Circuit Breaker",
    },
    "dashboard.clear_all": {
        "ar": "مسح الكل",
        "en": "Clear All",
    },
    "dashboard.clear_all_confirm": {
        "ar": "مسح كل الموافقات المعلقة؟",
        "en": "Clear all pending approvals?",
    },
    "dashboard.col_action": {
        "ar": "إجراء",
        "en": "Action",
    },
    "dashboard.col_actions": {
        "ar": "إجراءات",
        "en": "Actions",
    },
    "dashboard.col_cost": {
        "ar": "التكلفة",
        "en": "Cost",
    },
    "dashboard.col_created": {
        "ar": "تاريخ الإنشاء",
        "en": "Created",
    },
    "dashboard.col_date": {
        "ar": "تاريخ الإنشاء",
        "en": "Date Created",
    },
    "dashboard.col_duration": {
        "ar": "المدة",
        "en": "Duration",
    },
    "dashboard.col_keyword": {
        "ar": "حجم الكلمات",
        "en": "Keyword Size",
    },
    "dashboard.col_label": {
        "ar": "الوصف",
        "en": "Label",
    },
    "dashboard.col_messages": {
        "ar": "الرسائل",
        "en": "Messages",
    },
    "dashboard.col_name": {
        "ar": "اسم النسخة",
        "en": "Backup Name",
    },
    "dashboard.col_platform": {
        "ar": "المنصة",
        "en": "Platform",
    },
    "dashboard.col_status": {
        "ar": "الحالة",
        "en": "Status",
    },
    "dashboard.col_thread_id": {
        "ar": "معرف الجلسة",
        "en": "Thread ID",
    },
    "dashboard.col_time": {
        "ar": "الوقت",
        "en": "Time",
    },
    "dashboard.col_tokens": {
        "ar": "الرموز",
        "en": "Tokens",
    },
    "dashboard.col_type": {
        "ar": "النوع",
        "en": "Type",
    },
    "dashboard.col_user": {
        "ar": "المستخدم",
        "en": "User",
    },
    "dashboard.col_vector": {
        "ar": "حجم المتجهات",
        "en": "Vector Size",
    },
    "dashboard.comp.auto_store": {
        "ar": "التخزين التلقائي",
        "en": "Auto-store",
    },
    "dashboard.comp.consolidation": {
        "ar": "الموحّد",
        "en": "Consolidator",
    },
    "dashboard.comp.embedder": {
        "ar": "المضمّن",
        "en": "Embedder",
    },
    "dashboard.comp.layer_l1": {
        "ar": "L1 كروما",
        "en": "L1 Chroma",
    },
    "dashboard.comp.layer_l2": {
        "ar": "L2 الرسم",
        "en": "L2 Graph",
    },
    "dashboard.comp.layer_l3": {
        "ar": "L3 FTS5",
        "en": "L3 FTS5",
    },
    "dashboard.comp.layer_l4": {
        "ar": "L4 sqlite-vec",
        "en": "L4 sqlite-vec",
    },
    "dashboard.comp.memory_enabled": {
        "ar": "الذاكرة مفعّلة",
        "en": "Memory enabled",
    },
    "dashboard.comp.per_turn_retrieval": {
        "ar": "الاسترجاع لكل دورة",
        "en": "Per-turn retrieval",
    },
    "dashboard.comp.pkg_chromadb": {
        "ar": "حزمة: chromadb",
        "en": "Package: chromadb",
    },
    "dashboard.comp.pkg_sqlite_vec": {
        "ar": "حزمة: sqlite-vec",
        "en": "Package: sqlite-vec",
    },
    "dashboard.comp.pkg_st": {
        "ar": "حزمة: sentence-transformers",
        "en": "Package: sentence-transformers",
    },
    "dashboard.comp.vector_memory": {
        "ar": "ذاكرة المتجهات",
        "en": "Vector memory",
    },
    "dashboard.confirm_clear_all": {
        "ar": "حذف جميع الجلسات؟ لا يمكن التراجع عن هذا الإجراء.",
        "en": "Delete ALL sessions? This cannot be undone.",
    },
    "dashboard.confirm_delete_session": {
        "ar": "حذف الجلسة {thread_id}؟",
        "en": "Delete session {thread_id}?",
    },
    "dashboard.confirm_restore": {
        "ar": "هل أنت متأكد تماماً من رغبتك في استعادة النسخة الاحتياطية",
        "en": "Are you absolutely sure you want to restore backup",
    },
    "dashboard.connecting": {
        "ar": "جاري الاتصال…",
        "en": "Connecting…",
    },
    "dashboard.cost_over_time": {
        "ar": "التكلفة عبر الوقت",
        "en": "Cost Over Time",
    },
    "dashboard.cpu": {
        "ar": "المعالج",
        "en": "CPU",
    },
    "dashboard.create_backup": {
        "ar": "إنشاء نسخة احتياطية ساخنة",
        "en": "Create Hot Backup",
    },
    "dashboard.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "dashboard.error_clearing": {
        "ar": "خطأ في مسح الجلسات",
        "en": "Error clearing sessions",
    },
    "dashboard.error_deleting": {
        "ar": "خطأ في حذف الجلسة",
        "en": "Error deleting session",
    },
    "dashboard.export": {
        "ar": "تصدير",
        "en": "Export",
    },
    "dashboard.fts5_db": {
        "ar": "الحلقات",
        "en": "Episodes",
    },
    "dashboard.headroom": {
        "ar": "هامش متاح",
        "en": "headroom",
    },
    "dashboard.hitl_allow_tool": {
        "ar": "السماح بالأداة",
        "en": "Allow tool",
    },
    "dashboard.hitl_approved": {
        "ar": "تمت الموافقة",
        "en": "Approved — complete",
    },
    "dashboard.hitl_approving": {
        "ar": "جاري الموافقة…",
        "en": "Approving…",
    },
    "dashboard.hitl_denied": {
        "ar": "مرفوض",
        "en": "Denied",
    },
    "dashboard.hitl_deny": {
        "ar": "رفض",
        "en": "Deny",
    },
    "dashboard.hitl_denying": {
        "ar": "جاري الرفض…",
        "en": "Denying…",
    },
    "dashboard.hitl_description": {
        "ar": "مراجعة تنفيذ الأدوات بمشاركة الإنسان",
        "en": "Human-in-the-Loop tool execution review",
    },
    "dashboard.hitl_dismiss": {
        "ar": "إغلاق",
        "en": "Dismiss",
    },
    "dashboard.hitl_executing": {
        "ar": "تنفيذ الأداة: {name}…",
        "en": "Executing tool: {name}…",
    },
    "dashboard.hitl_once": {
        "ar": "مرة واحدة",
        "en": "Once",
    },
    "dashboard.hitl_running": {
        "ar": "تشغيل الأداة المعتمدة…",
        "en": "Running approved tool…",
    },
    "dashboard.hitl_stream_unavailable": {
        "ar": "البث غير متاح",
        "en": "Streaming unavailable",
    },
    "dashboard.hitl_yolo": {
        "ar": "YOLO",
        "en": "YOLO",
    },
    "dashboard.install_failed": {
        "ar": "فشل بدء التثبيت.",
        "en": "Failed to trigger installation.",
    },
    "dashboard.install_ml": {
        "ar": "تثبيت تبعيات التعلم الآلي",
        "en": "Install ML Dependencies",
    },
    "dashboard.installing": {
        "ar": "جارٍ التثبيت...",
        "en": "Installing...",
    },
    "dashboard.kpi_l1_chroma": {
        "ar": "معتقدات نشطة",
        "en": "active beliefs",
    },
    "dashboard.kpi_l2_graph": {
        "ar": "كيانات نشطة",
        "en": "active entities",
    },
    "dashboard.kpi_l3_bm25": {
        "ar": "حلقات نشطة",
        "en": "active episodes",
    },
    "dashboard.kpi_ok": {
        "ar": "سليم",
        "en": "OK",
    },
    "dashboard.llm_calls": {
        "ar": "استدعاءات النماذج",
        "en": "LLM calls",
    },
    "dashboard.loading_backups": {
        "ar": "جارٍ تحميل النسخ الاحتياطية...",
        "en": "Loading backups...",
    },
    "dashboard.memory": {
        "ar": "الذاكرة",
        "en": "Memory",
    },
    "dashboard.memory_active": {
        "ar": "نشط",
        "en": "ACTIVE",
    },
    "dashboard.memory_bitemporal": {
        "ar": "فرك زمني ثنائي",
        "en": "Bi-Temporal Scrub",
    },
    "dashboard.memory_bitemporal_hint": {
        "ar": "اسحب لرؤية الحالات السابقة.",
        "en": "Drag to see past states.",
    },
    "dashboard.memory_components": {
        "ar": "صحة المكوّنات",
        "en": "Component health",
    },
    "dashboard.memory_degraded": {
        "ar": "مخفّض",
        "en": "DEGRADED",
    },
    "dashboard.memory_desc_active": {
        "ar": "مكوّنات الذاكرة تعمل بشكل سليم.",
        "en": "Memory stack healthy.",
    },
    "dashboard.memory_desc_degraded": {
        "ar": "النظام يعمل في وضع مخفض باستخدام الكلمات المفتاحية فقط. تبعيات التعلم الآلي مفقودة.",
        "en": "System running in degraded mode using keyword-only fallback. ML dependencies missing.",
    },
    "dashboard.memory_desc_installing": {
        "ar": "جارٍ تثبيت تبعيات التعلم الآلي في الخلفية. يرجى الانتظار...",
        "en": "Installing ML dependencies in the background. Please wait...",
    },
    "dashboard.memory_governance": {
        "ar": "الذاكرة والحوكمة",
        "en": "Memory & Governance",
    },
    "dashboard.memory_graph_clear": {
        "ar": "مسح الرسم",
        "en": "Clear graph",
    },
    "dashboard.memory_graph_cleared": {
        "ar": "تم مسح الرسم",
        "en": "Graph cleared",
    },
    "dashboard.memory_graph_click_hint": {
        "ar": "انقر للتحديد · اسحب للتحريك · العجلة للتكبير",
        "en": "click to select · drag to move · wheel to zoom",
    },
    "dashboard.memory_graph_confirm_clear": {
        "ar": "مسح رسم العلاقات بالكامل؟ لا يمكن التراجع.",
        "en": "Clear the entire property graph? This cannot be undone.",
    },
    "dashboard.memory_graph_edges": {
        "ar": "حافة",
        "en": "edges",
    },
    "dashboard.memory_graph_empty": {
        "ar": "لا توجد عقد بعد. الذكريات الدائمة أو الدمج سيملآن الطبقة الثانية.",
        "en": "No graph nodes yet. Chat with durable facts or consolidation will populate L2.",
    },
    "dashboard.memory_graph_nodes": {
        "ar": "عقدة",
        "en": "nodes",
    },
    "dashboard.memory_graph_nodes_list": {
        "ar": "العقد",
        "en": "Nodes",
    },
    "dashboard.memory_graph_refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "dashboard.memory_graph_search": {
        "ar": "بحث",
        "en": "Search",
    },
    "dashboard.memory_graph_search_ph": {
        "ar": "ابحث في العقد…",
        "en": "Search nodes…",
    },
    "dashboard.memory_graph_title": {
        "ar": "الكيانات",
        "en": "Entities",
    },
    "dashboard.memory_graph_truncated": {
        "ar": "عرض أول {n} من {total} عقدة",
        "en": "showing first {n} of {total} nodes",
    },
    "dashboard.memory_group_features": {
        "ar": "الميزات",
        "en": "Features",
    },
    "dashboard.memory_group_layers": {
        "ar": "طبقات الذاكرة",
        "en": "Memory layers",
    },
    "dashboard.memory_group_packages": {
        "ar": "الحزم والمخازن",
        "en": "Packages & stores",
    },
    "dashboard.memory_health_kpi": {
        "ar": "الصحة",
        "en": "Health",
    },
    "dashboard.memory_include_kb": {
        "ar": "تضمين مكتبة المعرفة",
        "en": "Include Knowledge Library",
    },
    "dashboard.memory_installing": {
        "ar": "جارٍ التثبيت",
        "en": "INSTALLING",
    },
    "dashboard.memory_issues": {
        "ar": "يحتاج انتباهًا",
        "en": "Needs attention",
    },
    "dashboard.memory_legend_chunk": {
        "ar": "ذاكرة",
        "en": "memory",
    },
    "dashboard.memory_legend_entity": {
        "ar": "كيان",
        "en": "entity",
    },
    "dashboard.memory_legend_person": {
        "ar": "شخص",
        "en": "person",
    },
    "dashboard.memory_legend_tag": {
        "ar": "وسم",
        "en": "tag",
    },
    "dashboard.memory_live": {
        "ar": "مباشر",
        "en": "Live",
    },
    "dashboard.memory_live_now": {
        "ar": "مباشر (الآن)",
        "en": "Live (now)",
    },
    "dashboard.memory_pause": {
        "ar": "إيقاف",
        "en": "Pause",
    },
    "dashboard.memory_pipe_auto_store": {
        "ar": "تخزين تلقائي",
        "en": "Auto-store",
    },
    "dashboard.memory_pipe_consolidator": {
        "ar": "الموحّد",
        "en": "Consolidator",
    },
    "dashboard.memory_pipe_enabled": {
        "ar": "الذاكرة",
        "en": "Memory",
    },
    "dashboard.memory_pipe_per_turn": {
        "ar": "RAG لكل دورة",
        "en": "Per-turn RAG",
    },
    "dashboard.memory_play": {
        "ar": "تشغيل",
        "en": "Play",
    },
    "dashboard.memory_probe_federated": {
        "ar": "موحّد",
        "en": "Federated",
    },
    "dashboard.memory_probe_memory": {
        "ar": "الذاكرة",
        "en": "Memory",
    },
    "dashboard.memory_probing": {
        "ar": "جارٍ فحص أنظمة الذاكرة…",
        "en": "Probing memory subsystems…",
    },
    "dashboard.memory_refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "dashboard.memory_search_all": {
        "ar": "البحث في كل المعرفة",
        "en": "Search all knowledge",
    },
    "dashboard.na": {
        "ar": "غير متوفر",
        "en": "N/A",
    },
    "dashboard.no_active_sessions": {
        "ar": "لا توجد جلسات نشطة",
        "en": "No active sessions",
    },
    "dashboard.no_backups": {
        "ar": "لا توجد نسخ احتياطية. أنشئ واحدة أعلاه!",
        "en": "No backups found. Create one above!",
    },
    "dashboard.no_component_data": {
        "ar": "لا توجد بيانات مكوّنات بعد.",
        "en": "No component data yet.",
    },
    "dashboard.no_pending_approvals": {
        "ar": "لا توجد موافقات معلقة",
        "en": "No pending approvals",
    },
    "dashboard.no_pending_approvals_hint": {
        "ar": "الأدوات التي تتطلب موافقة بشرية ستظهر هنا",
        "en": "Tools requiring human approval will appear here",
    },
    "dashboard.no_traces": {
        "ar": "لا توجد تتبعات بعد",
        "en": "No traces yet",
    },
    "dashboard.no_traces_hint": {
        "ar": "تظهر التتبعات عندما تعالج كاظمه الطلبات",
        "en": "Traces appear when Kazma processes requests",
    },
    "dashboard.observability": {
        "ar": "لوحة المراقبة",
        "en": "Observability Dashboard",
    },
    "dashboard.optimize_db": {
        "ar": "تحسين قاعدة البيانات",
        "en": "Optimize Database",
    },
    "dashboard.optimize_failed": {
        "ar": "فشل التحسين",
        "en": "Optimization failed",
    },
    "dashboard.optimize_success": {
        "ar": "اكتمل التحسين بنجاح!",
        "en": "Optimization completed successfully!",
    },
    "dashboard.pending_approvals": {
        "ar": "الموافقات المعلقة",
        "en": "Pending Approvals",
    },
    "dashboard.persistence": {
        "ar": "التخزين",
        "en": "Persistence",
    },
    "dashboard.range_day": {
        "ar": "يوم",
        "en": "24H",
    },
    "dashboard.range_hour": {
        "ar": "ساعة",
        "en": "1H",
    },
    "dashboard.range_week": {
        "ar": "أسبوع",
        "en": "7D",
    },
    "dashboard.recent_traces": {
        "ar": "أحدث التتبعات",
        "en": "Recent Traces",
    },
    "dashboard.records": {
        "ar": "سجل",
        "en": "records",
    },
    "dashboard.refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "dashboard.restore": {
        "ar": "استعادة",
        "en": "Restore",
    },
    "dashboard.restore_success": {
        "ar": "تمت الاستعادة وإعادة تحميل مخازن الذاكرة بنجاح!",
        "en": "Successfully restored and hot-reloaded memory stores!",
    },
    "dashboard.restore_warning": {
        "ar": "سيؤدي هذا إلى استبدال حالة قاعدة بيانات الذاكرة الحالية.",
        "en": "This will overwrite the current memory database state.",
    },
    "dashboard.restoring": {
        "ar": "جارٍ الاستعادة...",
        "en": "Restoring...",
    },
    "dashboard.session_management": {
        "ar": "إدارة الجلسات",
        "en": "Session Management",
    },
    "dashboard.sessions_hint": {
        "ar": "تظهر الجلسات هنا عندما يبدأ المستخدمون بالمحادثة",
        "en": "Sessions appear here when users start chatting",
    },
    "dashboard.show_less_sessions": {
        "ar": "عرض أقل",
        "en": "Show less",
    },
    "dashboard.show_more_sessions": {
        "ar": "عرض المزيد من الجلسات",
        "en": "Show more sessions",
    },
    "dashboard.snapshot_maintain": {
        "ar": "تنظيف اللقطات",
        "en": "Clean Up Snapshots",
    },
    "dashboard.snapshot_maintain_confirm": {
        "ar": "ستُحذف اللقطات الأقدم من نافذة الاحتفاظ وتُضغط قاعدة البيانات لاستعادة مساحة القرص. يبقى سجل الإعادة داخل النافذة محفوظًا.",
        "en": "Snapshots older than the retention window will be deleted and the database vacuumed to reclaim disk space. Replay history inside the window is kept.",
    },
    "dashboard.snapshot_maintain_title": {
        "ar": "تنظيف لقطات السفر عبر الزمن؟",
        "en": "Clean up time-travel snapshots?",
    },
    "dashboard.system_resources": {
        "ar": "موارد النظام",
        "en": "System Resources",
    },
    "dashboard.title": {
        "ar": "لوحة التحكم",
        "en": "Dashboard",
    },
    "dashboard.tokens_over_time": {
        "ar": "الرموز عبر الوقت",
        "en": "Tokens Over Time",
    },
    "dashboard.tool_calls": {
        "ar": "استدعاءات الأدوات",
        "en": "Tool Calls",
    },
    "dashboard.total_cost": {
        "ar": "التكلفة الإجمالية",
        "en": "Total Cost",
    },
    "dashboard.total_tokens": {
        "ar": "إجمالي الرموز",
        "en": "Total Tokens",
    },
    "dashboard.traces_count": {
        "ar": "تتبع",
        "en": "traces",
    },
    "dashboard.uptime": {
        "ar": "وقت التشغيل",
        "en": "Uptime",
    },
    "dashboard.vector_store": {
        "ar": "المعتقدات",
        "en": "Beliefs",
    },
    "dashboard.vectors": {
        "ar": "متجه",
        "en": "vectors",
    },
}
