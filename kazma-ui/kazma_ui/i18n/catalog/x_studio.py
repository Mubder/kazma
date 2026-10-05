"""``x_studio`` UI strings."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "x_studio.filter_side": {"en": "Position", "ar": "الموقف"},
    "x_studio.all_sides": {"en": "All positions", "ar": "كل المواقف"},
    "x_studio.filter_mood": {"en": "Tone", "ar": "النبرة"},
    "x_studio.all_moods": {"en": "All tones", "ar": "كل النبرات"},
    "x_studio.filter_status": {"en": "Status", "ar": "الحالة"},
    "x_studio.health": {"en": "Operations health", "ar": "صحة التشغيل"},
    "x_studio.health_scope": {"en": "Tenant counters; heartbeats for this process. Mentions polling serves the default tenant. Last successful cycle is shown below.", "ar": "عدادات المستأجر؛ ونبضات هذه العملية. تخدم مراقبة الإشارات المستأجر الافتراضي. آخر دورة ناجحة موضحة أدناه."},
    "x_studio.health_running": {"en": "Running", "ar": "يعمل"},
    "x_studio.health_mentions": {"en": "Mention polling", "ar": "مراقبة الإشارات"},
    "x_studio.health_scheduler": {"en": "Scheduled publishing", "ar": "النشر المجدول"},
    "x_studio.health_stopped": {"en": "Stopped", "ar": "متوقف"},
    "x_studio.health_stale": {"en": "Heartbeat overdue", "ar": "نبضة التشغيل متأخرة"},
    "x_studio.health_repairs": {"en": "Pending result repairs", "ar": "إصلاحات النتائج المعلقة"},
    "x_studio.health_notifications": {"en": "Unacknowledged notifications", "ar": "الإشعارات غير المؤكدة"},
    "x_studio.health_unknown": {"en": "Unknown send outcomes", "ar": "نتائج الإرسال المجهولة"},
    "x_studio.health_models": {"en": "Recorded model calls / output tokens", "ar": "استدعاءات النماذج المسجلة / رموز المخرجات"},
    "x_studio.health_usage_missing": {"en": "Calls missing usage measurements", "ar": "استدعاءات بلا قياسات استخدام"},
    "x_studio.health_capabilities": {"en": "Account verification proves identity; a read does not attest write access. Media and analytics remain disabled until their account capabilities are verified.", "ar": "يثبت التحقق من الحساب الهوية؛ ولا تثبت القراءة صلاحية الكتابة. تبقى الوسائط والتحليلات معطلة حتى التحقق من صلاحيات الحساب الخاصة بها."},
    "x_studio.threads": {"en": "Threads", "ar": "سلاسل المنشورات"},
    "x_studio.thread_description": {
        "en": "Stage ordered posts, then approve the entire saved thread. A partial send stops; resuming skips confirmed posts. Unknown outcomes block resending. Published posts are retained when remaining segments are cancelled.",
        "ar": "احفظ المنشورات بالترتيب ثم وافق على السلسلة المحفوظة كاملة. يتوقف الإرسال الجزئي؛ والاستئناف يتجاوز المنشورات المؤكدة. تمنع النتائج المجهولة إعادة الإرسال. تبقى المنشورات المنشورة عند إلغاء الأجزاء المتبقية.",
    },
    "x_studio.thread_segment": {"en": "Segment", "ar": "الجزء"},
    "x_studio.thread_up": {"en": "Move up", "ar": "نقل للأعلى"},
    "x_studio.thread_down": {"en": "Move down", "ar": "نقل للأسفل"},
    "x_studio.thread_add": {"en": "Add segment", "ar": "إضافة جزء"},
    "x_studio.thread_stage": {"en": "Save thread for review", "ar": "حفظ السلسلة للمراجعة"},
    "x_studio.thread_publish": {"en": "Approve remaining posts", "ar": "الموافقة على المنشورات المتبقية"},
    "x_studio.thread_review": {"en": "Refresh review", "ar": "تحديث المراجعة"},
    "x_studio.thread_cancel": {"en": "Cancel remaining posts", "ar": "إلغاء المنشورات المتبقية"},
    "x_studio.thread_event_staged": {"en": "Saved for review", "ar": "حُفظت للمراجعة"},
    "x_studio.thread_event_approved_remaining": {"en": "Remaining posts approved", "ar": "تمت الموافقة على المنشورات المتبقية"},
    "x_studio.thread_event_published": {"en": "Published", "ar": "نُشرت"},
    "x_studio.thread_event_partial": {"en": "Stopped with posts remaining", "ar": "توقفت مع بقاء منشورات"},
    "x_studio.thread_event_cancelled_remaining": {"en": "Remaining posts cancelled", "ar": "أُلغيت المنشورات المتبقية"},
    "x_studio.thread_event_review_refreshed": {"en": "Review refreshed", "ar": "حُدّثت المراجعة"},
    "x_studio.all_states": {"en": "All states", "ar": "جميع الحالات"},
    "x_studio.time_gap": {"en": "Invalid local time or daylight-saving gap. Choose another time.", "ar": "الوقت المحلي غير صالح أو يقع في فجوة التوقيت الصيفي. اختر وقتًا آخر."},
    "x_studio.time_fold": {"en": "This local time occurs twice. Choose an unambiguous time, or use an explicit UTC offset through the scheduling command.", "ar": "يتكرر هذا الوقت المحلي مرتين. اختر وقتًا واضحًا أو استخدم فرق توقيت UTC صريحًا في أمر الجدولة."},
    "x_studio.qualification_title": {"en": "Unattended publishing qualification", "ar": "تأهيل النشر التلقائي"},
    "x_studio.qualification_description": {"en": "Automatic replies require a current human-reviewed bilingual report for these exact models and policies. Uploading a report does not switch draft mode to auto.", "ar": "تتطلب الردود التلقائية تقريرًا حديثًا ثنائي اللغة راجعه أشخاص لهذه النماذج والسياسات تحديدًا. تحميل التقرير لا يغيّر وضع المسودات إلى النشر التلقائي."},
    "x_studio.qualification_passed": {"en": "Current evaluation passes the release gate", "ar": "التقييم الحالي يجتاز شروط التأهيل"},
    "x_studio.qualification_upload": {"en": "Upload reviewed JSON report (administrator)", "ar": "تحميل تقرير JSON بعد مراجعته (للمدير)"},
    "x_studio.qualification_too_large": {"en": "The report must be smaller than two megabytes", "ar": "يجب أن يكون حجم التقرير أقل من ميغابايتين"},
    "x_studio.publication_history": {"en": "Publication history", "ar": "سجل النشر"},
    "x_studio.operation_history_hint": {"en": "Durable results and held sends. Unknown outcomes require verification on X before any resend.", "ar": "النتائج المحفوظة وعمليات الإرسال المعلّقة. تتطلب النتائج المجهولة التحقق في إكس قبل إعادة الإرسال."},
    "x_studio.activity": {"en": "API activity", "ar": "نشاط واجهة البرمجة"},
    "x_studio.section_operations": {"en": "Publication history", "ar": "سجل النشر"},
    "x_studio.status_published": {"en": "Published", "ar": "نُشر"},
    "x_studio.status_scheduled": {"en": "Scheduled", "ar": "مجدول"},
    "x_studio.status_review": {"en": "Ready for review", "ar": "جاهز للمراجعة"},
    "x_studio.status_running": {"en": "Publishing thread", "ar": "جارٍ نشر السلسلة"},
    "x_studio.status_partial": {"en": "Stopped with posts remaining", "ar": "توقف مع بقاء منشورات"},
    "x_studio.status_claimed": {"en": "Preparing to send", "ar": "جارٍ التحضير للإرسال"},
    "x_studio.status_deferred": {"en": "Deferred", "ar": "مؤجل"},
    "x_studio.status_failed_permanent": {"en": "Send failed — review required", "ar": "فشل الإرسال — تلزم المراجعة"},
    "x_studio.status_cancelled": {"en": "Cancelled", "ar": "أُلغي"},
    "x_studio.status_expired": {"en": "Expired — review required", "ar": "انتهت الصلاحية — تلزم المراجعة"},

    "x_studio.composer_saved": {"en": "Composer saved on this account", "ar": "تم حفظ المسودة في حسابك"},
    "x_studio.reload_saved_composer": {"en": "Reload saved composer (replaces current text)", "ar": "تحميل المسودة المحفوظة (يستبدل النص الحالي)"},
    "x_studio.composer_load_changed": {"en": "Text changed while loading. Your current text was kept; reload the saved composer when ready.", "ar": "تغيّر النص أثناء التحميل. تم الاحتفاظ بالنص الحالي؛ حمّل المسودة المحفوظة عندما تكون مستعدًا."},
    "x_studio.restore_pause": {"en": "Publishing paused after restore. Verify the account in Settings before resuming. Old queued work remains held for separate review.", "ar": "تم إيقاف النشر بعد الاستعادة. تحقق من الحساب في الإعدادات قبل الاستئناف. تظل المنشورات السابقة معلّقة لمراجعة منفصلة."},
    "x_studio.resume_publishing": {"en": "Resume new publishing", "ar": "استئناف النشر الجديد"},
    "x_studio.check_context": {"en": "Context", "ar": "السياق"},
    "x_studio.check_target": {"en": "Target", "ar": "الموضوع"},
    "x_studio.check_stance": {"en": "Position", "ar": "الموقف"},
    "x_studio.check_evidence": {"en": "Evidence", "ar": "الأدلة"},
    "x_studio.check_safety": {"en": "Safety", "ar": "السلامة"},
    "x_studio.verdict_pass": {"en": "Passed", "ar": "اجتاز"},
    "x_studio.verdict_fail": {"en": "Failed", "ar": "لم يجتز"},
    "x_studio.verdict_unknown": {"en": "Review needed", "ar": "يحتاج مراجعة"},
    "x_studio.draft_brief": {"en": "AI drafting brief", "ar": "فكرة لصياغة مسودات بالذكاء الاصطناعي"},
    "x_studio.brief_placeholder": {"en": "Describe your idea and provide the facts the draft may use…", "ar": "صف فكرتك وقدّم الحقائق التي يمكن استخدامها في المسودة…"},
    "x_studio.generate": {"en": "Generate drafts with X model", "ar": "إنشاء مسودات بنموذج إكس"},
    "x_studio.alternatives": {"en": "Number of alternatives", "ar": "عدد البدائل"},
    "x_studio.generating": {"en": "Drafting…", "ar": "جارٍ إعداد المسودات…"},
    "x_studio.generated_review": {"en": "Drafts saved below. Review facts and wording before posting.", "ar": "تم حفظ المسودات أدناه. راجع الحقائق والصياغة قبل النشر."},
    "x_studio.public_text": {"en": "Text to publish", "ar": "النص المراد نشره"},
    "x_studio.status_outcome_unknown": {"en": "Outcome unknown — verify on X", "ar": "نتيجة النشر غير معروفة — تحقق في إكس"},
    "x_studio.status_sending": {"en": "Sending — do not resend", "ar": "جارٍ الإرسال — لا تعد الإرسال"},
    "x_studio.all_clocks": {
        "ar": "كل الجداول",
        "en": "All clocks",
    },
    "x_studio.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "x_studio.composer": {
        "ar": "مسودة",
        "en": "Compose",
    },
    "x_studio.deleted": {
        "ar": "تم الحذف.",
        "en": "Deleted.",
    },
    "x_studio.delete_post": {
        "ar": "حذف",
        "en": "Delete",
    },
    "x_studio.clear_thread": {
        "ar": "إنهاء السلسلة",
        "en": "Clear thread",
    },
    "x_studio.confirm_delete": {
        "ar": "حذف هذا المنشور من إكس؟ لا يمكن التراجع.",
        "en": "Delete this post from X? This cannot be undone.",
    },
    "x_studio.confirm_post": {
        "ar": "نشر هذا النص الآن على إكس؟",
        "en": "Post this text to X now?",
    },
    "x_studio.connector_off": {
        "ar": "موصّل إكس غير جاهز. احفظ مفاتيح OAuth 1.0a في الإعدادات ثم فعّل النشر.",
        "en": "The X connector is not ready. Save the four OAuth 1.0a keys in Settings and enable posting.",
    },
    "x_studio.drafts": {
        "ar": "مسودات محفوظة",
        "en": "Saved drafts",
    },
    "x_studio.empty_audit": {
        "ar": "لا منشورات بعد.",
        "en": "Nothing posted yet.",
    },
    "x_studio.empty_drafts": {
        "ar": "لا مسودات. احفظ اقتراحاً من المحادثة فتظهر هنا.",
        "en": "No drafts. Save a proposal from chat and it appears here.",
    },
    "x_studio.empty_queue": {
        "ar": "لا منشورات مجدولة.",
        "en": "Nothing scheduled.",
    },
    "x_studio.open_settings": {
        "ar": "الإعدادات → إكس",
        "en": "Settings → X",
    },
    "x_studio.placeholder": {
        "ar": "ماذا يحدث؟",
        "en": "What's happening?",
    },
    "x_studio.planner": {
        "ar": "المجدول",
        "en": "Planner",
    },
    "x_studio.post_now": {
        "ar": "نشر الآن",
        "en": "Post now",
    },
    "x_studio.posted": {
        "ar": "تم النشر",
        "en": "Posted",
    },
    "x_studio.post_failed": {
        "ar": "فشل النشر",
        "en": "Post failed",
    },
    "x_studio.schedule_failed": {
        "ar": "فشلت الجدولة",
        "en": "Schedule failed",
    },
    "x_studio.reschedule_failed": {
        "ar": "فشلت إعادة الجدولة",
        "en": "Reschedule failed",
    },
    "x_studio.delete_failed": {
        "ar": "فشل الحذف",
        "en": "Delete failed",
    },
    "x_studio.cancel_failed": {
        "ar": "فشل الإلغاء",
        "en": "Cancel failed",
    },
    "x_studio.quota": {
        "ar": "اليوم",
        "en": "today",
    },
    "x_studio.reply": {
        "ar": "رد",
        "en": "Reply",
    },
    "x_studio.reply_to": {
        "ar": "رد على (معرّف التغريدة أو الرابط)",
        "en": "Reply to (tweet id or URL)",
    },
    "x_studio.reschedule": {
        "ar": "إعادة جدولة",
        "en": "Reschedule",
    },
    "x_studio.rescheduled": {
        "ar": "أُعيدت الجدولة.",
        "en": "Rescheduled.",
    },
    "x_studio.schedule": {
        "ar": "جدولة",
        "en": "Schedule",
    },
    "x_studio.scheduled_ok": {
        "ar": "تمت الجدولة.",
        "en": "Scheduled.",
    },
    "x_studio.text_required": {
        "ar": "اكتب النص أولاً.",
        "en": "Write the tweet first.",
    },
    "x_studio.timing_required": {
        "ar": "اختر وقتاً في المستقبل.",
        "en": "Pick a future time.",
    },
    "x_studio.title": {
        "ar": "استوديو إكس",
        "en": "X Studio",
    },
    "x_studio.use": {
        "ar": "استخدام",
        "en": "Use",
    },
    "x_studio.dismiss": {
        "ar": "استبعاد",
        "en": "Dismiss",
    },
    "x_studio.restore": {
        "ar": "استعادة",
        "en": "Restore",
    },
    "x_studio.show_dismissed": {
        "ar": "عرض المستبعدة",
        "en": "Show dismissed",
    },
    "x_studio.empty_dismissed": {
        "ar": "لا مسودات مستبعدة.",
        "en": "No dismissed drafts.",
    },
    "x_studio.draft_dismissed": {
        "ar": "استُبعدت المسودة ولن تُقترح مجددًا. استعدها من «عرض المستبعدة».",
        "en": "Draft dismissed — it will not be offered again. Restore it under Show dismissed.",
    },
    "x_studio.draft_restored": {
        "ar": "استُعيدت المسودة.",
        "en": "Draft restored.",
    },
    "x_studio.draft_unchanged": {
        "ar": "لم يتغير شيء — المسودة منشورة أو مجدولة أو مستبعدة مسبقًا.",
        "en": "Nothing changed — that draft was already posted, scheduled or dismissed.",
    },
    "x_studio.thread_on": {
        "ar": "سلسلة",
        "en": "Thread",
    },
    "x_studio.when": {
        "ar": "متى",
        "en": "When",
    },
    "x_studio.tab_conversations": {
        "ar": "المحادثات",
        "en": "Conversations",
    },
    "x_studio.writes_only": {
        "ar": "الكتابات فقط",
        "en": "writes only",
    },
    "x_studio.conv_refresh_hint": {
        "ar": "التحديث يجلب الإشارات من X، أما إعادة تحميل الصفحة فتعيد قراءة السجل المحلي فقط.",
        "en": "Refresh fetches mentions from X. A page reload only rereads the local log.",
    },
    "x_studio.conv_empty": {
        "ar": "لا استدعاءات بعد. أشر إلى الحساب مع رمز تعبيري يحدد النبرة (😂 سخرية، 🤬 غضب، 🙄 جفاف، ❤️ دعم) —",
        "en": "No summons yet. Mention the account with an emoji for tone (😂 roast, 🤬 angry, 🙄 dry, ❤️ supportive) —",
    },
    "x_studio.auto_reply_settings": {
        "ar": "إعدادات الرد التلقائي",
        "en": "auto-reply settings",
    },
    "x_studio.who_posted": {
        "ar": "نشر @{who}",
        "en": "@{who} posted",
    },
    "x_studio.someone": {
        "ar": "شخص ما",
        "en": "someone",
    },
    "x_studio.text_not_recorded": {
        "ar": "(لم يُسجَّل النص)",
        "en": "(text not recorded)",
    },
    "x_studio.who_summoned": {
        "ar": "استدعى @{who}",
        "en": "@{who} summoned",
    },
    "x_studio.kazma_replied": {
        "ar": "ردّ Kazma",
        "en": "Kazma replied",
    },
    "x_studio.kazma_no_reply": {
        "ar": "لم يردّ Kazma",
        "en": "Kazma did not reply",
    },
    "x_studio.status_awaiting_approval": {
        "ar": "بانتظار الموافقة",
        "en": "awaiting approval",
    },
    "x_studio.status_failed": {
        "ar": "فشل",
        "en": "failed",
    },
    "x_studio.status_skipped": {
        "ar": "تُخطّي",
        "en": "skipped",
    },
    "x_studio.status_posted": {
        "ar": "نُشر",
        "en": "posted",
    },
    "x_studio.status_deleted": {
        "ar": "حُذف",
        "en": "deleted",
    },
    "x_studio.status_pending": {
        "ar": "معلّق",
        "en": "pending",
    },
    "x_studio.status_drafting": {
        "ar": "قيد الصياغة",
        "en": "drafting",
    },
    "x_studio.open_on_x": {
        "ar": "افتح على X",
        "en": "open on X",
    },
    "x_studio.poll_failed": {
        "ar": "تعذّر جلب الإشارات من X",
        "en": "Could not poll X",
    },
    "x_studio.confirm_post_reply": {
        "ar": "نشر هذا الرد؟",
        "en": "Post this reply?",
    },
    "x_studio.confirm_delete_on_x": {
        "ar": "حذف هذا الرد على X؟",
        "en": "Delete this reply on X?",
    },
    "x_studio.confirm_remove_log": {
        "ar": "إزالته من السجل؟",
        "en": "Remove from the log?",
    },
    "x_studio.confirm_discard": {
        "ar": "تجاهل هذه المسودة؟",
        "en": "Discard this draft?",
    },
    "x_studio.removes_tweet": {
        "ar": "سيحذف هذا التغريدة من X.",
        "en": "This removes the tweet from X.",
    },
    "x_studio.posted_url": {
        "ar": "نُشر: {url}",
        "en": "Posted: {url}",
    },
    "x_studio.reply_deleted": {
        "ar": "حُذف.",
        "en": "Deleted.",
    },
    "x_studio.denied": {
        "ar": "رُفض.",
        "en": "Denied.",
    },
    "x_studio.redrafted": {
        "ar": "أُعيدت الصياغة — وافق للنشر.",
        "en": "Redrafted — approve to post.",
    },
    "x_studio.done": {
        "ar": "تم.",
        "en": "Done.",
    },
    "x_studio.refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "x_studio.load_failed": {"en": "Could not refresh {section}. Last available data is retained.", "ar": "تعذر تحديث {section}. تم الاحتفاظ بآخر بيانات متاحة."},
    "x_studio.loading": {"en": "Loading…", "ar": "جارٍ التحميل…"},
    "x_studio.section_status": {"en": "connection status", "ar": "حالة الاتصال"},
    "x_studio.section_queue": {"en": "publication queue", "ar": "قائمة النشر"},
    "x_studio.section_drafts": {"en": "drafts", "ar": "المسودات"},
    "x_studio.section_audit": {"en": "publication log", "ar": "سجل النشر"},
    "x_studio.section_conversations": {"en": "conversations", "ar": "المحادثات"},
    "x_studio.section_preview": {"en": "content preview", "ar": "معاينة المحتوى"},
    "x_studio.queue_count.zero": {"en": "No bookings", "ar": "لا توجد منشورات مجدولة"},
    "x_studio.queue_count.one": {"en": "One booking", "ar": "منشور واحد مجدول"},
    "x_studio.queue_count.two": {"en": "{count} bookings", "ar": "منشوران مجدولان"},
    "x_studio.queue_count.few": {"en": "{count} bookings", "ar": "{count} منشورات مجدولة"},
    "x_studio.queue_count.many": {"en": "{count} bookings", "ar": "{count} منشورًا مجدولًا"},
    "x_studio.queue_count.other": {"en": "{count} bookings", "ar": "{count} منشور مجدول"},
    "x_studio.show_finished": {"en": "Include finished bookings", "ar": "عرض المنشورات المجدولة المنتهية"},
    "x_studio.verify_unknown": {"en": "Publication may have reached X. Verify the result before any new send.", "ar": "قد يكون المنشور قد وصل إلى X. تحقق من النتيجة قبل أي إرسال جديد."},
    "x_studio.load_more": {"en": "Load more", "ar": "تحميل المزيد"},
    "x_studio.history": {"en": "Decision history", "ar": "سجل القرارات"},
    "x_studio.no_history": {"en": "No earlier decisions.", "ar": "لا توجد قرارات سابقة."},
    "x_studio.attempt": {"en": "Attempt {number}", "ar": "المحاولة {number}"},
    "x_studio.model_budget": {"en": "Model calls: {used}/{limit} · UTC day", "ar": "استدعاءات النموذج: {used}/{limit} · يوم UTC"},
    "x_studio.read_budget": {"en": "X reads: {used}/{limit} · UTC day", "ar": "قراءات X: {used}/{limit} · يوم UTC"},
}
