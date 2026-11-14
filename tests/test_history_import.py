"""Bring your history (REMAINING 4.2): a ChatGPT or Claude export into Kazma's
memory. Exports are built here in each service's shape (branches, hidden
instructions, tool calls, images, content blocks), written through the real
episode writer into this test's memory database, and recalled after.
"""

from __future__ import annotations

import json
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest

from kazma_core.memory import history_import
from kazma_core.memory.history_import import ImportRefused, import_history, read_export

T0 = 1_700_000_000.0  # 2023-11-14


def _node(nid, parent, role, text, at, *, children=(), hidden=False, recipient="all", parts=None,
          content_type="text"):
    message = {
        "id": nid, "author": {"role": role}, "create_time": at, "recipient": recipient,
        "content": {"content_type": content_type, "parts": parts if parts is not None else [text]},
        "metadata": {"is_visually_hidden_from_conversation": True} if hidden else {},
    }
    return nid, {"id": nid, "message": message, "parent": parent, "children": list(children)}


def chatgpt_export() -> list[dict]:
    mapping = dict([
        ("root", {"id": "root", "message": None, "parent": None, "children": ["sys"]}),
        _node("sys", "root", "system", "You are ChatGPT", T0, hidden=True),
        _node("u1", "sys", "user", "Which tile for the kitchen floor, porcelain or ceramic?", T0 + 10),
        _node("a1", "u1", "assistant", "Porcelain: it is denser and takes water better.", T0 + 20),
        # A branch the person left (an edited question): not on the current path.
        _node("u2x", "a1", "user", "THE OLD BRANCH QUESTION", T0 + 25),
        _node("u2", "a1", "user", "And for the bathroom walls?", T0 + 30),
        _node("tool", "u2", "assistant", "search('tiles')", T0 + 31, recipient="browser", content_type="code"),
        _node("out", "tool", "tool", "TOOL OUTPUT", T0 + 32),
        _node("a2", "out", "assistant", "Glazed ceramic is fine on walls.", T0 + 40),
        _node("a2b", "a2", "assistant", "", T0 + 41, content_type="multimodal_text",
              parts=[{"asset_pointer": "file-service://x"}, "Here is a picture of one."]),
    ])
    return [{"title": "Kitchen tiles", "create_time": T0, "conversation_id": "c0ffee-1",
             "current_node": "a2b", "mapping": mapping}]


def claude_export() -> list[dict]:
    return [{"uuid": "5e1f-77", "name": "Trip to Salalah", "created_at": "2024-07-01T08:00:00Z",
             "chat_messages": [
                 {"sender": "human", "text": "When is the khareef season in Salalah?",
                  "created_at": "2024-07-01T08:00:00Z"},
                 {"sender": "assistant", "text": "", "created_at": "2024-07-01T08:00:05Z",
                  "content": [{"type": "text", "text": "From late June to early September."}]},
             ]}]


def _zip(tmp_path: Path, data: list[dict], name: str = "conversations.json") -> Path:
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, json.dumps(data))
        z.writestr("user.json", "{}")
    return path


def test_a_chatgpt_export_reads_as_the_conversation_the_person_saw(tmp_path) -> None:
    history = read_export(_zip(tmp_path, chatgpt_export()))
    assert history.source == "chatgpt" and len(history.chats) == 1
    chat = history.chats[0]
    assert chat.title == "Kitchen tiles" and chat.chat_id == "c0ffee-1"
    assert [(t.user, t.assistant) for t in chat.turns] == [
        ("Which tile for the kitchen floor, porcelain or ceramic?", "Porcelain: it is denser and takes water better."),
        ("And for the bathroom walls?", "Glazed ceramic is fine on walls.\n\nHere is a picture of one."),
    ]
    assert chat.turns[0].at == T0 + 10
    text = json.dumps([t.__dict__ for t in chat.turns])
    for left_out in ("You are ChatGPT", "THE OLD BRANCH", "TOOL OUTPUT", "search('tiles')", "asset_pointer"):
        assert left_out not in text
    assert history.summary()["turns"] == 2


def test_a_claude_export_and_a_bare_conversations_json(tmp_path) -> None:
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(claude_export()), encoding="utf-8")
    history = read_export(path)
    assert history.source == "claude"
    turn = history.chats[0].turns[0]
    assert turn.user.startswith("When is the khareef") and turn.assistant == "From late June to early September."
    assert turn.at == pytest.approx(1719820800.0)


def test_what_is_not_an_export_is_refused(tmp_path, monkeypatch) -> None:
    other = tmp_path / "other.json"
    other.write_text(json.dumps([{"hello": "world"}]), encoding="utf-8")
    with pytest.raises(ImportRefused):
        read_export(other)
    with pytest.raises(ImportRefused):
        read_export(_zip(tmp_path, chatgpt_export(), name="notes.json"))
    (tmp_path / "x.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ImportRefused):
        read_export(tmp_path / "x.json")
    monkeypatch.setattr(history_import, "MAX_JSON_BYTES", 100)
    with pytest.raises(ImportRefused):
        read_export(_zip(tmp_path, chatgpt_export()))


@pytest.fixture
def memory(monkeypatch, tmp_path):
    from tests._no_models import keep_models_out

    from kazma_core.memory import dual_write

    keep_models_out(monkeypatch)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    dual_write._reset_mirror()
    yield tmp_path / "data"
    dual_write._reset_mirror()


def _rows(data_dir: Path) -> list[sqlite3.Row]:
    from kazma_core.paths import primary_memory_db

    if not Path(primary_memory_db()).exists():
        return []
    conn = sqlite3.connect(primary_memory_db())
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM episodes ORDER BY created_at").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def test_import_writes_each_turn_at_its_own_time_once(memory, tmp_path) -> None:
    history = read_export(_zip(tmp_path, chatgpt_export()))
    seen: list[tuple[int, int]] = []
    report = import_history(history, progress=lambda d, t: seen.append((d, t)))
    assert (report.written, report.already, report.failed) == (2, 0, 0)
    assert seen[-1] == (2, 2)
    rows = _rows(memory)
    assert [r["created_at"] for r in rows] == [T0 + 10, T0 + 30]
    assert {r["tier"] for r in rows} == {"episodic"}, "written after the fact: never the live working buffer"
    assert {r["session_id"] for r in rows} == {"import-chatgpt-c0ffee-1"}
    meta = json.loads(rows[0]["metadata_json"])
    assert meta["source"] == "history_import" and meta["chat_title"] == "Kitchen tiles"
    again = import_history(history)
    assert (again.written, again.already) == (0, 2), "a second import skips what is there"
    assert len(_rows(memory)) == 2


def test_an_imported_turn_is_recalled(memory, tmp_path) -> None:
    from kazma_core.memory.recall import recall

    import_history(read_export(_zip(tmp_path, chatgpt_export())))
    found = recall("porcelain kitchen floor tile", local_only=True)
    texts = " ".join(str(e) for e in (found.episodes or []))
    assert "Porcelain" in texts or "porcelain" in texts


def test_a_kept_out_turn_and_a_failed_write_are_counted(memory, tmp_path, monkeypatch) -> None:
    from kazma_core.memory import dual_write

    answers = iter([("refused", None), ("failed", None)])
    monkeypatch.setattr(dual_write, "write_episode", lambda **_kw: next(answers))
    report = import_history(read_export(_zip(tmp_path, chatgpt_export())))
    assert (report.written, report.refused, report.failed) == (0, 1, 1)


def test_stop_ends_it_between_turns(memory, tmp_path) -> None:
    report = import_history(read_export(_zip(tmp_path, chatgpt_export())), should_stop=lambda: True)
    assert report.stopped and report.written == 0


def test_the_command_says_what_it_holds_and_imports_on_yes(memory, tmp_path, capsys) -> None:
    from kazma_cli.history_import import main

    path = _zip(tmp_path, chatgpt_export())
    assert main([str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "A ChatGPT export: 1 conversation, 2 turns" in out
    assert main([str(path)], ask=lambda _p: "n") == 2 and not _rows(memory)
    assert main([str(path), "-y"]) == 0
    assert len(_rows(memory)) == 2
    assert main([str(tmp_path / "missing.zip")]) == 1


def test_the_page_routes_preview_then_import(memory, tmp_path, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import history_import_api

    monkeypatch.setattr(history_import_api, "_refusal", lambda: None)
    app = FastAPI()
    app.include_router(history_import_api.create_history_import_router())
    client = TestClient(app)
    body = _zip(tmp_path, chatgpt_export()).read_bytes()
    res = client.post("/api/import/history/preview", content=body)
    assert res.status_code == 200 and res.json()["summary"]["turns"] == 2
    token = res.json()["token"]
    assert client.post("/api/import/history/start", json={"token": "nope"}).status_code == 404
    assert client.post("/api/import/history/start", json={"token": token}).status_code == 200
    deadline = time.monotonic() + 30
    while client.get("/api/import/history/status").json()["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.1)
    status = client.get("/api/import/history/status").json()
    assert status["state"] == "done" and status["report"]["written"] == 2
    assert not list((memory / "history_import").glob("upload-*")), "the upload is deleted when its import ends"
    bad = client.post("/api/import/history/preview", content=b"[]{")
    assert bad.status_code == 422 and bad.json()["code"] == "not_an_export"


def test_the_routes_refuse_on_an_install_with_platform_users(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import history_import_api

    monkeypatch.setattr(history_import_api, "_refusal", lambda: "platform users")
    app = FastAPI()
    app.include_router(history_import_api.create_history_import_router())
    res = TestClient(app).post("/api/import/history/preview", content=b"[]")
    assert res.status_code == 409 and res.json()["code"] == "single_operator"


def test_the_routes_are_an_administrators() -> None:
    from kazma_core.security.platform_rbac import role_allows

    assert not role_allows("operator", "/api/import/history/preview", "POST")
    assert role_allows("admin", "/api/import/history/preview", "POST")
