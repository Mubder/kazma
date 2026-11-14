"""One catalog of every alert any of Kazma's senders can raise.

Stage 1 of the unified alerts plan (``docs/plans/UNIFIED_ALERTS_PLAN.md``).
Four senders page the owner, each with its own reach:

* ``ops``: Kazma's own ops alerts (``observability/ops_alerts.alert``), keyed
  by the condition (``backup.offsite_failed``). A key with a part only known
  at run time is a pattern: ``llm.provider_unreachable.{provider}``.
* ``lifecycle``: the start card and the server status messages
  (``lifecycle_notifier``), one entry per event (``lifecycle.started``).
* ``guard``: the guard's pages, named by the ``kind=`` of each ``_page``
  call in ``scripts/service/kazma_guard.py`` (``disk.critical``).
* ``watch``: Kazma Watch, the outside observer (``deploy/observer``), one
  entry per condition its diagnosis returns (``watch.tunnel_down``) and its
  recovery page (``watch.recovered``).
* ``cloudflare``: Cloudflare's own notifications, which reach Kazma Watch's
  webhook (``POST /v1/cloudflare``, ``deploy/observer/src/cloudflare.js``)
  and are paged by it with the source Cloudflare; never sent to Kazma, which
  is behind the tunnel they report on.

Each entry says how bad it is, its card title in English and Arabic, the page
of this site it is about, and the entry that says it is over (its recovery,
from the same sender), if one does. ``condition`` names the problem when more
than one sender can see it; ``owner`` marks the sender that pages it (Stage
2, "one page per problem"): a sender marked ``owner=False`` records it
instead while an owner can speak (``inside_hold``). Two inside senders
owning one condition is declared in ``SHARED_OWNERS`` with why: the first to
page it pages, the other records it.

Stage 2's page receipts (``page_receipts``, the guard's heartbeat) carry the
keys of the inside pages open now, so Kazma Watch stays quiet about what
Kazma or its guard already told the owner: ``ANSWERED_BY`` names the inside
conditions that answer for one of its conditions, ``SAME_CAUSE`` the
conditions one cause shows as (a Cloudflare notice of one joins an incident
of another).

The guard cannot import kazma_core and the observer runs on Cloudflare: each
reads a generated copy of this table (``COPIES``), written by
``python -m kazma_core.observability.alert_catalog --write`` and held equal
to it by ``tests/test_alert_catalog.py``, which also holds the table to the
senders' own source in both directions.

Reports are not alerts and stay out: the daily digest and the weekly report
are cards of their own, and X notices are the X notice outbox's.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ANSWERED_BY",
    "CATALOG",
    "COPIES",
    "INSIDE_SENDERS",
    "SAME_CAUSE",
    "SENDERS",
    "SEVERITIES",
    "SHARED_OWNERS",
    "closed_by_key",
    "entry_for",
    "inside_hold",
    "opens_incident",
    "recovery_keys",
]

SENDERS = ("ops", "lifecycle", "guard", "watch", "cloudflare")
#: The senders on Kazma's machine: their open pages are the page receipts.
INSIDE_SENDERS = ("ops", "guard")
SEVERITIES = ("info", "success", "warn", "error", "critical")


@dataclass(frozen=True)
class _Entry:
    """One alert a sender can raise."""

    key: str
    sender: str
    severity: str
    title: str
    title_ar: str
    #: The page of this site the alert is about (a path, ``/settings?tab=...``).
    page: str = "/dashboard"
    #: The key of the entry that says it is over (the same sender's recovery).
    closed_by: str = ""
    #: The problem it reports, when another sender can see it too.
    condition: str = ""
    #: True when this sender is the condition's one pager (Stage 2).
    owner: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "sender": self.sender,
            "severity": self.severity,
            "title": self.title,
            "title_ar": self.title_ar,
            "page": self.page,
            "closed_by": self.closed_by,
            "condition": self.condition,
            "owner": self.owner,
        }


_BACKUP = "/settings?tab=backup"
_SYSTEM = "/settings?tab=system"
_AGENT = "/settings?tab=agent"
_PROVIDERS = "/settings?tab=providers_connectors"
_EMAIL = "/settings?tab=email"
_PACKAGES = "/settings?tab=packages"
_DOCUMENTS_SETTINGS = "/settings?tab=documents"


def _ops(key, severity, title, title_ar, page="/dashboard", closed_by="", condition="", owner=True):
    return _Entry(key, "ops", severity, title, title_ar, page, closed_by, condition, owner)


def _guard(key, severity, title, title_ar, page="/dashboard", closed_by="", condition="", owner=True):
    return _Entry(key, "guard", severity, title, title_ar, page, closed_by, condition, owner)


def _life(key, severity, title, title_ar):
    return _Entry(key, "lifecycle", severity, title, title_ar, "/dashboard")


def _watch(key, severity, title, title_ar, condition="", owner=True, closed_by="watch.recovered"):
    return _Entry(key, "watch", severity, title, title_ar, "/dashboard", closed_by, condition, owner)


def _cf(key, severity, title, title_ar, condition="", closed_by=""):
    # Kazma Watch pages a Cloudflare notice; a notice of one of Kazma Watch's
    # own conditions is a second witness of it, never a second owner.
    return _Entry(key, "cloudflare", severity, title, title_ar, "/dashboard", closed_by, condition,
                  owner=not condition)


# Research and crawl results (``web_acquire/monitor._LABELS``): the titles are
# that table's, the severity its ``_severity`` rule's.
_ACQUISITION = (
    ("done", "success", "Research completed", "اكتمل البحث"),
    ("completed", "success", "Crawl completed", "اكتمل الزحف"),
    ("partial", "warn", "Partial results — review missing sources", "نتائج جزئية — راجع المصادر الناقصة"),
    ("error", "error", "Research failed — review saved details", "فشل البحث — راجع التفاصيل المحفوظة"),
    ("failed", "error", "Crawl failed — review saved details", "فشل الزحف — راجع التفاصيل المحفوظة"),
    ("empty", "warn", "Crawl stored nothing — every page was kept out",
     "لم يُخزّن الزحف شيئاً — استُبعدت كل الصفحات"),
    ("cancelled", "info", "Research cancelled", "أُلغي البحث"),
    ("interrupted", "warn", "Run interrupted — explicit recovery required", "انقطع التنفيذ — يلزم استئناف صريح"),
    ("stalled", "warn", "No observed progress — inspect the run", "لا يوجد تقدم مرصود — راجع التنفيذ"),
    ("resumed", "info", "Progress resumed", "استؤنف التقدم"),
    ("low_credits", "warn", "Provider credits are low", "رصيد الخدمة منخفض"),
    ("payment_required", "error", "Provider requires payment or credits", "الخدمة تتطلب دفعاً أو رصيداً"),
    ("rate_limited", "warn", "Provider rate limit reached", "تم بلوغ حد طلبات الخدمة"),
    ("provider_failed", "error", "Provider request failed", "فشل طلب الخدمة"),
    ("allowance", "warn", "Remote request allowance exhausted (not billed credits)",
     "نفدت حصة الطلبات الخارجية (ليست رصيداً مفوتراً)"),
)


CATALOG: tuple[_Entry, ...] = (
    # -- Kazma's ops alerts: turns and replies ---------------------------------
    _ops("turn.timed_out", "warn", "A turn hit its time limit and was cut off",
         "بلغ دور حدّه الزمني فتوقّف"),
    _ops("turn.empty", "warn", "A turn finished without a reply", "انتهى دور دون رد"),
    _ops("reply.persist_failed", "error", "A reply was not saved to the transcript",
         "لم يُحفظ رد في سجل المحادثة"),
    _ops("reply.detached_persist_failed", "error", "A background turn's reply was not saved",
         "لم يُحفظ رد دور جرى في الخلفية"),
    _ops("session.persist_spooled", "warn", "A chat save was kept in the local spool",
         "حُفظت محادثة في المخزن المحلي المؤقت"),
    _ops("chat.reply_undeliverable.{platform}", "warn", "A chat reply could not be delivered",
         "تعذّر إيصال رد في تطبيق محادثة", page=_PROVIDERS),
    _ops("cost.daily_limit", "error", "The daily model cost limit", "حد تكلفة النماذج اليومي", page=_SYSTEM),
    # -- the model providers ------------------------------------------------------
    _ops("llm.key_switched.{provider}", "warn", "A provider key was switched", "تم تبديل مفتاح مزوّد",
         page=_PROVIDERS),
    _ops("llm.provider_unreachable.{provider}", "warn", "A model provider cannot be reached",
         "تعذّر الوصول إلى مزوّد نماذج", page=_PROVIDERS, closed_by="llm.provider_reachable.{provider}"),
    _ops("llm.provider_reachable.{provider}", "info", "A model provider answers again",
         "عاد مزوّد النماذج يستجيب", page=_PROVIDERS),
    _ops("llm.fallback.substitution:{provider}", "warn", "A provider without a key was replaced",
         "استُبدل مزوّد بلا مفتاح", page=_PROVIDERS,
         closed_by="llm.fallback.substitution:{provider}.resolved"),
    _ops("llm.fallback.substitution:{provider}.resolved", "info", "Model fallback resolved",
         "انتهى استبدال النموذج", page=_PROVIDERS),
    _ops("llm.fallback.rebind:{provider}", "warn", "A new provider key is saved but not in use yet",
         "حُفظ مفتاح المزوّد الجديد ولم يُستخدم بعد", page=_PROVIDERS),
    _ops("llm.fallback.failover:{model}", "warn", "Model failover: the backup model answered",
         "تحويل النموذج: أجاب النموذج الاحتياطي", page=_AGENT),
    _ops("llm.failover_drill_failed", "warn", "The backup-model drill failed",
         "فشل تمرين النموذج الاحتياطي", page=_AGENT),
    # -- the database and the settings store --------------------------------------
    _ops("database.away", "error", "Kazma's database is not answering", "قاعدة بيانات كاظمه لا تستجيب",
         page=_SYSTEM, closed_by="database.back", condition="database_away"),
    _ops("database.back", "success", "Kazma's database answers again", "عادت قاعدة بيانات كاظمه تستجيب",
         page=_SYSTEM),
    _ops("config_store.volatile", "critical", "Settings saves are not persisting",
         "حفظ الإعدادات لا يدوم", page=_SYSTEM),
    # -- backups --------------------------------------------------------------------
    _ops("backup.restic_passphrase_missing", "critical", "Encrypted snapshots are skipped: no restic passphrase",
         "تُتخطّى اللقطات المشفّرة: لا توجد كلمة مرور restic", page=_BACKUP),
    _ops("backup.restic_remote_read_only", "critical", "The offsite backup remote is read-only",
         "وجهة النسخ الاحتياطي الخارجية للقراءة فقط", page=_BACKUP),
    _ops("backup.restic_snapshot_failed", "warn", "A restic repository missed a snapshot",
         "فات مستودع restic لقطة", page=_BACKUP),
    _ops("backup.restic_check_failed", "critical", "A backup repository failed verification",
         "فشل التحقق من مستودع نسخ احتياطي", page=_BACKUP),
    _ops("backup.restore_drill_failed", "critical", "Backup verification failed", "فشل التحقق من النسخ الاحتياطي",
         page=_BACKUP),
    _ops("backup.restore_drill_unverified", "warn", "Backup verification is incomplete",
         "التحقق من النسخ الاحتياطي غير مكتمل", page=_BACKUP),
    _ops("backup.databases_failed", "critical", "Databases are missing from the backup",
         "قواعد بيانات غائبة عن النسخة الاحتياطية", page=_BACKUP),
    _ops("backup.files_failed", "warn", "Files are missing from the backup", "ملفات غائبة عن النسخة الاحتياطية",
         page=_BACKUP),
    _ops("backup.offsite_failed", "critical", "Backups are local only: the offsite copy is failing",
         "النسخ الاحتياطية محلية فقط: النسخة الخارجية تفشل", page=_BACKUP),
    _ops("backup.offsite_degraded", "warn", "Offsite backups run on the fallback path only",
         "النسخ الخارجية تعمل عبر المسار البديل فقط", page=_BACKUP),
    _ops("backup.offsite_prune_failed", "warn", "Old offsite backup copies could not be deleted",
         "تعذّر حذف النسخ الخارجية القديمة", page=_BACKUP),
    _ops("backup.pg_dump", "critical", "The Postgres dump failed", "فشل تفريغ Postgres", page=_BACKUP),
    _ops("backup.pg_offsite", "critical", "The Postgres offsite copy failed", "فشلت النسخة الخارجية من Postgres",
         page=_BACKUP),
    _ops("backup.pg_tools", "critical", "Postgres backups cannot run", "لا يمكن تشغيل نسخ Postgres الاحتياطية",
         page=_BACKUP),
    _ops("backup.pg_base_backup", "warn", "The weekly database base backup failed",
         "فشلت النسخة الأساسية الأسبوعية لقاعدة البيانات", page=_BACKUP),
    _ops("backup.wal_archive_stale", "warn", "The database's WAL archive is not current",
         "أرشيف WAL لقاعدة البيانات غير محدّث", page=_BACKUP),
    # -- scheduled jobs ----------------------------------------------------------------
    _ops("cron.job_failure", "warn", "A scheduled job failed", "فشلت مهمة مجدولة", page="/scheduled"),
    _ops("cron.job_failed", "error", "A scheduled job failed again and stopped", "فشلت مهمة مجدولة مرارًا فتوقفت",
         page="/scheduled"),
    _ops("cron.delivery_failed", "error", "A scheduled message was not delivered", "لم تُسلَّم رسالة مجدولة",
         page="/scheduled"),
    _ops("cron.delivery_unknown", "warn", "A scheduled message may not have arrived",
         "ربما لم تصل رسالة مجدولة", page="/scheduled"),
    # -- documents, knowledge and research ------------------------------------------------
    _ops("documents.worker_image_outdated", "warn", "The document worker image is outdated",
         "صورة عامل المستندات قديمة", page=_DOCUMENTS_SETTINGS),
    _ops("documents.malware_signatures", "warn", "Malware signatures are out of date",
         "تواقيع البرمجيات الخبيثة قديمة", page=_DOCUMENTS_SETTINGS),
    _ops("documents.index_drift", "warn", "The Knowledge Library holds chunks the documents store does not record",
         "مكتبة المعرفة تحمل مقاطع لا يسجلها مخزن المستندات", page="/knowledge"),
    _ops("knowledge.meaning_search", "warn", "Knowledge Library meaning search is degraded",
         "البحث بالمعنى في مكتبة المعرفة متراجع", page="/knowledge"),
    _ops("search.searxng_silent", "warn", "Local search (SearXNG) answers nothing", "البحث المحلي (SearXNG) لا يجيب",
         page="/research"),
    _ops("acquisition.canary_failed", "error", "The daily crawl check failed", "فشل فحص الزحف اليومي",
         page="/knowledge", closed_by="acquisition.canary_recovered"),
    _ops("acquisition.canary_recovered", "info", "The daily crawl check passes again", "عاد فحص الزحف اليومي ينجح",
         page="/knowledge"),
    _ops("acquisition.monitor_unavailable", "error", "Research and crawl monitoring is unavailable",
         "مراقبة البحث والزحف غير متاحة", page=_SYSTEM, closed_by="acquisition.monitor_recovered"),
    _ops("acquisition.monitor_recovered", "info", "Research and crawl monitoring is back",
         "عادت مراقبة البحث والزحف", page=_SYSTEM),
    *(_ops(f"acquisition.{c}", sev, en, ar, page="/research",
           closed_by="acquisition.resumed" if c == "stalled" else "")
      for c, sev, en, ar in _ACQUISITION),
    # -- memory -----------------------------------------------------------------------------
    _ops("memory.dead_letter", "warn", "Memory gave up on a task", "تخلّت الذاكرة عن مهمة", page="/memory"),
    _ops("memory.enqueue_failed", "warn", "The memory queue refused a task", "رفض طابور الذاكرة مهمة",
         page="/memory"),
    _ops("memory.region_conflict", "warn", "Memory mirror changes another region owns were refused",
         "رُفضت تغييرات في مرآة الذاكرة تملكها منطقة أخرى", page="/memory"),
    _ops("time_travel.prune_not_sticking", "warn", "Time Travel snapshots are not being pruned",
         "لقطات السفر عبر الزمن لا تُقلَّم", page="/replay"),
    # -- MCP servers and connectors -------------------------------------------------------------
    _ops("mcp.servers_unavailable", "warn", "MCP servers are unavailable", "خوادم MCP غير متاحة", page="/mcp"),
    _ops("mcp.reconnect_failing", "warn", "An MCP server has not come back", "لم يعد خادم MCP", page="/mcp",
         closed_by="mcp.server_recovered"),
    _ops("mcp.server_recovered", "info", "An MCP server is back", "عاد خادم MCP", page="/mcp"),
    _ops("connector.google_expired", "critical", "Google sign-in has expired: Gmail is not working",
         "انتهت صلاحية تسجيل الدخول إلى Google: لا يعمل Gmail", page=_EMAIL),
    _ops("connector.google_expiring", "warn", "Google sign-in expires today", "تنتهي صلاحية تسجيل الدخول إلى Google اليوم",
         page=_EMAIL),
    _ops("connector.google_calendar_expired", "critical", "Google Calendar sign-in has expired",
         "انتهت صلاحية تسجيل الدخول إلى تقويم Google", page=_EMAIL),
    _ops("connector.google_calendar_expiring", "warn", "Google Calendar sign-in expires today",
         "تنتهي صلاحية تسجيل الدخول إلى تقويم Google اليوم", page=_EMAIL),
    _ops("email.token_not_kept", "warn", "Microsoft mail: a refreshed sign-in was not saved",
         "بريد Microsoft: لم يُحفظ تسجيل دخول مُجدَّد", page=_EMAIL),
    # -- running work ---------------------------------------------------------------------------
    _ops("runs.stalled.{incident}", "warn", "A run has made no progress", "توقف عمل جارٍ عن التقدم",
         closed_by="runs.recovered.{incident}"),
    _ops("runs.dead.{incident}", "error", "A run is stuck: nothing is running it", "عمل جارٍ عالق: لا شيء يشغّله",
         closed_by="runs.recovered.{incident}"),
    _ops("runs.failed.{incident}", "error", "A run failed", "فشل عمل جارٍ"),
    _ops("runs.recovered.{incident}", "info", "A run moves again", "عاد العمل الجاري يتقدم"),
    _ops("runs.diagnosis.{incident}", "info", "The diagnosis of a run", "تشخيص عمل جارٍ"),
    _ops("runs.supervisor_unavailable", "error", "Run supervision is not working", "مراقبة الأعمال الجارية لا تعمل",
         closed_by="runs.supervisor_recovered"),
    _ops("runs.supervisor_recovered", "info", "Run supervision works again", "عادت مراقبة الأعمال الجارية تعمل"),
    # -- the install ------------------------------------------------------------------------------
    _ops("install.requirements_unmet", "warn", "Installed packages are older than this build requires",
         "الحزم المثبّتة أقدم مما يتطلبه هذا الإصدار", page=_PACKAGES),
    _ops("security.advisories_new", "warn", "A dependency advisory affects this install",
         "تنبيه أمني لإحدى الاعتماديات يخص هذا التثبيت"),
    _ops("security.service_exposed", "warn", "A service of Kazma's is reachable beyond this machine",
         "إحدى خدمات كاظمه متاحة خارج هذا الجهاز"),
    _ops("guard.gone", "critical", "Kazma is running without its guard", "يعمل كاظمه دون حارسه",
         condition="guard_gone", owner=False),
    _ops("resilience.firing_ledger", "warn", "The weekly report of what fired", "التقرير الأسبوعي لما عمل"),
    # -- the lifecycle messages (``lifecycle_notifier._EVENTS``) -----------------------------------
    _life("lifecycle.starting", "info", "Kazma is starting", "كاظمه يبدأ التشغيل"),
    _life("lifecycle.started", "success", "Kazma started", "بدأ تشغيل كاظمه"),
    _life("lifecycle.restarted", "success", "Kazma restarted", "أُعيد تشغيل كاظمه"),
    _life("lifecycle.shutting_down", "warn", "Kazma is shutting down", "كاظمه يتوقف عن العمل"),
    _life("lifecycle.startup_failed", "error", "Kazma failed to start", "فشل بدء تشغيل كاظمه"),
    # -- the guard's pages (the kind of each _page call in kazma_guard.py) ---------------------------
    _guard("guard.restarting", "warn", "Kazma stopped and is being restarted", "توقف كاظمه ويُعاد تشغيله",
           closed_by="guard.recovered", condition="kazma_restarting"),
    _guard("guard.recovered", "success", "Kazma is healthy again", "عاد كاظمه سليمًا"),
    _guard("guard.crash_loop", "critical", "Kazma is crash-looping", "كاظمه يتعطل مرارًا"),
    _guard("guard.operator_reload", "info", "Kazma is restarting for an operator reload",
           "يُعاد تشغيل كاظمه بطلب المشغّل"),
    _guard("guard.port_still_held", "error", "A restart did not take effect", "لم تنجح إعادة التشغيل"),
    _guard("guard.refused_to_start", "error", "Kazma's guard did not start", "لم يبدأ حارس كاظمه"),
    _guard("guard.internal_error", "error", "Kazma's guard hit an error in its own code",
           "واجه حارس كاظمه خطأً في شيفرته"),
    _guard("guard.crashed", "critical", "Kazma's guard crashed", "تعطّل حارس كاظمه"),
    _guard("guard.page_command", "error", "A page sent through the guard's --page", "تنبيه أُرسل عبر --page في الحارس"),
    _guard("guard.test_page", "info", "Test page", "صفحة تجريبية", page="/settings?tab=alerts"),
    _guard("health.dependency_down", "warn", "Kazma is up, but not ready", "كاظمه يعمل لكنه غير جاهز",
           closed_by="health.ready_again", condition="dependency_down"),
    _guard("health.ready_again", "success", "Kazma is ready again", "عاد كاظمه جاهزًا"),
    _guard("health.needs_setup", "warn", "Kazma is up and needs setup", "كاظمه يعمل ويحتاج إلى الإعداد", page="/setup"),
    _guard("health.probe_unrunnable", "warn", "This machine has run out of network ports",
           "نفدت منافذ الشبكة على هذا الجهاز"),
    _guard("child.waiting_for_database", "warn", "Kazma is waiting for its database", "ينتظر كاظمه قاعدة بياناته",
           page=_SYSTEM, closed_by="child.database_answers", condition="database_away"),
    _guard("child.database_answers", "success", "Kazma is up: its database answers again",
           "كاظمه يعمل: عادت قاعدة بياناته تستجيب", page=_SYSTEM),
    _guard("maintenance.active", "warn", "Kazma supervision paused", "أُوقفت مراقبة كاظمه مؤقتًا",
           closed_by="maintenance.resumed"),
    _guard("maintenance.still_paused", "warn", "Kazma is still paused", "ما زالت مراقبة كاظمه موقوفة",
           closed_by="maintenance.resumed"),
    _guard("maintenance.resumed", "info", "Kazma supervision resumed", "استُؤنفت مراقبة كاظمه"),
    _guard("broker.docker_unavailable", "warn", "Docker does not answer: documents are refused",
           "Docker لا يستجيب: تُرفض المستندات", page="/documents", closed_by="broker.recovered"),
    _guard("broker.failed", "error", "Kazma's document broker keeps failing", "وسيط مستندات كاظمه يفشل مرارًا",
           page="/documents", closed_by="broker.recovered"),
    _guard("broker.recovered", "success", "Kazma's document broker is running again",
           "عاد وسيط مستندات كاظمه يعمل", page="/documents"),
    _guard("deps.engine_down", "warn", "Docker does not answer", "Docker لا يستجيب",
           closed_by="deps.engine_up", condition="docker_away"),
    _guard("deps.engine_up", "success", "Docker is back", "عاد Docker"),
    _guard("deps.container_start_failed", "warn", "A container Kazma needs will not start",
           "حاوية يحتاجها كاظمه لا تبدأ", closed_by="deps.container_recovered"),
    _guard("deps.container_recovered", "success", "A container Kazma needs runs again",
           "عادت حاوية يحتاجها كاظمه تعمل"),
    _guard("dbsvc.down", "critical", "Kazma's database service is down", "خدمة قاعدة بيانات كاظمه متوقفة",
           page=_SYSTEM, closed_by="dbsvc.back", condition="database_away"),
    _guard("dbsvc.back", "success", "Kazma's database is running again", "عادت قاعدة بيانات كاظمه تعمل",
           page=_SYSTEM),
    _guard("disk.low", "warn", "Disk space is low", "مساحة القرص منخفضة", closed_by="disk.back"),
    _guard("disk.critical", "critical", "The disk is almost full", "القرص ممتلئ تقريبًا", closed_by="disk.back",
           condition="disk_critical"),
    _guard("disk.back", "success", "Disk space is back", "عادت مساحة القرص"),
    _guard("power.on_battery", "warn", "On battery power", "يعمل على البطارية", closed_by="power.back"),
    _guard("power.stopping", "critical", "Stopping Kazma: the battery is running low",
           "إيقاف كاظمه: البطارية توشك على النفاد", closed_by="power.back"),
    _guard("power.back", "success", "Power is back", "عادت الكهرباء"),
    # -- Kazma Watch (deploy/observer/src/diagnose.js) ---------------------------------------------------
    _watch("watch.machine_unreachable", "error", "Kazma's machine is not reachable (power, network or sleep)",
           "جهاز كاظمه غير متاح (الكهرباء أو الشبكة أو السكون)", condition="machine_unreachable"),
    _watch("watch.tunnel_down", "error", "The tunnel is down; Kazma itself is running",
           "النفق متوقف؛ كاظمه نفسه يعمل", condition="tunnel_down"),
    _watch("watch.kazma_restarting", "warn", "Kazma is down; the guard is restarting it",
           "كاظمه متوقف؛ والحارس يعيد تشغيله", condition="kazma_restarting", owner=False),
    _watch("watch.kazma_down", "error", "Kazma is down", "كاظمه متوقف"),
    _watch("watch.dependency_down", "warn", "Kazma is up; a dependency is away", "كاظمه يعمل؛ وإحدى اعتمادياته غائبة",
           condition="dependency_down", owner=False),
    _watch("watch.guard_gone", "warn", "The guard stopped; Kazma is unsupervised",
           "توقف الحارس؛ كاظمه بلا مراقبة", condition="guard_gone"),
    _watch("watch.disk_critical", "critical", "Kazma's disk is almost full", "قرص كاظمه ممتلئ تقريبًا",
           condition="disk_critical", owner=False),
    _watch("watch.observer_refused", "warn", "The observer cannot pass Cloudflare Access",
           "لا يستطيع المراقب اجتياز Cloudflare Access", condition="observer_refused"),
    _watch("watch.unreachable", "error", "Kazma is not reachable", "كاظمه غير متاح"),
    # A watch with no address to look at hears only the heartbeats (kazma observer setup).
    _watch("watch.heartbeat_silent", "error", "Kazma Watch hears nothing from Kazma (power, network, sleep or its guard)",
           "لا يسمع Kazma Watch شيئًا من كاظمه (الكهرباء أو الشبكة أو السكون أو حارسه)",
           condition="heartbeat_silent"),
    _watch("watch.recovered", "success", "Kazma is back", "عاد كاظمه", closed_by=""),
    # -- Cloudflare's notifications (deploy/observer/src/cloudflare.js) -----------------------------------
    _cf("cloudflare.tunnel_down", "error", "Cloudflare says the tunnel is down", "تقول Cloudflare إن النفق متوقف",
        condition="tunnel_down", closed_by="cloudflare.tunnel_healthy"),
    _cf("cloudflare.tunnel_degraded", "warn", "Cloudflare says the tunnel is degraded",
        "تقول Cloudflare إن النفق متراجع", condition="tunnel_down", closed_by="cloudflare.tunnel_healthy"),
    _cf("cloudflare.tunnel_healthy", "success", "Cloudflare says the tunnel is healthy again",
        "تقول Cloudflare إن النفق عاد سليمًا"),
    _cf("cloudflare.tunnel_deleted", "error", "A Cloudflare tunnel was deleted", "حُذف نفق في Cloudflare"),
    _cf("cloudflare.tunnel_changed", "info", "A Cloudflare tunnel was created or changed",
        "أُنشئ نفق في Cloudflare أو تغيّر"),
    _cf("cloudflare.access_expiring", "warn", "A Cloudflare Access credential expires soon",
        "تنتهي قريبًا صلاحية أحد اعتمادات Cloudflare Access", condition="observer_refused"),
    _cf("cloudflare.worker_errors", "warn", "Cloudflare reports Worker errors", "تبلغ Cloudflare عن أخطاء في Worker",
        closed_by="cloudflare.worker_errors_resolved"),
    _cf("cloudflare.worker_errors_resolved", "success", "Cloudflare says the Worker errors are resolved",
        "تقول Cloudflare إن أخطاء Worker انتهت"),
    _cf("cloudflare.notification", "warn", "A Cloudflare notification", "إشعار من Cloudflare"),
    _cf("cloudflare.notification_resolved", "success", "A Cloudflare notification is resolved",
        "انتهى إشعار من Cloudflare"),
)


#: Conditions two inside senders (Kazma and its guard) both own, and why.
SHARED_OWNERS: dict[str, str] = {
    "database_away": (
        "Kazma pages its database not answering (db/availability), and the "
        "guard pages the OS's Postgres service down (DatabaseServiceWatch): "
        "the guard's page is the one that arrives when Kazma itself cannot "
        "start. Stage 2 makes one hold back while the other has paged."
    ),
}


#: A Kazma Watch condition, and the inside conditions whose page answers for
#: it: Kazma not ready, or down while it boots, because its database is away
#: is the database's page (``database.away``, the guard's ``dbsvc.down`` or
#: ``child.waiting_for_database``), not a second one; because the Docker
#: engine does not answer, the guard's ``deps.engine_down`` (2026-11-12: both
#: paged one problem, and Kazma Watch reminded of it every hour for five).
ANSWERED_BY: dict[str, tuple[str, ...]] = {
    "dependency_down": ("database_away", "docker_away"),
    "kazma_restarting": ("database_away", "docker_away"),
}

#: Kazma Watch's conditions one cause shows as: the probe reads the tunnel's
#: 530 whether the tunnel or the whole machine is gone, so a Cloudflare
#: "tunnel down" notice joins an incident of either (one incident, two
#: witnesses).
SAME_CAUSE: dict[str, tuple[str, ...]] = {
    "tunnel_down": ("machine_unreachable", "unreachable"),
}


_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _key_regex(key: str) -> re.Pattern[str]:
    return re.compile("^" + ".+?".join(re.escape(p) for p in _PLACEHOLDER.split(key)) + "$")


def entry_for(key: str) -> _Entry | None:
    """The catalog entry a raised *key* belongs to (``_entry_for``): page
    receipts and the holds read its condition and owner, Settings -> Alerts'
    recent alerts its severity, titles and page (``as_dict``)."""
    return _entry_for(key)


def opens_incident(entry: _Entry | None) -> bool:
    """An inside page that opens an incident: one a recovery closes, or one
    another sender can see. Its page is a receipt until the recovery."""
    return bool(entry is not None and entry.sender in INSIDE_SENDERS
                and (entry.closed_by or entry.condition))


def closed_by_key(key: str) -> list[str]:
    """The entries the recovery *key* closes: their receipts are settled
    when it is sent."""
    entry = _entry_for(key)
    if entry is None:
        return []
    return sorted(e.key for e in CATALOG if e.closed_by and e.closed_by == entry.key)


def inside_hold(key: str, receipts: list[dict] | tuple, speaks: dict[str, bool]) -> str:
    """Who pages *key*'s condition instead of its sender; "" to page it.

    One page per problem on Kazma's machine (Stage 2): an entry marked
    ``owner=False`` records its condition while an owning sender can speak
    (``speaks``: ``"ops"``, ``"guard"``, ``"watch"``); an owning entry records
    it when another entry of the same condition has an open page that
    reached someone (``receipts``: both inside senders' open page receipts,
    ``{"key", "paged_at", "delivered"}``). The guard keeps a stdlib copy
    (``kazma_guard._inside_hold``); both are held to
    ``tests/fixtures/alert_receipts/holds.json``.
    """
    entry = _entry_for(key)
    if entry is None or not entry.condition:
        return ""
    if not entry.owner:
        owners = sorted({e.sender for e in CATALOG if e.condition == entry.condition and e.owner})
        return next((owner for owner in owners if speaks.get(owner)), "")
    for receipt in receipts or ():
        other = _entry_for(str(receipt.get("key") or ""))
        if (other is not None and other.key != entry.key and other.condition == entry.condition
                and receipt.get("delivered") is True):
            return other.key
    return ""


def _entry_for(key: str) -> _Entry | None:
    """The catalog entry a raised *key* belongs to: the same key, else the
    pattern it fills (``llm.provider_unreachable.deepseek``), the most
    specific first (``x.{p}.resolved`` before ``x.{p}``)."""
    for entry in CATALOG:
        if entry.key == key:
            return entry
    patterns = sorted((e for e in CATALOG if "{" in e.key),
                      key=lambda e: len(_PLACEHOLDER.sub("", e.key)), reverse=True)
    for entry in patterns:
        if _key_regex(entry.key).match(key):
            return entry
    return None


def recovery_keys() -> frozenset[str]:
    """The keys that say a condition is over (some entry's ``closed_by``)."""
    return frozenset(e.closed_by for e in CATALOG if e.closed_by)


# -- the copies the guard and the observer read --------------------------------

#: Where each copy lives, relative to the repository's root.
COPIES = {
    "guard": Path("scripts") / "service" / "alert_catalog.json",
    "observer": Path("deploy") / "observer" / "src" / "catalog.js",
}

_GENERATOR = "python -m kazma_core.observability.alert_catalog --write"


def _export_table() -> dict[str, object]:
    """The table as plain data, in key order."""
    return {
        "version": 1,
        "generated_by": _GENERATOR,
        "answered_by": {k: list(v) for k, v in sorted(ANSWERED_BY.items())},
        "same_cause": {k: list(v) for k, v in sorted(SAME_CAUSE.items())},
        "alerts": [e.as_dict() for e in sorted(CATALOG, key=lambda e: e.key)],
    }


def _table_text() -> str:
    """The table as JSON, one alert a line (a change reads as one line)."""
    table = _export_table()
    head = {k: v for k, v in table.items() if k != "alerts"}
    lines = [f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    rows = ",\n".join("  " + json.dumps(a, ensure_ascii=False) for a in table["alerts"])
    return "{\n" + "\n".join(lines) + '\n "alerts": [\n' + rows + "\n ]\n}"


def _render_json() -> str:
    """The guard's copy (``scripts/service/alert_catalog.json``)."""
    return _table_text() + "\n"


def _render_observer_module() -> str:
    """Kazma Watch's copy: an ES module (a Worker and Node both import it
    without a JSON import attribute)."""
    body = _table_text()
    return (
        f"// Generated by `{_GENERATOR}`: do not edit.\n"
        "// The alert catalog (kazma_core/observability/alert_catalog.py) for Kazma Watch.\n"
        f"export const CATALOG = {body};\n"
    )


def _rendered() -> dict[str, str]:
    return {"guard": _render_json(), "observer": _render_observer_module()}


def _read_copy(root: Path, name: str) -> str | None:
    """A copy as committed (line endings as LF: a Windows checkout has CRLF)."""
    path = root / COPIES[name]
    if not path.exists():
        return None
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _stale_copies(root: Path) -> list[str]:
    """The copies under *root* that differ from the table."""
    return [COPIES[n].as_posix() for n, text in _rendered().items() if _read_copy(root, n) != text]


def _write_copies(root: Path) -> list[Path]:
    """Write every copy under *root* (the repository); returns what changed."""
    changed = []
    for name, text in _rendered().items():
        if _read_copy(root, name) != text:
            path = root / COPIES[name]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
            changed.append(path)
    return changed


def _repo_root() -> Path:
    # kazma-core/kazma_core/observability/alert_catalog.py
    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = _repo_root()
    if args == ["--write"]:
        for path in _write_copies(root):
            print(f"wrote {path.relative_to(root).as_posix()}")
        return 0
    if args in ([], ["--check"]):
        stale = _stale_copies(root)
        for name in stale:
            print(f"stale: {name} (run {_GENERATOR})")
        return 1 if stale else 0
    print("usage: python -m kazma_core.observability.alert_catalog [--check | --write]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
