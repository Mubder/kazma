"""``workspace`` UI strings.

One slice of the translation catalog, extracted from the former
2,962-line ``kazma_ui/i18n.py`` (audit O5). Entries are verbatim;
``kazma_ui.i18n`` merges every slice back into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "workspace.active_env": {
        "ar": "البيئة النشطة:",
        "en": "Active Environment:",
    },
    "workspace.activity_title": {
        "ar": "النشاط الأخير",
        "en": "Recent Activity",
    },
    "workspace.autocomplete_hint": {
        "ar": "اكتب مساراً للإكمال التلقائي، أو استخدم تبديل المستودع للاختيار من GitHub.",
        "en": "Type a path to autocomplete, or use Switch Repo to pick from GitHub.",
    },
    "workspace.bookmark_prompt": {
        "ar": "أدخل ملفاً أو رابطاً للتمييز:",
        "en": "Enter file or URL to bookmark:",
    },
    "workspace.cancel": {
        "ar": "إلغاء",
        "en": "Cancel",
    },
    "workspace.clean": {
        "ar": "نظيف",
        "en": "Clean",
    },
    "workspace.cloning": {
        "ar": "جارٍ استنساخ المستودع...",
        "en": "Cloning repository...",
    },
    "workspace.cloning_wait": {
        "ar": "جارٍ الاستنساخ... يرجى الانتظار.",
        "en": "Cloning... please wait.",
    },
    "workspace.configure_pat": {
        "ar": "تكوين رمز PAT",
        "en": "Configure PAT Token",
    },
    "workspace.connect_for_activity": {
        "ar": "اتصل بـ GitHub لرؤية النشاط.",
        "en": "Connect GitHub to see activity.",
    },
    "workspace.create": {
        "ar": "إنشاء مساحة عمل",
        "en": "Create Workspace",
    },
    "workspace.create_modal_title": {
        "ar": "إنشاء مساحة عمل مشروع جديدة",
        "en": "Create New Project Workspace",
    },
    "workspace.create_switch": {
        "ar": "إنشاء وتبديل",
        "en": "Create & Switch",
    },
    "workspace.delete": {
        "ar": "حذف",
        "en": "Delete",
    },
    "workspace.delete_confirm": {
        "ar": "إزالة مساحة العمل “{name}” من كاظمه؟\n\nالمسار: {path}\n\nيمكنك بعدها اختيار حذف الملفات من القرص.",
        "en": "Remove workspace “{name}” from Kazma?\n\nPath: {path}\n\nYou can choose next whether to also delete files on disk.",
    },
    "workspace.delete_files_btn": {
        "ar": "احذف الملفات أيضاً",
        "en": "Delete files too",
    },
    "workspace.delete_files_confirm": {
        "ar": "هل تريد أيضاً حذف كل الملفات نهائياً في:\n\n{path}\n\nمسموح فقط تحت ~/kazma-repos. لا يمكن التراجع.",
        "en": "Also permanently delete all files at:\n\n{path}\n\nOnly allowed under ~/kazma-repos (or KAZMA_CLONE_DIR). Cannot be undone.",
    },
    "workspace.delete_files_title": {
        "ar": "حذف الملفات من القرص؟",
        "en": "Delete files on disk?",
    },
    "workspace.delete_registry": {
        "ar": "إزالة من القائمة",
        "en": "Remove from list",
    },
    "workspace.delete_title": {
        "ar": "حذف مساحة العمل",
        "en": "Delete workspace",
    },
    "workspace.deploy_confirm": {
        "ar": "النشر إلى الإنتاج؟",
        "en": "Deploy to production?",
    },
    "workspace.dirty": {
        "ar": "تعديلات غير ملتزم بها",
        "en": "Uncommitted",
    },
    "workspace.disconnect_confirm": {
        "ar": "قطع اتصال GitHub؟ سيُزال الرمز للقراءة فقط من كاظمه.",
        "en": "Disconnect GitHub? The read-only token will be removed from Kazma.",
    },
    "workspace.engine_desc": {
        "ar": "إدارة بيئات وسياقات مشاريع منفصلة ديناميكياً.",
        "en": "Manage separate project environments and contexts dynamically.",
    },
    "workspace.engine_title": {
        "ar": "محرك مساحات العمل المتعددة",
        "en": "Multi-Workspace Engine",
    },
    "workspace.field_folder_path": {
        "ar": "المسار المطلق للمجلد",
        "en": "Absolute Folder Path",
    },
    "workspace.field_folder_path_ph": {
        "ar": "مثال: C:/Projects/my-app",
        "en": "e.g., C:/Projects/my-app",
    },
    "workspace.field_name": {
        "ar": "اسم مساحة العمل",
        "en": "Workspace Name",
    },
    "workspace.field_name_ph": {
        "ar": "مثال: مشروعي الجديد",
        "en": "e.g., My New Project",
    },
    "workspace.field_path": {
        "ar": "المسار المطلق للمجلد",
        "en": "Absolute Directory Path",
    },
    "workspace.field_path_ph": {
        "ar": "مثال: C:/Users/balfa/my-project",
        "en": "e.g., C:/Users/balfa/my-project",
    },
    "workspace.files_title": {
        "ar": "ملفات المشروع",
        "en": "Project Files",
    },
    "workspace.filter_all": {
        "ar": "الكل",
        "en": "All",
    },
    "workspace.filter_ci": {
        "ar": "CI",
        "en": "CI",
    },
    "workspace.filter_commits": {
        "ar": "الالتزامات",
        "en": "Commits",
    },
    "workspace.filter_issues": {
        "ar": "المشاكل",
        "en": "Issues",
    },
    "workspace.filter_prs": {
        "ar": "السحب",
        "en": "PRs",
    },
    "workspace.folder_autocreate": {
        "ar": "سيُنشأ المجلد تلقائياً إذا لم يكن موجوداً.",
        "en": "Folder will be automatically created on the filesystem if it doesn't exist.",
    },
    "workspace.folder_modal_title": {
        "ar": "فتح مجلد محلي كمساحة عمل",
        "en": "Open Local Folder as Workspace",
    },
    "workspace.gh_actions_title": {
        "ar": "إجراءات GitHub — أتمتة سير العمل",
        "en": "GitHub Actions — Workflow Automation",
    },
    "workspace.gh_connect": {
        "ar": "ربط GitHub",
        "en": "Connect GitHub",
    },
    "workspace.gh_connect_body": {
        "ar": "صرّح لكاظمه (للقراءة فقط) برؤية طلبات السحب والمشاكل والالتزامات وتشغيلات سير العمل المباشرة لـ",
        "en": "Authorize Kazma (read-only) to see live pull requests, issues, commits, and workflow runs for",
    },
    "workspace.gh_connect_heading": {
        "ar": "اتصل بـ GitHub",
        "en": "Connect to GitHub",
    },
    "workspace.gh_connect_with": {
        "ar": "اتصل عبر GitHub",
        "en": "Connect with GitHub",
    },
    "workspace.gh_connected": {
        "ar": "متصل",
        "en": "Connected",
    },
    "workspace.gh_disconnect": {
        "ar": "قطع الاتصال",
        "en": "Disconnect",
    },
    "workspace.gh_failed": {
        "ar": "فشل",
        "en": "Failed",
    },
    "workspace.gh_forks": {
        "ar": "التفرعات",
        "en": "Forks",
    },
    "workspace.gh_issues": {
        "ar": "المشاكل",
        "en": "Issues",
    },
    "workspace.gh_no_workflows": {
        "ar": "لا توجد تشغيلات سير عمل لهذا المستودع.",
        "en": "No workflow runs detected for this repository.",
    },
    "workspace.gh_oauth_note": {
        "ar": "مصادقة آمنة — لا كلمة مرور أو رمز للصق. وصول للقراءة فقط.",
        "en": "Secure OAuth — no password or token to paste. Read-only access.",
    },
    "workspace.gh_offline": {
        "ar": "تكامل GitHub غير متصل",
        "en": "GitHub Integration Offline",
    },
    "workspace.gh_offline_body": {
        "ar": "لم يتم تكوين مستودع GitHub بعيد لهذا الدليل النشط.",
        "en": "No remote GitHub repository has been configured for this active workspace directory.",
    },
    "workspace.gh_passed": {
        "ar": "ناجح",
        "en": "Passed",
    },
    "workspace.gh_private": {
        "ar": "خاص",
        "en": "Private",
    },
    "workspace.gh_prs": {
        "ar": "السحب",
        "en": "PRs",
    },
    "workspace.gh_public": {
        "ar": "عام",
        "en": "Public",
    },
    "workspace.gh_rate_limited": {
        "ar": "تم تجاوز الحد",
        "en": "Rate Limited",
    },
    "workspace.gh_running": {
        "ar": "قيد التشغيل",
        "en": "Running",
    },
    "workspace.gh_stars": {
        "ar": "النجوم",
        "en": "Stars",
    },
    "workspace.gh_tab_actions": {
        "ar": "الإجراءات",
        "en": "Actions",
    },
    "workspace.gh_tab_commits": {
        "ar": "الالتزامات",
        "en": "Commits",
    },
    "workspace.gh_tab_issues": {
        "ar": "المشاكل",
        "en": "Issues",
    },
    "workspace.gh_tab_overview": {
        "ar": "نظرة عامة",
        "en": "Overview",
    },
    "workspace.gh_tab_prs": {
        "ar": "السحب",
        "en": "PRs",
    },
    "workspace.gh_tab_releases": {
        "ar": "الإصدارات",
        "en": "Releases",
    },
    "workspace.gh_title": {
        "ar": "قياسات GitHub",
        "en": "GitHub Telemetry",
    },
    "workspace.gh_token_missing": {
        "ar": "الرمز مفقود",
        "en": "Token Missing",
    },
    "workspace.gh_view_run": {
        "ar": "عرض التشغيل",
        "en": "View Run",
    },
    "workspace.git_status": {
        "ar": "حالة Git",
        "en": "Git Status",
    },
    "workspace.id_prefix": {
        "ar": "المعرف:",
        "en": "ID:",
    },
    "workspace.loading_path": {
        "ar": "جارٍ التحميل...",
        "en": "Loading...",
    },
    "workspace.location": {
        "ar": "الموقع:",
        "en": "Location:",
    },
    "workspace.modified": {
        "ar": "الملفات المعدلة",
        "en": "Modified Files",
    },
    "workspace.no_activity": {
        "ar": "لا يوجد نشاط حديث.",
        "en": "No recent activity.",
    },
    "workspace.no_description": {
        "ar": "لا يوجد وصف للمستودع.",
        "en": "No repository description set.",
    },
    "workspace.no_files": {
        "ar": "لا توجد ملفات",
        "en": "No files found",
    },
    "workspace.no_modified": {
        "ar": "لا توجد تغييرات غير ملتزم بها",
        "en": "No unstaged changes",
    },
    "workspace.no_open_issues": {
        "ar": "لا توجد مشاكل مفتوحة.",
        "en": "No open issues.",
    },
    "workspace.no_open_prs": {
        "ar": "لا توجد طلبات سحب مفتوحة.",
        "en": "No open pull requests.",
    },
    "workspace.no_recent_commits": {
        "ar": "لا توجد التزامات حديثة.",
        "en": "No recent commits.",
    },
    "workspace.no_releases": {
        "ar": "لا توجد إصدارات منشورة.",
        "en": "No releases published.",
    },
    "workspace.no_repos": {
        "ar": "لا توجد مستودعات.",
        "en": "No repos found.",
    },
    "workspace.no_staged": {
        "ar": "لا توجد تغييرات مجهزة",
        "en": "No staged changes",
    },
    "workspace.no_untracked": {
        "ar": "لا توجد ملفات غير متتبعة",
        "en": "No untracked files",
    },
    "workspace.no_workflow_runs": {
        "ar": "لا توجد تشغيلات سير عمل.",
        "en": "No workflow runs.",
    },
    "workspace.not_repo": {
        "ar": "ليس مستودع Git",
        "en": "Not a Git repository",
    },
    "workspace.open_folder": {
        "ar": "فتح المجلد",
        "en": "Open Folder",
    },
    "workspace.page_title": {
        "ar": "مساحة العمل",
        "en": "Workspace",
    },
    "workspace.pat_desc": {
        "ar": "رفع رمز PAT لحدود معدل الاستدعاءات (الحد غير المصادق عليه 60/ساعة) ويتيح الوصول الآمن لبيانات المستودعات الخاصة. يُحفظ الرمز في إعدادات SQLite المحلية ويُزامن مباشرة مع ملف .env الخاص بك.",
        "en": "Providing a PAT raises API rate limits (unauthenticated limits are 60/hr) and enables secure access to private repository metadata. The token is saved in your local SQLite settings and synced directly to your .env file.",
    },
    "workspace.pat_label": {
        "ar": "رمز الوصول الشخصي (PAT)",
        "en": "Personal Access Token (PAT)",
    },
    "workspace.pat_modal_title": {
        "ar": "تكوين رمز GitHub",
        "en": "Configure GitHub Token",
    },
    "workspace.pat_ph": {
        "ar": "ghp_...",
        "en": "ghp_...",
    },
    "workspace.pat_save": {
        "ar": "حفظ وتحقق",
        "en": "Save & Verify",
    },
    "workspace.private_badge": {
        "ar": "خاص",
        "en": "private",
    },
    "workspace.qa_build": {
        "ar": "بناء",
        "en": "Build",
    },
    "workspace.qa_deploy": {
        "ar": "نشر",
        "en": "Deploy",
    },
    "workspace.qa_refresh": {
        "ar": "تحديث",
        "en": "Refresh",
    },
    "workspace.qa_run_tests": {
        "ar": "تشغيل الاختبارات",
        "en": "Run Tests",
    },
    "workspace.qa_terminal": {
        "ar": "الطرفية",
        "en": "Terminal",
    },
    "workspace.repo_picker_title": {
        "ar": "تبديل مستودع GitHub",
        "en": "Switch GitHub Repo",
    },
    "workspace.repo_search_ph": {
        "ar": "بحث في المستودعات...",
        "en": "Search repos...",
    },
    "workspace.select_folder": {
        "ar": "اختيار مجلد",
        "en": "Select Folder",
    },
    "workspace.staged": {
        "ar": "الملفات المجهزة",
        "en": "Staged Files",
    },
    "workspace.switch_repo": {
        "ar": "تبديل المستودع",
        "en": "Switch Repo",
    },
    "workspace.terminal_close": {
        "ar": "إغلاق",
        "en": "Close",
    },
    "workspace.terminal_command_ph": {
        "ar": "أدخل أمراً…",
        "en": "Enter command…",
    },
    "workspace.terminal_ready": {
        "ar": "الطرفية جاهزة. اكتب أمراً أدناه.",
        "en": "Terminal ready. Type a command below.",
    },
    "workspace.terminal_run": {
        "ar": "تشغيل",
        "en": "Run",
    },
    "workspace.terminal_title": {
        "ar": "الطرفية",
        "en": "Terminal",
    },
    "workspace.title": {
        "ar": "كاظمه — مساحة العمل",
        "en": "Kazma — Workspace",
    },
    "workspace.toast_bookmark_failed": {
        "ar": "فشل وضع العلامة",
        "en": "Failed to bookmark",
    },
    "workspace.toast_bookmarked": {
        "ar": "تم وضع علامة: {name}",
        "en": "Bookmarked: {name}",
    },
    "workspace.toast_build_started": {
        "ar": "بدأ البناء...",
        "en": "Build started...",
    },
    "workspace.toast_conn_error": {
        "ar": "خطأ في الاتصال: {msg}",
        "en": "Connection error: {msg}",
    },
    "workspace.toast_create_failed": {
        "ar": "فشل إنشاء مساحة العمل",
        "en": "Failed to create workspace",
    },
    "workspace.toast_created": {
        "ar": "تم إنشاء مساحة العمل: {name}",
        "en": "Workspace created: {name}",
    },
    "workspace.toast_deleted": {
        "ar": "أُزيلت مساحة “{name}” (الملفات بقيت على القرص)",
        "en": "Workspace “{name}” removed (files kept on disk)",
    },
    "workspace.toast_deleted_with_files": {
        "ar": "حُذفت مساحة “{name}” وملفاتها",
        "en": "Workspace “{name}” and its files deleted",
    },
    "workspace.toast_deploy_initiated": {
        "ar": "تم بدء النشر",
        "en": "Deployment initiated",
    },
    "workspace.toast_disconnect_failed": {
        "ar": "فشل قطع الاتصال: {msg}",
        "en": "Disconnect failed: {msg}",
    },
    "workspace.toast_error": {
        "ar": "خطأ: {msg}",
        "en": "Error: {msg}",
    },
    "workspace.toast_folder_failed": {
        "ar": "فشل تسجيل المجلد",
        "en": "Failed to register folder",
    },
    "workspace.toast_folder_registered": {
        "ar": "تم تسجيل المجلد: {name}",
        "en": "Folder registered: {name}",
    },
    "workspace.toast_gh_conn_failed": {
        "ar": "فشل ربط GitHub.",
        "en": "GitHub connection failed.",
    },
    "workspace.toast_gh_connected": {
        "ar": "تم ربط GitHub.",
        "en": "GitHub connected.",
    },
    "workspace.toast_gh_disconnected": {
        "ar": "تم قطع اتصال GitHub.",
        "en": "GitHub disconnected.",
    },
    "workspace.toast_gh_status_failed": {
        "ar": "فشل تحميل حالة تكامل GitHub.",
        "en": "Failed to load GitHub integration status.",
    },
    "workspace.toast_opening": {
        "ar": "فتح {path}",
        "en": "Opening {path}",
    },
    "workspace.toast_pat_saved": {
        "ar": "تم حفظ رمز الوصول الشخصي لـ GitHub ومزامنته.",
        "en": "GitHub Personal Access Token saved and synced.",
    },
    "workspace.toast_remove_bookmark_failed": {
        "ar": "فشل إزالة العلامة",
        "en": "Failed to remove bookmark",
    },
    "workspace.toast_repo_failed": {
        "ar": "فشل فتح المستودع.",
        "en": "Failed to open repo.",
    },
    "workspace.toast_repo_opened": {
        "ar": "تم فتح {name}.",
        "en": "Opened {name}.",
    },
    "workspace.toast_request_failed": {
        "ar": "فشل الطلب",
        "en": "Request failed",
    },
    "workspace.toast_switch_failed": {
        "ar": "فشل تبديل مساحة العمل",
        "en": "Failed to switch workspace",
    },
    "workspace.toast_switched": {
        "ar": "تم تبديل مساحة العمل بنجاح",
        "en": "Switched workspace successfully",
    },
    "workspace.toast_token_save_failed": {
        "ar": "خطأ في حفظ الرمز: {detail}",
        "en": "Error saving token: {detail}",
    },
    "workspace.triggered_by": {
        "ar": "تم تشغيله بواسطة",
        "en": "Triggered by",
    },
    "workspace.untracked": {
        "ar": "الملفات غير المتتبعة",
        "en": "Untracked Files",
    },
    "workspace.up_one_level": {
        "ar": "مستوى واحد للأعلى",
        "en": "Up one level",
    },
}
