"""Every place that says whose statements it runs, and how (S4-01, step 0a).

This gate reads ``spaces.row_work``'s forms from the product's source:

* each use of ``install_statements`` (and of ``install_loops``) is declared in
  :data:`INSTALL`, with why no one space is the answer, and nothing is declared
  that is not used. Step 1 of S4-01 takes its install door from this list;
* each use of a form that names a space is declared in :data:`ROWS`, with
  where that space comes from: never an id a request brought (H6);
* the forms that fall back to a space's own account are Kazma's own work only;
  a person's own act (a move) binds that person or refuses (M1);
* a form is entered, never only called or passed about: in a ``with``, or
  decorating a plain function (a coroutine or a generator runs its body after
  the block ended). What a resolver answers is entered where it was asked;
* none is held across a ``yield`` (M5): in a generator the variable is in
  force in the consumer between rows.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGES = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui", "kazma-skills")
ROW_WORK = "kazma-core/kazma_core/spaces/row_work.py"

#: The install's own statements: ``file::function`` -> why no one space is the
#: answer. A name is a function, and everything it calls is the install's.
INSTALL: dict[str, str] = {
    "kazma-core/kazma_core/checkpoint_retention.py::_prune_postgres_checkpoints":
        "retention, by age alone, over every space's chats",
    "kazma-ui/kazma_ui/hitl_timeout.py::_watchdog_loop":
        "lists every space's waiting cards, with nothing bound; each is then timed out as its own",
    "kazma-ui/kazma_ui/turn_resume.py::_find_cut_turns":
        "lists every space's newest checkpoints at a start; each turn goes on as its own",
    "kazma-core/kazma_core/checkpoints_pg.py::open_postgres_checkpointer":
        "the checkpoint tables are made once, for every space",
    "kazma-core/kazma_core/config_store.py::ConfigStore._pg_access":
        "the settings table: one key space and one cache for the install, outside every space's lock "
        "(S4-01 decision 4)",
    "kazma-core/kazma_core/db/postgres_pool.py::_ensure_core_schema":
        "the shared tables are made once, for every space",
    "kazma-core/kazma_core/db/pg_backup.py::verify_required_pg_tables":
        "which tables the database has, no row of any space",
    "kazma-core/kazma_core/documents/audit.py::DocumentAuditStore._ensure_pg_guard":
        "the audit's trigger, one for every space's rows",
    "kazma-core/kazma_core/documents/admission.py::IntakeAdmissionStore._transaction":
        "one queue, one mutex and one set of budgets, counted over every space",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._storage_bytes":
        "the install's totals, of every space together",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService.run_maintenance":
        "the collection's audit row is the install's, under no space",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService.start_workers":
        "loops that serve every space, whoever's turn started them",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository._ensure_schema":
        "the job tables are made once, for every space",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.claim_next":
        "the fair claim reads every space's queue; the job's stages then run in its own",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.interrupted_intake_jobs":
        "lists every space's jobs; each is then recovered in its own",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.queue_stats":
        "the one queue's depth, which an upload's admission counts over every space",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.recover_expired_leases":
        "leases that ran out, whoever's jobs they hold",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.release_leases":
        "a stopping worker's leases, whoever's jobs they hold",
    "kazma-core/kazma_core/documents/repository_pg.py::PostgresDocumentRepository._ensure_schema":
        "the document tables are made once, for every space",
    "kazma-core/kazma_core/documents/repository_pg.py::PostgresDocumentRepository.active_index_entries":
        "lists what every space must find; each entry is then put back in its own",
    "kazma-core/kazma_core/documents/retention.py::DocumentGarbageCollector.collect":
        "what any space refers to decides what may go",
    "kazma-core/kazma_core/memory/health.py::build_memory_health":
        "a ping only: the probes round it read the owner's memory as before",
    "kazma-core/kazma_core/memory/state_backend.py::PostgresStateBackend.available":
        "a ping: whether the memory mirror answers, no row of any space",
    "kazma-core/kazma_core/memory/state_backend.py::PostgresStateBackend._ensure":
        "the mirror's tables are made once, for every space",
    "kazma-core/kazma_core/memory/state_backend.py::sync_state_mirror":
        "the whole mirror compared with the local store; each push in its row's own space",
    "kazma-core/kazma_core/memory/state_backend.py::reconcile_state_beliefs":
        "the mirror's facts of every space compared with the local store's",
    "kazma-core/kazma_core/memory/state_backend.py::mirror_drift_summary":
        "how far the whole mirror is from the local store, for memory health",
    "kazma-core/kazma_core/memory/backends.py::PgvectorBackend.probe":
        "a ping: the catalog says whether this Postgres can hold vectors",
    "kazma-core/kazma_core/memory/backends.py::PgvectorBackend._ensure_table":
        "the vector table is made once, for every space",
    "kazma-core/kazma_core/swarm/task_store.py::TaskStore.record_worker_metric":
        "the workers' metrics table has no space column: the install's (S4-01 step 2's rule)",
    "kazma-core/kazma_core/swarm/task_store.py::TaskStore.get_worker_metrics":
        "the workers' metrics table has no space column: the install's (S4-01 step 2's rule)",
    "kazma-core/kazma_core/swarm/task_store.py::TaskStore.get_all_worker_metrics":
        "the workers' metrics table has no space column: the install's (S4-01 step 2's rule)",
    "kazma-ui/kazma_ui/session_manager.py::SessionManager._load_all_from_primary":
        "every space's chats into the one process-wide cache, keyed by space; each is then served to its own "
        "space (design section 5)",
    "kazma-core/kazma_core/memory/forget.py::converge":
        "every space's pending forgets; each row it removes in its own space",
    "kazma-core/kazma_core/memory/rehydrate.py::run_rehydrate_pass":
        "lists every space's chats and checkpoints for erased turns; each restored row is its own",
    "kazma-core/kazma_core/memory/turn_reconcile.py::run_turn_reconcile_pass":
        "lists every space's chats changed since its one cursor (the sweep, the memory restore); "
        "each turn is written as its own",
    "kazma-core/kazma_core/memory/text_fill.py::run_text_fill_pass":
        "reads every space's chats that still hold a cut turn whole; each row is its own",
    "kazma-core/kazma_core/runs/adapters/documents.py::_DocumentAdapter._read":
        "the supervisor's list of every space's jobs, with nothing bound only",
    "kazma-core/kazma_core/security/service_exposure.py::_role_answer":
        "the role that signed in, no row of any space",
    "kazma-core/kazma_core/swarm/engine.py::SwarmEngine.redispatch_recovered_tasks":
        "every space's legacy recovery rows, listed at a start; each is written as its own",
    "kazma-core/kazma_core/swarm/task_store.py::TaskStore._init_db":
        "the swarm's tables are made once, for every space",
    "kazma-core/kazma_core/swarm/task_store.py::TaskStore.get_paused_tasks":
        "lists every space's paused tasks at a start; each is restored as its own",
    "kazma-core/kazma_core/swarm/task_store.py::TaskStore.prune_tasks":
        "retention, by age alone, over every space's finished tasks",
    "kazma-core/kazma_core/swarm/task_store.py::TaskStore.requeue_orphaned_running":
        "lists every space's interrupted tasks at a start; each is written as its own",
    "kazma-core/kazma_core/runs/adapters/swarm.py::_SwarmAdapter._stored":
        "the supervisor's list of every space's tasks, with nothing bound only",
    "kazma-ui/kazma_ui/health.py::check_database":
        "a ping: whether the database answers, no row of any space",
}

#: A form that names a space: ``file::function`` -> where the space comes from.
#: ``statements:`` names the row, ``Kazma's own work:`` may act for the space's
#: own account, ``a person's act:`` is that person's or refused. And each use
#: in it, as written: a second one, or another argument, is declared anew.
ROWS: dict[str, tuple[str, tuple[str, ...]]] = {
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._put_back_in_the_index": (
        "Kazma's own work: the entry the documents store lists, as its owner",
        ("statements_of(tenant)", "work_of(tenant, record.owner_id)",)),
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._recover_stranded_intakes": (
        "statements: the reservation's own tenant",
        ("statements_of(row['tenant_id'])", "statements_of(row['tenant_id'])",)),
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._settle_one_move": (
        "Kazma's own work: the journal's two spaces, the copy's taking back in its own",
        ("statements_of(target)", "work_of(target, actor)",)),
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._settle_unrecorded": (
        "Kazma's own work: the entry the library holds",
        ("statements_of(tenant)", "work_of(tenant)",)),
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._still_in_both": (
        "a person's act: the space of theirs they move into, asked again under the lock",
        ("resolve_act_of(target, actor)",)),
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._still_there": (
        "statements: a move's journal, or its own two spaces",
        ("statements_of(tenant)",)),
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService.move_document": (
        "statements: the audit row of each of the move's two spaces, whose rule was asked",
        ("statements_of(tenant)",)),
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService.recover_interrupted_intake": (
        "Kazma's own work: the job's own tenant and person",
        ("work_of(snapshot.tenant_id, snapshot.actor_id)",)),
    "kazma-core/kazma_core/documents/telemetry.py::tenant_quota_snapshot": (
        "statements: one space's own total, which the caller's route allows (a number, no row)",
        ("statements_of(tenant_id)",)),
    "kazma-core/kazma_core/documents/worker.py::DocumentWorker._not_worked_on": (
        "statements: the claimed job's own tenant",
        ("statements_of(job.tenant_id)",)),
    "kazma-core/kazma_core/documents/worker.py::DocumentWorker._process_claimed": (
        "Kazma's own work: the claimed job's own tenant and person",
        ("work_of_async(job.tenant_id, job.actor_id)", "statements_of(job.tenant_id)",)),
    "kazma-core/kazma_core/runs/adapters/documents.py::_DocumentAdapter._read": (
        "statements: the listed job's own tenant, with nothing bound only",
        ("statements_of(job.tenant_id)",)),
    "kazma-core/kazma_core/spaces/people.py::_after_leaving": (
        "Kazma's own work: the space whose membership change just took effect",
        ("resolve_work_of(space_id)", "statements_of(space_id)",)),
    "kazma-core/kazma_core/spaces/space_limits.py::_documents_used": (
        "statements: a space the admin's page, or the space's own, counts (a number, no row)",
        ("statements_of(space_id)",)),
    "kazma-gateway/kazma_gateway/stores/checkpoint.py::CheckpointManager._door": (
        "statements: the thread's own space, whoever asks (its record), or the bound space for a call that "
        "names no thread (Spaces S4-01 step 4)",
        ("statements_of(space)",)),
    "kazma-core/kazma_core/swarm/checkpoint_manager.py::CheckpointManager.handle_pipeline_checkpoint": (
        "statements: the paused task's own space",
        ("statements_of(space_of_task(task))",)),
    "kazma-core/kazma_core/swarm/checkpoint_manager.py::CheckpointManager.restore_paused_tasks": (
        "statements: each restored task's own space",
        ("statements_of(space_of_task(task))",)),
    "kazma-core/kazma_core/swarm/engine.py::SwarmEngine._admit_to_store": (
        "statements: the admitted task's own space",
        ("statements_of(space_of_task(task))",)),
    "kazma-core/kazma_core/swarm/engine.py::SwarmEngine._admit_to_store._admit": (
        "statements: the admitted task's own space",
        ("statements_of(space_of_task(snapshot))",)),
    "kazma-core/kazma_core/swarm/engine.py::SwarmEngine._journal_step": (
        "statements: the resumed task's own space",
        ("statements_of(space_of_task(task))",)),
    "kazma-core/kazma_core/swarm/engine.py::SwarmEngine._persist._write": (
        "statements: the written task's own space, whoever ends it",
        ("statements_of(space_of_task(snapshot))",)),
    "kazma-core/kazma_core/swarm/task_store.py::TaskStore.requeue_orphaned_running": (
        "statements: each interrupted task's own space",
        ("statements_of(space_of_task(task))",)),
    "kazma-ui/kazma_ui/history_import_api.py::_run": (
        "a person's act: the space they brought their history into",
        ("resolve_act_of(space, person)",)),
    "kazma-ui/kazma_ui/session_manager.py::SessionManager._add_spooled_only": (
        "statements: the spooled chat's own space",
        ("statements_of(tenant_id)",)),
    "kazma-ui/kazma_ui/session_manager.py::SessionManager._drain_spool": (
        "statements: the spooled chat's own space",
        ("statements_of(tenant_id)",)),
    "kazma-ui/kazma_ui/session_manager.py::SessionManager.hand_over": (
        "statements: the space a chat moves to, whose rule the move asked",
        ("statements_of(to.tenant_id)", "statements_of(to.tenant_id)",)),
    "kazma-ui/kazma_ui/health.py::_check_memory_recall": (
        "statements: the owner's space, the canary's",
        ("statements_of(OWNER_SPACE_ID)",)),
    "kazma-core/kazma_core/memory/state_backend.py::PostgresStateBackend.mirror_episode_rows": (
        "statements: each pushed memory's own tenant, where Kazma pushes it itself",
        ("row_statements(space)",)),
    "kazma-core/kazma_core/memory/state_backend.py::PostgresStateBackend.mirror_belief": (
        "statements: the pushed fact's own tenant, where Kazma pushes it itself",
        ("row_statements(space_of_record(row.get('tenant_id')))",)),
    "kazma-core/kazma_core/memory/state_backend.py::PostgresStateBackend.delete_belief": (
        "statements: the removed fact's own tenant, which its caller names",
        ("row_statements(named)",)),
    "kazma-core/kazma_core/memory/backends.py::PgvectorBackend.upsert": (
        "statements: the vector's own row's tenant, where Kazma writes it itself",
        ("row_statements(tenant_id)",)),
    "kazma-core/kazma_core/memory/backends.py::PgvectorBackend.delete": (
        "statements: the forgotten row's own tenant, which the forget names",
        ("row_statements(named)",)),
}

#: Who asks for the account a space's own work acts for: ``file::function`` ->
#: why it is never a person's own act.
ACTS_FOR_A_SPACE: dict[str, str] = {
    "kazma-core/kazma_core/cron/scheduler.py::_acting_account": "whom a job that names nobody acts for",
    "kazma-core/kazma_core/memory/task_queue.py::_persons_binding": "a queued memory task of a person who left",
    "kazma-core/kazma_core/memory/task_queue.py::_task_binding": "a queued memory task that names nobody",
    "kazma-core/kazma_core/spaces/identity.py::work_scope": "queued work that names nobody (a person who left is refused)",
    "kazma-core/kazma_core/spaces/row_work.py::resolve_work_of": "Kazma's own work for a row, declared in ROWS",
    "kazma-core/kazma_core/swarm/single_operator.py::_acts_for": "who may stop a task of a person who left",
    "kazma-ui/kazma_ui/hitl_timeout.py::_close_turn_of_departed":
        "ending the turn of a card whose asker left: nothing is resumed",
    "kazma-ui/kazma_ui/hitl_timeout.py::_close_turn_unread":
        "ending the turn of a card whose person is unread: nothing is resumed",
    "kazma-core/kazma_core/skills/self_improvement.py::_learn_from_turn":
        "the lesson a finished turn of nobody's teaches",
    "kazma-ui/kazma_ui/hitl_answerers.py::whose_turn": "whose card a thread that names nobody shows",
    "kazma-ui/kazma_ui/runs_chat_turn.py::_acting": "whose turn a thread that names nobody ran",
}

#: The forms, by the name they are called by.
INSTALL_FORMS = {"install_statements", "install_loops"}
#: A row Kazma keeps for itself: its own space where Kazma does the work, the
#: bound one where a request does (``row_work.row_statements``).
OWN_ROW_FORMS = {"row_statements"}
#: What answers something to enter: a call inside the ``with``, or kept for one.
RESOLVERS = {"resolve_work_of", "resolve_act_of", "work_of_async"}
#: The forms that may act for a space's own account.
FALLS_BACK = {"work_of", "resolve_work_of", "work_of_async"}
ROW_FORMS_DECLARED = FALLS_BACK | OWN_ROW_FORMS | {"statements_of", "resolve_act_of"}
FORMS = INSTALL_FORMS | OWN_ROW_FORMS | {"statements_of", "work_of"}


@dataclass(frozen=True)
class Use:
    """One use of a form in one function."""

    where: str
    form: str
    how: str  # "with", "decorator", "kept", "call" or "passed"
    function: str  # "plain", "coroutine", "generator", "context manager", "module"
    yields: bool
    line: int
    written: str = ""


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _own(node: ast.AST) -> list[ast.AST]:
    """The nodes of *node*'s own body, not of a function inside it."""
    out: list[ast.AST] = []
    todo = list(ast.iter_child_nodes(node))
    while todo:
        child = todo.pop()
        out.append(child)
        if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            todo.extend(ast.iter_child_nodes(child))
    return out


def _yields(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Yield | ast.YieldFrom) for n in _own(node))


def _kind(function: ast.AST | None) -> str:
    if function is None:
        return "module"
    names = {_name(d) for d in function.decorator_list}
    if names & {"contextmanager"}:
        return "context manager"  # entered and left in the caller's own context
    if _yields(function):
        return "generator"
    return "coroutine" if isinstance(function, ast.AsyncFunctionDef) else "plain"


def _kept_and_entered(call: ast.Call, function: ast.AST | None) -> bool:
    """Whether what a resolver answered is returned, or kept under a name a
    ``with`` of the same function enters."""
    if function is None:
        return False
    own = _own(function)
    if any(isinstance(n, ast.Return) and n.value is not None and call in ast.walk(n.value) for n in own):
        return True
    names = {t.id for n in own if isinstance(n, ast.Assign) and call in ast.walk(n.value)
             for t in n.targets if isinstance(t, ast.Name)}
    return any(isinstance(c, ast.Call) and _name(c) in names
               for n in own if isinstance(n, ast.With | ast.AsyncWith)
               for item in n.items for c in ast.walk(item.context_expr))


def uses_in(source: str, rel: str) -> list[Use]:
    """Every use of a form in *source*."""
    found: list[Use] = []
    every = FORMS | RESOLVERS
    tree = ast.parse(source)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    exported = {id(c) for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets) for c in ast.walk(n)}

    def visit(node: ast.AST, stack: list[str], function: ast.AST | None) -> None:
        taken: set[int] = set()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            stack = [*stack, node.name]
            if not isinstance(node, ast.ClassDef):
                function = node
                for mark in node.decorator_list:
                    if isinstance(mark, ast.Call) and _name(mark) in FORMS:
                        taken.add(id(mark))
                        found.append(Use(f"{rel}::{'.'.join(stack)}", _name(mark), "decorator",
                                         _kind(node), _yields(node), mark.lineno))
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                for call in ast.walk(item.context_expr):
                    if isinstance(call, ast.Call) and _name(call) in FORMS | RESOLVERS:
                        taken.add(id(call))
                        found.append(Use(f"{rel}::{'.'.join(stack)}", _name(call), "with",
                                         _kind(function), _yields(node), node.lineno, ast.unparse(call)))
        here = f"{rel}::{'.'.join(stack)}"
        if isinstance(node, ast.Call) and _name(node) in every and id(node) not in seen:
            kept = _name(node) in RESOLVERS and _kept_and_entered(node, function)
            found.append(Use(here, _name(node), "kept" if kept else "call", _kind(function),
                             function is not None and _yields(function), node.lineno, ast.unparse(node)))
        if isinstance(node, ast.Name | ast.Attribute) and _name(node) in every and id(node) not in called \
                and isinstance(node.ctx, ast.Load):
            found.append(Use(here, _name(node), "passed", _kind(function), False, node.lineno))
        if isinstance(node, ast.Constant) and node.value in every and id(node) not in exported:
            # getattr(row_work, "statements_of"): the form by its name.
            found.append(Use(here, str(node.value), "passed", _kind(function), False, node.lineno))
        if isinstance(node, ast.ImportFrom):
            found.extend(Use(here, a.name, "passed", _kind(function), False, node.lineno)
                         for a in node.names if a.name in every and a.asname not in (None, a.name))
        seen.update(taken)
        for child in ast.iter_child_nodes(node):
            visit(child, stack, function)

    seen: set[int] = set()
    visit(tree, [], None)
    return found


def _product_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", *[f"{p}/*.py" for p in PACKAGES]],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if "/tests/" not in line and "_tests/" not in line]


def _uses() -> list[Use]:
    found: list[Use] = []
    for rel in _product_files():
        if rel == ROW_WORK:
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if any(form in text for form in FORMS | RESOLVERS):
            found += uses_in(text, rel)
    return found


def _problems(uses: list[Use]) -> list[str]:
    bad: list[str] = []
    for use in uses:
        at = f"{use.where}:{use.line}: {use.form}"
        if use.how == "call":
            bad.append(f"{at} is called and never entered")
        if use.how == "passed":
            bad.append(f"{at} is named without being called, or under another name")
        if use.how == "decorator" and use.function != "plain":
            bad.append(f"{at} decorates a {use.function}: its body runs after the block ended")
        if use.how in ("with", "kept") and use.yields and use.function == "generator":
            bad.append(f"{at} is held across a yield")
    return bad


def test_every_form_is_entered_and_none_is_held_across_a_yield() -> None:
    assert not _problems(_uses()), "\n".join(_problems(_uses()))


def test_the_installs_statements_are_the_declared_ones() -> None:
    used = {u.where for u in _uses() if u.form in INSTALL_FORMS}
    assert used - set(INSTALL) == set(), "declare these in INSTALL with why no one space is the answer"
    assert set(INSTALL) - used == set(), "declared, and no longer the install's"
    assert all(len(reason.split()) >= 4 for reason in INSTALL.values())


def _reason_problems(uses: list[Use], rows: dict[str, tuple[str, tuple[str, ...]]]) -> list[str]:
    """A form used for another reason than its function declares."""
    bad: list[str] = []
    for use in uses:
        reason, _written = rows.get(use.where, ("", ()))
        if use.form in FALLS_BACK and not reason.startswith("Kazma's own work:"):
            bad.append(f"{use.where}: {use.form} may act for the space's own account")
        if use.form == "resolve_act_of" and not reason.startswith("a person's act:"):
            bad.append(f"{use.where}: {use.form} is a person's own act")
        name = use.where.rsplit(".", 1)[-1]
        if use.form in FALLS_BACK and "move" in name and "settle" not in name:
            bad.append(f"{use.where}: a move is a person's own act, whatever it is declared as")
    return bad


def _written_problems(uses: list[Use], rows: dict[str, tuple[str, tuple[str, ...]]]) -> list[str]:
    """Uses in a declared function that are not the ones it declares."""
    now: dict[str, list[str]] = {}
    for use in sorted(uses, key=lambda u: u.line):
        now.setdefault(use.where, []).append(use.written.replace('"', "'"))
    return [f"{where}: {sorted(now.get(where, []))}, declared {sorted(rows[where][1])}" for where in rows
            if sorted(now.get(where, [])) != sorted(rows[where][1])]


def test_a_second_use_in_a_declared_function_is_found() -> None:
    uses = uses_in("def f(row, request):\n    with statements_of(row.t):\n        read()\n"
                   "    with statements_of(request.query_params['space']):\n        read()\n", "x.py")
    assert _written_problems(uses, {"x.py::f": ("statements: the row's own", ("statements_of(row.t)",))})
    assert not _written_problems(uses[:1], {"x.py::f": ("statements: the row's own", ("statements_of(row.t)",))})


def test_every_form_that_names_a_space_is_declared_with_where_it_comes_from() -> None:
    used = [u for u in _uses() if u.form in ROW_FORMS_DECLARED]
    where = {u.where for u in used}
    assert where - set(ROWS) == set(), "declare these in ROWS with where the space comes from"
    assert set(ROWS) - where == set(), "declared, and no longer there"
    kinds = ("statements:", "Kazma's own work:", "a person's act:")
    assert all(r.startswith(kinds) and len(r.split()) >= 5 for r, _written in ROWS.values())
    assert _written_problems(used, ROWS) == []
    assert _reason_problems(used, ROWS) == []


# ── a delete that trusts "nothing there" asks whose door it is (0b M1) ──

_DELETE = re.compile(r"\bDELETE\s+FROM\b", re.I)
_EXISTS = re.compile(r"\bSELECT\s+1\s+FROM\b", re.I)


def _texts(node: ast.AST) -> str:
    """Every string a function holds, f-strings' fixed parts included."""
    return " ".join(n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str))


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[f"{node.name}.{item.name}"] = item
    return found


def trusting_deletes(source: str, roots: set[str]) -> dict[str, bool]:
    """The functions *roots* run in this file (each, and the functions of its
    class or module it calls) that send a ``DELETE`` and then ask whether a
    row of that id is left: their name -> whether they ask ``door_hides``
    first. Once the lock is armed, a door of another space sees neither the
    row nor its absence, so its "nothing there" is no "gone" (0b review M1)."""
    functions = _functions(ast.parse(source))
    reached: set[str] = set()
    for root in roots:
        node = functions.get(root)
        if node is None:
            continue
        reached.add(root)
        cls = root.rpartition(".")[0]
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            fn = call.func
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "self" and cls:
                reached.add(f"{cls}.{fn.attr}")
            elif isinstance(fn, ast.Name):
                reached.add(fn.id)
    out: dict[str, bool] = {}
    for name in sorted(reached & set(functions)):
        text = _texts(functions[name])
        if _DELETE.search(text) and _EXISTS.search(text):
            out[name] = any(isinstance(n, ast.Call) and _name(n.func) == "door_hides"
                            for n in ast.walk(functions[name]))
    return out


def _rows_by_file() -> dict[str, set[str]]:
    by_file: dict[str, set[str]] = {}
    for key in ROWS:
        rel, _, where = key.partition("::")
        by_file.setdefault(rel, set()).add(where)
    return by_file


def test_a_delete_that_trusts_nothing_there_asks_whose_door_it_is() -> None:
    found = {f"{rel}::{name}": asks for rel, roots in _rows_by_file().items()
             for name, asks in trusting_deletes((REPO / rel).read_text(encoding="utf-8"), roots).items()}
    assert {"kazma-core/kazma_core/memory/state_backend.py::PostgresStateBackend._delete_fact",
            "kazma-core/kazma_core/memory/backends.py::PgvectorBackend._delete_vector"} <= set(found), found
    assert [name for name, asks in found.items() if not asks] == [], (
        "a delete in a declared row's space that trusts an existence SELECT asks row_work.door_hides first")


_PLANTED_DELETE = """
class Store:
    def remove(self, row_id, tenant_id):
        with row_statements(tenant_id):
            return self._gone(row_id, tenant_id)

    def _gone(self, row_id, space):
        cur.execute(f"DELETE FROM {self.table} WHERE id = %s RETURNING id", (row_id,))
        if not cur.fetchall():
            {ask}cur.execute("SELECT 1 FROM t WHERE id = %s", (row_id,))
            return cur.fetchone() is None
        return True
"""


@pytest.mark.parametrize(("ask", "asks"), [("", False), ("door_hides(space)\n            ", True)])
def test_a_planted_delete_that_trusts_nothing_there_is_found(ask: str, asks: bool) -> None:
    assert trusting_deletes(_PLANTED_DELETE.replace("{ask}", ask), {"Store.remove"}) == {"Store._gone": asks}


def test_the_real_deletes_without_their_question_are_found() -> None:
    """The negative control on the product's own source."""
    rel = "kazma-core/kazma_core/memory/backends.py"
    source = (REPO / rel).read_text(encoding="utf-8").replace("door_hides(", "door_kept_quiet(")
    assert trusting_deletes(source, _rows_by_file()[rel])["PgvectorBackend._delete_vector"] is False


@pytest.mark.parametrize(("form", "reason"), [
    ("work_of", "statements: the id the request brought"),
    ("resolve_work_of", "a person's act: the space they move into"),
    ("resolve_act_of", "Kazma's own work: the space they move into"),
    ("resolve_work_of", "Kazma's own work: a move that says it is not one"),
])
def test_a_planted_persons_act_that_falls_back_is_found(form: str, reason: str) -> None:
    uses = uses_in(f"def move_page(a):\n    with {form}(a, 'omar')():\n        go()\n", "x.py")
    assert _reason_problems(uses, {"x.py::move_page": (reason, ())})


def test_a_planted_route_with_a_requests_id_is_seen() -> None:
    """The review's control: a route that took its space from the request."""
    uses = uses_in("async def route(request):\n    with statements_of(request.query_params['space']):\n"
                   "        return await read()\n", "x.py")
    assert [u.where for u in uses] == ["x.py::route"] and uses[0].where not in ROWS


def _callers_of(name: str) -> set[str]:
    found: set[str] = set()

    def visit(node: ast.AST, stack: list[str], rel: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = [*stack, child.name] if isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) else stack
            if isinstance(child, ast.Name | ast.Attribute) and _name(child) == name and isinstance(child.ctx, ast.Load):
                found.add(f"{rel}::{'.'.join(inner)}")  # called, or handed on (asyncio.to_thread)
            visit(child, inner, rel)

    for rel in _product_files():
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if name in text:
            visit(ast.parse(text), [], rel)
    return found


def test_who_acts_for_a_space_is_declared() -> None:
    """The other fallback to a space's own account: each caller is declared."""
    callers = _callers_of("acting_account")
    assert callers - set(ACTS_FOR_A_SPACE) == set(), "whose act is it? a person's own is theirs or refused"
    assert set(ACTS_FOR_A_SPACE) - callers == set(), "declared, and no longer there"


@pytest.mark.parametrize(("source", "said"), [
    ("async def f():\n    enter = resolve_work_of('a')\n    with enter():\n        yield 1\n", "held across a yield"),
    ("def f():\n    enter = resolve_work_of('a')\n    go()\n", "never entered"),
    ("async def f():\n    await work_of_async('a')\n    go()\n", "never entered"),
    ("def f():\n    cm = statements_of('a')\n    with cm:\n        yield 1\n", "never entered"),
    ("@asynccontextmanager\nasync def f():\n    with statements_of('a'):\n        yield\n", "held across a yield"),
    ("from kazma_core.spaces.row_work import statements_of as of\n", "another name"),
    ("def f():\n    form = row_work.statements_of\n    with form('a'):\n        go()\n", "without being called"),
    ("def f():\n    run(functools.partial(work_of, 'a'))\n", "without being called"),
    ("def f():\n    with getattr(rw, 'statements_of')('a'):\n        go()\n", "without being called"),
    ("def f():\n    install_statements('a ping')\n    ping()\n", "never entered"),
    ("def f():\n    g(statements_of(space))\n", "never entered"),
    ("@install_statements('x')\nasync def f():\n    await g()\n", "decorates a coroutine"),
    ("@row_work.install_statements('x')\ndef f():\n    yield 1\n", "decorates a generator"),
    ("async def f():\n    with statements_of('a'):\n        async for x in g():\n            yield x\n",
     "held across a yield"),
    ("def f():\n    with work_of('a'):\n        yield from g()\n", "held across a yield"),
    ("async def f():\n    with (await work_of_async('a'))():\n        yield 1\n", "held across a yield"),
])
def test_a_planted_wrong_use_is_found(source: str, said: str) -> None:
    problems = _problems(uses_in(source, "x.py"))
    assert len(problems) == 1 and said in problems[0], problems


@pytest.mark.parametrize("source", [
    "def f():\n    with statements_of('a'):\n        return g()\n",
    "def f():\n    with (bind_scope('a', 'b') if on() else statements_of('a')):\n        return g()\n",
    "def f():\n    enter = resolve_act_of('a', 'b')\n    with open(x) as s, enter():\n        return g(s)\n",
    "async def f():\n    enter = await work_of_async('a')\n    with enter():\n        await g()\n",
    "def f():\n    return resolve_work_of('a')\n",
    "@install_statements('x')\ndef f():\n    return 1\n",
    "async def f():\n    with statements_of('a'):\n        await g()\n",
    "@contextmanager\ndef f():\n    with statements_of('a'):\n        yield\n",
    # the yield is another function's
    "def f():\n    with statements_of('a'):\n        def g():\n            yield 1\n        return list(g())\n",
])
def test_a_right_use_is_not_a_finding(source: str) -> None:
    uses = uses_in(source, "x.py")
    assert len(uses) == 1 and not _problems(uses)
    assert uses[0].where == "x.py::f"


def test_an_undeclared_install_statement_is_seen() -> None:
    uses = uses_in("class A:\n    @install_statements('x')\n    def f(self):\n        return 1\n", "x.py")
    assert [u.where for u in uses] == ["x.py::A.f"]


# ── a loop over rows of several spaces enters one per row ────────────────

#: What answers rows of every space: the store method, or the function.
LISTINGS = {"interrupted_intake_jobs", "recent_jobs", "unpublished", "fenced_recent", "active_index_entries",
            "document_index_entries", "_moves_under_way", "get_paused_tasks"}

#: A loop over such rows that runs nothing for a row: ``file::function`` ->
#: what it does instead.
NO_ROW_WORK: dict[str, str] = {}

#: What a row's work is entered by.
ROW_FORMS = ROW_FORMS_DECLARED | {"bind_scope"}


def loops_without_a_space(source: str, rel: str) -> set[str]:
    """Functions of *source* that loop over a listing and enter no row's space
    in the loop's body, nor in a method of theirs the body calls."""
    tree = ast.parse(source)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    found: set[str] = set()

    def enters(nodes: list[ast.stmt], seen: frozenset[str] = frozenset()) -> bool:
        for node in (n for stmt in nodes for n in ast.walk(stmt)):
            if isinstance(node, ast.Call):
                name = _name(node)
                if name in ROW_FORMS:
                    return True
                if name in functions and name not in seen and enters(functions[name].body, seen | {name}):
                    return True
        return False

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = [*stack, child.name] if isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) else stack
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                own = _own(child)
                listed = {t.id for n in own if isinstance(n, ast.Assign) and _lists(n.value)
                          for t in n.targets if isinstance(t, ast.Name)}
                for loop in own:
                    if isinstance(loop, ast.For | ast.AsyncFor) and (
                            _lists(loop.iter) or listed & {n.id for n in ast.walk(loop.iter) if isinstance(n, ast.Name)}):
                        if not enters(loop.body):
                            found.add(f"{rel}::{'.'.join(inner)}")
            visit(child, inner)

    visit(tree, [])
    return found


def _lists(expr: ast.AST) -> bool:
    """Whether *expr* calls a listing, or reads one by its name (``getattr``)."""
    return any((isinstance(n, ast.Call) and _name(n) in LISTINGS)
               or (isinstance(n, ast.Constant) and n.value in LISTINGS) for n in ast.walk(expr))


def test_a_loop_over_every_spaces_rows_enters_one_per_row() -> None:
    found: set[str] = set()
    for rel in _product_files():
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if any(name in text for name in LISTINGS):
            found |= loops_without_a_space(text, rel)
    assert found - set(NO_ROW_WORK) == set(), "enter work_of or statements_of for each row"
    assert set(NO_ROW_WORK) - found == set(), "declared, and no longer so"


@pytest.mark.parametrize(("source", "found"), [
    ("def f(s):\n    for j in s.interrupted_intake_jobs():\n        s.get(j)\n", True),
    ("def f(s):\n    rows = s.unpublished()\n    for r in sorted(rows):\n        s.claim(r)\n", True),
    ("def f(s):\n    rows = getattr(s, 'unpublished', None)\n    for r in rows():\n        s.claim(r)\n", True),
    ("def f(s):\n    with work_of('a'):\n        for r in s.unpublished():\n            s.claim(r)\n", True),
    ("def f(s):\n    for r in s.unpublished():\n        with statements_of(r.t):\n            s.claim(r)\n", False),
    ("def f(s):\n    for r in s.unpublished():\n        one(r)\ndef one(r):\n    with work_of(r.t):\n        go()\n", False),
    ("def f(s):\n    for r in s.mine():\n        s.claim(r)\n", False),
])
def test_a_planted_loop_is_found(source: str, found: bool) -> None:
    assert loops_without_a_space(source, "x.py") == ({"x.py::f"} if found else set())


# ── who binds a space itself ─────────────────────────────────────────────

#: Files that bind a scope of their own (``bind_scope``, ``set_scope``) ->
#: where its space comes from. A route binds none: the tenant middleware does,
#: after it checked the membership.
BINDERS: dict[str, str] = {
    "kazma-cli/kazma_cli/history_import.py": "the owner's: the command is the owner's",
    "kazma-core/kazma_core/backup/cloud_sync.py": "the owner's: the install's backups are the owner's",
    "kazma-core/kazma_core/chat_files.py": "a chat move's two spaces, whose rule was asked",
    "kazma-core/kazma_core/cron/scheduler.py": "the job's own space and creator (work_scope)",
    "kazma-core/kazma_core/memory/task_queue.py": "the queued task's own tenant and person",
    "kazma-core/kazma_core/observability/connector_health.py": "the checked account's own space",
    "kazma-core/kazma_core/observability/daily_digest.py": "the owner's: the digest is the owner's",
    "kazma-core/kazma_core/runs/runbooks.py": "the owner's: runbooks act for the owner",
    "kazma-core/kazma_core/skills/self_improvement.py": "the finished turn's own space",
    "kazma-core/kazma_core/spaces/machine.py": "a space of the owner's, after its own membership check",
    "kazma-core/kazma_core/spaces/people.py": "the shared space just made",
    "kazma-core/kazma_core/spaces/threads.py": "the thread's record",
    "kazma-core/kazma_core/swarm/patterns.py": "the task's own space and person (work_scope)",
    "kazma-core/kazma_core/web_acquire/canary.py": "the owner's: the canary's library",
    "kazma-gateway/kazma_gateway/agent_handler/graph.py": "the linked account's space of the message's chat",
    "kazma-gateway/kazma_gateway/agent_handler/hitl.py": "the paused thread's home, as its own asker",
    "kazma-ui/kazma_ui/auth.py": "the request's principal, after the membership check",
    "kazma-ui/kazma_ui/chat_delete.py": "the deleted chat's own scope",
    "kazma-ui/kazma_ui/chat_move.py": "a chat move's two spaces, whose rule was asked",
    "kazma-ui/kazma_ui/health.py": "the owner's: the recall canary's",
    "kazma-ui/kazma_ui/hitl_answerers.py": "the card's own space and its answerer",
    "kazma-ui/kazma_ui/hitl_timeout.py": "the timed-out card's own space",
    "kazma-ui/kazma_ui/kb_api.py": "the resumed crawl's own space and asker (work_scope)",
    "kazma-ui/kazma_ui/oauth_sign_in.py": "the sign-in attempt's own space and asker",
    "kazma-ui/kazma_ui/runs_chat_turn.py": "the run's own thread",
    "kazma-ui/kazma_ui/space_binding.py": "the space a request names, after the membership check",
    "kazma-ui/kazma_ui/thread_rewind.py": "a chat move's target, whose rule was asked",
}


def binds_in(source: str) -> list[tuple[int, str]]:
    """Every binding of a scope: its line, as written."""
    return [(n.lineno, ast.unparse(n)) for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and _name(n) in ("bind_scope", "set_scope")]


def _of_a_request(written: str) -> bool:
    """Whether a binding's arguments come straight from a request."""
    names = {n.id for n in ast.walk(ast.parse(written)) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(ast.parse(written)) if isinstance(n, ast.Attribute)}
    return bool(names & {"request", "body", "payload", "query_params", "path_params", "headers", "params"})


def test_every_file_that_binds_a_space_is_declared_and_none_binds_a_requests() -> None:
    found: set[str] = set()
    bad: list[str] = []
    for rel in _product_files():
        if rel in (ROW_WORK, "kazma-core/kazma_core/spaces/scope.py"):
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if "bind_scope" in text or "set_scope" in text:
            binds = binds_in(text)
            found |= {rel} if binds else set()
            bad += [f"{rel}:{line}: {written}" for line, written in binds if _of_a_request(written)]
    assert bad == [], "a space a request names is the middleware's to bind, after it checked the membership"
    assert found - set(BINDERS) == set(), "declare the file in BINDERS with where its spaces come from"
    assert set(BINDERS) - found == set(), "declared, and binds nothing"


@pytest.mark.parametrize("written", ["bind_scope(request.query_params['space'], who)", "bind_scope(body.space, who)",
                                     "set_scope(request.headers['X-Kazma-Space'], who)"])
def test_a_planted_route_that_binds_a_requests_space_is_found(written: str) -> None:
    [(_, seen)] = binds_in(f"async def route(request):\n    with {written}:\n        pass\n")
    assert _of_a_request(seen)


def test_a_binding_of_a_records_space_is_not_a_requests() -> None:
    assert not _of_a_request("bind_scope(entry['source'], entry['by'])")


# ── who lists swarm tasks ────────────────────────────────────────────────

#: Who lists the task store's tasks: ``file::function`` -> for whom. Once
#: Spaces is enforced the store keeps a list to the bound space and refuses
#: one with nothing bound; only a caller that asks for "*" lists every
#: space's, and it is never reached with a space bound.
TASK_LISTS: dict[str, str] = {
    "kazma-core/kazma_core/runs/adapters/swarm.py::_SwarmAdapter._stored":
        "every space's for the supervisor (nothing bound), else the bound space's",
    "kazma-core/kazma_core/swarm/chat_tasks.py::_tasks_of": "the bound space's, of one chat",
    "kazma-core/kazma_core/swarm/engine.py::SwarmEngine.redispatch_recovered_tasks":
        "every space's, at a start",
    "kazma-ui/kazma_ui/research_panel/routes.py::create_research_router.list_research":
        "the request's space's",
    "kazma-ui/kazma_ui/swarm_panel/routes_tasks.py::register_tasks_routes.swarm_tasks":
        "the request's space's",
    "kazma-ui/kazma_ui/swarm_panel/routes_tasks.py::register_tasks_routes.swarm_tasks_export":
        "the request's space's",
}


def task_lists_in(source: str, rel: str) -> dict[str, bool]:
    """Every function that lists a task store -> whether it asks for "*"."""
    found: dict[str, bool] = {}

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = [*stack, child.name] if isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) else stack
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                own = _own(child)
                if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "list_tasks"
                       and "store" in ast.unparse(n.func.value) for n in own):
                    found[f"{rel}::{'.'.join(inner)}"] = any(
                        isinstance(n, ast.Constant) and n.value == "*" for n in own)
            visit(child, inner)

    visit(ast.parse(source), [])
    return found


def test_who_lists_swarm_tasks_is_declared() -> None:
    found: dict[str, bool] = {}
    for rel in _product_files():
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if "list_tasks" in text:
            found |= task_lists_in(text, rel)
    assert set(found) - set(TASK_LISTS) == set(), "declare for whom it lists"
    assert set(TASK_LISTS) - set(found) == set(), "declared, and no longer there"
    asks = {where for where, every in found.items() if every}
    assert asks == {where for where, whose in TASK_LISTS.items() if whose.startswith("every space's")}


def test_a_planted_task_list_is_found() -> None:
    source = ("class A:\n    def f(self):\n        return self._task_store.list_tasks(metadata_filter={'tenant_id': '*'})\n"
              "def g(store):\n    return store.list_tasks()\ndef h(engine):\n    return engine.list_tasks()\n")
    assert task_lists_in(source, "x.py") == {"x.py::A.f": True, "x.py::g": False}


# ── one way to read a task's space ───────────────────────────────────────

#: The swarm's code, and what decides or delivers by a task.
SWARM = ("kazma-core/kazma_core/swarm/", "kazma-ui/kazma_ui/swarm_panel/", "kazma-ui/kazma_ui/research_panel/",
         "kazma-core/kazma_core/runs/adapters/swarm.py")

#: Where a raw ``tenant_id`` is read, instead of ``space_of_task`` (a task of
#: before Spaces names none, and is the owner's) -> why.
RAW_TENANT: dict[str, str] = {
    "kazma-core/kazma_core/runs/adapters/swarm.py::_SwarmAdapter._run":
        "with Spaces off, the run's space as it always was",
    "kazma-core/kazma_core/swarm/engine.py::SwarmEngine._dispatch":
        "whether new work names a space at all: refused when it does not",
    "kazma-core/kazma_core/swarm/single_operator.py::space_of_task": "the one way itself",
    "kazma-core/kazma_core/swarm/task_store.py::TaskStore.list_tasks": "a list's filter, as it was asked",
    "kazma-core/kazma_core/swarm/worker_dispatch.py::_whose":
        "what a dispatch carries: none refuses, never the owner's (M2)",
}


def raw_tenants_in(source: str, rel: str) -> set[str]:
    """Functions that read ``tenant_id`` out of a mapping (``.get`` or ``[]``)."""
    found: set[str] = set()

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = [*stack, child.name] if isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) else stack
            read = (isinstance(child, ast.Call) and _name(child) == "get" and child.args
                    and isinstance(child.args[0], ast.Constant) and child.args[0].value == "tenant_id") or (
                isinstance(child, ast.Subscript) and isinstance(child.ctx, ast.Load)
                and isinstance(child.slice, ast.Constant) and child.slice.value == "tenant_id")
            if read:
                found.add(f"{rel}::{'.'.join(inner)}")
            visit(child, inner)

    visit(ast.parse(source), [])
    return found


def test_a_tasks_space_is_read_one_way() -> None:
    found: set[str] = set()
    for rel in _product_files():
        if rel.startswith(SWARM):
            found |= raw_tenants_in((REPO / rel).read_text(encoding="utf-8", errors="replace"), rel)
    assert found - set(RAW_TENANT) == set(), "ask swarm.single_operator.space_of_task"
    assert set(RAW_TENANT) - found == set(), "declared, and no longer there"


def test_a_planted_raw_tenant_is_found() -> None:
    source = "def f(t):\n    return (t.metadata or {}).get('tenant_id')\ndef g(m):\n    return m['tenant_id']\n"
    assert raw_tenants_in(source, "x.py") == {"x.py::f", "x.py::g"}
    assert raw_tenants_in("def f(t):\n    m['tenant_id'] = space_of_task(t)\n", "x.py") == set()
