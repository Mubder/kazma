"""The Neo4j copy of the memory graph converges on SQLite (2026-11-13).

``kazma setup install`` sets Neo4j up by default since 2026-11-13, and the
copy was kept by the dual-write alone (best effort, after each commit):

* a fact written while Neo4j was down never reached it;
* an install that had memories before Neo4j was added had an empty Neo4j
  (only the Settings button filled it: a one-shot push that removed nothing);
* a fact invalidated, superseded or forgotten while Neo4j was down kept its
  edge for good -- a forgotten value stayed in the copy;
* Neo4j down when the first fact of a process was written made the process
  use SQLite for good: no dual-write until a restart.

The maintenance sweep "memory graph sync" (``memory/graph_sync.py``) keeps
it whole. An in-memory stand-in for Neo4j (``FakeNeo4j``) answers the
statements the backend sends, and the facts go through the product's own
writers (``mutate_belief``, ``forget_mentions``). Each test shows the copy
wrong first -- what the old code left -- and right after one pass. The
real-server cases at the end run when ``KAZMA_TEST_NEO4J_URL`` names a
throwaway Neo4j (never this machine's 7687 unless
``KAZMA_TEST_NEO4J_THROWAWAY=1`` says it is one); they touch only a tenant of
their own.
"""

from __future__ import annotations

import copy
import dataclasses
import itertools
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
_REL_IN_QUERY = re.compile(r"-\[r:`([^`]+)`\]->")
_SYNC_LOGGER = "kazma_core.memory.graph_sync"


# ── an in-memory Neo4j ─────────────────────────────────────────────────


class _Result(list):
    def __init__(self, rows=(), deleted: int = 0):
        super().__init__(rows)
        self.deleted = deleted

    def consume(self):
        return SimpleNamespace(counters=SimpleNamespace(relationships_deleted=self.deleted))

    def single(self):
        return self[0] if self else None


class _Session:
    def __init__(self, db: FakeNeo4j) -> None:
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query: str, **params: Any) -> _Result:
        db = self.db
        if db.down:
            raise ConnectionRefusedError("the stand-in Neo4j is down")
        flat = " ".join(query.split())
        db.statements.append(flat)
        if db.probe is not None:
            db.probe(flat)
        tag = re.match(r"// kazma:(\w+)", query.strip())
        if tag:
            return getattr(db, "_op_" + tag.group(1))(query, params)
        return db._legacy(flat, params)


class FakeNeo4j:
    """Nodes and relationships in dicts; each statement the backend sends is
    carried out as Neo4j would (dispatched on its ``// kazma:`` line, and the
    dual-write's own delete statements by their text). A statement it does not
    know fails the test. ``constraint`` is the uniqueness constraint on
    ``(tenant_id, key)``: once made, a write giving two nodes of a tenant one
    key is refused, as Neo4j refuses it."""

    def __init__(self) -> None:
        self.nodes: dict[int, dict[str, Any]] = {}
        self.rels: dict[int, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self.down = False
        self.refuse_values: set[str] = set()
        self.statements: list[str] = []
        self.probe = None
        self.constraint = False
        self.refuse_constraint = False

    # the driver's surface
    def session(self, **_kw: Any) -> _Session:
        return _Session(self)

    def verify_connectivity(self) -> None:
        if self.down:
            raise ConnectionRefusedError("the stand-in Neo4j is down")

    def close(self) -> None:
        pass

    # building blocks
    def add_node(self, *labels: str, **props: Any) -> int:
        nid = next(self._ids)
        self.nodes[nid] = {"labels": set(labels), "props": dict(props)}
        return nid

    def entity(self, name: str, tid: str) -> int | None:
        for nid, n in self.nodes.items():
            if "Entity" in n["labels"] and n["props"].get("name") == name and n["props"].get("tenant_id") == tid:
                return nid
        return None

    def merge_entity(self, name: str, tid: str) -> int:
        """A node as the code before keys made it: found by name, no key."""
        return self.entity(name, tid) or self.add_node("Entity", name=name, tenant_id=tid)

    def add_rel(self, start: int, end: int, rtype: str, **props: Any) -> int:
        rid = next(self._ids)
        self.rels[rid] = {"type": rtype, "start": start, "end": end, "props": dict(props)}
        return rid

    def _in_tenant(self, nid: int, tid: str) -> bool:
        n = self.nodes.get(nid)
        return bool(n) and "Entity" in n["labels"] and n["props"].get("tenant_id") == tid

    def _tenant_rels(self, tid: str) -> list[int]:
        return [rid for rid, r in self.rels.items()
                if self._in_tenant(r["start"], tid) and self._in_tenant(r["end"], tid)]

    def _keyed(self, nid: int) -> bool:
        return self.nodes[nid]["props"].get("key") is not None

    def _row(self, rid: int) -> dict[str, Any]:
        r = self.rels[rid]
        p = r["props"]
        return {"eid": f"5:fake:{rid}", "bid": p.get("belief_id") or "",
                "sub": self.nodes[r["start"]]["props"].get("name"), "rel": r["type"],
                "obj": self.nodes[r["end"]]["props"].get("name"), "conf": p.get("confidence"),
                "pred": p.get("predicate"), "install": p.get("install"),
                "keyed": self._keyed(r["start"]) and self._keyed(r["end"])}

    # node keys
    @staticmethod
    def node_eid(nid: int) -> str:
        return f"4:fake:{nid:08d}"

    @staticmethod
    def _nid(eid: str) -> int:
        return int(eid.rsplit(":", 1)[1])

    def _named(self, name: str, tid: str) -> list[int]:
        return [nid for nid, n in self.nodes.items()
                if "Entity" in n["labels"] and n["props"].get("name") == name and n["props"].get("tenant_id") == tid]

    def _with_key(self, key: str, tid: Any) -> list[int]:
        return [nid for nid, n in self.nodes.items()
                if "Entity" in n["labels"] and n["props"].get("key") == key and n["props"].get("tenant_id") == tid]

    def _set_key(self, nid: int, key: str) -> None:
        tid = self.nodes[nid]["props"].get("tenant_id")
        if self.constraint and any(other != nid for other in self._with_key(key, tid)):
            from kazma_core.memory.graph_backend import GraphWriteRefused

            raise GraphWriteRefused("ConstraintValidationFailed: the key is already held")
        self.nodes[nid]["props"]["key"] = key

    def per_name(self, tid: str = "default") -> dict[str, int]:
        """How many Entity nodes of *tid* hold each name."""
        out: dict[str, int] = {}
        for n in self.nodes.values():
            if "Entity" in n["labels"] and n["props"].get("tenant_id") == tid:
                out[n["props"].get("name")] = out.get(n["props"].get("name"), 0) + 1
        return out

    def _touches(self, nid: int) -> bool:
        return any(r["start"] == nid or r["end"] == nid for r in self.rels.values())

    def _drop_orphans(self, nids: set[int]) -> None:
        for nid in nids:
            if nid in self.nodes and not self._touches(nid):
                del self.nodes[nid]

    # what a test reads
    def edges(self, tid: str = "default") -> set[tuple]:
        return {(row["sub"], row["rel"], row["obj"], row["bid"], row["install"])
                for row in (self._row(rid) for rid in self._tenant_rels(tid))}

    def facts(self, tid: str = "default") -> set[tuple[str, str, str]]:
        return {(s, r, o) for s, r, o, _b, _i in self.edges(tid)}

    def names(self, tid: str = "default") -> set[str]:
        return {n["props"].get("name") for n in self.nodes.values()
                if "Entity" in n["labels"] and n["props"].get("tenant_id") == tid}

    # the backend's statements
    def _merge(self, row: dict[str, Any], end: str, tid: str, how: str) -> list[int]:
        """MERGE of one end, as Neo4j does it: every node that matches, else a new one."""
        name, key = row[end], row.get("sk" if end == "sub" else "ok")
        if how == "key":
            found = self._with_key(key, tid)
            if not found:
                nid = self.add_node("Entity", tenant_id=tid)
                self._set_key(nid, key)
                self.nodes[nid]["props"]["name"] = name
                found = [nid]
            return found
        found = self._named(name, tid)
        if not found:
            nid = self.add_node("Entity", name=name, tenant_id=tid)
            if how == "name":  # ON CREATE SET key (the old statement set none)
                self._set_key(nid, key)
            found = [nid]
        return found

    def _upsert(self, query: str, p: dict[str, Any], how: str) -> _Result:
        rtype = _REL_IN_QUERY.search(query).group(1)
        from kazma_core.memory.graph_backend import GraphWriteRefused

        if any(row["obj"] in self.refuse_values or row["sub"] in self.refuse_values for row in p["rows"]):
            raise GraphWriteRefused("the stand-in refuses this statement")  # all or nothing, as a transaction
        before = copy.deepcopy((self.nodes, self.rels))
        try:
            self._upsert_rows(rtype, p, how)
        except GraphWriteRefused:
            self.nodes, self.rels = before  # a refused statement leaves nothing
            raise
        return _Result()

    def _upsert_rows(self, rtype: str, p: dict[str, Any], how: str) -> None:
        tid, inst = p["tid"], p["inst"]
        for row in p["rows"]:
            for a in self._merge(row, "sub", tid, how):
                for b in self._merge(row, "obj", tid, how):
                    rid = next((k for k, r in self.rels.items()
                                if r["start"] == a and r["end"] == b and r["type"] == rtype), None)
                    mine = {"belief_id": row["bid"], "confidence": row["conf"], "predicate": row["pred"]}
                    if rid is None:
                        rid = self.add_rel(a, b, rtype, **mine)
                    elif not (self.rels[rid]["props"].get("belief_id")
                              and self.rels[rid]["props"].get("install") in (None, inst)):
                        continue
                    props = self.rels[rid]["props"]
                    props.update(mine)
                    if inst is None:
                        props.pop("install", None)
                    else:
                        props["install"] = inst

    def _op_upsert_edges(self, query: str, p: dict[str, Any]) -> _Result:
        """The statement before keys (``_OLD_UPSERT``): by name, no key."""
        return self._upsert(query, p, "old")

    def _op_upsert_edges_by_name(self, query: str, p: dict[str, Any]) -> _Result:
        return self._upsert(query, p, "name")

    def _op_upsert_edges_by_key(self, query: str, p: dict[str, Any]) -> _Result:
        return self._upsert(query, p, "key")

    def _op_delete_edges(self, _query: str, p: dict[str, Any]) -> _Result:
        ends: set[int] = set()
        for row in p["rows"]:
            rid = int(row["eid"].rsplit(":", 1)[1])
            r = self.rels.get(rid)
            if (r and r["props"].get("belief_id") == row["bid"]
                    and self._in_tenant(r["start"], p["tid"]) and self._in_tenant(r["end"], p["tid"])):
                ends |= {r["start"], r["end"]}
                del self.rels[rid]
        self._drop_orphans(ends)
        return _Result()

    def _op_tenant_edges(self, _query: str, p: dict[str, Any]) -> _Result:
        return _Result(self._row(rid) for rid in self._tenant_rels(p["tid"]))

    def _op_edges_for_beliefs(self, _query: str, p: dict[str, Any]) -> _Result:
        bids = set(p["bids"])
        return _Result(self._row(rid) for rid in self._tenant_rels(p["tid"])
                       if self.rels[rid]["props"].get("belief_id") in bids)

    def _op_edges_at_names(self, _query: str, p: dict[str, Any]) -> _Result:
        out = []
        for k in p["keys"]:
            subs, objs = set(self._named(k["sub"], p["tid"])), set(self._named(k["obj"], p["tid"]))
            out += [self._row(rid) for rid, r in self.rels.items()
                    if r["start"] in subs and r["end"] in objs and r["type"] == k["rel"]]
        return _Result(out)

    #: The same read before keys (``// kazma:edges_at_keys``, by name): the
    #: stand-in still answers the code before keys, so each key test can be
    #: run against that code and fail on what it checks.
    _op_edges_at_keys = _op_edges_at_names

    def _op_edges_at_node_keys(self, _query: str, p: dict[str, Any]) -> _Result:
        out = []
        for k in p["keys"]:
            subs, objs = set(self._with_key(k["sk"], p["tid"])), set(self._with_key(k["ok"], p["tid"]))
            out += [self._row(rid) for rid, r in self.rels.items()
                    if r["start"] in subs and r["end"] in objs and r["type"] == k["rel"]]
        return _Result(out)

    def _op_key_constraint_present(self, _query: str, _p: dict[str, Any]) -> _Result:
        return _Result([{"n": int(self.constraint)}])

    def _op_key_constraint(self, _query: str, _p: dict[str, Any]) -> _Result:
        from kazma_core.memory.graph_backend import GraphWriteRefused

        if self.refuse_constraint:
            raise GraphWriteRefused("Neo.ClientError.Security.Forbidden")
        if not self.constraint and self._duplicate_keys():
            raise GraphWriteRefused("Neo.ClientError.Schema.ConstraintCreationFailed")
        self.constraint = True
        return _Result()

    def _duplicate_keys(self) -> list[dict[str, Any]]:
        groups: dict[tuple, list[str]] = {}
        for nid, n in self.nodes.items():
            props = n["props"]
            if "Entity" in n["labels"] and props.get("tenant_id") is not None and props.get("key") is not None:
                groups.setdefault((props["tenant_id"], props["key"]), []).append(nid)
        return [{"tid": t, "key": k, "nodes": [
                    {"eid": self.node_eid(n), "degree": sum(1 for r in self.rels.values() if n in (r["start"], r["end"]))}
                    for n in nids]}
                for (t, k), nids in groups.items() if len(nids) > 1]

    def _op_duplicate_keys(self, _query: str, p: dict[str, Any]) -> _Result:
        return _Result(self._duplicate_keys()[: p["lim"]])

    def _op_unstamp_keys(self, _query: str, p: dict[str, Any]) -> _Result:
        for eid in p["eids"]:
            n = self.nodes.get(self._nid(eid))
            if n is not None:
                n["props"].pop("key", None)
        return _Result()

    def _keyless(self, nid: int) -> bool:
        n = self.nodes[nid]
        props = n["props"]
        return ("Entity" in n["labels"] and props.get("tenant_id") is not None and props.get("key") is None
                and isinstance(props.get("name"), str))

    def _op_keyless_nodes(self, _query: str, _p: dict[str, Any]) -> _Result:
        return _Result({"eid": self.node_eid(nid), "tid": self.nodes[nid]["props"]["tenant_id"],
                        "name": self.nodes[nid]["props"]["name"]} for nid in list(self.nodes) if self._keyless(nid))

    def _op_count_keyless(self, _query: str, _p: dict[str, Any]) -> _Result:
        keyless = unkeyable = 0
        for nid, n in self.nodes.items():
            props = n["props"]
            if "Entity" in n["labels"] and props.get("tenant_id") is not None and props.get("key") is None:
                if isinstance(props.get("name"), str):
                    keyless += 1
                else:
                    unkeyable += 1
        return _Result([{"keyless": keyless, "unkeyable": unkeyable}])

    def _op_stamp_keys(self, _query: str, p: dict[str, Any]) -> _Result:
        from kazma_core.memory.graph_backend import GraphWriteRefused

        if any(row["name"] in self.refuse_values for row in p["rows"]):
            raise GraphWriteRefused("the stand-in refuses this statement")
        out = []
        for row in p["rows"]:
            nid = self._nid(row["eid"])
            n = self.nodes.get(nid)
            ok = (n is not None and n["props"].get("tenant_id") == row["tid"]
                  and n["props"].get("name") == row["name"] and n["props"].get("key") is None)
            twin = self._with_key(row["key"], row["tid"])
            if ok and not twin:
                self._set_key(nid, row["key"])
            out.append({"eid": row["eid"], "outcome": "gone" if not ok else ("twin" if twin else "stamped")})
        return _Result(out)

    def _op_node_edges(self, _query: str, p: dict[str, Any]) -> _Result:
        out = []
        for eid in p["eids"]:
            nid = self._nid(eid)
            for rid, r in self.rels.items():
                if nid not in (r["start"], r["end"]):
                    continue
                a, b = self.nodes[r["start"]]["props"], self.nodes[r["end"]]["props"]
                if a.get("tenant_id") is not None and a.get("tenant_id") == b.get("tenant_id"):
                    out.append({**self._row(rid), "node": eid, "tid": a["tenant_id"]})
        return _Result(out)

    def _op_twin_degrees(self, _query: str, p: dict[str, Any]) -> _Result:
        out = []
        for eid in p["eids"]:
            nid = self._nid(eid)
            if nid in self.nodes and self.nodes[nid]["props"].get("key") is None:
                out.append({"node": eid, "degree": sum(1 for r in self.rels.values() if nid in (r["start"], r["end"]))})
        return _Result(out)

    def _op_remove_keyless_twins(self, _query: str, p: dict[str, Any]) -> _Result:
        removed = 0
        for row in p["rows"]:
            nid = self._nid(row["eid"])
            n = self.nodes.get(nid)
            if (n and n["props"].get("key") is None and n["props"].get("tenant_id") == row["tid"]
                    and n["props"].get("name") == row["name"] and not self._touches(nid)
                    and self._with_key(row["key"], row["tid"])):
                del self.nodes[nid]
                removed += 1
        return _Result([{"removed": removed}])

    def _op_edge_tenants(self, _query: str, _p: dict[str, Any]) -> _Result:
        tids = set()
        for r in self.rels.values():
            a, b = self.nodes[r["start"]], self.nodes[r["end"]]
            tid = a["props"].get("tenant_id")
            if (r["props"].get("belief_id") and "Entity" in a["labels"] and "Entity" in b["labels"]
                    and tid is not None and tid == b["props"].get("tenant_id")):
                tids.add(tid)
        return _Result({"tid": t} for t in sorted(tids))

    def _op_isolated_nodes(self, _query: str, p: dict[str, Any]) -> _Result:
        alone = [nid for nid in self.nodes if self._in_tenant(nid, p["tid"]) and not self._touches(nid)][: p["lim"]]
        for nid in alone:
            del self.nodes[nid]
        return _Result([{"removed": len(alone)}])

    def _legacy(self, flat: str, p: dict[str, Any]) -> _Result:
        """The dual-write's delete and graph-clear statements."""
        if flat.startswith("MATCH (a:Entity {") and "-[r]-(b:Entity)" in flat:  # neighbors
            keyed = "key: $key" in flat
            ends = set(self._with_key(p["key"], p["tid"]) if keyed else self._named(p["name"], p["tid"]))
            return _Result({"subject": self.nodes[n]["props"].get("name"), "predicate": r["type"],
                            "object": self.nodes[r["end"] if r["start"] == n else r["start"]]["props"].get("name"),
                            "confidence": r["props"].get("confidence"), "id": r["props"].get("belief_id")}
                           for r in self.rels.values() for n in ends if n in (r["start"], r["end"]))
        if "WHERE r.belief_id = $bid" in flat:
            gone = [rid for rid, r in self.rels.items() if r["props"].get("belief_id") == p["bid"]]
        elif flat.startswith("MATCH (a:Entity {name: $sub, tenant_id: $tid})"):
            subs, objs = set(self._named(p["sub"], p["tid"])), set(self._named(p["obj"], p["tid"]))
            gone = [rid for rid, r in self.rels.items() if r["start"] in subs and r["end"] in objs]
        elif flat.startswith("MATCH (a:Entity {tenant_id: $tid, key: $sk})"):
            subs, objs = set(self._with_key(p["sk"], p["tid"])), set(self._with_key(p["ok"], p["tid"]))
            gone = [rid for rid, r in self.rels.items() if r["start"] in subs and r["end"] in objs]
        elif "DELETE r" in flat and "MATCH (a:Entity {tenant_id: $tid})-[r]->(:Entity {tenant_id: $tid})" in flat:
            guarded = "r.install = $inst" in flat
            gone = [rid for rid in self._tenant_rels(p["tid"])
                    if not guarded or (self.rels[rid]["props"].get("belief_id")
                                       and self.rels[rid]["props"].get("install") in (None, p.get("inst")))]
        elif flat == "MATCH (n:Entity {tenant_id: $tid}) WHERE NOT (n)--() DELETE n":
            self._op_isolated_nodes("", {"tid": p["tid"], "lim": 10**9})
            return _Result()
        else:
            raise AssertionError(f"the stand-in Neo4j does not know this statement: {flat}")
        ends = {e for rid in gone for e in (self.rels[rid]["start"], self.rels[rid]["end"])}
        for rid in gone:
            del self.rels[rid]
        if "WHERE NOT (n)--() DELETE n" in flat:
            self._drop_orphans(ends)
        return _Result(deleted=len(gone))


# ── the world: a private memory database, the stand-in as the graph store ─


#: ``graph_backend.KEYS_KEY`` (spelled out: the fixture must run on the code
#: before keys, where each key test then fails on what it checks).
_KEYS_KEY = "memory.v2.graph_keys"


def _forget_findings() -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.memory.graph_sync import FINDINGS_KEY

    get_config_store().delete(FINDINGS_KEY)
    get_config_store().delete(_KEYS_KEY)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    from kazma_core.memory import backends, graph_backend, graph_sync
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    state = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    neo = FakeNeo4j()
    cfg = copy.deepcopy(backends.DEFAULT_BACKENDS_CFG)
    cfg["graph"] = {"provider": "neo4j", "url": "bolt://fake-neo4j.invalid:7687", "user": "neo4j",
                    "password": "pw"}
    monkeypatch.setattr(backends, "get_backends_cfg", lambda: cfg)
    monkeypatch.setattr(graph_backend, "_neo4j_driver_installed", lambda: True)
    monkeypatch.setattr(graph_backend, "_make_driver", lambda url, auth: neo)
    monkeypatch.setattr(graph_backend, "this_install", lambda: "install-a")
    monkeypatch.setattr(graph_sync, "_STATE", {})
    graph_backend.reset_graph_backend_cache()
    _forget_findings()
    conn = sqlite3.connect(str(state), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    yield SimpleNamespace(conn=conn, neo=neo, cfg=cfg, state=state, mp=monkeypatch)
    graph_backend.reset_graph_backend_cache()
    _forget_findings()
    conn.close()


def _fact(w, subject: str, predicate: str, obj: str, *, tenant: str = "default", **kw: Any) -> str:
    from kazma_core.memory.belief_mutation import mutate_belief

    result = mutate_belief(w.conn, subject, predicate, obj, tenant_id=tenant, confidence=0.8, **kw)
    assert result.get("belief_id"), result
    return str(result["belief_id"])


def _sync(w, **kw: Any) -> dict[str, Any]:
    from kazma_core.memory.graph_sync import run_graph_sync

    return run_graph_sync(w.conn, **kw)


def _queue(w) -> list[str]:
    return [r[0] for r in w.conn.execute("SELECT belief_id FROM memory_graph_outbox ORDER BY seq")]


def _graph_store(w, provider: str) -> None:
    from kazma_core.memory import graph_backend

    w.cfg["graph"]["provider"] = provider
    graph_backend.reset_graph_backend_cache()


def _old_sync(be, conn, *, tenant_id: str = "default", limit: int = 500) -> int:
    """The Settings button until 2026-11-13 (``sync_beliefs_to_neo4j``): push
    the first *limit* current facts by importance, remove nothing."""
    rows = conn.execute(
        "SELECT id, subject, predicate, object, confidence, tenant_id FROM beliefs "
        "WHERE valid_until IS NULL AND invalidated_at IS NULL AND tenant_id = ? "
        "ORDER BY structural_importance DESC, confidence DESC LIMIT ?", (tenant_id, limit)).fetchall()
    return sum(bool(be.upsert_triple(subject=r["subject"], predicate=r["predicate"] or "related",
                                     obj=r["object"], belief_id=r["id"], tenant_id=r["tenant_id"],
                                     confidence=float(r["confidence"] or 0.5))) for r in rows)


# ── filling, catching up, removing ──────────────────────────────────────


def test_an_empty_neo4j_is_filled_from_the_facts_the_install_already_holds(world):
    _graph_store(world, "sqlite")
    bids = {_fact(world, "user", "lives_in", "Kuwait"), _fact(world, "user", "works_at", "Kazma"),
            _fact(world, "kazma", "is_a", "agent")}
    assert _sync(world)["status"] == "not_configured"
    assert _queue(world) == [], "with no Neo4j the queue is emptied"
    _graph_store(world, "neo4j")
    assert world.neo.edges() == set(), "the old code: the dual-write alone never fills a new copy"

    report = _sync(world)

    assert report["mode"] == "full" and report["why"] == "a new copy"
    assert world.neo.facts() == {("user", "LIVES_IN", "Kuwait"), ("user", "WORKS_AT", "Kazma"),
                                 ("kazma", "IS_A", "agent")}
    assert {e[3] for e in world.neo.edges()} == bids
    assert {e[4] for e in world.neo.edges()} == {"install-a"}, "every edge carries this install's tag"
    assert report["status"] == "in_sync" and report["behind"] == 0
    rel = next(r for r in world.neo.rels.values() if r["type"] == "LIVES_IN")
    assert rel["props"] == {"belief_id": rel["props"]["belief_id"], "confidence": 0.8,
                            "predicate": "lives_in", "install": "install-a"}


def test_a_fact_written_while_neo4j_was_down_is_there_after_one_pass(world):
    _fact(world, "user", "lives_in", "Kuwait")
    assert _sync(world)["status"] == "in_sync"
    world.neo.down = True
    bid = _fact(world, "user", "likes", "tea")
    world.neo.down = False
    assert ("user", "LIKES", "tea") not in world.neo.facts(), "the old code: the dual-write lost it for good"
    assert bid in _queue(world)

    report = _sync(world)

    assert report["mode"] == "incremental", "the queue brought it, no full comparison needed"
    assert ("user", "LIKES", "tea", bid, "install-a") in world.neo.edges()
    assert _queue(world) == []


def test_neo4j_down_when_the_first_fact_was_written_does_not_stop_the_dual_write_for_good(world, monkeypatch):
    from kazma_core.memory import graph_backend

    world.neo.down = True
    assert isinstance(graph_backend.get_graph_backend(), graph_backend.SqliteGraphBackend)
    world.neo.down = False
    assert isinstance(graph_backend.get_graph_backend(), graph_backend.SqliteGraphBackend), (
        "a stand-in for a while (the old code: for the life of the process)")
    assert 0 < graph_backend._cached_until <= time.monotonic() + graph_backend._FALLBACK_RETRY_S
    monkeypatch.setattr(graph_backend, "_cached_until", time.monotonic() - 1)  # its minute is over
    assert graph_backend.get_graph_backend().name == "neo4j"


def test_a_fact_forgotten_while_neo4j_was_down_leaves_it_with_its_value_node(world):
    from kazma_core.memory.forget import forget_mentions

    _fact(world, "user", "test_codeword", "BLUE-HERON-42")
    _fact(world, "user", "lives_in", "Kuwait")
    _sync(world)
    assert "BLUE-HERON-42" in world.neo.names()
    world.neo.down = True
    assert forget_mentions("BLUE-HERON-42", tenant_id="default")["facts_invalidated"]
    world.neo.down = False
    assert "BLUE-HERON-42" in world.neo.names(), "the old code: the forgotten value stayed in the copy"

    _sync(world)

    assert "BLUE-HERON-42" not in world.neo.names()
    assert world.neo.facts() == {("user", "LIVES_IN", "Kuwait")}


def test_a_fact_superseded_while_neo4j_was_down_leaves_only_the_new_value(world):
    _fact(world, "user", "lives_in", "Paris", predicate_type="functional")
    _sync(world)
    world.neo.down = True
    _fact(world, "user", "lives_in", "London", predicate_type="functional")
    world.neo.down = False
    assert ("user", "LIVES_IN", "Paris") in world.neo.facts()

    _sync(world)

    assert world.neo.facts() == {("user", "LIVES_IN", "London")}
    assert "Paris" not in world.neo.names()


def test_the_full_comparison_removes_an_edge_whose_fact_is_gone_and_corrects_a_stale_one(world):
    keep = _fact(world, "user", "lives_in", "Kuwait")
    _sync(world)
    gone = _fact(world, "user", "likes", "coffee")
    _sync(world)
    # Out of band (a deleted row's trigger lost with an older build, an edit by
    # hand): no queue entry names them.
    world.conn.execute("DELETE FROM beliefs WHERE id = ?", (gone,))
    world.conn.execute("UPDATE beliefs SET confidence = 0.3 WHERE id = ?", (keep,))
    world.conn.execute("DELETE FROM memory_graph_outbox")
    world.conn.commit()

    report = _sync(world, full=True)

    assert report["removed"] == 1 and report["written"] == 1
    assert world.neo.facts() == {("user", "LIVES_IN", "Kuwait")}
    rel = next(iter(world.neo.rels.values()))
    assert rel["props"]["confidence"] == 0.3
    assert "coffee" not in world.neo.names()


def test_two_facts_on_one_edge_give_it_the_newest(world):
    from kazma_core.memory.graph_sync import Held, Want, _plan_changes

    key = ("user", "WORKS_AT", "Kazma")
    wants = {key: Want("b-new", "works-at", 0.9, 200.0)}
    plan = _plan_changes(wants, [Held("e1", "b-old", key, 0.5, "works_at", "install-a")],
                        install="install-a", known={"b-old"})
    assert plan.upserts == [(key, wants[key])] and plan.deletes == []

    for bid, predicate, at in (("b-old", "works_at", 100.0), ("b-new", "works-at", 200.0)):
        _row(world, bid, "user", predicate, "Kazma", at=at)  # both sanitize to WORKS_AT
    _sync(world, full=True)
    (edge,) = world.neo.edges()
    assert edge[3] == "b-new"


def _row(w, bid: str, subject: str, predicate: str, obj: str, *, at: float | None = None,
         tenant: str = "default") -> None:
    """A fact row written directly (no dual-write, the trigger still queues it)."""
    at = time.time() if at is None else at
    w.conn.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "valid_from, ingested_at) VALUES (?, ?, ?, ?, 'set', ?, 0.7, ?, ?)",
        (bid, tenant, subject, predicate, obj, at, at))
    w.conn.commit()


# ── what is not this install's ──────────────────────────────────────────

_OLD_CLEAR = "MATCH (a:Entity {tenant_id: $tid})-[r]->(:Entity {tenant_id: $tid}) DELETE r"


def _foreign(neo: FakeNeo4j) -> dict[str, int]:
    """Edges Kazma did not write, or another install did."""
    a, b = neo.merge_entity("user", "default"), neo.merge_entity("Kuwait", "default")
    c, d = neo.merge_entity("owner", "default"), neo.merge_entity("notes", "default")
    p, q = neo.add_node("Person", name="Ali"), neo.add_node("Person", name="Mona")
    return {
        "no_fact_id": neo.add_rel(c, d, "WROTE"),
        "at_a_wanted_key": neo.add_rel(a, b, "LIVES_IN", note="written by hand"),
        "other_install": neo.add_rel(c, b, "VISITED", belief_id="b-elsewhere", install="install-b"),
        "untagged_unknown": neo.add_rel(d, b, "MENTIONS", belief_id="b-unknown-legacy"),
        "other_label": neo.add_rel(p, q, "KNOWS", belief_id="b-person"),
    }


def test_edges_kazma_did_not_write_are_never_touched(world, caplog):
    _graph_store(world, "sqlite")
    _fact(world, "user", "lives_in", "Kuwait")
    _fact(world, "user", "likes", "tea")
    _graph_store(world, "neo4j")
    planted = _foreign(world.neo)
    before = {rid: copy.deepcopy(world.neo.rels[rid]) for rid in planted.values()}
    caplog.set_level(logging.INFO, logger=_SYNC_LOGGER)

    report = _sync(world)

    for name, rid in planted.items():
        assert world.neo.rels.get(rid) == before[rid], f"{name} was changed"
    assert ("user", "LIKES", "tea") in world.neo.facts()
    lives = [r for r in world.neo.rels.values() if r["type"] == "LIVES_IN"]
    assert len(lives) == 1, "the hand-written edge at that key stands for the fact; no second one"
    assert report["held_elsewhere"] == 1 and report["unknown_edges"] == 1
    assert report["status"] == "in_sync"
    again = _sync(world, full=True)
    assert again["written"] == 0, "an edge held elsewhere is not pushed again every pass"
    assert "edges of facts this install does not know, left alone: 1" in caplog.text


def test_negative_control_the_unguarded_clear_takes_what_is_not_kazmas(world):
    from kazma_core.memory.graph_backend import clear_tenant_edges

    planted = _foreign(world.neo)
    own = world.neo.add_rel(world.neo.merge_entity("user", "default"), world.neo.merge_entity("tea", "default"),
                            "LIKES", belief_id="b-mine", install="install-a")
    assert clear_tenant_edges(tenant_id="default")["cleared"] == 2  # this install's and the untagged one
    assert own not in world.neo.rels and planted["untagged_unknown"] not in world.neo.rels
    assert {planted["no_fact_id"], planted["at_a_wanted_key"], planted["other_install"]} <= set(world.neo.rels)

    world.neo.session().run(_OLD_CLEAR, tid="default")
    assert planted["no_fact_id"] not in world.neo.rels and planted["other_install"] not in world.neo.rels


def test_the_dual_write_does_not_take_over_another_installs_edge(world):
    from kazma_core.memory import graph_backend

    a, b = world.neo.merge_entity("user", "default"), world.neo.merge_entity("tea", "default")
    rid = world.neo.add_rel(a, b, "LIKES", belief_id="b-theirs", install="install-b", confidence=0.4)
    assert graph_backend.upsert_belief_edge(subject="user", predicate="likes", obj="tea", belief_id="b-mine")
    assert world.neo.rels[rid]["props"]["belief_id"] == "b-theirs"


def test_tenants_are_kept_apart(world):
    from kazma_core.memory.hygiene import invalidate_belief

    t1 = _fact(world, "user", "lives_in", "Kuwait", tenant="t1")
    _fact(world, "user", "lives_in", "Kuwait", tenant="t2")
    _sync(world)
    assert world.neo.facts("t1") == world.neo.facts("t2") == {("user", "LIVES_IN", "Kuwait")}
    world.neo.down = True
    invalidate_belief(t1, conn=world.conn, tenant_id="t1")
    world.neo.down = False

    _sync(world)

    assert world.neo.facts("t1") == set()
    assert world.neo.facts("t2") == {("user", "LIVES_IN", "Kuwait")}
    assert {e[0] for e in world.neo.edges("t2")} == {"user"}


# ── bounded, ordered, waited out ────────────────────────────────────────


def test_a_large_set_takes_two_passes_newest_first(world):
    _graph_store(world, "sqlite")
    bids = [f"b-{i:02d}" for i in range(30)]
    for i, bid in enumerate(bids):
        _row(world, bid, "user", "noted", f"value {i}", at=1000.0 + i)
    _sync(world)
    _graph_store(world, "neo4j")

    first = _sync(world, limit=20)

    assert first["written"] == 20 and first["status"] == "behind" and first["behind"] == 10
    newest = [r[0] for r in world.conn.execute(
        "SELECT id FROM beliefs ORDER BY ingested_at DESC, id DESC LIMIT 20")]
    assert {e[3] for e in world.neo.edges()} == set(newest)
    second = _sync(world, limit=20)
    assert second["mode"] == "full" and second["why"] == "catching up"
    assert {e[3] for e in world.neo.edges()} == set(bids)
    assert second["status"] == "in_sync"


def test_removals_come_first(world):
    _fact(world, "user", "keeps", "this")
    _sync(world)
    for i in range(5):
        world.neo.add_rel(world.neo.merge_entity("user", "default"), world.neo.merge_entity(f"old {i}", "default"),
                          "NOTED", belief_id=f"b-gone-{i}", install="install-a")
    world.conn.execute("DELETE FROM memory_graph_outbox")
    world.conn.commit()
    for i in range(5):
        _row(world, f"b-new-{i}", "user", "noted", f"new {i}")
    world.conn.execute("DELETE FROM memory_graph_outbox")
    world.conn.commit()

    report = _sync(world, full=True, limit=5)

    assert report["removed"] == 5 and report["written"] == 0
    assert not any(e[2].startswith("old") for e in world.neo.edges())


def test_a_value_neo4j_refuses_does_not_hold_up_the_rest(world):
    _graph_store(world, "sqlite")
    for i, value in enumerate(("Kuwait", "refused value", "tea")):
        _row(world, f"b-{i}", "user", "noted", value, at=100.0 + i)
    _sync(world)
    _graph_store(world, "neo4j")
    world.neo.refuse_values = {"refused value"}

    first = _sync(world)

    assert first["written"] == 2 and first["failed"] == 1
    assert world.neo.facts() == {("user", "NOTED", "Kuwait"), ("user", "NOTED", "tea")}
    batch = [s for s in world.neo.statements if s.startswith("// kazma:upsert_edges")]
    assert len(batch) == 4, "the batch, refused whole, then each row on its own"
    assert first["status"] == "behind"
    second = _sync(world)
    assert second["mode"] == "incremental", "a refusal waits for the daily comparison, not every pass"


def test_a_neo4j_that_does_not_answer_is_waited_out_quietly(world, caplog):
    from kazma_core.memory import worker_bootstrap as wb

    _fact(world, "user", "lives_in", "Kuwait")
    _sync(world)
    world.neo.down = True
    _fact(world, "user", "likes", "tea")
    caplog.set_level(logging.INFO, logger=_SYNC_LOGGER)
    states = [_sync(world)["status"] for _ in range(3)]
    world.mp.setattr(wb, "_SWEEP_STATE", {})
    import asyncio

    asyncio.run(wb._run_sweeps((("memory graph sync", lambda: _sync(world)),)))

    assert states == ["unreachable"] * 3
    assert wb._SWEEP_STATE["memory graph sync"]["consecutive_failures"] == 0, "never a failed sweep"
    warnings = [r for r in caplog.records if r.name == _SYNC_LOGGER and r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "does not answer" in warnings[0].getMessage()
    assert len(_queue(world)) >= 1, "the queue keeps the change"
    world.neo.down = False
    assert _sync(world)["status"] == "in_sync"
    assert ("user", "LIKES", "tea") in world.neo.facts()
    assert any("answers again" in r.getMessage() for r in caplog.records if r.name == _SYNC_LOGGER)


def test_a_queue_past_its_bound_becomes_a_full_comparison(world, monkeypatch):
    from kazma_core.memory import graph_sync

    _fact(world, "user", "lives_in", "Kuwait")
    _sync(world)
    monkeypatch.setattr(graph_sync, "OUTBOX_MAX_ROWS", 5)
    world.neo.down = True
    for i in range(8):
        _fact(world, "user", "noted", f"value {i}", predicate_type="set")
    _sync(world)
    assert _queue(world) == []
    world.neo.down = False

    report = _sync(world)

    assert report["mode"] == "full" and report["why"] == "queue past its bound"
    assert len(world.neo.facts()) == 9


def test_a_neo4j_that_went_back_is_compared_in_full_at_once(world, monkeypatch):
    from kazma_core.memory import graph_sync

    for i in range(4):
        _fact(world, "user", "noted", f"value {i}", predicate_type="set")
    _sync(world)
    assert len(world.neo.facts()) == 4
    world.neo.nodes.clear()
    world.neo.rels.clear()  # recreated empty, or restored from an older backup
    with monkeypatch.context() as m:
        m.setattr(graph_sync, "_went_back", lambda *a, **k: False)
        assert _sync(world)["mode"] == "incremental"
        assert world.neo.facts() == set(), "the old code: nothing queued brings them back"

    report = _sync(world)

    assert report["mode"] == "full" and report["why"] == "went back"
    assert len(world.neo.facts()) == 4


def test_a_memory_restore_makes_the_next_pass_compare_in_full(world):
    import ast
    import inspect

    from kazma_core.memory import backup
    from kazma_core.memory.graph_sync import mark_full_due

    _fact(world, "user", "lives_in", "Kuwait")
    _sync(world)
    assert _sync(world)["mode"] == "incremental"
    mark_full_due("memory restored")
    assert _sync(world)["why"] == "memory restored"
    assert _sync(world)["mode"] == "incremental", "done once, then incremental again"
    called = {n.func.id for n in ast.walk(ast.parse(inspect.getsource(backup.restore_memory_backup)))
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "mark_full_due" in called


# ── the queue, and no Neo4j call under a memory write ───────────────────


def test_the_queue_names_every_change_an_edge_depends_on(world):
    from kazma_core.memory.schema_v2 import GRAPH_EDGE_COLUMNS

    bid = _fact(world, "user", "lives_in", "Kuwait")
    values = {"subject": "owner", "predicate": "resides_in", "object": "Kuwait City", "confidence": 0.2,
              "valid_until": 1.0, "invalidated_at": 1.0, "tenant_id": "t9"}
    assert set(values) == set(GRAPH_EDGE_COLUMNS)
    for column, value in values.items():
        world.conn.execute("DELETE FROM memory_graph_outbox")
        world.conn.execute(f"UPDATE beliefs SET {column} = ? WHERE id = ?", (value, bid))  # nosec B608 - test columns
        world.conn.commit()
        assert bid in _queue(world), column
    world.conn.execute("DELETE FROM memory_graph_outbox")
    world.conn.execute("UPDATE beliefs SET access_count = 7, last_accessed = 1.0 WHERE id = ?", (bid,))
    world.conn.commit()
    assert _queue(world) == [], "an access bump changes no edge"
    world.conn.execute("DELETE FROM beliefs WHERE id = ?", (bid,))
    world.conn.commit()
    assert _queue(world) == [bid]


def test_negative_control_without_the_queue_a_change_waits_a_day(world):
    keep = _fact(world, "user", "lives_in", "Kuwait")
    _sync(world)
    for trigger in ("beliefs_graph_ai", "beliefs_graph_au", "beliefs_graph_ad"):
        world.conn.execute(f"DROP TRIGGER {trigger}")  # nosec B608 - fixed names
    world.conn.execute("UPDATE beliefs SET valid_until = 1.0, invalidated_at = 1.0 WHERE id = ?", (keep,))
    world.conn.commit()
    assert _sync(world)["mode"] == "incremental"
    assert ("user", "LIVES_IN", "Kuwait") in world.neo.facts()


def test_no_neo4j_call_inside_a_memory_write_transaction(world):
    seen: list[tuple[str, bool]] = []

    def probe(statement: str) -> None:
        other = sqlite3.connect(str(world.state), timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
            seen.append((statement[:40], True))
        except sqlite3.OperationalError:
            seen.append((statement[:40], False))
        finally:
            other.close()

    _graph_store(world, "sqlite")
    for i in range(3):
        _fact(world, "user", "noted", f"value {i}", predicate_type="set")
    _graph_store(world, "neo4j")
    world.neo.add_rel(world.neo.merge_entity("user", "default"), world.neo.merge_entity("stale", "default"),
                      "NOTED", belief_id="b-stale", install="install-a")
    world.neo.probe = probe
    _sync(world)  # the full comparison: reads, removals, writes
    world.neo.down = True
    _fact(world, "user", "noted", "later", predicate_type="set")
    world.neo.down = False
    _sync(world)  # the queue
    assert len(seen) > 5
    assert [s for s in seen if not s[1]] == []


def test_negative_control_an_open_write_stops_the_pass(world):
    world.conn.execute("BEGIN IMMEDIATE")
    world.conn.execute("UPDATE beliefs SET confidence = confidence")
    with pytest.raises(RuntimeError, match="write transaction"):
        _sync(world, full=True)
    world.conn.rollback()


# ── one implementation: the Settings button, health ────────────────────


def test_the_settings_button_runs_the_same_sync(world):
    from kazma_core.memory import graph_backend
    from kazma_core.memory.graph_sync import sync_now

    _graph_store(world, "sqlite")
    _fact(world, "user", "lives_in", "Kuwait")
    _graph_store(world, "neo4j")
    world.neo.add_rel(world.neo.merge_entity("user", "default"), world.neo.merge_entity("forgotten", "default"),
                      "NOTED", belief_id="b-gone", install="install-a")

    answer = sync_now()

    assert answer == {"ok": True, "status": "in_sync", "synced": 1, "removed": 1, "failed": 0,
                      "behind": 0, "facts": 1}
    assert world.neo.facts() == {("user", "LIVES_IN", "Kuwait")}
    assert not hasattr(graph_backend, "sync_beliefs_to_neo4j"), "one implementation"
    route = (REPO / "kazma-ui/kazma_ui/routes_direct/settings.py").read_text(encoding="utf-8")
    assert "from kazma_core.memory.graph_sync import sync_now" in route
    world.neo.down = True
    refused = sync_now()
    assert refused["ok"] is False and refused["status"] == "unreachable"


def test_negative_control_the_old_button_left_the_rest_and_removed_nothing(world):
    from kazma_core.memory import graph_backend

    _graph_store(world, "sqlite")
    for i in range(6):
        _fact(world, "user", "noted", f"value {i}", predicate_type="set")
    _graph_store(world, "neo4j")
    world.neo.add_rel(world.neo.merge_entity("user", "default"), world.neo.merge_entity("forgotten", "default"),
                      "NOTED", belief_id="b-gone", install="install-a")
    assert _old_sync(graph_backend.get_graph_backend(), world.conn, limit=4) == 4
    assert len(world.neo.facts()) == 5, "two facts left out, the forgotten one kept"


def test_health_reports_the_copy(world):
    from kazma_core.memory.graph_backend import get_graph_backend
    from kazma_core.memory.graph_sync import graph_copy_status
    from kazma_core.memory.health import _graph_copy_component
    from kazma_core.memory.v2_health import build_v2_health

    assert graph_copy_status(world.conn)["status"] == "unknown"
    _fact(world, "user", "lives_in", "Kuwait")
    _sync(world)
    graph = build_v2_health()["graph"]
    # The graph store's entries and the hygiene counts side by side: the
    # hygiene report replaced the whole entry from 2026-10-03, and the Memory
    # page said "graph: sqlite" over a Neo4j store.
    assert graph["provider"] == "neo4j" and graph["online"] is True and "empty" in graph
    copy_state = graph["copy"]
    assert copy_state["status"] == "in_sync" and copy_state["facts"] == 1
    assert _graph_copy_component(copy_state, {}, get_graph_backend)["ok"] is True
    world.neo.down = True
    _fact(world, "user", "likes", "tea")
    _sync(world)
    down = graph_copy_status(world.conn)
    assert down["status"] == "unreachable" and down["pending_changes"] >= 1
    comp = _graph_copy_component(down, {}, get_graph_backend)
    assert comp["ok"] is False and "does not answer" in comp["detail"] and "change" in comp["detail"]
    _graph_store(world, "sqlite")
    assert _sync(world)["status"] == "not_configured"
    assert graph_copy_status(world.conn)["status"] == "not_configured"


def test_the_sweep_runs_after_the_mirror_sync():
    from kazma_core.memory import worker_bootstrap as wb

    labels = [label for label, _ in wb._MAINTENANCE_SWEEPS]
    assert ("memory graph sync", wb._sync_memory_graph) in wb._MAINTENANCE_SWEEPS
    assert labels.index("forget convergence") < labels.index("memory graph sync")
    assert labels.index("memory mirror sync") < labels.index("memory graph sync")


# ── node keys: one node per name, found by an index ──────────────────────
#
# Every MERGE by {name, tenant_id} scanned every Entity node (no index): 500
# rows over 40,000 nodes took 8 s on a real Neo4j. Each node now carries the
# SHA-256 of its name as ``key`` under a uniqueness constraint; the sync keys
# the copy, and once every node has its key writes find nodes by key.

#: The fact-edge write before keys (2026-11-13): by name, no key.
_OLD_UPSERT = """// kazma:upsert_edges
UNWIND $rows AS row
MERGE (a:Entity {name: row.sub, tenant_id: $tid})
MERGE (b:Entity {name: row.obj, tenant_id: $tid})
MERGE (a)-[r:%s]->(b)
ON CREATE SET r.belief_id = row.bid, r.confidence = row.conf, r.predicate = row.pred, r.install = $inst
WITH r, row
WHERE coalesce(r.belief_id, '') <> '' AND (r.install IS NULL OR r.install = $inst)
SET r.belief_id = row.bid, r.confidence = row.conf, r.predicate = row.pred, r.install = $inst"""


def _old_write(session_of, subject: str, predicate: str, obj: str, bid: str, *, tenant: str = "default",
               install: str | None = "install-a") -> None:
    """One edge written as the code before keys wrote it (an older build, or
    another install's): its new nodes have no key."""
    from kazma_core.memory.graph_backend import _rel_type, edge_row

    with session_of() as session:
        result = session.run(_OLD_UPSERT % _rel_type(predicate), tid=tenant, inst=install,
                             rows=[edge_row(subject, predicate, obj, bid, 0.7)])
        result.consume()


def _keys_record() -> dict[str, Any]:
    from kazma_core.config_store import get_config_store
    from kazma_core.memory.graph_backend import KEYS_KEY

    assert KEYS_KEY == _KEYS_KEY
    return dict(get_config_store().get(KEYS_KEY) or {})


def _backend():
    from kazma_core.memory import graph_backend

    return graph_backend.get_graph_backend()


def _statements_since(neo: FakeNeo4j, start: int) -> list[str]:
    return [re.match(r"// kazma:(\w+)", s).group(1) for s in neo.statements[start:] if s.startswith("// kazma:")]


def _all_keyed(neo: FakeNeo4j, tid: str = "default") -> bool:
    from kazma_core.memory.graph_backend import entity_key

    return all(n["props"].get("key") == entity_key(n["props"]["name"]) for n in neo.nodes.values()
               if "Entity" in n["labels"] and n["props"].get("tenant_id") == tid)


def test_a_copy_written_before_keys_is_keyed_in_one_pass_and_then_found_by_key(world):
    from kazma_core.memory import graph_backend

    _graph_store(world, "sqlite")
    _fact(world, "user", "lives_in", "Kuwait")
    _fact(world, "user", "likes", "tea")
    _graph_store(world, "neo4j")
    for subject, predicate, obj in (("user", "lives_in", "Kuwait"), ("user", "likes", "tea")):
        _old_write(world.neo.session, subject, predicate, obj, f"b-{obj}")
    assert not any(n["props"].get("key") for n in world.neo.nodes.values()), "the old code: no key, no index"
    start = len(world.neo.statements)
    assert graph_backend.upsert_belief_edge(subject="user", predicate="works_at", obj="Kazma", belief_id="b-k")
    assert _statements_since(world.neo, start) == ["upsert_edges_by_name"], "not keyed yet: by name"
    assert world.neo.per_name() == {"user": 1, "Kuwait": 1, "tea": 1, "Kazma": 1}
    assert world.neo.nodes[world.neo.entity("Kazma", "default")]["props"]["key"], "a new node gets its key"

    report = _sync(world)

    assert world.neo.constraint, "the uniqueness constraint is made"
    assert _all_keyed(world.neo)
    assert report["keys"]["state"] == "keyed" and report["keys"]["stamped"] == 3
    assert _keys_record()["state"] == "keyed" and _backend().keyed()
    start = len(world.neo.statements)
    assert graph_backend.upsert_belief_edge(subject="user", predicate="speaks", obj="Arabic", belief_id="b-a")
    assert _statements_since(world.neo, start) == ["upsert_edges_by_key"]
    assert set(world.neo.per_name().values()) == {1}


def test_one_node_per_name_through_every_phase_with_writes_between(world, monkeypatch):
    from kazma_core.memory import graph_backend, graph_sync

    names = lambda: world.neo.per_name()  # noqa: E731

    def write(subject: str, obj: str, bid: str) -> None:
        assert graph_backend.upsert_belief_edge(subject=subject, predicate="noted", obj=obj, belief_id=bid)
        assert max(names().values()) == 1, names()

    _graph_store(world, "sqlite")
    for i in range(6):
        _row(world, f"b-{i}", "user", "noted", f"value {i}")
    _graph_store(world, "neo4j")
    for i in range(6):  # the copy as the code before keys left it
        _old_write(world.neo.session, "user", "noted", f"value {i}", f"b-{i}")
    write("user", "value 1", "b-1")  # a new build, before its first pass
    write("kazma", "value 7", "b-7")
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 3)
    cut = _sync(world)
    assert cut["keys"]["state"] == "transitional" and cut["keys"]["stamped"] == 3, cut["keys"]
    assert not _backend().keyed()
    write("user", "value 2", "b-2")
    write("someone", "value 8", "b-8")
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 100_000)
    done = _sync(world)
    assert done["keys"]["state"] == "keyed", done["keys"]
    write("user", "value 3", "b-3")
    write("other", "value 9", "b-9")
    assert _all_keyed(world.neo)
    assert max(names().values()) == 1


def test_negative_control_switching_before_the_backfill_duplicates(world):
    from kazma_core.memory import graph_backend

    _old_write(world.neo.session, "user", "lives_in", "Kuwait", "b-1")
    world.neo.constraint = True
    _backend().set_keyed(True)  # what keying before every node has its key would do
    assert graph_backend.upsert_belief_edge(subject="user", predicate="likes", obj="tea", belief_id="b-2")
    assert world.neo.per_name()["user"] == 2, "a second 'user' node: the keyless one is not found by key"


def test_an_old_writer_after_the_switch_converges_in_one_pass(world):
    from kazma_core.memory import graph_backend

    _fact(world, "user", "lives_in", "Kuwait")
    assert _sync(world)["keys"]["state"] == "keyed"
    _row(world, "b-fresh", "user", "noted", "fresh")
    _row(world, "b-mention", "kazma", "mentions", "fresh")
    world.conn.execute("DELETE FROM memory_graph_outbox")
    world.conn.commit()
    # An older build of this install writes one edge (a node "fresh" with no
    # key), another install's older build writes an edge to "a place".
    _old_write(world.neo.session, "user", "noted", "fresh", "b-fresh")
    _old_write(world.neo.session, "owner", "visited", "a place", "b-theirs", install="install-b")
    theirs = next(rid for rid, r in world.neo.rels.items() if r["type"] == "VISITED")
    before = copy.deepcopy(world.neo.rels[theirs])
    _row(world, "b-place", "user", "noted", "a place")
    world.conn.execute("DELETE FROM memory_graph_outbox")
    world.conn.commit()
    # This build writes by key: "fresh" and "a place" get keyed twins.
    for subject, predicate, obj, bid in (("kazma", "mentions", "fresh", "b-mention"),
                                         ("user", "noted", "a place", "b-place")):
        assert graph_backend.upsert_belief_edge(subject=subject, predicate=predicate, obj=obj, belief_id=bid)
    assert world.neo.per_name()["fresh"] == 2 and world.neo.per_name()["a place"] == 2

    report = _sync(world)

    assert report["keys"]["state"] == "keyed", report["keys"]
    assert world.neo.per_name()["fresh"] == 1, "this install's edge moved to the keyed node, the twin gone"
    fresh = world.neo.entity("fresh", "default")
    assert world.neo.nodes[fresh]["props"]["key"]
    assert {r["props"]["belief_id"] for r in world.neo.rels.values() if r["end"] == fresh} == {"b-fresh", "b-mention"}
    assert world.neo.rels[theirs] == before, "another install's edge stays where it was"
    assert world.neo.per_name()["a place"] == 2 and report["keys"]["held_elsewhere"] == 1
    assert _sync(world)["keys"]["state"] == "keyed", "a twin another install holds does not hold up the switch"


def test_of_two_edges_at_one_key_the_one_between_keyed_nodes_stands():
    from kazma_core.memory.graph_sync import Held, Want, _plan_changes

    key = ("user", "NOTED", "fresh")
    wants = {key: Want("b-1", "noted", 0.7, 1.0)}
    at_twin = Held("e-twin", "b-1", key, 0.7, "noted", "install-a", keyed=False)
    at_keyed = Held("e-keyed", "b-1", key, 0.7, "noted", "install-a", keyed=True)
    plan = _plan_changes(wants, [at_twin, at_keyed], install="install-a", known={"b-1"})
    assert [h.eid for h in plan.deletes] == ["e-twin"] and plan.in_sync == 1
    # Negative control: told nothing of keys (as edges were read before), the
    # first read stands -- here the twin's, and the keyed node's edge goes.
    blind = [dataclasses.replace(at_twin, keyed=True), at_keyed]
    old = _plan_changes(wants, blind, install="install-a", known={"b-1"})
    assert [h.eid for h in old.deletes] == ["e-keyed"]


def test_shared_keys_written_without_the_constraint_are_repaired(world):
    from kazma_core.memory.graph_backend import entity_key

    _graph_store(world, "sqlite")
    _row(world, "b-1", "user", "lives_in", "Kuwait")
    _row(world, "b-2", "kazma", "serves", "Kuwait")
    _graph_store(world, "neo4j")
    key = entity_key("Kuwait")
    user = world.neo.add_node("Entity", name="user", tenant_id="default", key=entity_key("user"))
    kazma = world.neo.add_node("Entity", name="kazma", tenant_id="default", key=entity_key("kazma"))
    copies = [world.neo.add_node("Entity", name="Kuwait", tenant_id="default", key=key) for _ in range(3)]
    world.neo.add_rel(user, copies[0], "LIVES_IN", belief_id="b-1", install="install-a", confidence=0.7,
                      predicate="lives_in")
    world.neo.add_rel(kazma, copies[1], "SERVES", belief_id="b-2", install="install-a", confidence=0.7,
                      predicate="serves")  # copies[2] has no edge

    report = _sync(world)

    assert report["keys"]["unstamped"] == 2 and world.neo.constraint
    assert world.neo.per_name()["Kuwait"] == 1
    assert world.neo.facts() == {("user", "LIVES_IN", "Kuwait"), ("kazma", "SERVES", "Kuwait")}
    assert report["keys"]["state"] == "keyed"


def test_a_constraint_neo4j_refuses_keeps_writes_finding_nodes_by_name(world):
    from kazma_core.memory import graph_backend
    from kazma_core.memory.graph_sync import graph_copy_status
    from kazma_core.memory.health import _graph_copy_component

    world.neo.refuse_constraint = True
    _fact(world, "user", "lives_in", "Kuwait")
    report = _sync(world)
    assert report["keys"]["state"] == "transitional" and report["keys"]["constraint"] is False
    assert report["status"] == "in_sync", "the copy itself is whole; only slower to write"
    start = len(world.neo.statements)
    graph_backend.upsert_belief_edge(subject="user", predicate="likes", obj="tea", belief_id="b-t")
    assert _statements_since(world.neo, start) == ["upsert_edges_by_name"]
    comp = _graph_copy_component(graph_copy_status(world.conn), {}, _backend)
    assert "did not take the uniqueness constraint" in comp["detail"] and "refused" in comp["detail"]
    world.neo.refuse_constraint = False
    assert _sync(world)["keys"]["state"] == "keyed"


def test_a_pass_that_cannot_finish_drops_the_copy_back_to_transitional(world, monkeypatch, caplog):
    from kazma_core.memory import graph_sync

    _fact(world, "user", "lives_in", "Kuwait")
    assert _sync(world)["keys"]["state"] == "keyed"
    caplog.set_level(logging.INFO, logger=_SYNC_LOGGER)
    world.neo.down = True
    assert _sync(world)["status"] == "unreachable"
    assert _keys_record()["state"] == "transitional" and not _backend().keyed()
    warnings = [r for r in caplog.records if r.name == _SYNC_LOGGER and r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "does not answer" in warnings[0].getMessage(), "an outage says it once"
    world.neo.down = False
    assert _sync(world)["keys"]["state"] == "keyed"
    for i in range(5):
        _old_write(world.neo.session, "user", "noted", f"old {i}", f"b-old-{i}", install="install-b")
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 2)
    cut = _sync(world)
    assert cut["keys"]["state"] == "transitional" and cut["keys"]["why"] == "more nodes than a pass keys"
    assert any("not keyed now" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 100_000)
    assert _sync(world)["keys"]["state"] == "keyed"


def test_a_key_neo4j_refuses_keys_the_rest_and_the_pass_goes_on(world):
    for value in ("Kuwait", "refused", "tea"):
        _old_write(world.neo.session, "user", "noted", value, f"b-{value}", install="install-b")
    world.neo.refuse_values = {"refused"}

    report = _sync(world)

    assert report["status"] == "in_sync" and report["keys"]["state"] == "transitional", report
    assert report["keys"]["why"] == "Neo4j refused a node's key" and report["keys"]["stamped"] == 3
    assert [n["props"]["name"] for n in world.neo.nodes.values() if not n["props"].get("key")] == ["refused"]
    world.neo.refuse_values = set()
    assert _sync(world)["keys"]["state"] == "keyed"


def test_tenantless_nodes_and_foreign_edges_are_left_as_they_were(world):
    from kazma_core.memory import graph_backend

    _fact(world, "user", "lives_in", "Kuwait")
    planted = _foreign(world.neo)  # edges Kazma did not write, or another install did
    loose = [world.neo.add_node("Entity", name="no tenant"), world.neo.add_node("Entity", name="Kuwait"),
             world.neo.add_node("Topic", name="maps", tenant_id="default")]
    odd = world.neo.add_node("Entity", name=42, tenant_id="default")  # not a string: never keyed
    planted["at_an_unkeyable_node"] = world.neo.add_rel(odd, world.neo.entity("owner", "default"), "CITES")
    before_rels = {rid: copy.deepcopy(world.neo.rels[rid]) for rid in planted.values()}
    before_nodes = {nid: copy.deepcopy(world.neo.nodes[nid]) for nid in [*loose, odd]}

    _sync(world)
    graph_backend.upsert_belief_edge(subject="user", predicate="likes", obj="tea", belief_id="b-t")
    report = _sync(world, full=True)

    assert report["keys"]["state"] == "keyed" and report["keys"]["unkeyable"] == 1
    for name, rid in planted.items():
        assert world.neo.rels.get(rid) == before_rels[rid], name
    for nid, node in before_nodes.items():
        assert world.neo.nodes.get(nid) == node


def test_health_says_whether_nodes_are_found_by_key(world, monkeypatch):
    from kazma_core.memory import graph_sync
    from kazma_core.memory.graph_sync import graph_copy_status
    from kazma_core.memory.health import _graph_copy_component

    for i in range(4):
        _old_write(world.neo.session, "user", "noted", f"v{i}", f"b-{i}", install="install-b")
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 2)
    _sync(world)
    status = graph_copy_status(world.conn)
    assert status["keys"]["state"] == "transitional" and status["keys"]["keyless"] == 3
    detail = _graph_copy_component(status, {}, _backend)["detail"]
    assert "3 nodes still to give a key" in detail and "by name" in detail
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 100_000)
    _sync(world)
    status = graph_copy_status(world.conn)
    assert status["keys"] == {"state": "keyed", "keyless": 0, "held_elsewhere": 0, "unkeyable": 0,
                              "constraint": True, "why": ""}
    assert "found by key" in _graph_copy_component(status, {}, _backend)["detail"]


def test_a_long_value_is_found_by_its_digest(world):
    from kazma_core.memory.graph_backend import entity_key

    value = "\U0001F600" * 4000  # 4,000 characters, 16,000 bytes of UTF-8
    bid = _fact(world, "user", "noted", value)
    _sync(world)
    node = world.neo.nodes[world.neo.entity(value, "default")]
    assert node["props"]["key"] == entity_key(value) and len(node["props"]["key"]) == 64
    assert ("user", "NOTED", value, bid, "install-a") in world.neo.edges()
    assert _backend().keyed()
    near = _backend().neighbors(value)
    assert [(n["subject"], n["object"]) for n in near] == [(value, "user")]


def test_once_keyed_the_dual_writes_delete_and_read_by_key(world):
    from kazma_core.memory import graph_backend

    _fact(world, "user", "lives_in", "Kuwait")
    _sync(world)
    start = len(world.neo.statements)
    assert graph_backend.delete_belief_edge(subject="user", predicate="lives_in", obj="Kuwait")
    assert _backend().neighbors("user") == []
    seen = world.neo.statements[start:]
    assert seen[0].startswith("MATCH (a:Entity {tenant_id: $tid, key: $sk})"), seen
    assert "MATCH (a:Entity {tenant_id: $tid, key: $key})-[r]-(b:Entity)" in seen[1]
    assert world.neo.facts() == set()


# ── the same on a real Neo4j (opt-in) ───────────────────────────────────

_REAL_URL = os.environ.get("KAZMA_TEST_NEO4J_URL", "").strip()


@pytest.fixture()
def real(tmp_path, monkeypatch):
    if not _REAL_URL:
        pytest.skip("KAZMA_TEST_NEO4J_URL is not set (a throwaway Neo4j server)")
    host_port = _REAL_URL.split("://", 1)[-1].rstrip("/").lower()
    if host_port in ("127.0.0.1:7687", "localhost:7687") and os.environ.get("KAZMA_TEST_NEO4J_THROWAWAY") != "1":
        pytest.skip("this machine's 7687 may be the install's own Neo4j: set KAZMA_TEST_NEO4J_THROWAWAY=1 "
                    "if it is a throwaway one")
    pytest.importorskip("neo4j")
    from kazma_core.memory import backends, graph_backend, graph_sync
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    state = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    cfg = copy.deepcopy(backends.DEFAULT_BACKENDS_CFG)
    cfg["graph"] = {"provider": "neo4j", "url": _REAL_URL,
                    "user": os.environ.get("KAZMA_TEST_NEO4J_USER", "neo4j"),
                    "password": os.environ.get("KAZMA_TEST_NEO4J_PASSWORD", "")}
    monkeypatch.setattr(backends, "get_backends_cfg", lambda: cfg)
    install = f"test-{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(graph_backend, "this_install", lambda: install)
    monkeypatch.setattr(graph_sync, "_STATE", {})
    graph_backend.reset_graph_backend_cache()
    _forget_findings()
    be, state_name, _target = graph_backend.neo4j_target()
    if not be.reachable()[0]:
        pytest.fail(f"KAZMA_TEST_NEO4J_URL={_REAL_URL} is set but the server does not answer")
    tenant = f"kazma-test-{uuid.uuid4().hex[:12]}"
    conn = sqlite3.connect(str(state), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    yield SimpleNamespace(conn=conn, be=be, tenant=tenant, install=install)
    _cypher(be, "MATCH (n:Entity {tenant_id: $tid}) DETACH DELETE n", tid=tenant)
    graph_backend.reset_graph_backend_cache()
    _forget_findings()
    conn.close()


def _cypher(be, query: str, **params: Any) -> list[dict]:
    with be._get_driver().session() as session:
        return [record.data() for record in session.run(query, **params)]


def _real_edges(be, tenant: str) -> set[tuple]:
    rows = _cypher(be, "MATCH (a:Entity {tenant_id: $tid})-[r]->(b:Entity {tenant_id: $tid}) "
                       "RETURN a.name AS a, type(r) AS t, b.name AS b, r.belief_id AS bid, r.install AS inst",
                   tid=tenant)
    return {(r["a"], r["t"], r["b"], r["bid"], r["inst"]) for r in rows}


def test_real_server_converges_and_leaves_what_is_not_its_own(real):
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.forget import forget_mentions
    from kazma_core.memory.graph_sync import run_graph_sync

    t = real.tenant
    keep = mutate_belief(real.conn, "user", "2fa_enabled", "true", tenant_id=t)["belief_id"]
    code = mutate_belief(real.conn, "user", "test_codeword", "BLUE-HERON-42", tenant_id=t)["belief_id"]
    _cypher(real.be, "MERGE (a:Entity {name: 'owner', tenant_id: $tid}) MERGE (b:Entity {name: 'notes', tenant_id: $tid}) "
                     "MERGE (a)-[:WROTE]->(b)", tid=t)
    _cypher(real.be, "MERGE (a:Entity {name: 'owner', tenant_id: $tid}) MERGE (b:Entity {name: 'Kuwait', tenant_id: $tid}) "
                     "MERGE (a)-[r:VISITED]->(b) SET r.belief_id = 'b-elsewhere', r.install = 'another-install'", tid=t)
    _cypher(real.be, "MERGE (a:Entity {name: 'user', tenant_id: $tid}) MERGE (b:Entity {name: 'stale', tenant_id: $tid}) "
                     "MERGE (a)-[r:NOTED]->(b) SET r.belief_id = 'b-stale', r.install = $inst", tid=t, inst=real.install)
    _cypher(real.be, "MERGE (n:Entity {name: 'alone', tenant_id: $tid})", tid=t)

    report = run_graph_sync(real.conn, full=True)
    assert report["status"] == "in_sync", report
    edges = _real_edges(real.be, t)
    assert ("user", "2FA_ENABLED", "true", keep, real.install) in edges
    assert ("user", "TEST_CODEWORD", "BLUE-HERON-42", code, real.install) in edges
    assert ("owner", "WROTE", "notes", None, None) in edges
    assert ("owner", "VISITED", "Kuwait", "b-elsewhere", "another-install") in edges
    assert not any(e[3] == "b-stale" for e in edges)
    names = {r["n"] for r in _cypher(real.be, "MATCH (n:Entity {tenant_id: $tid}) RETURN n.name AS n", tid=t)}
    assert "stale" not in names and "alone" not in names

    forget_mentions("BLUE-HERON-42", tenant_id=t)
    run_graph_sync(real.conn)
    names = {r["n"] for r in _cypher(real.be, "MATCH (n:Entity {tenant_id: $tid}) RETURN n.name AS n", tid=t)}
    assert "BLUE-HERON-42" not in names and "true" in names


def test_real_server_runs_every_statement_the_sync_sends(real, monkeypatch):
    """Every ``// kazma:<op>`` statement of ``graph_backend`` runs against the
    real server at least once (each was written against the in-memory stand-in
    first): passes while the copy is not keyed yet (a node it may not key this
    pass), a fact written and then superseded once it is, each followed by an
    ordinary pass, a full comparison that meets an isolated node, and keys
    two nodes share (written with the constraint dropped: this test drops
    and remakes ``kazma_entity_key``, as a pass does). The copy ends holding
    the current value only, one node per name."""
    from neo4j import Session

    from kazma_core.memory import graph_backend, graph_sync
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.graph_sync import run_graph_sync

    seen: set[str] = set()
    original = Session.run

    def recording(self, query, *args, **kwargs):
        match = re.match(r"\s*// kazma:(\w+)", str(query))
        if match:
            seen.add(match.group(1))
        return original(self, query, *args, **kwargs)

    monkeypatch.setattr(Session, "run", recording)
    t = real.tenant
    session = real.be._get_driver().session
    # Not keyed yet: a pass may key no node, writes find nodes by name.
    _old_write(session, "kazma", "noted", "keyless", "b-keyless", tenant=t, install="another-install")
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 0)
    run_graph_sync(real.conn, full=True)
    first = mutate_belief(real.conn, "user", "favorite_color", "blue", tenant_id=t)["belief_id"]
    assert run_graph_sync(real.conn)["keys"]["state"] == "transitional"
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 100_000)
    assert run_graph_sync(real.conn)["keys"]["state"] == "keyed"
    second = mutate_belief(real.conn, "user", "favorite_color", "green", tenant_id=t)["belief_id"]
    run_graph_sync(real.conn)
    _cypher(real.be, "MERGE (n:Entity {name: 'stray', tenant_id: $tid})", tid=t)
    # An edge of this install whose fact is gone (written while a forget's
    # delete could not reach the server): the comparison deletes it.
    _cypher(real.be, "MERGE (a:Entity {name: 'user', tenant_id: $tid}) MERGE (b:Entity {name: 'gone', tenant_id: $tid}) "
                     "MERGE (a)-[r:NOTED]->(b) SET r.belief_id = 'b-gone', r.install = $inst", tid=t, inst=real.install)
    report = run_graph_sync(real.conn, full=True)
    assert report["status"] == "in_sync", report
    # Keys two nodes share: two more 'green' nodes with its key, one holding
    # a copy of this install's edge, one none.
    key = graph_backend.entity_key("green")
    try:
        _cypher(real.be, f"DROP CONSTRAINT {graph_backend.KEY_CONSTRAINT} IF EXISTS")
        _cypher(real.be, "MATCH (u:Entity {name: 'user', tenant_id: $tid}) "
                         "CREATE (u)-[:FAVORITE_COLOR {belief_id: $bid, install: $inst, confidence: 0.5, "
                         "predicate: 'favorite_color'}]->(:Entity {name: 'green', tenant_id: $tid, key: $key}) "
                         "CREATE (:Entity {name: 'green', tenant_id: $tid, key: $key})",
                tid=t, bid=second, inst=real.install, key=key)
        repaired = run_graph_sync(real.conn)
    finally:
        real.be.ensure_key_constraint()
    assert repaired["keys"]["unstamped"] == 2 and repaired["keys"]["state"] == "keyed", repaired["keys"]

    statements = set(re.findall(r"// kazma:(\w+)", Path(graph_backend.__file__).read_text(encoding="utf-8")))
    assert statements and statements <= seen, f"never run on a real server: {sorted(statements - seen)}"
    edges = {e for e in _real_edges(real.be, t) if e[0] == "user"}
    assert edges == {("user", "FAVORITE_COLOR", "green", second, real.install)}, (edges, first)
    names = {r["n"] for r in _cypher(real.be, "MATCH (n:Entity {tenant_id: $tid}) RETURN n.name AS n", tid=t)}
    assert "blue" not in names and "stray" not in names and "gone" not in names
    assert _real_per_name(real.be, t)["green"] == 1


# ── node keys on a real Neo4j (opt-in) ──────────────────────────────────


def _real_per_name(be, tenant: str) -> dict[str, int]:
    rows = _cypher(be, "MATCH (n:Entity {tenant_id: $tid}) RETURN n.name AS n, count(*) AS c", tid=tenant)
    return {r["n"]: r["c"] for r in rows}


def _plan_ops(be, query: str, **params: Any) -> list[str]:
    """The operators of the plan Neo4j makes for *query* (EXPLAIN: nothing runs)."""
    with be._get_driver().session() as session:
        plan = session.run("EXPLAIN " + query, **params).consume().plan
    out: list[str] = []

    def walk(op: dict) -> None:
        out.append(str(op["operatorType"]).split("@")[0])
        for child in op.get("children") or []:
            walk(child)

    walk(plan)
    return out


def test_real_server_keys_a_copy_written_before_keys_and_then_seeks_by_index(real):
    from kazma_core.memory import graph_backend
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.graph_sync import run_graph_sync

    t = real.tenant
    mutate_belief(real.conn, "user", "lives_in", "Kuwait", tenant_id=t)
    _old_write(real.be._get_driver().session, "user", "likes", "tea", "b-tea", tenant=t, install=real.install)
    keyless = _cypher(real.be, "MATCH (n:Entity {tenant_id: $tid}) WHERE n.key IS NULL RETURN n.name AS n", tid=t)
    assert {r["n"] for r in keyless} == {"tea"}, "the old code: a node with no key"

    report = run_graph_sync(real.conn, full=True)

    assert report["keys"]["state"] == "keyed", report["keys"]
    rows = _cypher(real.be, "MATCH (n:Entity {tenant_id: $tid}) RETURN n.name AS n, n.key AS k", tid=t)
    assert rows and all(r["k"] == graph_backend.entity_key(r["n"]) for r in rows)
    row = graph_backend.edge_row("user", "noted", "x", "b", 0.5)
    for query, params in (
        (graph_backend._UPSERT_EDGES_BY_KEY % "`NOTED`", {"rows": [row], "tid": t, "inst": "i"}),
        (graph_backend._EDGES_AT_NODE_KEYS, {"keys": [{"sk": row["sk"], "rel": "NOTED", "ok": row["ok"]}], "tid": t}),
        ("MATCH (a:Entity {tenant_id: $tid, key: $key})-[r]-(b:Entity) RETURN a.name", {"tid": t, "key": row["sk"]}),
        (graph_backend._STAMP_KEYS, {"rows": []}),
    ):
        ops = _plan_ops(real.be, query, **params)
        assert any(op.startswith("NodeUniqueIndexSeek") for op in ops) and "NodeByLabelScan" not in ops, (query, ops)
    # Negative control: the write by name, the only one there was, scans the label.
    ops = _plan_ops(real.be, graph_backend._UPSERT_EDGES_BY_NAME % "`NOTED`", rows=[row], tid=t, inst="i")
    assert "NodeByLabelScan" in ops and not any(op.startswith("NodeUniqueIndexSeek") for op in ops)
    constraints = _cypher(real.be, "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
                                   "WHERE name = $n RETURN type, labelsOrTypes, properties",
                          n=graph_backend.KEY_CONSTRAINT)
    assert constraints == [{"type": "UNIQUENESS", "labelsOrTypes": ["Entity"], "properties": ["tenant_id", "key"]}]


def test_real_server_a_pass_over_a_keyed_copy_draws_no_notice(real, caplog):
    """The driver logs every notice Neo4j sends; ``CREATE CONSTRAINT ... IF
    NOT EXISTS`` over a constraint that is there sends one, and a pass that
    made it every 15 minutes would put that line in the log each time."""
    from kazma_core.memory import graph_backend
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.graph_sync import run_graph_sync

    mutate_belief(real.conn, "user", "lives_in", "Kuwait", tenant_id=real.tenant)
    assert run_graph_sync(real.conn, full=True)["keys"]["state"] == "keyed"
    caplog.set_level(logging.INFO, logger="neo4j.notifications")
    caplog.clear()
    assert run_graph_sync(real.conn, full=True)["keys"]["state"] == "keyed"
    assert [r.getMessage()[:120] for r in caplog.records if r.name == "neo4j.notifications"] == []
    # Negative control: the statement alone, over the constraint that is there.
    _cypher(real.be, graph_backend._KEY_CONSTRAINT_DDL)
    assert any("already exists" in r.getMessage() for r in caplog.records if r.name == "neo4j.notifications")


def test_real_server_one_node_per_name_through_every_phase(real, monkeypatch):
    from kazma_core.memory import graph_backend, graph_sync
    from kazma_core.memory.graph_sync import run_graph_sync

    t = real.tenant
    session = real.be._get_driver().session

    def write(subject: str, obj: str, bid: str) -> None:
        assert graph_backend.upsert_belief_edge(subject=subject, predicate="noted", obj=obj, belief_id=bid,
                                                tenant_id=t)
        assert max(_real_per_name(real.be, t).values()) == 1, _real_per_name(real.be, t)

    for i in range(6):
        _row(real, f"b-{i}", "user", "noted", f"value {i}", tenant=t)
        _old_write(session, "user", "noted", f"value {i}", f"b-{i}", tenant=t, install=real.install)
    write("user", "value 1", "b-1")
    write("kazma", "value 7", "b-7")
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 3)
    assert run_graph_sync(real.conn)["keys"]["state"] == "transitional"
    write("user", "value 2", "b-2")
    write("someone", "value 8", "b-8")
    monkeypatch.setattr(graph_sync, "KEY_STAMPS_PER_PASS", 100_000)
    assert run_graph_sync(real.conn)["keys"]["state"] == "keyed"
    write("user", "value 3", "b-3")
    write("other", "value 9", "b-9")
    rows = _cypher(real.be, "MATCH (n:Entity {tenant_id: $tid}) RETURN n.name AS n, n.key AS k", tid=t)
    assert all(r["k"] == graph_backend.entity_key(r["n"]) for r in rows)


def test_real_server_negative_control_switching_before_the_backfill_duplicates(real):
    from kazma_core.memory import graph_backend

    t = real.tenant
    _old_write(real.be._get_driver().session, "user", "lives_in", "Kuwait", "b-1", tenant=t, install=real.install)
    real.be.ensure_key_constraint()
    real.be.set_keyed(True)  # keyed before every node has its key
    assert graph_backend.upsert_belief_edge(subject="user", predicate="likes", obj="tea", belief_id="b-2",
                                            tenant_id=t)
    assert _real_per_name(real.be, t)["user"] == 2


def test_real_server_an_old_writer_after_the_switch_converges_in_one_pass(real):
    from kazma_core.memory import graph_backend
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.graph_sync import run_graph_sync

    t = real.tenant
    session = real.be._get_driver().session
    mutate_belief(real.conn, "user", "lives_in", "Kuwait", tenant_id=t)
    assert run_graph_sync(real.conn, full=True)["keys"]["state"] == "keyed"
    _row(real, "b-fresh", "user", "noted", "fresh", tenant=t)
    _row(real, "b-mention", "kazma", "mentions", "fresh", tenant=t)
    _row(real, "b-place", "user", "noted", "a place", tenant=t)
    real.conn.execute("DELETE FROM memory_graph_outbox")
    real.conn.commit()
    _old_write(session, "user", "noted", "fresh", "b-fresh", tenant=t, install=real.install)
    _old_write(session, "owner", "visited", "a place", "b-theirs", tenant=t, install="another-install")
    theirs = _cypher(real.be, "MATCH (:Entity {tenant_id: $tid})-[r:VISITED]->(b:Entity) "
                              "RETURN elementId(r) AS r, properties(r) AS p, elementId(b) AS b", tid=t)
    for subject, predicate, obj, bid in (("kazma", "mentions", "fresh", "b-mention"),
                                         ("user", "noted", "a place", "b-place")):
        assert graph_backend.upsert_belief_edge(subject=subject, predicate=predicate, obj=obj, belief_id=bid,
                                                tenant_id=t)
    assert _real_per_name(real.be, t)["fresh"] == 2, "the old writer's node and this build's keyed twin"

    report = run_graph_sync(real.conn)

    assert report["keys"]["state"] == "keyed" and report["keys"]["held_elsewhere"] >= 1, report["keys"]
    assert _real_per_name(real.be, t)["fresh"] == 1
    ends = _cypher(real.be, "MATCH (:Entity {tenant_id: $tid})-[r]->(b:Entity {name: 'fresh', tenant_id: $tid}) "
                            "RETURN r.belief_id AS bid, b.key AS k", tid=t)
    assert {r["bid"] for r in ends} == {"b-fresh", "b-mention"} and all(r["k"] for r in ends)
    assert _cypher(real.be, "MATCH (:Entity {tenant_id: $tid})-[r:VISITED]->(b:Entity) "
                            "RETURN elementId(r) AS r, properties(r) AS p, elementId(b) AS b", tid=t) == theirs


def test_real_server_writes_and_reads_back_a_value_of_4000_four_byte_characters(real):
    from kazma_core.memory import graph_backend
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.graph_sync import run_graph_sync

    t = real.tenant
    value = "\U0001F600" * 4000
    assert len(value.encode("utf-8")) == 16_000
    bid = mutate_belief(real.conn, "user", "noted", value, tenant_id=t)["belief_id"]
    assert run_graph_sync(real.conn, full=True)["keys"]["state"] == "keyed"
    rows = _cypher(real.be, "MATCH (n:Entity {tenant_id: $tid, key: $k}) RETURN n.name AS n", tid=t,
                   k=graph_backend.entity_key(value))
    assert rows == [{"n": value}]
    assert ("user", "NOTED", value, bid, real.install) in _real_edges(real.be, t)
    near = real.be.neighbors(value, tenant_id=t)
    assert [(n["subject"], n["object"], n["id"]) for n in near] == [(value, "user", bid)]


def test_real_server_negative_control_an_index_on_name_refuses_the_value(real):
    from neo4j.exceptions import Neo4jError

    label = f"KazmaTestNameIndex{uuid.uuid4().hex[:8]}"
    index = f"kazma_test_name_{uuid.uuid4().hex[:8]}"
    try:
        _cypher(real.be, f"CREATE INDEX {index} FOR (n:{label}) ON (n.name)")
        _cypher(real.be, "CALL db.awaitIndexes(60)")
        with pytest.raises(Neo4jError, match="too large to index"):
            _cypher(real.be, f"CREATE (:{label} {{name: $v}})", v="\U0001F600" * 4000)
    finally:
        _cypher(real.be, f"MATCH (n:{label}) DELETE n")
        _cypher(real.be, f"DROP INDEX {index} IF EXISTS")


def test_real_server_leaves_foreign_edges_and_tenantless_nodes_byte_identical(real):
    from kazma_core.memory import graph_backend
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.graph_sync import run_graph_sync

    t = real.tenant
    marker = f"kazma-test-{uuid.uuid4().hex[:8]}"
    _cypher(real.be, "MERGE (a:Entity {name: 'owner', tenant_id: $tid}) MERGE (b:Entity {name: 'notes', tenant_id: $tid}) "
                     "MERGE (c:Entity {name: 'Kuwait', tenant_id: $tid}) "
                     "CREATE (a)-[:WROTE {note: 'by hand'}]->(b) "
                     "CREATE (a)-[:VISITED {belief_id: 'b-elsewhere', install: 'another-install'}]->(c) "
                     "CREATE (:Entity {name: 'no tenant', marker: $m}) CREATE (:Entity {name: 'Kuwait', marker: $m}) "
                     "CREATE (:Topic {name: 'maps', tenant_id: $tid, marker: $m})", tid=t, m=marker)

    def foreign() -> list[dict]:
        return _cypher(real.be, "MATCH (a:Entity {tenant_id: $tid})-[r]->(b) "
                                "WHERE coalesce(r.belief_id, '') = '' OR r.install <> $inst "
                                "RETURN elementId(r) AS r, type(r) AS t, properties(r) AS p, "
                                "elementId(a) AS a, elementId(b) AS b ORDER BY r", tid=t, inst=real.install)

    def tenantless() -> list[dict]:
        return _cypher(real.be, "MATCH (n) WHERE n.tenant_id IS NULL OR n.marker = $m "
                                "RETURN elementId(n) AS e, labels(n) AS l, properties(n) AS p ORDER BY e", m=marker)

    before = (foreign(), tenantless())
    assert len(before[0]) == 2 and len(before[1]) >= 3
    try:
        mutate_belief(real.conn, "user", "lives_in", "Kuwait", tenant_id=t)
        run_graph_sync(real.conn, full=True)
        graph_backend.upsert_belief_edge(subject="owner", predicate="likes", obj="maps", belief_id="b-maps",
                                         tenant_id=t)
        run_graph_sync(real.conn, full=True)
        assert (foreign(), tenantless()) == before
    finally:
        _cypher(real.be, "MATCH (n {marker: $m}) DETACH DELETE n", m=marker)


def _race(be, statement: str, params: dict[str, Any], *, hold_s: float = 2.0) -> list[BaseException]:
    """Run *statement* in two transactions at once: each commits only after
    both have run it, or after *hold_s* (a transaction blocked behind the
    other's lock cannot run it until that one commits)."""
    import threading

    barrier = threading.Barrier(2, timeout=hold_s)
    errors: list[BaseException] = []

    def one() -> None:
        with be._get_driver().session() as session:
            tx = session.begin_transaction()
            try:
                tx.run(statement, **params).consume()
            except Exception as exc:  # the constraint refusing the second writer
                errors.append(exc)
                tx.close()
                return
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            tx.commit()

    threads = [threading.Thread(target=one) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)
    return errors


def test_real_server_two_writers_of_a_new_name_make_one_node(real):
    from kazma_core.memory import graph_backend
    from neo4j.exceptions import ConstraintError

    t = real.tenant
    real.be.ensure_key_constraint()

    def count(name: str, label: str = "Entity") -> int:
        return _cypher(real.be, f"MATCH (n:{label} {{tenant_id: $tid, name: $n}}) RETURN count(n) AS c",
                       tid=t, n=name)[0]["c"]

    def params(name: str) -> dict[str, Any]:
        return {"rows": [graph_backend.edge_row("user", "noted", name, "b-race", 0.5)], "tid": t, "inst": real.install}

    assert _race(real.be, graph_backend._UPSERT_EDGES_BY_KEY % "`NOTED`", params("by key")) == []
    assert count("by key") == 1, "the second writer waited for the first and found its node"
    errors = _race(real.be, graph_backend._UPSERT_EDGES_BY_NAME % "`NOTED`", params("by name"))
    assert count("by name") == 1 and len(errors) == 1 and isinstance(errors[0], ConstraintError), errors
    # Negative controls: the write before keys made two; so does the keyed
    # write with a plain index in place of the constraint.
    assert _race(real.be, _OLD_UPSERT % "`NOTED`", params("old")) == []
    assert count("old") == 2
    label = f"KazmaTestPlain{uuid.uuid4().hex[:8]}"
    index = f"kazma_test_plain_{uuid.uuid4().hex[:8]}"
    try:
        _cypher(real.be, f"CREATE INDEX {index} FOR (n:{label}) ON (n.tenant_id, n.key)")
        _cypher(real.be, "CALL db.awaitIndexes(60)")
        plain = (graph_backend._UPSERT_EDGES_BY_KEY % "`NOTED`").replace(":Entity", f":{label}")
        assert _race(real.be, plain, params("plain index")) == []
        assert count("plain index", label) == 2
    finally:
        _cypher(real.be, f"MATCH (n:{label}) DETACH DELETE n")
        _cypher(real.be, f"DROP INDEX {index} IF EXISTS")
