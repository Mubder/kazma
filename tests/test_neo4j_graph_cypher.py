"""The Neo4j dual-write sends Cypher Neo4j accepts, and Forget leaves no value
behind (audit 2026-10-26 AUD-049, AUD-063).

* AUD-049: a predicate starting with a digit (``2fa_enabled``) made the
  relationship type ``2FA_ENABLED`` -- an unquoted Cypher name cannot start
  with a digit, the driver raised, the failure was logged at DEBUG and the
  fact's edge was never written.
* AUD-063: Forget deleted the edge and kept both ends; a forgotten value is a
  node (``Entity.name`` = the value), so a codeword stayed in Neo4j.

A fake driver records every statement, and most checks read those. The
real-server tests at the end run the same calls against a Neo4j server when
``KAZMA_TEST_NEO4J_URL`` names one (a throwaway server: they touch only nodes
of their own test tenant), with the old statements as negative controls.
First run 2026-10-28 on neo4j:5 with driver 6.2.0.
"""

from __future__ import annotations

import os
import re
import uuid

import pytest
from kazma_core.memory import graph_backend
from kazma_core.memory.graph_backend import Neo4jGraphBackend

#: A Cypher name Neo4j takes unquoted, or one quoted in backticks.
_NAME = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*|`[^`]+`)$")
_REL = re.compile(r"-\[r:([^\]]+)\]->")


class _Session:
    def __init__(self, log: list[tuple[str, dict]]) -> None:
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.log.append((" ".join(query.split()), params))

        class _Result:
            def consume(self):
                return None

        return _Result()


class _Driver:
    def __init__(self) -> None:
        self.log: list[tuple[str, dict]] = []

    def session(self, **kw):
        return _Session(self.log)


@pytest.fixture()
def neo():
    be = Neo4jGraphBackend(url="bolt://example.invalid:7687")
    be._driver = _Driver()
    return be


def _rel_types(log) -> list[str]:
    return [m.group(1) for q, _p in log for m in _REL.finditer(q)]


@pytest.mark.parametrize("predicate", ["2fa_enabled", "lives_in", "يسكن_في", "has-3 kids", ""])
def test_every_relationship_type_is_a_name_neo4j_takes(neo, predicate):
    assert neo.upsert_triple(subject="user", predicate=predicate, obj="x", belief_id="b1")
    assert neo.delete_triple(subject="user", predicate=predicate, obj="x", tenant_id="default")
    types = _rel_types(neo._driver.log)
    assert len(types) == 2 and all(_NAME.match(t) for t in types), types
    assert types[0] == types[1]  # the delete names the edge the upsert wrote


def test_negative_control_the_old_type_was_not_a_cypher_name():
    def old(predicate: str) -> str:
        pred = (predicate or "related_to").replace(" ", "_")
        return "".join(c if c.isalnum() or c == "_" else "_" for c in pred).upper() or "RELATED"

    assert not _NAME.match(old("2fa_enabled"))
    assert _NAME.match(graph_backend._rel_type("2fa_enabled"))


def _deletes_orphaned_ends(query: str) -> bool:
    """``DELETE r``, then each end left without a relationship."""
    return bool(re.search(
        r"DELETE r WITH a, b UNWIND \[a, b\] AS n WITH DISTINCT n WHERE NOT \(n\)--\(\) DELETE n$", query))


@pytest.mark.parametrize("by_id", [True, False])
def test_forget_deletes_the_value_node_left_without_edges(neo, by_id):
    kw = {"belief_id": "b-codeword"} if by_id else {
        "subject": "user", "predicate": "test_codeword", "obj": "BLUE-HERON-42"}
    assert neo.delete_triple(tenant_id="default", **kw)
    (query, _params), = neo._driver.log
    assert _deletes_orphaned_ends(query), query
    if by_id:  # the ends are bound, so the orphan check can reach them
        assert query.startswith("MATCH (a)-[r]->(b) WHERE r.belief_id = $bid")


def test_graph_clear_takes_the_nodes_too(neo):
    neo.clear_tenant_edges(tenant_id="t1")
    queries = [q for q, _p in neo._driver.log]
    assert queries[-1] == "MATCH (n:Entity {tenant_id: $tid}) WHERE NOT (n)--() DELETE n"


def test_negative_control_the_old_delete_kept_the_value_node():
    old_by_id = "MATCH ()-[r]->() WHERE r.belief_id = $bid DELETE r"
    assert not _deletes_orphaned_ends(old_by_id)


# ── the same calls on a real Neo4j server (opt-in) ─────────────────────────

_REAL_URL = os.environ.get("KAZMA_TEST_NEO4J_URL", "").strip()


@pytest.fixture()
def real_neo():
    if not _REAL_URL:
        pytest.skip("KAZMA_TEST_NEO4J_URL is not set (a throwaway Neo4j server)")
    pytest.importorskip("neo4j")
    be = Neo4jGraphBackend(
        url=_REAL_URL,
        user=os.environ.get("KAZMA_TEST_NEO4J_USER", "neo4j"),
        password=os.environ.get("KAZMA_TEST_NEO4J_PASSWORD", ""),
    )
    if not be.available:
        pytest.fail(f"KAZMA_TEST_NEO4J_URL={_REAL_URL} is set but the server does not answer")
    tenant = f"kazma-test-{uuid.uuid4().hex[:12]}"
    yield be, tenant
    _cypher(be, "MATCH (n:Entity {tenant_id: $tid}) DETACH DELETE n", tid=tenant)


def _cypher(be, query: str, **params) -> list[dict]:
    with be._get_driver().session() as session:
        return [record.data() for record in session.run(query, **params)]


def _names(be, tenant: str) -> list[str]:
    rows = _cypher(be, "MATCH (n:Entity {tenant_id: $tid}) RETURN n.name AS n ORDER BY n", tid=tenant)
    return [row["n"] for row in rows]


def test_real_server_writes_an_edge_whose_predicate_starts_with_a_digit(real_neo):
    be, tenant = real_neo
    assert be.upsert_triple(subject="user", predicate="2fa_enabled", obj="true",
                            belief_id=f"{tenant}-b1", tenant_id=tenant)
    rows = _cypher(be, "MATCH (a:Entity {tenant_id: $tid})-[r]->(b) RETURN type(r) AS t, b.name AS b",
                   tid=tenant)
    assert rows == [{"t": "2FA_ENABLED", "b": "true"}]


def test_real_server_negative_control_refuses_the_old_unquoted_type(real_neo):
    from neo4j.exceptions import CypherSyntaxError

    be, tenant = real_neo
    with pytest.raises(CypherSyntaxError):
        _cypher(be, "MERGE (a:Entity {name: 'user', tenant_id: $tid}) "
                    "MERGE (b:Entity {name: 'true', tenant_id: $tid}) "
                    "MERGE (a)-[r:2FA_ENABLED]->(b)", tid=tenant)


@pytest.mark.parametrize("by_id", [True, False])
def test_real_server_forget_takes_the_value_node_and_keeps_shared_ones(real_neo, by_id):
    be, tenant = real_neo
    assert be.upsert_triple(subject="user", predicate="noted", obj="BLUE-HERON-42",
                            belief_id=f"{tenant}-code", tenant_id=tenant)
    assert be.upsert_triple(subject="user", predicate="lives in", obj="Kuwait",
                            belief_id=f"{tenant}-home", tenant_id=tenant)
    kw = {"belief_id": f"{tenant}-code"} if by_id else {
        "subject": "user", "predicate": "noted", "obj": "BLUE-HERON-42"}
    assert be.delete_triple(tenant_id=tenant, **kw)
    # The codeword is gone; "user" still has an edge, so it stays.
    assert _names(be, tenant) == ["Kuwait", "user"]


def test_real_server_negative_control_the_old_delete_kept_the_codeword(real_neo):
    be, tenant = real_neo
    assert be.upsert_triple(subject="user", predicate="noted", obj="BLUE-HERON-42",
                            belief_id=f"{tenant}-code", tenant_id=tenant)
    _cypher(be, "MATCH ()-[r]->() WHERE r.belief_id = $bid DELETE r", bid=f"{tenant}-code")
    assert "BLUE-HERON-42" in _names(be, tenant)


def test_real_server_graph_clear_leaves_no_node(real_neo):
    be, tenant = real_neo
    for i, obj in enumerate(("Kuwait", "BLUE-HERON-42")):
        assert be.upsert_triple(subject="user", predicate=f"p{i}", obj=obj,
                                belief_id=f"{tenant}-{i}", tenant_id=tenant)
    assert be.clear_tenant_edges(tenant_id=tenant) == 2
    assert _names(be, tenant) == []
