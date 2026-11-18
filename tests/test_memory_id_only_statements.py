"""Memory statements that pick a row by its id alone: a ratchet (Spaces S1-09).

Turns, facts, archived facts, weekly summaries and tool procedures keep
``id`` as their whole key (the owner's decision 17: the key is not rebuilt).
A statement that picks such a row by ``id`` and names no ``tenant_id`` in the
same WHERE relies on something else to keep two spaces apart: an id that
carries its space (episodes since S1-09), a tenant check made just before,
or a row the same statement chose by tenant. Each one is safe or not on its
own, and the memory audit's item 7 asks that each say so -- the entity
key's gate (``tests/test_memory_entity_key.py``, Memory audit M21), widened.

That is its own item in the plan (the S1-09 state row). Until it is done this
ratchet counts them: the count may only go down, so nothing new adds one
while they wait. A new id-only statement names its tenant instead.

Negative control: planted statements are counted, a scoped one is not.
"""

from __future__ import annotations

import re
import textwrap

from tests.test_memory_entity_key import _product_sources, _SEGMENT_END, _statement_text

#: The memory tables keyed by ``id`` alone (entities have their own gate).
_TABLE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:episodes|beliefs|beliefs_archive|memory_summaries|procedural_dags)\b",
    re.I,
)
#: A WHERE that picks a row out by its id.
_BY_ID = re.compile(r"(?<![\w])(?:\w+\.)?id\s*(?:=|\bIN\b)", re.I)

#: How many there are. It may only go down: lower it in the change that
#: scopes one, never raise it.
BASELINE = 81


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


def test_id_only_memory_statements_only_go_down() -> None:
    found = id_only_statements(_product_sources())
    assert len(found) <= BASELINE, (
        f"{len(found)} memory statements pick a row by id alone (baseline {BASELINE}). A new one names "
        "its tenant in the same WHERE:\n  " + "\n  ".join(f"{f}:{line} in {fn}()" for f, fn, line in sorted(found))
    )
    assert len(found) == BASELINE, f"the count went down to {len(found)}: lower BASELINE to lock it in"


def test_the_ratchet_counts_a_planted_statement() -> None:
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
            '''
        )
    }
    assert {fn for _f, fn, _l in id_only_statements(planted)} == {"bump", "read", "summary"}
