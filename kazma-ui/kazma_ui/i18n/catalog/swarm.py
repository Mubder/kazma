"""``swarm`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "swarm.accumulated_cost": {
        "ar": "التكلفة المتراكمة",
        "en": "Accumulated Cost",
    },
    "swarm.activated_step": {
        "ar": "تنشيط (مرحلة ",
        "en": "activated (Step ",
    },
    "swarm.active_node": {
        "ar": "العقدة النشطة",
        "en": "Active Node",
    },
    "swarm.active_tasks_hint": {
        "ar": "أرسل مهمة من منشئ المهام لرؤية التقدم المباشر هنا",
        "en": "Submit a task from the Task Builder to see live progress here",
    },
    "swarm.active_tasks_sse_hint": {
        "ar": "سلاسل التسليم، نقاط توقف HITL، وتقدم كل عامل تظهر في الوقت الحقيقي عبر SSE",
        "en": "Handoff chains, HITL checkpoints, and per-worker progress shown in real-time via SSE",
    },
    "swarm.add_step": {
        "ar": "إضافة مرحلة",
        "en": "Add Step",
    },
    "swarm.add_template": {
        "ar": "إضافة قالب",
        "en": "Add Template",
    },
    "swarm.add_worker": {
        "ar": "إضافة عامل",
        "en": "Add Worker",
    },
    "swarm.add_workers_hint": {
        "ar": "استخدم النموذج لإضافة أو إنشاء عمال",
        "en": "Use the form to add or spawn workers",
    },
    "swarm.advanced_options": {
        "ar": "خيارات متقدمة",
        "en": "Advanced Options",
    },
    "swarm.agg.collect": {
        "ar": "تجميع — إرجاع كل النتائج",
        "en": "Collect — Return all results",
    },
    "swarm.agg.first_valid": {
        "ar": "الأول الصالح — أقدم نتيجة بدون خطأ",
        "en": "First Valid — Earliest non-error",
    },
    "swarm.agg.merge_all": {
        "ar": "دمج الكل — جمع المخرجات",
        "en": "Merge All — Combine outputs",
    },
    "swarm.agg.synthesize": {
        "ar": "توليف — دمج مدعوم بالنموذج",
        "en": "Synthesize — LLM-powered consolidation",
    },
    "swarm.agg.vote": {
        "ar": "تصويت — أغلبية",
        "en": "Vote — Majority agreement",
    },
    "swarm.aggregated_output": {
        "ar": "المخرجات المجمّعة",
        "en": "Aggregated Output",
    },
    "swarm.aggregation_strategy": {
        "ar": "استراتيجية التجميع",
        "en": "Aggregation Strategy",
    },
    "swarm.all_results": {
        "ar": "جميع النتائج",
        "en": "All Results",
    },
    "swarm.all_statuses": {
        "ar": "جميع الحالات",
        "en": "All Statuses",
    },
    "swarm.all_types": {
        "ar": "جميع الأنواع",
        "en": "All Types",
    },
    "swarm.all_workers": {
        "ar": "الكل",
        "en": "all",
    },
    "swarm.api_key_hint": {
        "ar": "اختياري",
        "en": "optional",
    },
    "swarm.api_key_optional": {
        "ar": "مفتاح API",
        "en": "API Key",
    },
    "swarm.approval_failed": {
        "ar": "فشلت الموافقة: {msg}",
        "en": "Approval failed: {msg}",
    },
    "swarm.approve": {
        "ar": "موافقة",
        "en": "Approve",
    },
    "swarm.approve_continue": {
        "ar": "موافقة ومتابعة",
        "en": "Approve & Continue",
    },
    "swarm.approve_failed": {
        "ar": "فشلت الموافقة: {msg}",
        "en": "Approve failed: {msg}",
    },
    "swarm.arabic_dialect": {
        "ar": "اللهجة العربية: ",
        "en": "Arabic Dialect: ",
    },
    "swarm.avg_latency": {
        "ar": "متوسط الاستجابة",
        "en": "Avg Latency",
    },
    "swarm.awaiting_confirm": {
        "ar": "). بانتظار تأكيد المستخدم...",
        "en": "). Awaiting user confirmation...",
    },
    "swarm.backend": {
        "ar": "الخادم",
        "en": "Backend",
    },
    "swarm.boost_label": {
        "ar": "+{boost} تعزيز",
        "en": "+{boost} Boost",
    },
    "swarm.busy": {
        "ar": "نشط",
        "en": "busy",
    },
    "swarm.cancel_failed": {
        "ar": "فشل الإلغاء: {msg}",
        "en": "Cancel failed: {msg}",
    },
    "swarm.cancel_task": {
        "ar": "إلغاء المهمة",
        "en": "Cancel Task",
    },
    "swarm.checkpoint_approved": {
        "ar": "تمت الموافقة على نقطة التحقق، استئناف السلسلة",
        "en": "Checkpoint approved, pipeline resuming",
    },
    "swarm.checkpoint_rejected": {
        "ar": "تم رفض نقطة التحقق، تم إحباط السلسلة",
        "en": "Checkpoint rejected, pipeline aborted",
    },
    "swarm.checkpoint_step": {
        "ar": "نقطة توقف HITL — الخطوة {step}",
        "en": "HITL Checkpoint — Step {step}",
    },
    "swarm.circuit_failures": {
        "ar": "الفشل: {n}/{threshold}",
        "en": "Failures: {n}/{threshold}",
    },
    "swarm.clear": {
        "ar": "مسح",
        "en": "Clear",
    },
    "swarm.clear_all": {
        "ar": "مسح الكل",
        "en": "Clear All",
    },
    "swarm.coding": {
        "ar": "البرمجة",
        "en": "Coding",
    },
    "swarm.completed": {
        "ar": "مكتملة",
        "en": "completed",
    },
    "swarm.completed_status": {
        "ar": "اكتمل بالحالة: ",
        "en": "completed with status: ",
    },
    "swarm.completed_tasks_hint": {
        "ar": "ستظهر المهام المكتملة هنا مع تصورات خاصة بالنمط",
        "en": "Complete tasks will appear here with pattern-specific visualizations",
    },
    "swarm.context": {
        "ar": "السياق",
        "en": "Context",
    },
    "swarm.context_hint": {
        "ar": "معلومات خيارية للخلفية",
        "en": "optional background information",
    },
    "swarm.context_placeholder": {
        "ar": "سياق إضافي، قيود، أو خلفية…",
        "en": "Additional context, constraints, or background…",
    },
    "swarm.cost": {
        "ar": "التكلفة",
        "en": "Cost",
    },
    "swarm.create_task": {
        "ar": "إنشاء مهمة",
        "en": "Create Task",
    },
    "swarm.create_task_btn": {
        "ar": "إنشاء مهمة",
        "en": "Create Task",
    },
    "swarm.creative": {
        "ar": "الإبداع",
        "en": "Creative",
    },
    "swarm.custom": {
        "ar": "مخصص",
        "en": "Custom",
    },
    "swarm.detail_context": {
        "ar": "السياق:",
        "en": "Context:",
    },
    "swarm.detail_duration": {
        "ar": "المدة:",
        "en": "Duration:",
    },
    "swarm.detail_prompt": {
        "ar": "المهمة:",
        "en": "Prompt:",
    },
    "swarm.detail_status": {
        "ar": "الحالة:",
        "en": "Status:",
    },
    "swarm.detail_type": {
        "ar": "النوع:",
        "en": "Type:",
    },
    "swarm.detail_workers": {
        "ar": "العمال:",
        "en": "Workers:",
    },
    "swarm.diag_boost": {
        "ar": "تعزيز اللهجة",
        "en": "Dialect Boost",
    },
    "swarm.diag_overlap": {
        "ar": "تطابق الكلمات المفتاحية",
        "en": "Keyword Overlap",
    },
    "swarm.diag_routed": {
        "ar": "موجّه",
        "en": "Routed",
    },
    "swarm.diag_score": {
        "ar": "النتيجة النهائية",
        "en": "Final Score",
    },
    "swarm.diag_similarity": {
        "ar": "التشابه الدلالي",
        "en": "Semantic Similarity",
    },
    "swarm.diag_worker": {
        "ar": "العامل",
        "en": "Worker",
    },
    "swarm.diag_yes": {
        "ar": "نعم",
        "en": "YES",
    },
    "swarm.diagram_cleared": {
        "ar": "تم مسح المخطط. أضف مراحل...",
        "en": "Diagram cleared. Populate steps...",
    },
    "swarm.dialect_khaleeji": {
        "ar": "اللهجة الخليجية",
        "en": "Khaleeji Dialect",
    },
    "swarm.dialect_msa": {
        "ar": "العربية الفصحى",
        "en": "Modern Standard Arabic",
    },
    "swarm.dispatch_btn": {
        "ar": "إرسال المهمة",
        "en": "Dispatch Task",
    },
    "swarm.dispatch_failed": {
        "ar": "فشل الإرسال",
        "en": "Dispatch failed",
    },
    "swarm.dispatch_failed_msg": {
        "ar": "فشل الإرسال: {msg}",
        "en": "Dispatch failed: {msg}",
    },
    "swarm.dispatching": {
        "ar": "جارٍ الإرسال...",
        "en": "Dispatching...",
    },
    "swarm.done": {
        "ar": "تم",
        "en": "Done",
    },
    "swarm.duration": {
        "ar": "المدة",
        "en": "Duration",
    },
    "swarm.dynamic_spawn": {
        "ar": "إنشاء ديناميكي",
        "en": "Dynamic Spawn",
    },
    "swarm.edit_template": {
        "ar": "تحرير القالب",
        "en": "Edit Template",
    },
    "swarm.edit_worker": {
        "ar": "تعديل العامل",
        "en": "Edit worker",
    },
    "swarm.edit_worker_modal": {
        "ar": "تعديل العامل",
        "en": "Edit Worker",
    },
    "swarm.enable_routing": {
        "ar": "تفعيل توجيه المخرجات",
        "en": "Enable output routing",
    },
    "swarm.err_add_step": {
        "ar": "يرجى إضافة مرحلة واحدة على الأقل.",
        "en": "Please add at least one step to the pipeline.",
    },
    "swarm.err_chat_id": {
        "ar": "أدخل معرّف محادثة لتفعيل التوجيه.",
        "en": "Enter a chat ID to enable routing.",
    },
    "swarm.err_invalid_routes": {
        "ar": "JSON المسارات غير صالح",
        "en": "Invalid routes JSON",
    },
    "swarm.err_master_prompt": {
        "ar": "نص المهمة الرئيسية مطلوب.",
        "en": "Master Task Prompt is required.",
    },
    "swarm.err_prompt_required": {
        "ar": "مطلوب نص المهمة",
        "en": "Task prompt is required",
    },
    "swarm.err_select_model": {
        "ar": "يرجى اختيار نموذج",
        "en": "Please select a model",
    },
    "swarm.err_select_worker": {
        "ar": "اختر عاملاً واحداً على الأقل",
        "en": "Select at least one worker",
    },
    "swarm.err_worker_name_missing": {
        "ar": "اسم العامل مفقود",
        "en": "Worker name is missing",
    },
    "swarm.err_worker_name_required": {
        "ar": "اسم العامل مطلوب",
        "en": "Worker name is required",
    },
    "swarm.error": {
        "ar": "خطأ",
        "en": "error",
    },
    "swarm.error_loading_task": {
        "ar": "خطأ في تحميل المهمة: ",
        "en": "Error loading task: ",
    },
    "swarm.example": {
        "ar": "مثال",
        "en": "Example",
    },
    "swarm.example_prompt": {
        "ar": "اجمع وحلل تقارير السوق، وراجعها وفق إرشادات السياسات، ثم صغ ملخصاً تنفيذياً.",
        "en": "Compile and analyze market reports, review for policy guidelines, and draft executive summary.",
    },
    "swarm.executing": {
        "ar": "⏳ جارٍ التنفيذ...",
        "en": "⏳ Executing...",
    },
    "swarm.expertise_hint": {
        "ar": "مفصولة بفواصل",
        "en": "comma separated",
    },
    "swarm.expertise_placeholder": {
        "ar": "python, api_design, database",
        "en": "python, api_design, database",
    },
    "swarm.expertise_tags": {
        "ar": "علامات الخبرة",
        "en": "Expertise Tags",
    },
    "swarm.failed_to_load_logs": {
        "ar": "فشل تحميل السجلات",
        "en": "Failed to load logs",
    },
    "swarm.fallback_worker": {
        "ar": "العامل البديل",
        "en": "Fallback Worker",
    },
    "swarm.fast": {
        "ar": "سريع",
        "en": "Fast",
    },
    "swarm.filter_status": {
        "ar": "تصفية حسب الحالة",
        "en": "Filter by status",
    },
    "swarm.filter_type": {
        "ar": "تصفية حسب النوع",
        "en": "Filter by type",
    },
    "swarm.frontend": {
        "ar": "الواجهة",
        "en": "Frontend",
    },
    "swarm.handoff": {
        "ar": "تسليم: ",
        "en": "Handoff: ",
    },
    "swarm.handoff_label": {
        "ar": "تسليم السرب: ",
        "en": "Swarm Handoff: ",
    },
    "swarm.hint_broadcast": {
        "ar": "سيتم استهداف جميع العمال المسجلين (الاختيار اختياري)",
        "en": "All registered workers will be targeted (selection optional)",
    },
    "swarm.hint_conditional": {
        "ar": "اختر عاملاً موجّهاً وعمال الوجهة",
        "en": "Select a router worker and destination workers",
    },
    "swarm.hint_consult": {
        "ar": "اختر العمال لتقديم آراء مستقلة",
        "en": "Select workers to provide independent opinions",
    },
    "swarm.hint_dispatch": {
        "ar": "اختر عاملاً واحداً للإرسال",
        "en": "Select a single worker for dispatch",
    },
    "swarm.hint_fan_out": {
        "ar": "اختر العمال للتنفيذ المتوازي",
        "en": "Select workers for parallel execution",
    },
    "swarm.hint_pipeline": {
        "ar": "اختر العمال بترتيب التنفيذ: الأول ← الأوسط ← الأخير",
        "en": "Select workers in execution order: first → middle → last",
    },
    "swarm.hitl_approval": {
        "ar": "موافقة التدخل البشري",
        "en": "Human-in-the-Loop Approval",
    },
    "swarm.hitl_checkpoint": {
        "ar": "مواجهة نقطة تحقق بشرية (مرحلة ",
        "en": "Human-in-the-Loop checkpoint encountered (Step ",
    },
    "swarm.individual_opinions": {
        "ar": "الآراء الفردية (جنباً إلى جنب)",
        "en": "Individual Opinions (Side-by-Side)",
    },
    "swarm.initializing_pipeline": {
        "ar": "جارٍ تهيئة جلسة السلسلة...",
        "en": "Initializing pipeline session...",
    },
    "swarm.initializing_swarm": {
        "ar": "جارٍ تهيئة جلسة السرب...",
        "en": "Initializing swarm session...",
    },
    "swarm.instances": {
        "ar": "نشط",
        "en": "active",
    },
    "swarm.invalid_metadata_json": {
        "ar": "JSON البيانات الوصفية غير صالح: {msg}",
        "en": "Invalid metadata JSON: {msg}",
    },
    "swarm.load_history_hint": {
        "ar": "انقر على تحديث أو انتقل هنا لتحميل السجل",
        "en": "Click refresh or navigate here to load history",
    },
    "swarm.loading": {
        "ar": "جاري التحميل…",
        "en": "Loading…",
    },
    "swarm.loading_logs": {
        "ar": "جارٍ تحميل السجلات…",
        "en": "Loading logs…",
    },
    "swarm.loading_status": {
        "ar": "جاري تحميل حالة السرب…",
        "en": "Loading swarm status…",
    },
    "swarm.logs": {
        "ar": "السجلات",
        "en": "Logs",
    },
    "swarm.logs_live": {
        "ar": "سجل النشاط المباشر",
        "en": "Live Activity Log",
    },
    "swarm.logs_title": {
        "ar": "السجلات: {name}",
        "en": "Logs: {name}",
    },
    "swarm.master_task_ph": {
        "ar": "ما الذي يجب أن تعالجه السلسلة المتسلسلة؟",
        "en": "What should the sequential pipeline process?",
    },
    "swarm.master_task_prompt": {
        "ar": "المهمة الرئيسية",
        "en": "Master Task Prompt",
    },
    "swarm.max_retry_count": {
        "ar": "أقصى عدد محاولات",
        "en": "Max Retry Count",
    },
    "swarm.mermaid_not_loaded": {
        "ar": "مكتبة Mermaid غير محمّلة.",
        "en": "Mermaid library not loaded.",
    },
    "swarm.mermaid_render_error": {
        "ar": "خطأ في عرض Mermaid:",
        "en": "Mermaid Render Error:",
    },
    "swarm.metadata": {
        "ar": "البيانات الوصفية",
        "en": "Metadata",
    },
    "swarm.metadata_json": {
        "ar": "بيانات وصفية JSON",
        "en": "Metadata JSON",
    },
    "swarm.model": {
        "ar": "النموذج",
        "en": "Model",
    },
    "swarm.model_auto": {
        "ar": "تلقائي (الأفضل المتاح)",
        "en": "auto (best available)",
    },
    "swarm.model_specialty": {
        "ar": "تخصص النموذج",
        "en": "Model Specialty",
    },
    "swarm.network_clearing_routing": {
        "ar": "خطأ في الشبكة أثناء مسح توجيه المخرجات.",
        "en": "Network error clearing output routing.",
    },
    "swarm.network_error": {
        "ar": "خطأ في الشبكة: ",
        "en": "Network error: ",
    },
    "swarm.network_error_action": {
        "ar": "خطأ في الشبكة {action}: {msg}",
        "en": "Network error {action}: {msg}",
    },
    "swarm.network_error_approving": {
        "ar": "خطأ في الشبكة أثناء الموافقة: {msg}",
        "en": "Network error approving: {msg}",
    },
    "swarm.network_error_dispatching": {
        "ar": "خطأ في الشبكة أثناء الإرسال: {msg}",
        "en": "Network error dispatching: {msg}",
    },
    "swarm.network_error_rejecting": {
        "ar": "خطأ في الشبكة أثناء الرفض: {msg}",
        "en": "Network error rejecting: {msg}",
    },
    "swarm.network_routing_failed": {
        "ar": "خطأ في الشبكة أثناء حفظ توجيه المخرجات.",
        "en": "Network error saving output routing.",
    },
    "swarm.network_saving_routing": {
        "ar": "خطأ في الشبكة أثناء حفظ توجيه المخرجات.",
        "en": "Network error saving output routing.",
    },
    "swarm.next": {
        "ar": "التالي ←",
        "en": "Next →",
    },
    "swarm.no_active_tasks": {
        "ar": "لا توجد مهام نشطة",
        "en": "No active tasks",
    },
    "swarm.no_completed_tasks": {
        "ar": "لا توجد مهام مكتملة بعد",
        "en": "No completed tasks yet",
    },
    "swarm.no_logs": {
        "ar": "لا توجد سجلات بعد",
        "en": "No logs yet",
    },
    "swarm.no_logs_yet": {
        "ar": "لا توجد سجلات بعد",
        "en": "No logs yet",
    },
    "swarm.no_models": {
        "ar": "— لا توجد نماذج متاحة —",
        "en": "— no models available —",
    },
    "swarm.no_preview": {
        "ar": "لا توجد معاينة",
        "en": "No preview",
    },
    "swarm.no_preview_available": {
        "ar": "لا توجد معاينة متاحة.",
        "en": "No preview available.",
    },
    "swarm.no_steps_added": {
        "ar": "لم تُضاف مراحل بعد. انقر \"إضافة مرحلة\" أعلاه.",
        "en": "No steps added yet. Click \"Add Step\" above.",
    },
    "swarm.no_synthesis": {
        "ar": "لم تُرجع أي مخرجات توليف نهائية.",
        "en": "No final synthesis output returned.",
    },
    "swarm.no_task_history": {
        "ar": "لم يتم تحميل سجل المهام",
        "en": "No task history loaded",
    },
    "swarm.no_task_id": {
        "ar": "لم يُرجع الإرسال معرّف مهمة.",
        "en": "No task_id returned by dispatch.",
    },
    "swarm.no_tasks_dispatched": {
        "ar": "لم يتم إرسال مهام بعد",
        "en": "No tasks dispatched yet",
    },
    "swarm.no_tasks_found": {
        "ar": "لا توجد مهام",
        "en": "No tasks found",
    },
    "swarm.no_templates": {
        "ar": "لا توجد قوالب معرفة. لن يتم إنشاء العمال تلقائيًا حتى تضيف قالبًا.",
        "en": "No templates defined. Workers won't auto-spawn until you add one.",
    },
    "swarm.no_workers": {
        "ar": "لا يوجد عمال مسجلون",
        "en": "No workers registered",
    },
    "swarm.no_workers_registered": {
        "ar": "لا يوجد عمال مسجلون. أضف عمالاً في تبويب سجل العمال.",
        "en": "No workers registered. Add workers in the Worker Registry tab.",
    },
    "swarm.no_yaml": {
        "ar": "لا يوجد نص YAML لنسخه.",
        "en": "Visual pipeline has no YAML text to copy.",
    },
    "swarm.none": {
        "ar": "لا شيء",
        "en": "None",
    },
    "swarm.none_fail_closed": {
        "ar": "لا شيء (فشل مغلق)",
        "en": "None (Fail Closed)",
    },
    "swarm.observer": {
        "ar": "المراقب",
        "en": "Observer",
    },
    "swarm.orchestration_pattern": {
        "ar": "نمط التنسيق",
        "en": "Orchestration Pattern",
    },
    "swarm.orchestrator": {
        "ar": "المنسّق",
        "en": "Orchestrator",
    },
    "swarm.output_routing": {
        "ar": "توجيه المخرجات",
        "en": "Output Routing",
    },
    "swarm.output_routing_desc": {
        "ar": "إرسال نتائج السرب إلى محادثة تيليجرام (بالإضافة للمحادثة الأصلية). استخدم بوت سرب مخصص للتوجيه المباشر، أو البوت الرئيسي للمجموعات.",
        "en": "Mirror swarm results to a Telegram chat (in addition to the originating chat). Use a dedicated swarm bot for DM routing, or the main bot for group routing.",
    },
    "swarm.page": {
        "ar": "صفحة",
        "en": "Page",
    },
    "swarm.page_x_of_y": {
        "ar": "صفحة {x} من {y}",
        "en": "Page {x} of {y}",
    },
    "swarm.pattern.broadcast": {
        "ar": "بث — جميع العمال بالتوازي",
        "en": "Broadcast — All workers in parallel",
    },
    "swarm.pattern.broadcast_short": {
        "ar": "بث",
        "en": "Broadcast",
    },
    "swarm.pattern.conditional": {
        "ar": "شرطي — توجيه بناءً على التقييم",
        "en": "Conditional — Route based on evaluation",
    },
    "swarm.pattern.conditional_short": {
        "ar": "شرطي",
        "en": "Conditional",
    },
    "swarm.pattern.consult": {
        "ar": "استشارة — آراء متعددة + توليف",
        "en": "Consult — Multi-opinion + synthesis",
    },
    "swarm.pattern.consult_short": {
        "ar": "استشارة",
        "en": "Consult",
    },
    "swarm.pattern.dispatch": {
        "ar": "إرسال — تنفيذ عامل واحد",
        "en": "Dispatch — Single worker execution",
    },
    "swarm.pattern.dispatch_short": {
        "ar": "إرسال",
        "en": "Dispatch",
    },
    "swarm.pattern.fan_out": {
        "ar": "توزيع — تنفيذ متوازي + تجميع",
        "en": "Fan-Out — Parallel execution + aggregation",
    },
    "swarm.pattern.fan_out_short": {
        "ar": "توزيع",
        "en": "Fan-Out",
    },
    "swarm.pattern.pipeline": {
        "ar": "سلسلة — تسلسل متتالي (أ ← ب ← ج)",
        "en": "Pipeline — Sequential chain (A → B → C)",
    },
    "swarm.pattern.pipeline_short": {
        "ar": "سلسلة",
        "en": "Pipeline",
    },
    "swarm.pattern_hint_dispatch": {
        "ar": "اختر عاملاً أو أكثر للإرسال",
        "en": "Select one or more workers for dispatch",
    },
    "swarm.pending": {
        "ar": "قيد الانتظار",
        "en": "pending",
    },
    "swarm.pipeline_checkpoint_approved": {
        "ar": "تمت الموافقة على نقطة تحقق السلسلة. متابعة التنفيذ...",
        "en": "Pipeline checkpoint approved. Continuing execution...",
    },
    "swarm.pipeline_checkpoint_rejected": {
        "ar": "تم رفض نقطة تحقق السلسلة. تم إحباط المهمة.",
        "en": "Pipeline checkpoint rejected. Task aborted.",
    },
    "swarm.pipeline_finalized": {
        "ar": "اكتمل تنفيذ السلسلة.",
        "en": "Pipeline execution finalized.",
    },
    "swarm.pipeline_gated": {
        "ar": "السلسلة متوقفة: بانتظار الموافقة",
        "en": "Pipeline Gated: Awaiting Approval",
    },
    "swarm.pipeline_logs": {
        "ar": "سجلات تشغيل السلسلة",
        "en": "Pipeline Run Logs",
    },
    "swarm.pipeline_stages": {
        "ar": "مراحل السلسلة المتسلسلة",
        "en": "Pipeline Sequence Stages",
    },
    "swarm.pipeline_started": {
        "ar": "بدأ تنفيذ السلسلة.",
        "en": "Pipeline execution started.",
    },
    "swarm.pipeline_verified": {
        "ar": "تم التحقق من السلسلة",
        "en": "Pipeline Verified",
    },
    "swarm.play_hint_broadcast": {
        "ar": "بث: يرسل نصك إلى جميع العمال المحددين بالتوازي.",
        "en": "Broadcast: Broadcasts your prompt to all selected workers in parallel.",
    },
    "swarm.play_hint_conditional": {
        "ar": "شرطي: يقيّم الشرط لتحديد المسار.",
        "en": "Conditional: Evaluates condition to determine route.",
    },
    "swarm.play_hint_consult": {
        "ar": "استشارة: يجمع آراء متعددة ويولّفها.",
        "en": "Consult: Gathers multiple opinions and synthesizes them.",
    },
    "swarm.play_hint_dispatch": {
        "ar": "إرسال: يشغّل عاملاً واحداً على نصك.",
        "en": "Dispatch: Runs a single worker on your prompt.",
    },
    "swarm.play_hint_fan_out": {
        "ar": "توزيع: ينفذ العمال بالتوازي، ثم يجمع النتائج.",
        "en": "Fan-Out: Executes workers in parallel, then aggregates results.",
    },
    "swarm.play_hint_pipeline": {
        "ar": "سلسلة: ينفذ العمال المحددين بالتسلسل كسلسلة (أ ب ج).",
        "en": "Pipeline: Executes selected workers sequentially as a chain (A B C).",
    },
    "swarm.playground_approved": {
        "ar": "تمت الموافقة على نقطة التحقق في الساحة.",
        "en": "Playground checkpoint approved.",
    },
    "swarm.playground_idle": {
        "ar": "الطرفية خاملة. اضبط المدخلات واضغط \"تشغيل المهمة\" لمراقبة السجلات المباشرة...",
        "en": "Playground terminal idle. Set your inputs and click \"Run Task\" to observe live trace logs...",
    },
    "swarm.playground_rejected": {
        "ar": "تم رفض نقطة التحقق في الساحة. توقف التنفيذ.",
        "en": "Playground checkpoint rejected. Execution stopped.",
    },
    "swarm.playground_title": {
        "ar": "مساحة تجربة تنفيذ المهام",
        "en": "Task Execution Playground",
    },
    "swarm.prev": {
        "ar": "→ السابق",
        "en": "← Prev",
    },
    "swarm.prompt": {
        "ar": "الموجه",
        "en": "Prompt",
    },
    "swarm.prompt_col": {
        "ar": "الموجه",
        "en": "Prompt",
    },
    "swarm.prompt_placeholder": {
        "ar": "صف المهمة للعمال…",
        "en": "Describe the task for the workers…",
    },
    "swarm.provider": {
        "ar": "المزود",
        "en": "Provider",
    },
    "swarm.reap_idle": {
        "ar": "إزالة الخامل",
        "en": "Reap Idle",
    },
    "swarm.reaped": {
        "ar": "تمت الإزالة",
        "en": "Reaped",
    },
    "swarm.reasoning": {
        "ar": "التفكير",
        "en": "Reasoning",
    },
    "swarm.recent_results": {
        "ar": "النتائج الأخيرة",
        "en": "Recent Results",
    },
    "swarm.refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "swarm.registered_workers": {
        "ar": "العمال المسجلون",
        "en": "Registered Workers",
    },
    "swarm.reject": {
        "ar": "رفض",
        "en": "Reject",
    },
    "swarm.reject_failed": {
        "ar": "فشل الرفض: {msg}",
        "en": "Reject failed: {msg}",
    },
    "swarm.reject_stop": {
        "ar": "رفض وإيقاف",
        "en": "Reject & Stop",
    },
    "swarm.rejection_failed": {
        "ar": "فشل الرفض: {msg}",
        "en": "Rejection failed: {msg}",
    },
    "swarm.remove_worker": {
        "ar": "إزالة العامل",
        "en": "Remove worker",
    },
    "swarm.remove_worker_confirm": {
        "ar": "إزالة العامل \"{name}\" من السرب؟",
        "en": "Remove worker \"{name}\" from the swarm?",
    },
    "swarm.rerendering": {
        "ar": "إعادة الرسم...",
        "en": "Re-rendering...",
    },
    "swarm.researcher": {
        "ar": "الباحث",
        "en": "Researcher",
    },
    "swarm.reset": {
        "ar": "إعادة تعيين",
        "en": "Reset",
    },
    "swarm.results.conditional": {
        "ar": "شرطي",
        "en": "Conditional",
    },
    "swarm.results.consult": {
        "ar": "استشارة",
        "en": "Consult",
    },
    "swarm.results.dispatch": {
        "ar": "إرسال",
        "en": "Dispatch",
    },
    "swarm.results.fan_out": {
        "ar": "توزيع",
        "en": "Fan-Out",
    },
    "swarm.results.pipeline": {
        "ar": "سلسلة",
        "en": "Pipeline",
    },
    "swarm.results_appear_hint": {
        "ar": "ستظهر النتائج هنا بعد إرسال مهمة",
        "en": "Results will appear here after dispatching a task",
    },
    "swarm.retry_failed": {
        "ar": "فشلت إعادة المحاولة",
        "en": "Failed to retry task",
    },
    "swarm.retry_failed_msg": {
        "ar": "فشلت إعادة المحاولة: {msg}",
        "en": "Retry failed: {msg}",
    },
    "swarm.retry_task": {
        "ar": "إعادة المحاولة",
        "en": "Retry Task",
    },
    "swarm.reviewer": {
        "ar": "المراجع",
        "en": "Reviewer",
    },
    "swarm.role": {
        "ar": "الدور",
        "en": "Role",
    },
    "swarm.role_hint": {
        "ar": "مثال: backend, researcher",
        "en": "e.g., backend, researcher",
    },
    "swarm.routed_to": {
        "ar": "تم التوجيه إلى: ",
        "en": "Routed to: ",
    },
    "swarm.routing_active": {
        "ar": "● نشط ← {id}{mode}",
        "en": "● Active → {id}{mode}",
    },
    "swarm.routing_cleared": {
        "ar": "تم مسح توجيه المخرجات.",
        "en": "Output routing cleared.",
    },
    "swarm.routing_diagnostics": {
        "ar": "تشخيصات التوجيه الموحدة",
        "en": "Unified Routing Diagnostics",
    },
    "swarm.routing_disabled": {
        "ar": "● معطّل",
        "en": "● Disabled",
    },
    "swarm.routing_gateway": {
        "ar": " (البوابة)",
        "en": " (gateway)",
    },
    "swarm.routing_rules": {
        "ar": "قواعد التوجيه",
        "en": "Routing Rules",
    },
    "swarm.routing_rules_hint": {
        "ar": "تعيين JSON لأسماء المسارات إلى أسماء العمال",
        "en": "JSON mapping of route names to worker names",
    },
    "swarm.routing_save_failed": {
        "ar": "فشل حفظ توجيه المخرجات.",
        "en": "Failed to save output routing.",
    },
    "swarm.routing_saved": {
        "ar": "تم حفظ توجيه المخرجات.",
        "en": "Output routing saved.",
    },
    "swarm.routing_swarm_bot": {
        "ar": " (بوت السرب)",
        "en": " (swarm bot)",
    },
    "swarm.run_pipeline": {
        "ar": "تشغيل السلسلة المرئية",
        "en": "Run Visual Pipeline",
    },
    "swarm.run_pipeline_btn": {
        "ar": "تشغيل السلسلة المرئية",
        "en": "Run Visual Pipeline",
    },
    "swarm.run_task": {
        "ar": "تشغيل المهمة",
        "en": "Run Task",
    },
    "swarm.run_task_btn": {
        "ar": "تشغيل المهمة",
        "en": "Run Task",
    },
    "swarm.running": {
        "ar": "قيد التشغيل",
        "en": "Running",
    },
    "swarm.running_lower": {
        "ar": "قيد التشغيل",
        "en": "running",
    },
    "swarm.save": {
        "ar": "حفظ",
        "en": "Save",
    },
    "swarm.save_failed": {
        "ar": "فشل الحفظ",
        "en": "Save failed",
    },
    "swarm.save_template": {
        "ar": "حفظ القالب",
        "en": "Save Template",
    },
    "swarm.search": {
        "ar": "بحث",
        "en": "Search",
    },
    "swarm.search_placeholder": {
        "ar": "البحث بمعرف المهمة أو الموجه…",
        "en": "Search by task ID or prompt…",
    },
    "swarm.select_worker": {
        "ar": "اختر عاملاً واحداً للإرسال",
        "en": "Select a single worker for dispatch",
    },
    "swarm.spawn_name_hint": {
        "ar": "مثال: python-expert",
        "en": "e.g., python-expert",
    },
    "swarm.spawn_worker": {
        "ar": "إنشاء عامل",
        "en": "Spawn Worker",
    },
    "swarm.start_all": {
        "ar": "تشغيل الكل",
        "en": "Start All",
    },
    "swarm.start_worker": {
        "ar": "تشغيل العامل",
        "en": "Start worker",
    },
    "swarm.start_worker_failed": {
        "ar": "فشل تشغيل العامل",
        "en": "Failed to start worker",
    },
    "swarm.state_awaiting": {
        "ar": "بانتظار الموافقة",
        "en": "AWAITING APPROVAL",
    },
    "swarm.state_idle": {
        "ar": "خامل",
        "en": "IDLE",
    },
    "swarm.state_inspector": {
        "ar": "مراقب الحالة",
        "en": "State Inspector",
    },
    "swarm.state_rejected": {
        "ar": "مرفوض",
        "en": "REJECTED",
    },
    "swarm.state_running": {
        "ar": "قيد التشغيل",
        "en": "RUNNING",
    },
    "swarm.status": {
        "ar": "الحالة",
        "en": "Status",
    },
    "swarm.status_cancelled": {
        "ar": "ملغى",
        "en": "Cancelled",
    },
    "swarm.status_col": {
        "ar": "الحالة",
        "en": "Status",
    },
    "swarm.status_completed": {
        "ar": "مكتمل",
        "en": "Completed",
    },
    "swarm.status_failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "swarm.status_partial": {
        "ar": "جزئي",
        "en": "Partial",
    },
    "swarm.status_running": {
        "ar": "● السرب يعمل — {count} عامل نشط",
        "en": "● Swarm running — {count} worker(s) active",
    },
    "swarm.status_stopped": {
        "ar": "● السرب متوقف — {count} عامل مسجل",
        "en": "● Swarm stopped — {count} worker(s) registered",
    },
    "swarm.status_timeout": {
        "ar": "انتهت المهلة",
        "en": "Timeout",
    },
    "swarm.step": {
        "ar": "الخطوة",
        "en": "Step",
    },
    "swarm.step_n": {
        "ar": "مرحلة {n}",
        "en": "Step {n}",
    },
    "swarm.step_timeout": {
        "ar": "مهلة الخطوة (ثانية)",
        "en": "Step Timeout (sec)",
    },
    "swarm.step_x_of_pipeline": {
        "ar": "المرحلة {n} من تسلسل السلسلة.",
        "en": "Step {n} of pipeline sequence.",
    },
    "swarm.stop_all": {
        "ar": "إيقاف الكل",
        "en": "Stop All",
    },
    "swarm.stop_worker": {
        "ar": "إيقاف العامل",
        "en": "Stop worker",
    },
    "swarm.stop_worker_failed": {
        "ar": "فشل إيقاف العامل",
        "en": "Failed to stop worker",
    },
    "swarm.stopped": {
        "ar": "متوقف",
        "en": "Stopped",
    },
    "swarm.strategy": {
        "ar": "الاستراتيجية: ",
        "en": "Strategy: ",
    },
    "swarm.success": {
        "ar": "النجاح",
        "en": "Success",
    },
    "swarm.swarm_active": {
        "ar": "السرب نشط",
        "en": "swarm active",
    },
    "swarm.swarm_bot_token": {
        "ar": "رمز بوت السرب المخصص",
        "en": "Dedicated Swarm Bot Token",
    },
    "swarm.swarm_bot_token_desc": {
        "ar": "أنشئ بوتاً منفصلاً عبر @BotFather، أرسل /start، ثم الصق الرمز هنا. ستُرسل المخرجات عبر هذا البوت مباشرة.",
        "en": "Create a separate bot via @BotFather, send it /start, then paste the token here. Output will be sent via this bot directly (bypasses group membership issues).",
    },
    "swarm.swarm_bot_token_hint": {
        "ar": "(اختياري — لبوت منفصل مباشر)",
        "en": "(optional — for separate bot DM)",
    },
    "swarm.swarm_idle": {
        "ar": "السرب خامل",
        "en": "swarm idle",
    },
    "swarm.swarm_running_workers": {
        "ar": "السرب يعمل — {count} عامل نشط",
        "en": "Swarm running — {count} worker(s) active",
    },
    "swarm.swarm_stopped_workers": {
        "ar": "السرب متوقف — {count} عامل مسجل",
        "en": "Swarm stopped — {count} worker(s) registered",
    },
    "swarm.synthesis_output": {
        "ar": "مخرجات التوليف:",
        "en": "Synthesis Output:",
    },
    "swarm.synthesized_answer": {
        "ar": "الإجابة المولّفة",
        "en": "Synthesized Answer",
    },
    "swarm.system_prompt_desc": {
        "ar": "— شخصية/تعليمات العامل (اختياري)",
        "en": "— worker personality/instructions (optional)",
    },
    "swarm.system_prompt_ph": {
        "ar": "أنت مهندس خلفية خبير تكتب كوداً نظيفاً ومختبراً...",
        "en": "You are a senior backend engineer who writes clean, tested code...",
    },
    "swarm.tab_active_tasks": {
        "ar": "المهام النشطة",
        "en": "Active Tasks",
    },
    "swarm.tab_playground": {
        "ar": "مساحة التجربة",
        "en": "Playground",
    },
    "swarm.tab_results_dashboard": {
        "ar": "لوحة النتائج",
        "en": "Results Dashboard",
    },
    "swarm.tab_task_builder": {
        "ar": "منشئ المهام",
        "en": "Task Builder",
    },
    "swarm.tab_task_history": {
        "ar": "سجل المهام",
        "en": "Task History",
    },
    "swarm.tab_templates": {
        "ar": "القوالب",
        "en": "Templates",
    },
    "swarm.tab_worker_registry": {
        "ar": "سجل العمال",
        "en": "Worker Registry",
    },
    "swarm.tab_workflow_editor": {
        "ar": "محرر سير العمل",
        "en": "Workflow Editor",
    },
    "swarm.target_chat_id": {
        "ar": "معرّف المحادثة المستهدفة",
        "en": "Target Chat ID",
    },
    "swarm.target_chat_id_desc": {
        "ar": "معرّف المحادثة المباشرة (رقم موجب من البوت المخصص) أو معرّف المجموعة (سالب).",
        "en": "Your DM chat_id (positive number from the dedicated bot) or a group chat_id (negative).",
    },
    "swarm.task_cancelled": {
        "ar": "تم إلغاء المهمة",
        "en": "Task cancelled",
    },
    "swarm.task_completed": {
        "ar": "اكتملت المهمة",
        "en": "Task completed",
    },
    "swarm.task_detail": {
        "ar": "تفاصيل المهمة",
        "en": "Task Detail",
    },
    "swarm.task_dispatched_to": {
        "ar": "تم إرسال المهمة إلى {count} عامل",
        "en": "Task dispatched to {count} worker(s)",
    },
    "swarm.task_execution_started": {
        "ar": "بدأ تنفيذ المهمة.",
        "en": "Task execution started.",
    },
    "swarm.task_finalized": {
        "ar": "اكتملت المهمة. اكتمل التوليف.",
        "en": "Task finalized. Synthesis finished.",
    },
    "swarm.task_id": {
        "ar": "معرف المهمة",
        "en": "Task ID",
    },
    "swarm.task_not_found": {
        "ar": "المهمة غير موجودة",
        "en": "Task not found",
    },
    "swarm.task_retried": {
        "ar": "تمت إعادة محاولة المهمة كـ {id}",
        "en": "Task retried as {id}",
    },
    "swarm.task_started": {
        "ar": "بدأت المهمة",
        "en": "Task started",
    },
    "swarm.task_title_prefix": {
        "ar": "المهمة: ",
        "en": "Task: ",
    },
    "swarm.tasks_count": {
        "ar": "مهمة",
        "en": "tasks",
    },
    "swarm.tasks_count_inline": {
        "ar": "{count} مهمة",
        "en": "{count} tasks",
    },
    "swarm.tasks_today": {
        "ar": "مهام اليوم",
        "en": "Tasks Today",
    },
    "swarm.template_deleted": {
        "ar": "تم حذف القالب",
        "en": "Template deleted",
    },
    "swarm.template_saved": {
        "ar": "تم حفظ القالب",
        "en": "Template saved",
    },
    "swarm.templates_hint": {
        "ar": "تنشئ القوالب عمالاً تلقائيًا عند الطلب. عند وصول مهمة swarm ولا يوجد عامل مسجل مطابق، تعمل أفضل قالب وتنشئ عاملًا. اترك النموذج فارغًا لاختيار أفضل نموذج متاح لنوع المهمة تلقائيًا.",
        "en": "Templates auto-spawn workers on demand. When a swarm task arrives and no registered worker matches, the best template fires and spawns a worker. Leave the model blank to auto-pick the best available model for the task kind.",
    },
    "swarm.templates_load_failed": {
        "ar": "فشل تحميل القوالب",
        "en": "Failed to load templates",
    },
    "swarm.templates_title": {
        "ar": "قوالب العمال",
        "en": "Worker Templates",
    },
    "swarm.timeout_placeholder": {
        "ar": "المهلة",
        "en": "Timeout",
    },
    "swarm.timeout_seconds": {
        "ar": "مهلة الانتظار (ثانية)",
        "en": "Timeout (seconds)",
    },
    "swarm.title": {
        "ar": "تنسيق السرب",
        "en": "Swarm Orchestration",
    },
    "swarm.tmpl_expertise": {
        "ar": "وسوم الخبرة",
        "en": "Expertise tags",
    },
    "swarm.tmpl_expertise_hint": {
        "ar": "مفصولة بفواصل. تطابق المهمة إذا ظهرت هذه الكلمات فيها (كلمات كاملة فقط).",
        "en": "Comma-separated. A task matches if these words appear in it (whole words only).",
    },
    "swarm.tmpl_max_instances": {
        "ar": "أقصى عدد",
        "en": "Max instances",
    },
    "swarm.tmpl_model": {
        "ar": "النموذج",
        "en": "Model",
    },
    "swarm.tmpl_model_hint": {
        "ar": "اتركه فارغًا لاختيار أفضل نموذج لنوع المهمة تلقائيًا.",
        "en": "Leave blank to auto-select the best model for the task kind.",
    },
    "swarm.tmpl_name": {
        "ar": "الاسم",
        "en": "Name",
    },
    "swarm.tmpl_name_required": {
        "ar": "اسم القالب مطلوب",
        "en": "Template name is required",
    },
    "swarm.tmpl_prompt_ph": {
        "ar": "أنت عامل متخصص…",
        "en": "You are a specialist worker…",
    },
    "swarm.tmpl_role": {
        "ar": "الدور",
        "en": "Role",
    },
    "swarm.tmpl_system_prompt": {
        "ar": "موجه النظام (الروح)",
        "en": "System prompt (Soul)",
    },
    "swarm.toast_worker_added": {
        "ar": "تمت إضافة العامل \"{name}\"",
        "en": "Worker \"{name}\" added",
    },
    "swarm.toast_worker_removed": {
        "ar": "تمت إزالة العامل \"{name}\"",
        "en": "Worker \"{name}\" removed",
    },
    "swarm.toast_worker_started": {
        "ar": "تم تشغيل العامل \"{name}\"",
        "en": "Worker \"{name}\" started",
    },
    "swarm.toast_worker_stopped": {
        "ar": "تم إيقاف العامل \"{name}\"",
        "en": "Worker \"{name}\" stopped",
    },
    "swarm.toast_worker_updated": {
        "ar": "تم تحديث العامل \"{name}\"",
        "en": "Worker \"{name}\" updated",
    },
    "swarm.today": {
        "ar": "اليوم",
        "en": "today",
    },
    "swarm.token_usage": {
        "ar": "استخدام الرموز",
        "en": "Token Usage",
    },
    "swarm.tokens_count": {
        "ar": "{tokens} رمز",
        "en": "{tokens} tokens",
    },
    "swarm.tokens_inline": {
        "ar": "{count} رمز",
        "en": "{count} tokens",
    },
    "swarm.tokens_word": {
        "ar": "رمز",
        "en": "tokens",
    },
    "swarm.tools_hint": {
        "ar": "مفصولة بفواصل",
        "en": "comma separated",
    },
    "swarm.tools_label": {
        "ar": "الأدوات",
        "en": "Tools",
    },
    "swarm.tools_placeholder": {
        "ar": "file_edit, terminal, browser",
        "en": "file_edit, terminal, browser",
    },
    "swarm.total_cost": {
        "ar": "التكلفة الإجمالية",
        "en": "Total Cost",
    },
    "swarm.type": {
        "ar": "النوع",
        "en": "Type",
    },
    "swarm.type_inline": {
        "ar": " · النوع: ",
        "en": " · Type: ",
    },
    "swarm.type_label": {
        "ar": "النوع",
        "en": "Type",
    },
    "swarm.validate": {
        "ar": "تحقق من الصحة",
        "en": "Validate",
    },
    "swarm.validation_api_error": {
        "ar": "خطأ في واجهة التحقق",
        "en": "Validation API Error",
    },
    "swarm.validation_error": {
        "ar": "خطأ في بناء الجملة أو التحقق",
        "en": "Syntax / Validation Error",
    },
    "swarm.validation_issues": {
        "ar": "مشاكل في التحقق",
        "en": "Validation Issues",
    },
    "swarm.validation_schema": {
        "ar": "مخطط التحقق (JSON)",
        "en": "Validation Schema (JSON)",
    },
    "swarm.validation_success": {
        "ar": "سير العمل صالح ومكتمل!",
        "en": "Workflow is valid!",
    },
    "swarm.verified_desc": {
        "ar": "تم التحقق من الهيكل وتعريفات العمال.",
        "en": "Structure and worker definitions are verified.",
    },
    "swarm.view_logs": {
        "ar": "عرض السجلات",
        "en": "View logs",
    },
    "swarm.visual_pipeline_editor": {
        "ar": "محرر خط الأنابيب المرئي",
        "en": "Visual Pipeline Editor",
    },
    "swarm.waiting": {
        "ar": "بانتظار…",
        "en": "Waiting…",
    },
    "swarm.worker_name": {
        "ar": "اسم العامل",
        "en": "Worker Name",
    },
    "swarm.worker_name_hint": {
        "ar": "مثال: worker-1, code-reviewer",
        "en": "e.g., worker-1, code-reviewer",
    },
    "swarm.worker_prefix": {
        "ar": "العامل ",
        "en": "Worker ",
    },
    "swarm.worker_results": {
        "ar": "نتائج العمال",
        "en": "Worker Results",
    },
    "swarm.worker_started": {
        "ar": "بدأ العامل {worker} (الخطوة {step})",
        "en": "Worker {worker} started (step {step})",
    },
    "swarm.workers": {
        "ar": "العمال",
        "en": "Workers",
    },
    "swarm.workers_col": {
        "ar": "العمال",
        "en": "Workers",
    },
    "swarm.workers_label": {
        "ar": "العمال",
        "en": "Workers",
    },
    "swarm.workers_label_inline": {
        "ar": "العمال: ",
        "en": "Workers: ",
    },
    "swarm.workflow_definition": {
        "ar": "تعريف سير العمل (YAML/JSON)",
        "en": "Workflow Definition (YAML/JSON)",
    },
    "swarm.workflow_editor_title": {
        "ar": "محرر سير العمل المرئي",
        "en": "Visual Workflow Editor",
    },
    "swarm.workflow_mermaid_visual": {
        "ar": "مخطط سير العمل المرئي (DAG)",
        "en": "Visual DAG Diagram",
    },
    "swarm.working_processed": {
        "ar": "يعمل (تمت معالجة ",
        "en": "is working (Processed ",
    },
}
