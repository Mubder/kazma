"""The belief-graph export is well-formed whatever a fact holds, and says
when it was cut (Memory audit M33, MEM-S09).

``saxutils.escape`` left ``"`` in the GraphML ``id``/``source``/``target``
attributes: any fact holding a quote made the file unreadable. Both formats
cut at ``limit`` (at most 2,000) without saying so.
"""

from __future__ import annotations

import json
import sqlite3
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest

TRICKY = ['said "hi" x="1', "fish & chips", "a < b > c", "اسمي سارة", "tab\there"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_ui.routes_direct import register_direct_routes

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.tenant_isolation.multi_user_or_production", lambda: False)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    now = time.time()
    db.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "structural_importance, source_trust_weight, extraction_method, valid_from, ingested_at) "
        "VALUES (?, 'default', ?, 'noted', 'set', ?, 0.9, 3, 1.0, 'user_explicit', ?, ?)",
        [(f"b{i}", f"user{i}", obj, now, now) for i, obj in enumerate(TRICKY)])
    db.commit()
    db.close()
    app = FastAPI()
    register_direct_routes(SimpleNamespace(app=app))
    return SimpleNamespace(http=TestClient(app), dir=tmp_path)


def test_graphml_parses_with_quotes_ampersands_angles_and_arabic(client):
    r = client.http.get("/api/memory/v2/graph/export?format=graphml")
    assert r.status_code == 200 and r.headers["X-Kazma-Truncated"] == "false"
    root = ET.fromstring(r.text)
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    ids = {n.get("id") for n in root.iterfind(".//g:node", ns)}
    assert set(TRICKY) <= ids
    truncated = root.find(".//g:graph/g:data[@key='truncated']", ns)
    assert truncated is not None and truncated.text == "false"


def test_json_parses_and_says_whether_it_was_cut(client):
    r = client.http.get("/api/memory/v2/graph/export?format=json&limit=10")
    d = json.loads(r.text)
    assert d["truncated"] is False and {n["id"] for n in d["nodes"]} >= set(TRICKY)


def test_a_cut_graph_says_so(client):
    db = sqlite3.connect(str(client.dir / "memory_state.db"))
    now = time.time()
    db.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "structural_importance, source_trust_weight, extraction_method, valid_from, ingested_at) "
        "VALUES (?, 'default', 'user', 'likes', 'set', ?, 0.5, 1, 1.0, 'user_explicit', ?, ?)",
        [(f"x{i}", f"thing {i}", now, now) for i in range(20)])
    db.commit()
    db.close()
    r = client.http.get("/api/memory/v2/graph/export?format=json&limit=10")
    assert json.loads(r.text)["truncated"] is True and r.headers["X-Kazma-Truncated"] == "true"
    g = client.http.get("/api/memory/v2/graph/export?format=graphml&limit=10")
    assert g.headers["X-Kazma-Truncated"] == "true"
    ET.fromstring(g.text)


def test_saxutils_escape_left_the_quote():
    """Negative control: the old escaping inside an attribute."""
    with pytest.raises(ET.ParseError):
        ET.fromstring(f'<node id="{escape(TRICKY[0])}"/>')
