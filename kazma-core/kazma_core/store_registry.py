"""Kazma's own stores, declared once — and how the model reads each back.

Born 2026-09-25. Asked to "list the remaining posts", the agent spent 67
tool calls and an operator-approved ``python_exec`` byte-dumping
``agent_artifacts.db``: it could SAVE drafts (``save_proposal``) but had no
tool to READ them, and every direct route to its own database is refused.
The SQL tool refused and offered nothing; ``file_read`` returned the raw
SQLite bytes; ``python_exec`` let it through because its list of store
names had never heard of ``agent_artifacts.db``.

That exposed one decision kept as five hand-written answers that had
drifted apart — "is this file one of Kazma's stores?":

- the SQL tool's refusal list (15 names),
- path policy's name list (derived from ``kazma_core.paths``, 12 names),
  behind the approval-card disclosure and the ``python_exec`` refusal,
- path policy's location rule (any database under the data dir),
- the migration exporter's list of files to carry (14 names),
- the migration importer's map of files to restore.

This module is the one answer, in two tables:

``STORES``
    Every database Kazma keeps: what it holds and whether it travels in a
    ``kazma migrate`` bundle. ``tests/test_store_registry.py`` fails when
    product code names a database file that is not declared here.

``TOOL_WRITES``
    Every tool that can change anything: where its writes land and which
    tools (or which context feeder) give the model that data back. A write
    to a Kazma store MUST name a reader the model can call without an
    approval — no note excuses it. That rule is per WRITER, not per store:
    ``agent_artifacts.db`` always had a reader (the scratchpad is fed back
    into context every turn), and that is exactly why a per-store rule
    would have passed while the drafts half had none.

Every door the model might try — the SQL tools, the file tools,
``python_exec``, ``shell_exec``, the approval card — asks
:func:`is_kazma_store` and answers with :func:`store_refusal`, which names
the reader. A refusal that says only "no" is what sent the model digging.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CONTEXT_FEEDERS",
    "DYNAMIC_NAME_SITES",
    "MIGRATION_DISPOSITIONS",
    "STORES",
    "STORE_FAMILIES",
    "TOOL_WRITES",
    "Store",
    "Write",
    "bundle_family_patterns",
    "bundle_store_names",
    "distinctive_store_names",
    "is_kazma_store",
    "readers_for_store",
    "store_name_for",
    "store_refusal",
]

logger = logging.getLogger(__name__)

#: How a store crosses machines in a ``kazma migrate`` bundle.
MIGRATION_DISPOSITIONS = {
    "bundle": "copied into the bundle and restored on import",
    "settings": "carried inside the settings export (config.yaml + tables)",
    "documents": "carried with the document store export",
    "rebuilt": "not carried: rebuilt from other data or on demand",
    "machine": "not carried: only meaningful on this machine",
    "legacy": "not carried: nothing current writes it",
}


@dataclass(frozen=True)
class Store:
    """One of Kazma's databases."""

    holds: str
    migration: str
    #: Why, whenever migration is not "bundle".
    reason: str = ""
    #: Generic filename (``sessions.db``): recognised by LOCATION only, never
    #: by bare name — a user's own ``sessions.db`` must not trip anything.
    ambiguous_name: bool = False


STORES: dict[str, Store] = {
    # ── the agent's own working state ──────────────────────────────────
    "agent_artifacts.db": Store(
        "saved drafts (save_proposal) and scratchpad findings", "bundle"),
    "task_ledgers.db": Store(
        "task ledgers: each chat's goal, plan steps and findings", "bundle"),
    "memory_state.db": Store(
        "long-term memory: beliefs, entities and episodes", "bundle"),
    "memory_ops.db": Store(
        "the memory task queue and the memory audit log", "bundle"),
    "knowledge_graph.db": Store("the swarm memory property graph", "bundle"),
    "checkpoints.db": Store("conversation state checkpoints", "bundle"),
    "snapshots.db": Store("time-travel snapshots of the agent's state", "bundle"),
    # ── conversations and sessions ─────────────────────────────────────
    "chat_sessions.db": Store("chat transcripts", "bundle"),
    "chat_sessions_spool.db": Store(
        "chat saves the primary transcript store refused", "bundle"),
    "sessions.db": Store(
        "chat-platform session routing (chat ids)", "bundle", ambiguous_name=True),
    # ── scheduling, tasks, research ────────────────────────────────────
    "cron.db": Store(
        "scheduled tasks and reminders", "bundle", ambiguous_name=True),
    "swarm_tasks.db": Store("swarm tasks and their results", "bundle"),
    "pipeline_logs.db": Store("swarm pipeline logs", "bundle"),
    "research_sessions.db": Store("deep-research sessions", "bundle"),
    # ── X ──────────────────────────────────────────────────────────────
    "x_posts.db": Store(
        "the X post ledger: what was posted (duplicate and cap checks)", "bundle"),
    "x_scheduled.db": Store("scheduled X posts", "bundle"),
    "x_replies.db": Store("the X auto-reply queue", "bundle"),
    "x_audit.db": Store("the X API audit log", "bundle"),
    # ── control plane: decisions, secrets, permissions, evidence ───────
    "hitl_gates.db": Store(
        "approval decisions (the gate registry)", "bundle"),
    "vault.db": Store("encrypted secrets", "bundle"),
    "settings.db": Store(
        "settings, knowledge libraries, workspaces and bookmarks", "settings",
        reason="exported as config.yaml plus its knowledge and workspace tables",
        ambiguous_name=True),
    "rbac.db": Store("roles and permissions", "bundle"),
    "audit.db": Store("the audit log", "bundle", ambiguous_name=True),
    "security_audit.db": Store("the security audit trail", "bundle"),
    "disclosure.db": Store("security disclosure reports", "bundle"),
    "certifications.db": Store("skill certifications", "bundle"),
    "llm_calls.db": Store(
        "the model-call ledger: tokens, cost and latency", "bundle"),
    # ── documents and mail ─────────────────────────────────────────────
    "documents.db": Store(
        "the document catalog", "documents",
        reason="exported with the document store (migration/exporter.py)",
        ambiguous_name=True),
    "documents-jobs-fallback.db": Store(
        "document jobs when Postgres is unavailable", "documents",
        reason="lives under the document store root, exported with it"),
    "sandbox_emails.db": Store(
        "the sandbox mailbox used when no email account is connected", "bundle"),
    # ── caches and per-machine state ───────────────────────────────────
    "semantic_cache.db": Store(
        "the swarm semantic response cache", "rebuilt",
        reason="a cache; refilled by use"),
    "security_scan.db": Store(
        "dependency scan results", "rebuilt", reason="re-scanned on demand"),
    "file_checkpoints.db": Store(
        "IDE undo checkpoints of edited files", "machine",
        reason="undo history names this machine's absolute file paths"),
    "callbacks.db": Store(
        "chat-platform button callback tokens", "machine",
        reason="tokens expire within a day"),
    "registry.db": Store(
        "the skills hub registry (user level, outside the data dir)", "machine",
        reason="user-level hub state, not install data", ambiguous_name=True),
    "vector.db": Store(
        "the swarm memory sqlite-vec index", "rebuilt",
        reason="an index over memory; rebuilt from the memory stores",
        ambiguous_name=True),
    "workspaces.db": Store(
        "the workspaces table, as named inside a migration bundle", "settings",
        reason="a bundle-internal copy of settings.db's workspaces table"),
    # ── retired ────────────────────────────────────────────────────────
    "memory.db": Store(
        "the retired V1 FTS5 memory", "legacy",
        reason="read once by the V1→V2 backfill; nothing writes it",
        ambiguous_name=True),
    "kazma.db": Store(
        "an early storage file", "legacy", reason="created, never written"),
    "ops.db": Store(
        "an early operations file", "legacy", reason="created, never written",
        ambiguous_name=True),
    "config.db": Store(
        "an early config file (fallback path only)", "legacy",
        reason="only named on a fallback path when kazma_home is unknown",
        ambiguous_name=True),
    "chat_sessions_test.db": Store(
        "the transcript store of test mode", "machine",
        reason="test mode only; a real install never writes it"),
}

#: Stores whose file names are built at runtime, by glob pattern (relative to
#: the data dir). Matched by :func:`store_name_for` like a declared name.
STORE_FAMILIES: dict[str, Store] = {
    "checkpoints_*.db": Store(
        "per-tenant conversation checkpoints (gateway)", "bundle"),
    "code-index/*.db": Store(
        "codebase search indexes, one per workspace", "rebuilt",
        reason="re-indexed from the workspace"),
    "chat_sessions_test_*.db": Store(
        "per-process transcript stores of test mode", "machine",
        reason="test mode only"),
}

#: Product modules that build a database FILENAME at runtime (an f-string),
#: and the declared family or purpose each one produces. The literal-name
#: gate cannot read an f-string, so every such site is named here instead.
DYNAMIC_NAME_SITES: dict[str, str] = {
    "kazma-gateway/kazma_gateway/stores/checkpoint.py": "checkpoints_*.db",
    "kazma-core/kazma_core/code_index/store.py": "code-index/*.db",
    "kazma-ui/kazma_ui/session_manager.py": "chat_sessions_test_*.db",
    "kazma-ui/kazma_ui/session_spool.py": "chat_sessions_spool.db",
    "kazma-core/kazma_core/memory/backup.py": "backup copies (backups dir)",
    "kazma-core/kazma_core/migration/vault_pairing.py": "vault backup before a key reset",
}

#: The names a wrong read or write must never reach, wherever the file sits:
#: the record of what the operator approved, and the secrets.
_ALWAYS_PROTECTED = frozenset({"hitl_gates.db", "vault.db"})

_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_DB_SIDECARS = ("-wal", "-shm", "-journal")


# ── what reads what back ─────────────────────────────────────────────────

#: Data the model never has to fetch: fed into its context every turn.
#: name -> (module, attribute, what it is). The gate imports the attribute and
#: checks the prompt assembler (agent/graph_supervisor.py) uses it.
CONTEXT_FEEDERS: dict[str, tuple[str, str, str]] = {
    "scratchpad": (
        "kazma_core.agent.artifacts", "ArtifactStore.list_scratchpad",
        "scratchpad findings"),
    "task_ledger": (
        "kazma_core.agent.task_ledger", "format_ledger_block",
        "the task ledger"),
}


@dataclass(frozen=True)
class Write:
    """Where a tool's writes land, and how the model gets them back.

    ``targets``: store filenames from :data:`STORES` / :data:`STORE_FAMILIES`,
    or one of ``workspace`` (files, read with the file tools), ``host``
    (processes / the machine), ``none`` (nothing persists: the result IS
    the tool output), ``external:<system>``, ``user:<area>``.

    ``readers``: registered read-tier tool names, or ``context:<feeder>``,
    for THESE targets. Empty only with a ``note`` — and never when a target
    is a Kazma store. ``also`` holds further target groups with their own
    readers (a research run writes a session row AND report files; the
    file reader must not be offered as the way into the database).
    """

    targets: tuple[str, ...]
    readers: tuple[str, ...] = ()
    note: str = ""
    also: tuple["Write", ...] = ()

    def groups(self) -> tuple["Write", ...]:
        return (self, *self.also)


_MEMORY_READERS = ("memory_search", "memory_list_entities", "memory_list_beliefs")
_WORKSPACE_FILE = Write(("workspace",), ("file_read", "file_list"))
_OUTPUT_ONLY = "the result is the tool output; nothing is saved"

TOOL_WRITES: dict[str, Write] = {
    # ── the agent's own state ──────────────────────────────────────────
    "save_proposal": Write(("agent_artifacts.db",), ("list_proposals",)),
    "discard_proposal": Write(("agent_artifacts.db",), ("list_proposals",)),
    "update_scratchpad": Write(("agent_artifacts.db",), ("context:scratchpad",)),
    "task_ledger_update": Write(("task_ledgers.db",), ("context:task_ledger",)),
    "memory_store": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_link_entities": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_admin": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_delete_entity": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_invalidate": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_merge_entities": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_purge_empty_entities": Write(("memory_state.db",), _MEMORY_READERS),
    "knowledge_create_library": Write(
        ("settings.db",), ("knowledge_list_libraries", "knowledge_search")),
    "knowledge_ingest_url": Write(
        ("settings.db",), ("knowledge_list_libraries", "knowledge_search")),
    "knowledge_ingest_site": Write(
        ("settings.db",), ("knowledge_list_libraries", "knowledge_search")),
    "config_save": Write(("settings.db",), ("config_read",)),
    "request_path_access": Write(("settings.db",), ("config_read",)),
    "vault_store": Write(("vault.db",), ("vault_list",)),
    "vault_delete": Write(("vault.db",), ("vault_list",)),
    "vault_retrieve": Write(
        ("none",), note="reads one secret behind an approval; writes nothing"),
    # ── scheduling and delegation ──────────────────────────────────────
    "schedule_task": Write(("cron.db",), ("list_scheduled",)),
    "edit_scheduled": Write(("cron.db",), ("list_scheduled",)),
    "cancel_scheduled": Write(("cron.db",), ("list_scheduled",)),
    "dispatch_swarm": Write(("swarm_tasks.db",), ("check_swarm_task",)),
    "spawn_agent": Write(("none",), note="the sub-agent's answer is the tool result"),
    "spawn_agents": Write(("none",), note="the sub-agents' answers are the tool result"),
    "start_deep_research": Write(
        ("research_sessions.db",), ("list_research_papers",),
        also=(Write(("workspace",), ("file_read",)),)),
    "run_research_pipeline": Write(
        ("research_sessions.db",), ("list_research_papers",),
        also=(Write(("workspace",), ("file_read",)),)),
    "plan_research_queries": Write(("none",), note=_OUTPUT_ONLY),
    # ── X ──────────────────────────────────────────────────────────────
    # A publish also marks its saved draft used; that bookkeeping belongs to
    # the tool worker (mark_proposals_posted), and list_proposals shows it.
    "x_post": Write(("external:x", "x_posts.db"), ("x_status",)),
    "x_delete_post": Write(("external:x", "x_posts.db"), ("x_status",)),
    "x_schedule_post": Write(("x_scheduled.db",), ("x_list_scheduled",)),
    "x_cancel_scheduled_post": Write(("x_scheduled.db",), ("x_list_scheduled",)),
    # ── documents ──────────────────────────────────────────────────────
    "document_import": Write(
        ("documents.db",), ("document_status", "document_read", "document_search")),
    "document_index": Write(("documents.db",), ("document_search", "document_status")),
    "document_convert": Write(("documents.db",), ("document_status", "document_read")),
    "document_redact": Write(("documents.db",), ("document_read", "document_status")),
    "document_cancel": Write(("documents.db",), ("document_status",)),
    "convert_document": Write(("workspace",), ("read_document", "file_list")),
    "ocr_document": Write(("none",), note=_OUTPUT_ONLY),
    "generate_docx": Write(("workspace",), ("read_document", "file_list")),
    "generate_pdf": Write(("workspace",), ("read_document", "file_list")),
    "generate_xlsx": Write(("workspace",), ("read_document", "file_list")),
    "generate_pptx": Write(("workspace",), ("read_document", "file_list")),
    "generate_markdown_doc": _WORKSPACE_FILE,
    "pdf_merge": Write(("workspace",), ("pdf_info", "read_document")),
    "pdf_split": Write(("workspace",), ("pdf_info", "read_document")),
    "pdf_fill_form": Write(("workspace",), ("pdf_info", "read_document")),
    "pdf_redact": Write(("workspace",), ("pdf_info", "read_document")),
    # ── research files ─────────────────────────────────────────────────
    "read_url_to_file": Write(
        ("workspace",), ("list_research_chunks", "read_research_chunk", "file_read")),
    "digest_research_file": Write(("none",), note=_OUTPUT_ONLY),
    "summarize_research_file": Write(("none",), note=_OUTPUT_ONLY),
    "synthesize_from_digests": Write(("none",), note=_OUTPUT_ONLY),
    "export_session": _WORKSPACE_FILE,
    # ── files, code, git ───────────────────────────────────────────────
    "file_write": _WORKSPACE_FILE,
    "file_append": _WORKSPACE_FILE,
    "file_apply_patch": _WORKSPACE_FILE,
    "file_apply_patch_set": _WORKSPACE_FILE,
    "file_delete": _WORKSPACE_FILE,
    "format_code": _WORKSPACE_FILE,
    "git_checkout": Write(("workspace",), ("git_status", "file_read")),
    "git_commit": Write(("workspace",), ("git_status",)),
    "git_merge": Write(("workspace",), ("git_status", "file_read")),
    "git_pull": Write(("workspace",), ("git_status", "file_read")),
    "git_push": Write(("workspace", "external:github"), ("git_status",)),
    "github_create_issue": Write(("external:github",), ("github_list_issues",)),
    "github_comment_issue": Write(("external:github",), ("github_list_issues",)),
    "github_create_pr": Write(("external:github",), ("github_list_issues",)),
    "github_merge_pr": Write(("external:github",), ("github_list_issues",)),
    # ── host execution ─────────────────────────────────────────────────
    "shell_exec": Write(("host",), note="output is the tool result; files it writes are read with file_read"),
    "python_exec": Write(("host",), note="output is the tool result; files it writes are read with file_read"),
    "run_unit_tests": Write(("host",), note="the test report is the tool result"),
    "computer_use": Write(("host",), note="acts on the desktop; screenshots come back in the tool result"),
    "install_python_packages": Write(("host",), ("check_environment",)),
    "install_npm_packages": Write(("host",), ("check_environment",)),
    "mcp_test_server": Write(("none",), note="probes a server; the verdict is the tool result"),
    # ── browser ────────────────────────────────────────────────────────
    "browser_navigate": Write(("external:web",), ("browser_extract_text",)),
    "browser_click": Write(("external:web",), ("browser_extract_text",)),
    "browser_fill_form": Write(("external:web",), ("browser_extract_text",)),
    "browser_eval_js": Write(("external:web",), ("browser_extract_text",)),
    "browser_screenshot": Write(("workspace",), ("analyze_local_image", "analyze_image")),
    # ── media ──────────────────────────────────────────────────────────
    "generate_image": Write(("workspace",), ("analyze_local_image", "analyze_image")),
    "generate_ui_mockup": Write(("workspace",), ("analyze_local_image", "analyze_image")),
    # ── calendar and email ─────────────────────────────────────────────
    "create_event": Write(("external:calendar",), ("list_events", "find_free_slots")),
    "update_event": Write(("external:calendar",), ("list_events",)),
    "delete_event": Write(("external:calendar",), ("list_events",)),
    "email_send": Write(("external:email", "sandbox_emails.db"), ("email_list", "email_get")),
    "email_delete": Write(("external:email", "sandbox_emails.db"), ("email_list",)),
    "email_categorize": Write(("external:email", "sandbox_emails.db"), ("email_list", "email_get")),
    # ── messages to people ─────────────────────────────────────────────
    "send_message": Write(("external:chat",), note="delivered to a chat; nothing to read back"),
    "dispatch_notification": Write(("external:chat",), note="delivered to the operator; nothing to read back"),
    "send_approval_request": Write(("external:chat",), note="an approval card; its answer returns to the turn"),
    "send_file": Write(("external:chat",), note="sends a copy; the file itself stays readable with file_read"),
    # ── skills ─────────────────────────────────────────────────────────
    "activate_skill": Write(("none",), note="returns the skill's instructions in the tool result"),
    "install_agent_skill": Write(("user:skills",), ("list_agent_skills",)),
    "uninstall_agent_skill": Write(("user:skills",), ("list_agent_skills",)),
}


# ── the predicate every door asks ────────────────────────────────────────


def _strip_sidecar(name: str) -> str:
    lowered = name.lower()
    for sidecar in _DB_SIDECARS:
        if lowered.endswith(sidecar):
            return lowered[: -len(sidecar)]
    return lowered


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_kazma_store(path: str | Path) -> bool:
    """True if *path* is one of Kazma's databases (or its WAL/SHM sidecar).

    By LOCATION: any SQLite file under the data dir, except the default
    coding sandbox (``data_dir()/workspace``), whose databases are the
    user's. Matched by suffix rather than by name, so a store added later is
    covered on the day it is added. By NAME, anywhere: the gate registry and
    the vault — a stray copy of either is still the crown jewels.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    name = _strip_sidecar(resolved.name)
    if name in _ALWAYS_PROTECTED:
        return True
    if not name.endswith(_DB_SUFFIXES):
        return False
    try:
        from kazma_core.paths import data_dir

        root = data_dir().resolve()
    except (ImportError, OSError, RuntimeError):
        # Cannot classify; the callers' own rules still apply.
        return False
    return _under(resolved, root) and not _under(resolved, root / "workspace")


def store_name_for(path_or_name: str | Path) -> str | None:
    """The declared store (or family pattern) a path or filename belongs to."""
    raw = str(path_or_name or "")
    name = _strip_sidecar(Path(raw).name)
    if name in STORES:
        return name
    rel = raw.replace("\\", "/")
    try:
        from kazma_core.paths import data_dir

        rel = Path(raw).expanduser().resolve().relative_to(
            data_dir().resolve()
        ).as_posix()
    except (ImportError, OSError, RuntimeError, ValueError):
        pass  # not under the data dir: match by name only
    for pattern in STORE_FAMILIES:
        if "/" in pattern:
            if fnmatch.fnmatch(_strip_sidecar(rel), pattern):
                return pattern
        elif fnmatch.fnmatch(name, pattern):
            return pattern
    return None


def distinctive_store_names() -> frozenset[str]:
    """Store filenames specific enough to match as a bare substring.

    For checks that only have text (a command, a code snippet, an approval
    card). Generic names (``sessions.db``, ``audit.db``) are left to the
    location rule so a user's own file of that name is never flagged. Paths
    helpers are still consulted, so a store resolved through
    ``kazma_core.paths`` is covered even under a renamed file.
    """
    names = {n for n, s in STORES.items() if not s.ambiguous_name}
    names.update(_ALWAYS_PROTECTED)
    try:
        from kazma_core import paths as _paths

        for helper in (
            "vault_db_path", "checkpoints_db", "settings_db", "snapshots_db",
            "swarm_tasks_db", "audit_db", "rbac_db", "hub_registry_db",
            "primary_memory_db", "memory_ops_db", "knowledge_graph_db",
        ):
            fn = getattr(_paths, helper, None)
            if fn is None:
                continue
            try:
                names.add(Path(str(fn())).name.lower())
            except (OSError, RuntimeError, ValueError, TypeError):
                continue  # an unresolvable helper is skipped, never fatal
    except ImportError:
        pass
    return frozenset(n for n in names if n)


def readers_for_store(name: str) -> tuple[str, ...]:
    """Tools / context feeders that give the model this store's data back."""
    key = store_name_for(name) or _strip_sidecar(Path(str(name)).name)
    seen: list[str] = []
    for write in TOOL_WRITES.values():
        for group in write.groups():
            if key in group.targets:
                for reader in group.readers:
                    if reader not in seen:
                        seen.append(reader)
    return tuple(seen)


def store_refusal(path_or_name: str | Path, *, door: str) -> str:
    """Why *door* will not open this store, and what to use instead.

    ``door`` is how the model tried ("the SQL tools", "file tools",
    "python_exec", "shell_exec"). The reader is named because a bare "no"
    is what sent the model digging through every other door.
    """
    raw = Path(str(path_or_name)).name or str(path_or_name)
    key = store_name_for(path_or_name)
    store = STORES.get(key or "") or STORE_FAMILIES.get(key or "")
    what = f" ({store.holds})" if store else ""
    readers = readers_for_store(key or raw)
    tools = [r for r in readers if not r.startswith("context:")]
    fed = [
        CONTEXT_FEEDERS[r.split(":", 1)[1]][2]
        for r in readers
        if r.startswith("context:") and r.split(":", 1)[1] in CONTEXT_FEEDERS
    ]
    parts = [f"{raw} is one of Kazma's own stores{what}; {door} may not open it."]
    if tools:
        parts.append("Read it with " + ", ".join(tools) + ".")
    if fed:
        parts.append(
            "Already in your context every turn: " + ", ".join(fed) + "."
        )
    if not tools and not fed:
        parts.append(
            "Nothing in it is meant for you to read; tell the user what you "
            "need instead of looking for another way in."
        )
    return " ".join(parts)


def bundle_store_names() -> tuple[str, ...]:
    """Declared stores a migration bundle carries, sorted."""
    return tuple(sorted(n for n, s in STORES.items() if s.migration == "bundle"))


def bundle_family_patterns() -> tuple[str, ...]:
    """Store families (top-level globs) a migration bundle carries."""
    return tuple(
        sorted(p for p, s in STORE_FAMILIES.items() if s.migration == "bundle" and "/" not in p)
    )
