"""A file for a web chat is shown in that chat -- never sent to Telegram.

Live 2026-09-28: asked in the web chat to generate an image and "show it
here", the agent generated it, called ``send_file``, and the file went to the
operator's Telegram (``sent:telegram:1804015016``) while the answer said
"sent to this chat above"; the page showed nothing. A web turn's delivery
target IS the operator's Telegram (so reminders booked there ring), and
``send_file`` read it alone. Now a web turn shares a copy into the chat
(``kazma_core.chat_files``), served by ``GET /api/chat/files/{id}`` to the
owner of that chat only, and the tool hands the model the Markdown that
shows it; ``generate_image`` does the same for its own output.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core import chat_files
from kazma_core.agent.tool_builtins import filesystem
from kazma_core.ide.workspace_scope import workspace_path_scope
from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id
from kazma_core.tools.send_message import (
    reset_current_delivery_target,
    reset_current_platform,
    set_current_delivery_target,
    set_current_platform,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class _Capture:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register(self, **_kw: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def send_file():
    cap = _Capture()
    filesystem.register_filesystem_tools(cap)
    return cap.tools["send_file"]


@contextmanager
def turn(platform: str, thread: str, delivery: str):
    """Bind a conversation the way the tool worker does: platform, thread,
    and the delivery target (a web turn's is the operator's Telegram).
    Entered inside the test, so an async test resets in its own context."""
    tokens = [
        (reset_current_platform, set_current_platform(platform)),
        (reset_current_thread_id, set_current_thread_id(thread)),
        (reset_current_delivery_target, set_current_delivery_target(delivery)),
    ]
    try:
        yield
    finally:
        for reset, token in reversed(tokens):
            reset(token)


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Every platform send, recorded instead of made."""
    calls: list[dict] = []

    async def fake_send(target_id, text, *, file_path, backend="telegram"):
        calls.append({"target": target_id, "file": file_path, "backend": backend})
        return f"sent:{target_id}"

    monkeypatch.setattr("kazma_core.tools.send_message.send_file_message", fake_send)
    return calls


def _shared_id(text: str) -> str:
    m = re.search(r"/api/chat/files/(att_[0-9a-f]{32})", text)
    assert m, text
    return m.group(1)


# ── the store ────────────────────────────────────────────────────────────


def test_a_shared_file_is_a_copy_with_its_own_record(tmp_path):
    src = tmp_path / "cube.png"
    src.write_bytes(PNG)
    shared = chat_files.share_file(src, thread_id="thread-a")
    loaded, blob = chat_files.load_shared(shared.id)
    assert loaded == shared and blob.read_bytes() == PNG
    assert blob.parent == chat_files._shared_dir()
    src.unlink()  # the chat keeps what it was shown
    assert chat_files.load_shared(shared.id)[1].read_bytes() == PNG
    assert shared.markdown() == f"![cube.png](/api/chat/files/{shared.id})"


def test_the_type_comes_from_the_bytes_not_the_name(tmp_path):
    jpeg_named_png = tmp_path / "photo.png"
    jpeg_named_png.write_bytes(JPEG)
    assert chat_files.share_file(jpeg_named_png, thread_id="t").mime == "image/jpeg"
    svg = tmp_path / "logo.svg"
    svg.write_bytes(SVG)
    shared = chat_files.share_file(svg, thread_id="t")
    assert shared.mime == "application/octet-stream" and not shared.inline
    assert shared.markdown() == f"[logo.svg](/api/chat/files/{shared.id})"


@pytest.mark.parametrize(
    "bad", ["", "att_123", "../attachments/x", "att_" + "g" * 32, "ATT_" + "a" * 32, None]
)
def test_only_a_minted_id_resolves(bad):
    assert chat_files.load_shared(bad) is None


def test_nothing_is_shared_without_a_chat(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(PNG)
    with pytest.raises(ValueError):
        chat_files.share_file(src, thread_id="")


def test_only_a_web_turn_has_a_web_chat():
    assert chat_files.web_chat_thread() == ""
    with turn("telegram", "gw-telegram-1", "telegram:1"):
        assert chat_files.web_chat_thread() == ""


def test_a_web_turn_names_its_chat():
    with turn("web", "thread-web", "telegram:1804015016"):
        assert chat_files.web_chat_thread() == "thread-web"


# ── send_file ────────────────────────────────────────────────────────────


async def test_send_file_on_the_web_shows_the_file_in_that_chat(tmp_path, send_file, sent):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "cube.png").write_bytes(PNG)
    with turn("web", "thread-web", "telegram:1804015016"):
        async with workspace_path_scope(ws):
            out = await send_file("cube.png")
    assert sent == []  # nothing went to Telegram
    shared, _ = chat_files.load_shared(_shared_id(out))
    assert shared.thread_id == "thread-web"
    assert f"![cube.png](/api/chat/files/{shared.id})" in out


async def test_send_file_from_telegram_still_sends_to_telegram(tmp_path, send_file, sent):
    """Negative control: the platforms keep their own delivery."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "cube.png").write_bytes(PNG)
    with turn("telegram", "gw-telegram-9", "telegram:9"):
        async with workspace_path_scope(ws):
            out = await send_file("cube.png")
    assert [c["target"] for c in sent] == ["telegram:9"]
    assert "/api/chat/files/" not in out


# ── generate_image ───────────────────────────────────────────────────────


class _Backend:
    async def generate(self, prompt, width, height):
        return PNG


@pytest.fixture
def image_backend(monkeypatch, tmp_path):
    from kazma_core.tools import image_gen
    from kazma_core.tools.image_backends import router

    monkeypatch.setattr(image_gen, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(router, "get_backend", lambda provider: _Backend())
    monkeypatch.setattr(router, "resolve_provider", lambda provider: "pollinations")
    return image_gen


async def test_a_generated_image_is_shown_in_the_web_chat(image_backend):
    with turn("web", "thread-web", "telegram:1804015016"):
        out = await image_backend.generate_image("a small red cube")
    shared, blob = chat_files.load_shared(_shared_id(out))
    assert shared.inline and blob.read_bytes() == PNG
    assert shared.markdown() in out


async def test_a_generated_image_elsewhere_is_only_saved(image_backend):
    with turn("telegram", "gw-telegram-9", "telegram:9"):
        out = await image_backend.generate_image("a small red cube")
    assert "/api/chat/files/" not in out and "Saved to:" in out


# ── the route ────────────────────────────────────────────────────────────


class _Sessions:
    def __init__(self, owned: set[str]) -> None:
        self.owned = owned

    def get_by_thread_id(self, thread_id: str):
        return object() if thread_id in self.owned else None


@pytest.fixture
def client(monkeypatch) -> TestClient:
    from kazma_ui import routes_chat_upload, thread_ownership

    monkeypatch.setattr(thread_ownership, "_store", lambda store=None: _Sessions({"mine"}))
    app = FastAPI()
    app.include_router(routes_chat_upload.router)
    return TestClient(app)


def _share(tmp_path: Path, name: str, data: bytes, thread: str) -> str:
    src = tmp_path / name
    src.write_bytes(data)
    return chat_files.share_file(src, thread_id=thread).id


def test_the_owner_sees_an_image_in_the_page(tmp_path, client):
    fid = _share(tmp_path, "cube.png", PNG, "mine")
    r = client.get(f"/api/chat/files/{fid}")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]


def test_anything_but_a_raster_image_is_a_download(tmp_path, client):
    fid = _share(tmp_path, "logo.svg", SVG, "mine")
    r = client.get(f"/api/chat/files/{fid}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")


def test_another_chats_file_is_not_found(tmp_path, client):
    fid = _share(tmp_path, "cube.png", PNG, "someone-elses")
    assert client.get(f"/api/chat/files/{fid}").status_code == 404


def test_an_unknown_or_malformed_id_is_not_found(client):
    assert client.get("/api/chat/files/att_" + "0" * 32).status_code == 404
    assert client.get("/api/chat/files/..%2F..%2Fvault.db").status_code == 404


# ── the tool worker binds the conversation's platform ────────────────────


class _PlatformRecorder:
    """An executor that notes the platform and chat a tool runs under."""

    def __init__(self) -> None:
        self.seen: list[tuple] = []

    async def execute(self, name, args):
        from kazma_core.tools.send_message import get_current_platform

        self.seen.append((get_current_platform(), chat_files.web_chat_thread()))
        return {"content": "ok", "is_error": False}


class _NoopTracer:
    def trace_tool_execution(self, **kw):
        pass


@pytest.mark.parametrize(
    ("gateway", "expected"),
    [
        ("web", ("web", "thread-web")),
        ("telegram", ("telegram", "")),  # control: another platform has no web chat
    ],
)
async def test_the_tool_worker_binds_the_conversations_platform(gateway, expected):
    from kazma_core.agent.graph_builder import tool_worker_node
    from kazma_core.agent.state import initial_supervisor_state
    from kazma_core.tools.send_message import get_current_platform, web_gateway_block

    state = initial_supervisor_state(thread_id="thread-web")
    state["messages"] = [{"role": "user", "content": "list the files"}]
    state["tool_calls_pending"] = [{"id": "c1", "name": "file_list", "arguments": {}}]
    block = web_gateway_block("thread-web")
    block["platform"] = gateway
    state["_gateway"] = block
    exe = _PlatformRecorder()
    await tool_worker_node(state, tool_executor=exe, tracer=_NoopTracer(), hitl_config=None)
    assert exe.seen == [expected]
    assert get_current_platform() is None  # reset when the node ends
