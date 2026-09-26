"""Every field the reply sink stores on a chat row reaches the client, or says why not.

The history route (GET /api/chat/sessions/{id}/messages) serializes rows
through whitelists: a field the store holds is invisible to the client until
the serializer names it. That bit three times -- the revision went out as 0
(docs/plans/UNIFIED_TURN_BLOCK.md U05), and a turn's usage and its close time
vanished on every reload (2026-09-26). The names now live with the writer
(reply_sink.CLIENT_ROW_FIELDS / SERVER_ROW_FIELDS). This gate enumerates the
fields from reply_sink's own source, stores a row carrying every one of them,
reads it back through the real route, and requires each to arrive -- unless
it is declared server-only, with the reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_SINK = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "reply_sink.py"

#: Handled by bespoke serializer code rather than passed through by name.
_BESPOKE = {"role", "content", "pending", "open", "parts", "activity"}


def _module_strings(tree: ast.Module) -> dict[str, str]:
    """Module-level NAME = "literal" constants, so row[_CONST] resolves."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = node.value.value
    return out


def _key(node: ast.AST | None, consts: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def written_fields(source: str) -> set[str]:
    """Every row field the source writes: row[k] = ..., row.setdefault(k, ...),
    and the keys of a new row literal (a dict with a "role" key)."""
    tree = ast.parse(source)
    consts = _module_strings(tree)
    fields: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    key = _key(target.slice, consts)
                    if key:
                        fields.add(key)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "setdefault" and node.args:
                key = _key(node.args[0], consts)
                if key:
                    fields.add(key)
        elif isinstance(node, ast.Dict):
            keys = [_key(k, consts) for k in node.keys]
            if "role" in keys:
                fields.update(k for k in keys if k)
    return fields


_SAMPLES = {
    "role": "assistant",
    "content": "the answer",
    "turn_id": "turn-fields",
    "pending": True,
    "open": True,
    "parts": [{"type": "text", "text": "the answer"}],
    "activity": [{"kind": "status", "label": "Working", "state": "done"}],
    "tokens": 1234,
    "cost": 0.0042,
    "duration_ms": 245400,
    "rev": 3,
    "schema": 2,
}


def _row_with(fields: set[str]) -> dict:
    return {f: _SAMPLES.get(f, f"value-of-{f}") for f in fields}


@pytest.fixture
def history(tmp_path, monkeypatch):
    """GET the real history route for one stored session."""
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    from kazma_ui.session_manager import get_session_manager, reset_session_manager
    from kazma_ui.sse_chat import create_sse_chat_router

    reset_session_manager()
    app = FastAPI()
    app.include_router(create_sse_chat_router(graph=None, checkpointer=None))
    client = TestClient(app)

    def read(row: dict) -> dict:
        sess = get_session_manager().get_or_create("sess-fields")
        sess.messages = [{"role": "user", "content": "q", "ts": "2026-09-26T01:00:00+00:00"}, row]
        resp = client.get("/api/chat/sessions/sess-fields/messages")
        assert resp.status_code == 200, resp.text
        items = [m for m in resp.json() if m.get("role") == "assistant"]
        assert len(items) == 1, resp.json()
        return items[0]

    yield read
    reset_session_manager()


def test_the_enumeration_sees_the_real_writers() -> None:
    """If the AST walk broke, the gate below would pass on an empty set."""
    fields = written_fields(_SINK.read_text(encoding="utf-8"))
    expected = {"ts", "closed_at", "turn_id", "model", "rev", "schema", "tokens",
                "cost", "duration_ms", "lifecycle", "kind", "content", "parts"}
    assert expected <= fields, expected - fields


def test_every_stored_field_reaches_the_client_or_is_declared(history) -> None:
    from kazma_ui import reply_sink

    fields = written_fields(_SINK.read_text(encoding="utf-8"))
    undeclared = fields - _BESPOKE - set(reply_sink.CLIENT_ROW_FIELDS) - set(reply_sink.SERVER_ROW_FIELDS)
    assert not undeclared, (
        f"reply_sink stores {sorted(undeclared)} but neither CLIENT_ROW_FIELDS nor "
        "SERVER_ROW_FIELDS names them -- the history route will drop them"
    )
    item = history(_row_with(fields))
    missing = sorted(f for f in fields - set(reply_sink.SERVER_ROW_FIELDS) if f not in item)
    assert not missing, f"stored but not returned by the history route: {missing}"
    for field in reply_sink.SERVER_ROW_FIELDS:
        assert field not in item, f"{field} is declared server-only but was returned"


def test_negative_control_a_field_left_off_the_list_is_caught(history, monkeypatch) -> None:
    """Take closed_at off the list: the route stops returning it, and the
    comparison the gate makes reports it."""
    from kazma_ui import reply_sink

    monkeypatch.setattr(
        reply_sink, "CLIENT_ROW_FIELDS",
        tuple(f for f in reply_sink.CLIENT_ROW_FIELDS if f != "closed_at"),
    )
    fields = written_fields(_SINK.read_text(encoding="utf-8"))
    item = history(_row_with(fields))
    assert "closed_at" not in item
    assert "closed_at" in fields - _BESPOKE - set(reply_sink.CLIENT_ROW_FIELDS) - set(
        reply_sink.SERVER_ROW_FIELDS
    )


def test_the_turn_close_is_recorded_once() -> None:
    """closed_at is written by the write that closes the turn, and never moves."""
    from kazma_ui import reply_sink

    row: dict = {"role": "assistant", "content": "", "turn_id": "t"}
    reply_sink._write_lifecycle(row, reply_sink._LIVE)
    assert "closed_at" not in row
    reply_sink._write_lifecycle(row, reply_sink._CLOSED)
    first = row["closed_at"]
    reply_sink._write_lifecycle(row, reply_sink._CLOSED)  # a straggling re-close
    assert row["closed_at"] == first
    # A turn closed before the field existed is not given a made-up time.
    legacy = {"role": "assistant", "content": "old", "turn_id": "u", "lifecycle": "closed"}
    reply_sink._write_lifecycle(legacy, reply_sink._CLOSED)
    assert "closed_at" not in legacy
