"""Every kind of run Kazma supervises, declared once.

Each :class:`~kazma_core.runs.contract.KindSpec` names its adapter, the store
that holds the truth, its budgets and the conditions another mechanism already
announces. ``tests/test_run_kinds_declared.py`` reads the product source: a
table holding a status or state column is either the source of a kind here
or in :data:`NOT_RUNS` with the reason it is not one.

Budgets are the plan's defaults (``docs/plans/RUN_SUPERVISION_PLAN.md``,
"Open decisions", taken 2026-10-25): a chat turn 10 minutes without a step,
a swarm task 10 minutes without an event (its own deadline usually ends it
first), research and crawls 3 minutes (the acquisition monitor's), a
document job 10 minutes without a heartbeat.
"""

from __future__ import annotations

from kazma_core.runs.contract import KindSpec

__all__ = ["KINDS", "NOT_RUNS"]

_MONITOR = "the research and crawl monitor (acquisition alerts)"

KINDS: dict[str, KindSpec] = {spec.kind: spec for spec in (
    KindSpec(
        kind="chat_turn",
        adapter="kazma_ui.runs_chat_turn:ADAPTER",
        label="Chat turn", label_ar="دورة محادثة",
        source="the turn registry (active_turns), the open reply turns (reply_sink) and the checkpoint",
        stall_after_s=600.0, dead_after_s=600.0,
        # A failed turn says so in its own reply (turn_failed, AGENTS.md §3).
        announced_by={"failed": "the turn's own reply"},
    ),
    KindSpec(
        kind="swarm_task",
        adapter="kazma_core.runs.adapters.swarm:ADAPTER",
        label="Swarm task", label_ar="مهمة السرب",
        source="the swarm task store and the engine's in-flight runs",
        tables=("swarm_tasks", "kazma_swarm_tasks"),
        stall_after_s=600.0, dead_after_s=120.0,
    ),
    KindSpec(
        kind="scheduled_job",
        adapter="kazma_core.runs.adapters.cron:ADAPTER",
        label="Scheduled job", label_ar="مهمة مجدولة",
        source="cron.db: jobs, deliveries and the alert outbox",
        tables=("cron_jobs", "cron_deliveries"),
        dead_after_s=120.0,
        announced_by={"failed": "the scheduled jobs' alert outbox (cron.job_failure, cron.job_failed, "
                                 "cron.delivery_failed, cron.delivery_unknown)"},
    ),
    KindSpec(
        kind="document_job",
        adapter="kazma_core.runs.adapters.documents:ADAPTER",
        label="Document job", label_ar="مهمة مستند",
        source="the document job queue (document_jobs and its events)",
        tables=("document_jobs",),
        stall_after_s=600.0, dead_after_s=120.0,
    ),
    KindSpec(
        kind="research_session",
        adapter="kazma_core.runs.adapters.research:SESSIONS",
        label="Research session", label_ar="جلسة بحث",
        source="research_sessions.db and the acquisition monitor's progress ledger",
        tables=("research_sessions",),
        stall_after_s=180.0, dead_after_s=120.0,
        announced_by={"stalled": _MONITOR, "failed": _MONITOR, "dead": _MONITOR},
    ),
    KindSpec(
        kind="crawl",
        adapter="kazma_core.runs.adapters.research:CRAWLS",
        label="Crawl or research pipeline", label_ar="زحف أو مسار بحث",
        source="the crawl journal (acquisition.crawl.<digest>) and the monitor's progress ledger",
        stall_after_s=180.0, dead_after_s=300.0,
        announced_by={"stalled": _MONITOR, "failed": _MONITOR, "dead": _MONITOR},
    ),
    KindSpec(
        kind="x_post",
        adapter="kazma_core.runs.adapters.x_posts:ADAPTER",
        label="X post", label_ar="منشور X",
        source="the X publication store (x_operations)",
        tables=("x_operations", "x_operation_events", "x_decisions"),
        dead_after_s=600.0,
        announced_by={"failed": "the X publication notices (success, failure and held posts)"},
    ),
    KindSpec(
        kind="memory_task",
        adapter="kazma_core.runs.adapters.memory_queue:ADAPTER",
        label="Memory task", label_ar="مهمة ذاكرة",
        source="the memory task queue (memory_ops.db)",
        tables=("memory_task_queue",),
        dead_after_s=600.0,
        announced_by={"failed": "the memory.dead_letter ops alert"},
    ),
    KindSpec(
        kind="maintenance",
        adapter="kazma_core.runs.adapters.maintenance:ADAPTER",
        label="Maintenance sweep", label_ar="صيانة دورية",
        source="the maintenance runner's last outcome per sweep (worker_bootstrap.sweep_health)",
        read_only=True,
    ),
)}

#: Tables holding a status or state column that are not runs, with why.
NOT_RUNS: dict[str, str] = {
    "commitments": "commitment-layer decisions about one tool call, settled inside its turn",
    "entity_merges": "memory entity merge records (a ledger, not work)",
    "entity_merges_archive": "archived memory entity merge records",
    "hitl_gates": "approval decisions; their waiting turn is a chat_turn run",
    "knowledge_vector_status": "the Knowledge vector sweep's coverage record",
    "llm_calls": "the model-call ledger (one row per finished call)",
    "run_incidents": "the run supervisor's own incidents about runs (a record, not work)",
    "memory_summaries": "weekly topic summaries; writing them is a memory_task run",
    "memory_summary_periods": "which weeks were summarized; the work is a memory_task run",
    "procedural_dags": "learned tool procedures (memory content)",
    "reports": "security disclosure reports (documents, not work)",
    "task_ledgers": "a chat's goal and plan steps; the work is its chat_turn run",
    "tool_effects": "tool invocation receipts inside a turn (the effect journal)",
    "x_audit_log": "the X API audit log",
    "x_replies": "auto-reply drafts waiting for the owner; publishing them is an x_post run",
    "x_scheduled_posts": "the legacy schedule, migrated into x_operations at start",
    "x_thread_permissions": "who may summon Kazma in an X thread (permissions)",
    "x_threads": "X conversation threads Kazma follows (content)",
}
