"""No embedding, mirror, graph or vector-index call runs inside a memory
write transaction or the mutation lock; each row's vector is computed once
and written once, guarded (Memory audits M14 and M15: MEM-R06, MEM-L08,
MEM-R05).

Found 2026-10-13:

* a fact was embedded inside the mutation's ``BEGIN IMMEDIATE``, under the
  process-wide mutation lock -- every belief write in the process waited on
  one embedding (an embedder call can take two minutes); the superseded
  fact's graph delete and mirror tombstone ran there too;
* ``macro_sleep`` made one Postgres round trip per archived fact inside its
  transaction;
* a turn's recall waited the full busy timeout (5.54 s measured) for its
  access bump, then dropped it;
* an episode was embedded twice; the second write ran on the shared mirror
  connection without the lock or the tier guard (a forget in between got its
  vector back), and the local backend's upsert committed the caller's
  transaction.

A probe in every hook used by ``belief_mutation``, ``macro_sleep``,
``hygiene``, ``forget`` and the episode writer must find the database free
and no memory lock held. Each rule has its negative control.
"""

from __future__ import annotations

import ast
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
VEC = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    state = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    db = sqlite3.connect(str(state), check_same_thread=False, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, state=state, mp=monkeypatch)
    dual_write._reset_mirror()
    db.close()


class _Probe:
    """Records, at each hook call, whether another writer could take the
    database at once and whether a memory lock is held."""

    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.seen: list[tuple[str, bool, bool]] = []

    def check(self, hook: str) -> None:
        from kazma_core.memory import belief_mutation, dual_write

        other = sqlite3.connect(self.path, timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
            free = True
        except sqlite3.OperationalError:
            free = False
        finally:
            other.close()
        mirror = dual_write._mirror
        held = belief_mutation._mutation_lock.locked() or (mirror is not None and mirror._lock.locked())
        self.seen.append((hook, free, held))

    def blocked(self) -> list[tuple[str, bool, bool]]:
        return [s for s in self.seen if not s[1] or s[2]]


def _hooks(mem, probe: _Probe) -> None:
    """Every slow hook the memory writers call, each probing."""

    class Embedder:
        dim = 4

        def encode(self, text):
            probe.check("embed")
            return list(VEC)

    class Mirror:
        name, available, write_ready = "postgres", True, True

        def _call(self, *a, **k):
            probe.check("mirror")
            return True

        mirror_belief = mirror_episode = delete_belief = _call

    class Remote:
        available = True

        def upsert(self, *a, **k):
            probe.check("vector")
            return True

        def delete(self, *a, **k):
            probe.check("vector")
            return True

    def graph(**kw):
        probe.check("graph")
        return True

    emb = Embedder()
    mem.mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: emb)
    mem.mp.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "probe-model")
    mirror = Mirror()
    mem.mp.setattr("kazma_core.memory.state_backend.get_state_backend", lambda: mirror)
    remote = Remote()
    mem.mp.setattr("kazma_core.memory.backends.get_remote_vector_backend", lambda: remote)
    mem.mp.setattr("kazma_core.memory.graph_backend.upsert_belief_edge", graph)
    mem.mp.setattr("kazma_core.memory.graph_backend.delete_belief_edge", graph)


def _every_writer(mem) -> None:
    """Each writer the audit named, through its own entry point."""
    from kazma_core.memory import forget
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import mirror_episode
    from kazma_core.memory.hygiene import invalidate_belief
    from kazma_core.memory.macro_sleep import run_macro_sleep

    kw = {"tenant_id": "t", "extraction_method": "user_explicit"}
    mutate_belief(mem.db, "user", "lives_in", "Porto", predicate_type="functional", now=1000.0, **kw)
    mutate_belief(mem.db, "user", "lives_in", "Braga", predicate_type="functional", now=3000.0, **kw)
    mutate_belief(mem.db, "user", "lives_in", "Lisbon", predicate_type="functional", now=2000.0, **kw)  # history
    mutate_belief(mem.db, "user", "likes", "jazz", predicate_type="set", now=3000.0, **kw)
    jazz = mem.db.execute("SELECT id FROM beliefs WHERE object = 'jazz'").fetchone()[0]
    assert invalidate_belief(jazz, conn=mem.db)["ok"]
    eid = mirror_episode(session_id="s1", turn_number=1, user_text="Remember the pump housings come from "
                         "Nordvalve", assistant_text="Noted.", tenant_id="t")
    assert eid
    assert forget.forget_episode(eid, tenant_id="t", conn=mem.db)["ok"]
    mem.db.execute("UPDATE episodes SET tier = 'episodic', created_at = 0, last_accessed = 0, "
                   "user_text = 'an old note', assistant_text = 'kept' WHERE id != ?", (eid,))
    stats = run_macro_sleep(mem.db, cfg={}, tenant_id="t", now=time.time() + 400 * 86400)
    assert stats["archived_beliefs"] >= 1, stats
    _swarm_result_and_reconsolidation(mem)


SWARM_RESULT = "Task: pick a supplier for the pump housings\nResult: Nordvalve, for lead time."


def _swarm_result_and_reconsolidation(mem, probe: _Probe | None = None) -> None:
    """The two writers audit 2026-10-26 AUD-112 found outside this gate: the
    swarm bridge's episode write and reconsolidation's duplicate merges, each
    on a connection in Python's default mode, as their callers open it.
    *probe* also probes the merges' ops-DB audit rows."""
    from kazma_core.memory import belief_mutation
    from kazma_core.memory.global_reconsolidation import run_global_reconsolidation
    from kazma_core.memory.swarm_bridge import store_swarm_result

    assert store_swarm_result("coder", "task-1", SWARM_RESULT, tenant_id="t")
    conn = sqlite3.connect(str(mem.state), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        for n, (obj, conf) in enumerate((("Fado", 0.9), ("fado", 0.4), ("FADO", 0.3))):
            conn.execute(
                "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
                "structural_importance, source_trust_weight, valid_from, ingested_at) "
                "VALUES (?, 't', 'user', 'enjoys', 'set', ?, ?, 2, 0.5, 1.0, 1.0)", (f"b_dup_{n}", obj, conf))
        conn.commit()
        if probe is not None:
            real = belief_mutation._write_audit

            def audit(*a, **k):
                probe.check("audit")
                return real(*a, **k)

            mem.mp.setattr(belief_mutation, "_write_audit", audit)
        stats = run_global_reconsolidation(conn, tenant_id="t", reembed_limit=1, auto_partition=False)
    finally:
        conn.close()
    assert stats["duplicate_beliefs_merged"] == 2 and not stats["errors"], stats


def test_no_slow_hook_runs_under_a_memory_write_lock(mem):
    probe = _Probe(mem.state)
    _hooks(mem, probe)
    _every_writer(mem)
    assert {hook for hook, _f, _h in probe.seen} >= {"embed", "mirror", "graph", "vector"}, probe.seen
    assert probe.blocked() == [], probe.blocked()


def test_swarm_results_and_merges_run_no_slow_hook_under_the_write_lock(mem):
    """(audit 2026-10-26 AUD-112) The episode is committed before it is
    embedded; the merges commit before their graph, mirror and audit calls."""
    probe = _Probe(mem.state)
    _hooks(mem, probe)
    _swarm_result_and_reconsolidation(mem, probe)
    assert {hook for hook, _f, _h in probe.seen} >= {"embed", "mirror", "graph", "audit"}, probe.seen
    assert probe.blocked() == [], probe.blocked()
    row = mem.db.execute("SELECT embedding IS NOT NULL FROM episodes WHERE metadata_json LIKE '%swarm_result%'")
    assert row.fetchone()[0]  # embedded once, after the commit
    current = mem.db.execute("SELECT id FROM beliefs WHERE predicate = 'enjoys' AND invalidated_at IS NULL")
    assert [r[0] for r in current] == ["b_dup_0"]


def test_the_probe_sees_the_old_swarm_embedding_inside_the_transaction(mem):
    """Negative control: the old order -- insert, embed, then commit."""
    from kazma_core.memory.vector_write import encode_vector

    probe = _Probe(mem.state)
    _hooks(mem, probe)
    conn = sqlite3.connect(str(mem.state), check_same_thread=False)
    try:
        conn.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, "
                     "metadata_json) VALUES ('e_old', 't', 'swarm:coder', 1, ?, 'episodic', 1, '{}')",
                     (SWARM_RESULT,))
        encode_vector(SWARM_RESULT)
        conn.commit()
    finally:
        conn.close()
    assert ("embed", False, False) in probe.seen


def test_the_probe_sees_the_old_merge_mirror_inside_the_batch(mem):
    """Negative control: the old placement -- the graph delete and the mirror
    tombstone per merge, inside the uncommitted batch."""
    from kazma_core.memory import hygiene

    probe = _Probe(mem.state)
    _hooks(mem, probe)
    real = hygiene.beliefs_write

    def old(conn, sql, params=()):
        cur = real(conn, sql, params)
        from kazma_core.memory.graph_backend import delete_belief_edge
        from kazma_core.memory.state_backend import remirror_belief_by_id

        delete_belief_edge(belief_id=str(params[3]))
        remirror_belief_by_id(conn, str(params[3]))
        return cur

    mem.mp.setattr(hygiene, "beliefs_write", old)
    _swarm_result_and_reconsolidation(mem)
    assert ("graph", False, False) in probe.seen and ("mirror", False, False) in probe.seen


def test_the_probe_sees_the_old_embedding_inside_the_transaction(mem):
    """Negative control: the old placement -- the fact's embedding inside
    _insert_belief, in the mutation's transaction, under the mutation lock."""
    from kazma_core.memory import belief_mutation
    from kazma_core.memory.vector_write import encode_vector

    probe = _Probe(mem.state)
    _hooks(mem, probe)
    real = belief_mutation._insert_belief

    def old(conn, bid, tenant_id, sub, pred, ptype, obj, **kw):
        out = real(conn, bid, tenant_id, sub, pred, ptype, obj, **kw)
        encode_vector(f"{sub} {pred} {obj}")
        return out

    mem.mp.setattr(belief_mutation, "_insert_belief", old)
    belief_mutation.mutate_belief(mem.db, "user", "lives_in", "Porto", predicate_type="functional",
                                  tenant_id="t", now=1000.0)
    assert ("embed", False, True) in probe.seen


def test_recall_does_not_wait_out_the_busy_timeout_and_keeps_its_count(mem):
    from kazma_core.memory.recall import recall

    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json) VALUES ('e_maya', 't', 's1', 1, 'Maya works at the Lisbon aquarium', "
        "'Noted.', 'episodic', ?, '{}')", (time.time() - 3600,))
    holder = sqlite3.connect(str(mem.state), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        started = time.perf_counter()
        res = recall("where does maya work", tenant_id="t", local_only=True)
        waited = time.perf_counter() - started
    finally:
        holder.rollback()
        holder.close()
    assert [h.id for h in res.episodes] == ["e_maya"]
    assert waited < 1.5, waited
    assert not mem.db.execute("SELECT access_count FROM episodes WHERE id = 'e_maya'").fetchone()[0]
    recall("where does maya work", tenant_id="t", local_only=True)
    # Both recalls counted: the busy one was kept, not dropped.
    assert mem.db.execute("SELECT access_count FROM episodes WHERE id = 'e_maya'").fetchone()[0] == 2


def test_macro_sleep_writes_the_counts_recall_kept(mem):
    from kazma_core.memory import recall as recall_mod
    from kazma_core.memory.macro_sleep import run_macro_sleep

    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json) VALUES ('e_kept', 't', 's1', 1, 'q', 'a', 'episodic', ?, '{}')",
        (time.time(),))
    with recall_mod._pending_lock:
        recall_mod._pending_bumps[("episodes", "e_kept")] = (3, time.time())
    run_macro_sleep(mem.db, cfg={}, tenant_id="t")
    assert mem.db.execute("SELECT access_count FROM episodes WHERE id = 'e_kept'").fetchone()[0] == 3


# ── M15: one vector per row, written once, guarded ────────────────────────


class _Counting:
    dim = 4

    def __init__(self, on_encode=None):
        self.calls: list[str] = []
        self.on_encode = on_encode

    def encode(self, text):
        self.calls.append(text)
        if self.on_encode is not None:
            self.on_encode(text)
        return list(VEC)


def _use(mem, emb) -> None:
    mem.mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: emb)
    mem.mp.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "probe-model")


def test_one_embedding_per_episode_and_per_fact(mem):
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import mirror_episode

    emb = _Counting()
    _use(mem, emb)
    question = "Which supplier did we choose for the pump housings " + "and why " * 40
    eid = mirror_episode(session_id="s1", turn_number=1, user_text=question,
                         assistant_text="Nordvalve, for lead time.", tenant_id="t")
    assert len(emb.calls) == 1
    assert mem.db.execute("SELECT embedding IS NOT NULL FROM episodes WHERE id = ?", (eid,)).fetchone()[0]
    mutate_belief(mem.db, "user", "lives_in", "Porto", predicate_type="functional", tenant_id="t", now=1.0)
    assert emb.calls[1:] == ["user lives_in Porto"]
    assert mem.db.execute("SELECT embedding IS NOT NULL FROM beliefs WHERE object = 'Porto'").fetchone()[0]


def test_the_episode_vector_is_written_under_the_writers_lock(mem):
    from kazma_core.memory import dual_write, vector_write

    _use(mem, _Counting())
    held = []
    real = vector_write.write_local_vector

    def spy(conn, *a, **k):
        held.append((dual_write._get_mirror()._lock.locked(), conn is dual_write._get_mirror()._primary))
        return real(conn, *a, **k)

    mem.mp.setattr(vector_write, "write_local_vector", spy)
    assert dual_write.mirror_episode(session_id="s1", turn_number=1, user_text="the pump housings",
                                     assistant_text="Nordvalve.", tenant_id="t")
    assert held == [(True, True)]


def test_a_local_vector_write_never_commits_the_callers_transaction(mem):
    from kazma_core.memory.backends import LocalSqliteVectorBackend
    from kazma_core.memory.vector_write import write_local_vector

    _use(mem, _Counting())
    mem.db.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, "
                   "metadata_json) VALUES ('e1', 't', 's', 1, 'q', 'episodic', 1, '{}')")
    mem.db.execute("BEGIN")
    assert write_local_vector(mem.db, "episodes", "e1", VEC, still_sql="tier != 'forgotten'")
    assert mem.db.in_transaction  # still the caller's to end
    mem.db.rollback()
    assert mem.db.execute("SELECT embedding FROM episodes WHERE id = 'e1'").fetchone()[0] is None
    # Negative control: the local backend's upsert committed it.
    mem.db.execute("BEGIN")
    LocalSqliteVectorBackend(mem.db).upsert("e1", VEC, meta={"kind": "episode"})
    assert not mem.db.in_transaction


def test_a_forget_while_the_episode_embeds_keeps_its_vector_out(mem):
    """The forget lands between the embedding and the write: no local vector,
    and the remote copy is taken back out."""
    from kazma_core.memory import dual_write, forget

    ops: list[str] = []

    class Remote:
        available = True

        def upsert(self, *a, **k):
            ops.append("upsert")
            return True

        def delete(self, *a, **k):
            ops.append("delete")
            return True

    remote = Remote()
    mem.mp.setattr("kazma_core.memory.backends.get_remote_vector_backend", lambda: remote)
    eid = dual_write._episode_id("s-race", 1, "the pump housings come from Nordvalve")

    def forget_now(text):
        other = sqlite3.connect(str(mem.state), check_same_thread=False)
        other.row_factory = sqlite3.Row
        try:
            assert forget.forget_episode(eid, tenant_id="t", conn=other)["ok"]
        finally:
            other.close()

    _use(mem, _Counting(on_encode=forget_now))
    dual_write.mirror_episode(session_id="s-race", turn_number=1,
                              user_text="the pump housings come from Nordvalve", tenant_id="t")
    row = mem.db.execute("SELECT tier, embedding FROM episodes WHERE id = ?", (eid,)).fetchone()
    assert row["tier"] == "forgotten" and row["embedding"] is None
    assert ops[-1] == "delete"
    # Negative control: the old unguarded second write put it back.
    from kazma_core.memory.backends import LocalSqliteVectorBackend

    LocalSqliteVectorBackend(mem.db).upsert(eid, VEC, meta={"kind": "episode"})
    assert mem.db.execute("SELECT embedding FROM episodes WHERE id = ?", (eid,)).fetchone()[0] is not None


def _local_upserts(source: str) -> list[str]:
    """Functions that upsert through a backend that may be the local store."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            if ({"get_vector_backend", "LocalSqliteVectorBackend"} & (names | attrs)) and "upsert" in attrs:
                found.append(node.name)
    return found


def test_no_memory_writer_upserts_through_the_local_backend():
    """The local vector is the writer's own guarded write; only the remote
    index is upserted (``get_remote_vector_backend``)."""
    offenders = []
    for path in sorted((REPO / "kazma-core" / "kazma_core" / "memory").glob("*.py")):
        if path.name == "backends.py":
            continue
        offenders += [f"{path.name}:{fn}" for fn in _local_upserts(path.read_text(encoding="utf-8"))]
    assert offenders == []
    planted = ("def write(conn, eid, vec):\n"
               "    from kazma_core.memory.backends import get_vector_backend\n"
               "    get_vector_backend(conn).upsert(eid, vec)\n")
    assert _local_upserts(planted) == ["write"]  # negative control
