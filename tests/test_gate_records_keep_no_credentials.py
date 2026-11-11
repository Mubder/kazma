"""An approval record keeps a call as a card shows it, never its credentials.

The gate registry (``kazma_core.safety.hitl_gates``, ``hitl_gates.db``) kept
every call's arguments raw in ``args_json`` -- and again inside
``payload_json`` and the card's ``message`` -- an ``access_token``, a
``vault_store`` value, a ``git clone https://user:<token>@...`` URL, for the
90 days a decided gate is kept and in every backup of the file. The cards
already hid them (``security.approval_args``).

Now ``register_gate``, the one writer of a row's call, stores the arguments as
a card shows them, the payload with every call's arguments masked, the message
through the same redactor, and ``args_digest``: the hash of the REAL
arguments, which the swarm bus card and the Swarm page show, and which twin
matching compares (two calls that differ only in a hidden secret are two
calls). Rows an older build wrote are redacted once when the schema opens.

Each writer is driven for real and the raw SQLite row read back; the negative
controls put the old storage back and the secret is in the row.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
import time
from pathlib import Path

import pytest

from kazma_core.safety import hitl_gates as hg
from kazma_core.security.approval_args import HIDDEN, arguments_digest

REPO = Path(__file__).resolve().parents[1]

#: A planted fake secret, built at run time.
SECRET = "plant" + "ed-" + "q" * 14

#: A graph call whose arguments hold the secret four ways.
ARGS = {
    "url": f"https://user:{SECRET}@example.com/repo.git",
    "access_token": SECRET,
    "headers": {"X-Api": SECRET},
    "path": "notes/plan.md",
}


@pytest.fixture
def gate_db(tmp_path):
    path = tmp_path / "gates.db"
    hg.set_db_path_for_tests(str(path))
    hg.ensure_gate_schema()
    yield path
    hg.set_db_path_for_tests(None)


def _raw_rows(path: Path) -> list[dict]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM hitl_gates")]
    finally:
        conn.close()


def _assert_no_secret(path: Path) -> list[dict]:
    rows = _raw_rows(path)
    assert rows, "the writer registered nothing"
    for row in rows:
        for column, value in row.items():
            assert SECRET not in str(value), (column, value)
    return rows


def _graph_payload(iid: str = "intr-1", args: dict | None = None) -> dict:
    from kazma_core.agent.graph_helpers import _format_hitl_message

    call = dict(args if args is not None else ARGS)
    return {
        "type": "hitl_approval",
        "kind": "security",
        "thread_id": "t1",
        "interrupt_id": iid,
        "tool": "http_request",
        "args": call,
        "tools": [
            {"id": "c1", "name": "http_request", "args": call},
            {"id": "c2", "name": "vault_store", "args": {"name": "k", "value": SECRET}},
        ],
        "message": _format_hitl_message("http_request", call),
        "yolo_allowed": True,
        "always_ask": False,
    }


# ── every writer, driven for real ───────────────────────────────────────────


async def _graph_writer() -> None:
    from kazma_ui.hitl_gate_bridge import gate_pending_from_payload

    await gate_pending_from_payload(_graph_payload())


async def _swarm_bus_writer(monkeypatch) -> None:
    from tests.test_swarm_bus_approvals import _Card, _middleware

    card = _Card("telegram", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter, timeout=0.3)
    args = json.dumps({"name": "k", "value": SECRET, "category": "general"})
    assert await mw.check("vault_store", args) is False  # nobody answered
    assert card.posts and SECRET not in card.posts[0]


async def _bus_bridge_writer(monkeypatch) -> None:
    from kazma_core.safety import bus_bridge as bb

    monkeypatch.setattr(bb, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.delenv("KAZMA_BUS_BRIDGE", raising=False)
    bb.record_watcher(kind="ui", detail="test")
    try:
        await bb.request_approval_via_registry(
            tool_name="http_request", tool_args=json.dumps(ARGS), timeout=0.2,
        )
    finally:
        bb._schema_ready.discard(str(hg._db_path()))


async def _pipeline_writer() -> None:
    from kazma_core.swarm.checkpoint_manager import _gate_register_pipeline

    await asyncio.to_thread(
        _gate_register_pipeline, "task-1", 1, "writer", f"step said token={SECRET}",
    )


async def _claim_create_writer() -> None:
    from kazma_ui.hitl_gate_bridge import gate_claimed

    await gate_claimed("t1", "intr-lost", "approve", "web:me", tool="http_request",
                       payload=_graph_payload("intr-lost"))


#: Every function that constructs a gate row, and the driver above that runs
#: it. The gate below finds every ``GateRow(`` in product code; a new writer
#: needs a driver here.
WRITERS = {
    ("kazma-ui/kazma_ui/hitl_gate_bridge.py", "gate_pending_from_payload"): "graph",
    ("kazma-ui/kazma_ui/hitl_gate_bridge.py", "gate_claimed"): "claim_create",
    ("kazma-core/kazma_core/swarm/safety.py", "_ask_the_bus"): "swarm_bus",
    ("kazma-core/kazma_core/safety/bus_bridge.py", "request_approval_via_registry"): "bus_bridge",
    ("kazma-core/kazma_core/swarm/checkpoint_manager.py", "_gate_register_pipeline"): "pipeline",
}
#: Functions that build a GateRow from a row the registry already holds.
READERS = {("kazma-core/kazma_core/safety/hitl_gates.py", "_row_to_gate")}


@pytest.mark.parametrize("driver", sorted(set(WRITERS.values())))
def test_no_writer_puts_a_credential_in_the_record(gate_db, monkeypatch, driver):
    monkeypatch.delenv("KAZMA_GATE_REGISTRY", raising=False)
    run = {
        "graph": lambda: _graph_writer(),
        "claim_create": lambda: _claim_create_writer(),
        "swarm_bus": lambda: _swarm_bus_writer(monkeypatch),
        "bus_bridge": lambda: _bus_bridge_writer(monkeypatch),
        "pipeline": lambda: _pipeline_writer(),
    }[driver]
    asyncio.run(run())
    _assert_no_secret(gate_db)


def test_the_record_keeps_every_name_and_the_digest_of_the_real_call(gate_db):
    asyncio.run(_graph_writer())
    row = hg.gate_for("intr-1")
    assert row is not None
    shown = row.args()
    assert set(shown) == set(ARGS)  # every name stays
    assert shown["access_token"] == HIDDEN and shown["headers"] == HIDDEN
    assert shown["path"] == "notes/plan.md"
    assert row.args_digest == arguments_digest(json.dumps(ARGS, default=str))
    payload = row.payload()
    assert payload["tools"][1]["args"]["value"] == HIDDEN  # vault_store's own secret
    assert payload["tools"][1]["args"]["name"] == "k"
    assert payload["always_ask"] is False and payload["kind"] == "security"


def test_a_pending_card_still_renders_from_the_record(gate_db):
    from kazma_gateway.agent_handler.hitl import _build_approval_prompt
    from kazma_ui.hitl_gate_bridge import gate_row_to_pending_item

    asyncio.run(_graph_writer())
    row = hg.pending_gates()[0]
    item = gate_row_to_pending_item(row)
    assert item["interrupt_id"] == "intr-1" and item["tool_name"] == "http_request"
    assert set(item["arguments"]) == set(ARGS)
    assert SECRET not in json.dumps(item)
    text = _build_approval_prompt(row.payload(), "t1", platform="telegram")["text"]
    assert "http_request" in text and "vault_store" in text and SECRET not in text


def test_twins_are_matched_by_the_digest_of_the_real_call(gate_db):
    from kazma_ui.hitl_gate_bridge import gate_pending_from_payload

    # One pause registered twice (hash id, then LangGraph's): one row.
    asyncio.run(gate_pending_from_payload(_graph_payload(iid=None)))
    asyncio.run(gate_pending_from_payload(_graph_payload(iid="intr-real")))
    assert len(hg.pending_gates()) == 1
    assert hg.gate_for("intr-real") is not None

    # Two calls that differ only in the hidden secret: two rows, although
    # what the record shows of them is the same.
    other = dict(ARGS, access_token=SECRET + "-other")
    hg.register_gate(hg.GateRow(gate_id="b1", thread_id="t2", tool="http_request",
                                args_json=json.dumps(ARGS)))
    hg.register_gate(hg.GateRow(gate_id="b2", thread_id="t2", tool="http_request",
                                args_json=json.dumps(other)))
    b1, b2 = hg.gate_for("b1"), hg.gate_for("b2")
    assert b1.state == b2.state == "pending" and b1.gate_id != b2.gate_id
    assert b1.args_json == b2.args_json, "the masked text is the same"
    assert b1.args_digest != b2.args_digest
    # Claiming one does not supersede the other as its twin.
    hg.claim_gate("b1", "approve", "web:me")
    assert hg.gate_for("b2").state == "pending"


def test_the_swarm_page_shows_the_card_digest(gate_db, monkeypatch):
    from kazma_core.swarm import shared_approvals
    from kazma_core.swarm.approval_card import render_arguments

    args = json.dumps({"name": "k", "value": SECRET})
    hg.register_gate(hg.GateRow(gate_id="bus-1", thread_id="s", tool="vault_store",
                                mechanism="swarm_bus", args_json=args))
    view = shared_approvals.describe("bus-1")
    assert view is not None
    assert view["arguments_digest"] == render_arguments(args, tool="vault_store").digest
    assert SECRET not in json.dumps(view)


# ── rows an older build wrote ───────────────────────────────────────────────

_OLD_SCHEMA = """
CREATE TABLE hitl_gates (
  gate_id TEXT PRIMARY KEY, alias_id TEXT NOT NULL DEFAULT '', thread_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
  turn_id TEXT NOT NULL DEFAULT '', mechanism TEXT NOT NULL DEFAULT 'graph',
  kind TEXT NOT NULL DEFAULT 'security', tool TEXT NOT NULL,
  args_json TEXT NOT NULL DEFAULT '{}', message TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'pending',
  decision TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT '',
  supersedes TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
  claimed_at REAL, settled_at REAL, expires_at REAL
);
"""


def _old_database(path: Path) -> str:
    raw = json.dumps(ARGS)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO hitl_gates (gate_id, thread_id, tool, args_json, message, payload_json, "
            "state, decision, actor, created_at) VALUES "
            "('old-1', 't9', 'http_request', ?, ?, ?, 'settled', 'approve', 'web:me', ?)",
            (raw, f"Agent wants to run: http_request(access_token={SECRET})",
             json.dumps(_graph_payload("old-1")), time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return raw


def test_an_older_builds_rows_are_redacted_once_keeping_their_digest(tmp_path):
    path = tmp_path / "old.db"
    raw = _old_database(path)
    hg.set_db_path_for_tests(str(path))
    try:
        hg.ensure_gate_schema()
        rows = _assert_no_secret(path)
        assert rows[0]["args_digest"] == arguments_digest(raw)
        assert (rows[0]["state"], rows[0]["decision"]) == ("settled", "approve")
        # Idempotent: a second open converts nothing and changes nothing.
        conn = hg._connect()
        try:
            assert hg._redact_stored_rows(conn) == 0
        finally:
            conn.close()
        assert _raw_rows(path) == rows
    finally:
        hg.set_db_path_for_tests(None)


# ── negative controls: the old storage keeps the secret ─────────────────────


def _old_view(tool, args_json, payload_json, message):
    """register_gate before 2026-11-11: everything stored as given."""
    return args_json, "", payload_json, message


def test_control_the_old_storage_kept_the_credential(gate_db, monkeypatch):
    monkeypatch.setattr(hg, "_record_view", _old_view)
    asyncio.run(_graph_writer())
    rows = _raw_rows(gate_db)
    assert any(SECRET in str(v) for row in rows for v in row.values())


def test_control_twins_by_masked_text_would_merge_two_calls(gate_db, monkeypatch):
    """The old twin rule compared the stored text. On masked rows two calls
    that differ only in the secret are then one pause: the second call's
    question is answered by the first one's card."""

    def by_text(a, b):
        left, right = (a.args_json or "").strip(), (b.args_json or "").strip()
        return bool(left) and left not in ("{}", "null") and left == right

    monkeypatch.setattr(hg, "_same_pause_args", by_text)
    other = dict(ARGS, access_token=SECRET + "-other")
    hg.register_gate(hg.GateRow(gate_id="m1", thread_id="t3", tool="http_request",
                                args_json=json.dumps(ARGS)))
    second = hg.register_gate(hg.GateRow(gate_id="m2", thread_id="t3", tool="http_request",
                                         args_json=json.dumps(other)))
    assert second.gate_id == "m1", "merged into the first call's row"
    assert len(hg.pending_gates()) == 1


# ── the class gate: every writer of a row's call goes through register_gate ─


def _product_files():
    for pkg in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                "kazma-cli/kazma_cli", "kazma-tui/kazma_tui", "kazma-skills/kazma_skills"):
        for path in sorted((REPO / pkg).rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            yield path


def _gate_row_constructions(path: Path, source: str) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    rel = path.relative_to(REPO).as_posix()
    tree = ast.parse(source)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if name == "GateRow":
                    found.add((rel, fn.name))
    return found


def writers_in(sources: dict[Path, str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path, source in sources.items():
        if "GateRow(" in source:
            found |= _gate_row_constructions(path, source)
    return found - READERS


def table_writes_outside_the_registry(sources: dict[Path, str]) -> list[str]:
    out = []
    for path, source in sources.items():
        rel = path.relative_to(REPO).as_posix()
        if rel == "kazma-core/kazma_core/safety/hitl_gates.py":
            continue
        low = source.lower()
        for verb in ("insert into hitl_gates", "update hitl_gates", "replace into hitl_gates"):
            if verb in low:
                out.append(f"{rel}: {verb}")
    return out


def test_every_writer_of_a_gate_row_is_driven_and_nothing_else_writes_the_table():
    sources = {p: p.read_text(encoding="utf-8", errors="replace") for p in _product_files()}
    assert writers_in(sources) == set(WRITERS), (
        "a function builds a GateRow with no driver above: run it with a planted "
        "credential and read the row back"
    )
    assert table_writes_outside_the_registry(sources) == []
    # Inside the registry: the row's call is written by register_gate (masked
    # through _record_view) and the one-time conversion only, and the CAS
    # helper cannot name those columns.
    registry = (REPO / "kazma-core/kazma_core/safety/hitl_gates.py").read_text(encoding="utf-8")
    tree = ast.parse(registry)
    writers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            body = ast.get_source_segment(registry, fn) or ""
            if "INSERT INTO hitl_gates" in body or (
                "UPDATE hitl_gates SET args_json" in body
            ):
                writers.add(fn.name)
    assert writers == {"register_gate", "_redact_stored_rows"}, writers
    assert not ({"args_json", "payload_json", "args_digest"} & hg._CAS_COLUMNS)


def test_control_the_gate_finds_a_planted_writer():
    planted = {
        REPO / "kazma-core/kazma_core/planted.py": (
            "from kazma_core.safety.hitl_gates import GateRow\n"
            "def remember(args):\n    return GateRow(gate_id='x', thread_id='t', tool='y', args_json=args)\n"
        ),
        REPO / "kazma-ui/kazma_ui/planted.py": "SQL = 'UPDATE hitl_gates SET args_json = ?'\n",
    }
    assert ("kazma-core/kazma_core/planted.py", "remember") in writers_in(planted)
    assert table_writes_outside_the_registry(planted) == ["kazma-ui/kazma_ui/planted.py: update hitl_gates"]
