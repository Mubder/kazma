"""Memory statements that pick a row by its id alone: a gate (Spaces S1-35).

Turns, facts, archived facts, weekly summaries and tool procedures keep
``id`` as their whole key (the owner's decision 17: the key is not rebuilt).
A statement that picks such a row by ``id`` either names its tenant in the
same WHERE -- the form ``(? IS NULL OR tenant_id = ?)`` with
``tenant_view.narrowed_to``, which filters nothing on an install that does not
enforce Spaces -- or is declared here with why another tenant's row cannot be
reached: an id that carries its space, a tenant check just before, a row the
same code chose by tenant, Kazma's own maintenance over every tenant. The
memory audit's item 7: the entity key's gate (``tests/test_memory_entity_key.py``,
Memory audit M21), widened.

S1-09 counted 81 and held the count with a ratchet. S1-35 named the tenant in
the 16 that take an id from a request or the model -- the Memory page's edit,
undo, invalidate, detail and recall trail, Forget of a memory, a fact's
source and a weekly summary, the fact bridge of recall -- and declared the
rest by function, with how many statements each holds: a new id-only
statement fails, in a declared function too, and so does a declaration that
excuses nothing.

Negative controls: planted statements are found, a scoped one is not, and a
statement added to a declared function fails the gate.
"""

from __future__ import annotations

import re
import textwrap
from collections import Counter

from tests.test_memory_entity_key import _product_sources, _SEGMENT_END, _statement_text

#: The memory tables keyed by ``id`` alone (entities have their own gate).
_TABLE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:episodes|beliefs|beliefs_archive|memory_summaries|procedural_dags)\b",
    re.I,
)
#: A WHERE that picks a row out by its id.
_BY_ID = re.compile(r"(?<![\w])(?:\w+\.)?id\s*(?:=|\bIN\b)", re.I)

_MEMORY = "kazma-core/kazma_core/memory/"

#: Why a row of another tenant cannot be reached, said once for the shapes
#: that repeat.
_KAZMA_SWEEP = ("Kazma's own maintenance over every tenant's rows, by the ids the same pass read from the "
                "store; no request or model names them")
_CHOSEN_BY_TENANT = "the ids come from a statement of the same work that chose them by tenant"
_MUTATION = ("inside the belief mutation's lock, on a row the same mutation chose by tenant, subject and "
             "predicate, or the row it has just inserted under its own tenant")
_RECALL_OWN = ("recall's own candidates, held to the asker's tenant before anything reads one by id "
               "(recall._own_episodes)")
_FORGET_OWN = ("a memory or fact this forget already holds: forget_episode, forget_belief_source and "
               "forget_chat read it by the caller's tenant first")
_MIRROR = ("the mirror's copy of a row the local store holds, pushed by id: every tenant's rows are "
           "mirrored, and the mirror keeps each row's own tenant")
_ROW_NAMED = ("a queued task that names its own row (task_queue.ROW_NAMED_TASKS): it runs bound to that "
              "row's tenant, and the id is Kazma's own")
_OPERATOR = "the operator's repair script, run by hand on a copy, over the whole store"

#: (file, function) -> (how many id-only statements it holds, why each is safe).
DECLARED: dict[tuple[str, str], tuple[int, str]] = {
    (f"{_MEMORY}backends.py", "delete"):
        (1, "the local vector backend's delete clears a vector by the id it is handed: no product code "
            "deletes through it (recall only searches with it, AGENTS §15 D2)"),
    (f"{_MEMORY}belief_mutation.py", "_insert_landed"):
        (1, "asks whether the row this mutation has just inserted is there"),
    (f"{_MEMORY}belief_mutation.py", "_mutate_functional_locked"): (2, _MUTATION),
    (f"{_MEMORY}belief_mutation.py", "_record_earlier_statement"): (3, _MUTATION),
    (f"{_MEMORY}belief_mutation.py", "_still_held"):
        (1, "after the commit: whether the fact this mutation wrote still holds the value it embedded"),
    (f"{_MEMORY}belief_mutation.py", "mutate_belief"):
        (1, "the fact this mutation superseded (its result's superseded_id), chosen by tenant"),
    (f"{_MEMORY}consolidator.py", "promote_working_memory"): (1, _CHOSEN_BY_TENANT),
    (f"{_MEMORY}dual_write.py", "_id_in_tenant"):
        (1, "reads which tenant holds a candidate id on purpose: the writer takes an id no other tenant "
            "holds (an episode's id carries its space, S1-09)"),
    (f"{_MEMORY}dual_write.py", "_still_held"):
        (1, "after the commit: whether the memory this writer wrote was forgotten meanwhile"),
    (f"{_MEMORY}entity_repair.py", "repair_collapsed"): (1, _OPERATOR),
    (f"{_MEMORY}forget.py", "_after_facts"): (1, _FORGET_OWN),
    (f"{_MEMORY}forget.py", "_scrub_archived_rows"):
        (1, "rewrites the archived rows its caller chose by tenant (a forgotten turn's, a "
            "forget-by-text's) or found forgotten (an older tombstone, Kazma's own sweep)"),
    (f"{_MEMORY}forget.py", "finish_old_tombstones"):
        (1, "Kazma's own sweep: the facts already forgotten, chosen by being tombstones in any "
            "tenant; it only empties what they still name (part D)"),
    (f"{_MEMORY}forget.py", "_forget_fact_rows"): (2, _FORGET_OWN),
    (f"{_MEMORY}forget.py", "_forget_turn"): (1, _FORGET_OWN),
    (f"{_MEMORY}forget.py", "_memory_texts"): (1, _FORGET_OWN),
    (f"{_MEMORY}forget.py", "_tombstone"): (2, _FORGET_OWN),
    (f"{_MEMORY}forget.py", "forget_chat"): (1, _FORGET_OWN),
    (f"{_MEMORY}forget.py", "replay_turn"):
        (1, "the forget journal's replay: the entry names the memory it forgot, under that memory's tenant"),
    (f"{_MEMORY}forget.py", "retire_copy"):
        (2, "Kazma's own cleanup of a legacy copy and its original, both chosen by legacy_copies"),
    (f"{_MEMORY}global_reconsolidation.py", "_merge_duplicate_beliefs"): (1, _CHOSEN_BY_TENANT),
    (f"{_MEMORY}graph_sync.py", "_known"):
        (2, "which ids of the graph copy's edges the store still holds, whatever their tenant: an edge of "
            "a fact gone is removed"),
    (f"{_MEMORY}list_reopen.py", "reopen_entries"): (1, _OPERATOR),
    (f"{_MEMORY}macro_sleep.py", "<module>"): (1, _KAZMA_SWEEP),
    (f"{_MEMORY}macro_sleep.py", "_propagate_episode_moves"): (1, _KAZMA_SWEEP),
    (f"{_MEMORY}macro_sleep.py", "run_macro_sleep"): (3, _KAZMA_SWEEP),
    (f"{_MEMORY}procedural.py", "record_procedural_outcome"): (1, _CHOSEN_BY_TENANT),
    (f"{_MEMORY}procedural.py", "strip_argument_values"): (1, _KAZMA_SWEEP),
    (f"{_MEMORY}recall.py", "_apply_session_bias"): (1, _RECALL_OWN),
    (f"{_MEMORY}recall.py", "_episode_texts"): (1, _RECALL_OWN),
    (f"{_MEMORY}recall.py", "_locally_forgotten"):
        (1, "whether a mirror row's id is a local tombstone, whichever tenant holds it: a forgotten row "
            "is dropped from recall"),
    (f"{_MEMORY}recall.py", "_rank_episodes_by_evidence"): (1, _RECALL_OWN),
    (f"{_MEMORY}recall.py", "_weigh_archived"): (1, _RECALL_OWN),
    (f"{_MEMORY}rehydrate.py", "_from_backups"): (1, _KAZMA_SWEEP),
    (f"{_MEMORY}rehydrate.py", "_mark"): (1, _KAZMA_SWEEP),
    (f"{_MEMORY}rehydrate.py", "_restore"): (1, _KAZMA_SWEEP),
    (f"{_MEMORY}state_backend.py", "_drain_outbox"): (1, _MIRROR),
    (f"{_MEMORY}state_backend.py", "_episode_rows"): (1, _MIRROR),
    (f"{_MEMORY}state_backend.py", "episode_mirror_row"): (1, _MIRROR),
    (f"{_MEMORY}state_backend.py", "remirror_belief_by_id"): (1, _MIRROR),
    (f"{_MEMORY}swarm_bridge.py", "_insert_episode"):
        (1, "the memory this bridge has just written, under its task's tenant (its id carries its space)"),
    (f"{_MEMORY}topic_summaries.py", "_live_turn_ids"):
        (1, "the turns a summary was written from, its own tenant's (memory_summary_sources)"),
    (f"{_MEMORY}topic_summaries.py", "_reembed"): (1, _KAZMA_SWEEP),
    (f"{_MEMORY}topic_summaries.py", "_retire"): (1, _ROW_NAMED),
    (f"{_MEMORY}topic_summaries.py", "_retire_older"): (1, _CHOSEN_BY_TENANT),
    (f"{_MEMORY}topic_summaries.py", "_store"):
        (1, "the summary this week's work is writing, inside its own transaction: the work is the "
            "tenant's (TENANT_TASKS) or the row's (ROW_NAMED_TASKS)"),
    (f"{_MEMORY}topic_summaries.py", "on_turns_forgotten"):
        (1, "the summaries of the turns a forget took, read by those turns' ids: the forget's own tenant"),
    (f"{_MEMORY}topic_summaries.py", "prepare"): (1, _ROW_NAMED),
    (f"{_MEMORY}topic_summaries.py", "queue_due_work"): (1, _KAZMA_SWEEP),
    (f"{_MEMORY}turn_reconcile.py", "_set_aside"): (1, _KAZMA_SWEEP),
    (f"{_MEMORY}worker_bootstrap.py", "_apply_if_still_remembered"): (1, _ROW_NAMED),
    (f"{_MEMORY}worker_bootstrap.py", "_mark_deep_pass"): (1, _ROW_NAMED),
    (f"{_MEMORY}worker_bootstrap.py", "_micro_consolidation_prepare"): (1, _ROW_NAMED),
    ("kazma-ui/kazma_ui/memory_api.py", "<module>"):
        (1, "_OWNER_SQL reads a row's tenant by its id to check it before a route acts (_row_tenant_ok)"),
    ("kazma-ui/kazma_ui/memory_api.py", "_archive_invalidated_sync"): (1, _CHOSEN_BY_TENANT),
    ("scripts/cleanup_live_leftovers.py", "build_plan"):
        (2, "the operator's one-off cleanup of named test rows, shown before it runs"),
}


def _unscoped(text: str) -> bool:
    for segment in re.split(r"\bWHERE\b", text, flags=re.I)[1:]:
        segment = _SEGMENT_END.split(segment)[0]
        if _BY_ID.search(segment) and "tenant_id" not in segment:
            return True
    return False


def id_only_statements(sources: dict[str, str]) -> set[tuple[str, str, int]]:
    """(file, enclosing function, line) of every id-only memory statement."""
    import ast

    found: set[tuple[str, str, int]] = set()
    for rel, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        def visit(node: ast.AST, function: str, rel: str = rel) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            text = _statement_text(node)
            if text and _TABLE.search(text) and _unscoped(text):
                found.add((rel, function, getattr(node, "lineno", 0)))
            if not isinstance(node, ast.JoinedStr):
                for child in ast.iter_child_nodes(node):
                    visit(child, function)

        visit(tree, "<module>")
    return found


def _gate(sources: dict[str, str]) -> list[str]:
    """What the gate refuses: an id-only statement in no declared function,
    a declared function holding more or fewer than declared."""
    found = id_only_statements(sources)
    held = Counter((rel, fn) for rel, fn, _line in found)
    problems = []
    for (rel, fn), count in sorted(held.items()):
        declared = DECLARED.get((rel, fn))
        if declared is None:
            lines = sorted(line for r, f, line in found if (r, f) == (rel, fn))
            problems.append(f"{rel}:{lines[0]} in {fn}(): picks a row by id alone -- name its tenant "
                            "in the same WHERE ((? IS NULL OR tenant_id = ?), tenant_view.narrowed_to)")
        elif declared[0] != count:
            problems.append(f"{rel} {fn}(): declared {declared[0]} id-only statement(s), holds {count}")
    problems += [f"{rel} {fn}(): declared, holds none (remove it)"
                 for (rel, fn) in sorted(DECLARED) if (rel, fn) not in held]
    return problems


def test_every_id_only_memory_statement_names_its_tenant_or_is_declared() -> None:
    assert _gate(_product_sources()) == []


def test_the_gate_finds_a_planted_statement() -> None:
    planted = {
        "x.py": textwrap.dedent(
            '''
            def bump(conn, eid):
                conn.execute("UPDATE episodes SET access_count = access_count + 1 WHERE id = ?", (eid,))

            def read(conn, marks, ids):
                return conn.execute(f"SELECT id, object FROM beliefs WHERE id IN ({marks})", ids)

            def summary(conn, sid):
                return conn.execute("SELECT * FROM memory_summaries WHERE id = ?", (sid,))

            def fine(conn, eid, tid):
                conn.execute("UPDATE episodes SET tier='x' WHERE id = ? AND tenant_id = ?", (eid, tid))
                conn.execute("SELECT 1 FROM beliefs WHERE tenant_id = ? AND subject_id = ?", (tid, eid))
                conn.execute("SELECT 1 FROM beliefs WHERE id = ? AND (? IS NULL OR tenant_id = ?)", (eid, tid, tid))
            '''
        )
    }
    assert {fn for _f, fn, _l in id_only_statements(planted)} == {"bump", "read", "summary"}


def test_control_a_statement_added_to_a_declared_function_fails_the_gate() -> None:
    """A declaration covers the statements it counts, never a new one."""
    sources = _product_sources()
    rel = f"{_MEMORY}forget.py"
    sources[rel] = sources[rel].replace(
        "def _tombstone(",
        "def _tombstone_planted(conn, eid):\n"
        "    return conn.execute('SELECT 1 FROM episodes WHERE id = ?', (eid,))\n\n\n"
        "def _tombstone(", 1)
    sources[rel] += "\n\ndef _memory_texts_again(conn, eid):\n    return conn.execute('SELECT 1 FROM episodes WHERE id = ?', (eid,))\n"
    problems = _gate(sources)
    assert any("_tombstone_planted()" in p for p in problems), problems
    assert any("_memory_texts_again()" in p for p in problems), problems
    one_more = _product_sources()
    one_more[rel] = one_more[rel].replace(
        '    row = conn.execute("SELECT metadata_json FROM episodes WHERE id = ?", (eid,)).fetchone()',
        '    row = conn.execute("SELECT metadata_json FROM episodes WHERE id = ?", (eid,)).fetchone()\n'
        '    conn.execute("SELECT 1 FROM episodes WHERE id = ?", (eid,))', 1)
    assert f"{rel} _tombstone(): declared 2 id-only statement(s), holds 3" in _gate(one_more)
