"""A send that did not happen is never reported as sent.

Until 2026-10-02 the gateway's send backend answered ``sent:<target>``
whatever ``GatewayManager.send`` said, and the reminder delivery, the
scheduled-post notice and the cron denial notice did not read the answer at
all. A reminder a platform refused was logged as delivered; ``send_file``
told the model "File sent" over an ``Error:``; the document pipeline logged
"✓ Delivered". ``send_message.send_failed`` reads the answer, and every
caller of the send API uses it.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from kazma_core.tools import send_message as sm

ROOT = Path(__file__).resolve().parents[1]
_SEND_FUNCS = {"send_message", "send_file_message"}


# ── the gate: no caller drops the answer ─────────────────────────────────


def _send_names(tree: ast.Module) -> set[str]:
    """Local names bound to the send API (``import ... as`` included)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "kazma_core.tools.send_message":
            for alias in node.names:
                if alias.name in _SEND_FUNCS:
                    names.add(alias.asname or alias.name)
    return names


def _called_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def dropped_answers(source: str, path: str) -> list[str]:
    """Send calls whose answer nobody reads.

    A call is fine when its answer is returned, or when the function that
    makes it also calls ``send_failed``. A send whose answer is a bare
    statement, or kept and never checked, is a finding.
    """
    tree = ast.parse(source)
    names = _send_names(tree)
    if path.endswith("kazma_core/tools/send_message.py"):
        names |= _SEND_FUNCS
    if not names:
        return []
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        reads_answer = any(
            isinstance(n, ast.Call) and _called_name(n) == "send_failed" for n in ast.walk(fn)
        )
        returned = {
            id(n)
            for ret in ast.walk(fn) if isinstance(ret, ast.Return) and ret.value is not None
            for n in ast.walk(ret.value)
        }
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and _called_name(node) in names):
                continue
            if id(node) in returned:
                continue
            # A send nested in another function is judged in that function.
            owner = next(
                (f for f in ast.walk(fn)
                 if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f is not fn
                 and any(n is node for n in ast.walk(f))),
                None,
            )
            if owner is not None:
                continue
            if not reads_answer:
                found.append(f"{path}:{node.lineno} {fn.name}()")
    return found


def _product_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted({p for p in out if "/tests/" not in p and "_tests/" not in p})


def test_every_send_answer_is_read() -> None:
    findings: list[str] = []
    for rel in _product_files():
        findings += dropped_answers((ROOT / rel).read_text(encoding="utf-8"), rel)
    assert findings == [], "\n".join(findings)


def test_the_gate_sees_a_dropped_answer() -> None:
    dropped = (
        "from kazma_core.tools.send_message import send_message\n"
        "async def remind(t, x):\n"
        "    await send_message(t, x, backend='telegram')\n"
    )
    kept_unread = (
        "from kazma_core.tools.send_message import send_message as _send\n"
        "async def remind(t, x):\n"
        "    result = await _send(t, x)\n"
        "    print('delivering')\n"
    )
    read = (
        "from kazma_core.tools.send_message import send_failed, send_message\n"
        "async def remind(t, x):\n"
        "    result = await send_message(t, x)\n"
        "    if send_failed(result):\n"
        "        raise RuntimeError(result)\n"
    )
    returned = (
        "from kazma_core.tools.send_message import send_message\n"
        "async def tool(t, x):\n"
        "    return await send_message(t, x)\n"
    )
    assert dropped_answers(dropped, "x.py") == ["x.py:3 remind()"]
    assert dropped_answers(kept_unread, "x.py") == ["x.py:3 remind()"]
    assert dropped_answers(read, "x.py") == []
    assert dropped_answers(returned, "x.py") == []


def test_send_failed_reads_every_answer() -> None:
    assert not sm.send_failed("sent:telegram:1")
    assert sm.send_failed("Error: telegram did not take the message for telegram:1")
    assert sm.send_failed("Error: no backend 'slack'")
    assert sm.send_failed(None)


# ── the gateway backend says what the platform said ──────────────────────


class _Store:
    async def get(self, _tid: str) -> None:
        return None


class _Manager:
    def __init__(self, accepts: bool) -> None:
        self.accepts = accepts
        self.sent: list[Any] = []

    async def send(self, outbound: Any) -> bool:
        self.sent.append(outbound)
        return self.accepts


async def test_a_refused_send_is_an_error() -> None:
    from kazma_gateway.agent_handler.graph import make_gateway_send_handler

    refused = await make_gateway_send_handler(_Manager(False), _Store())("discord:42", "hi")
    taken = await make_gateway_send_handler(_Manager(True), _Store())("discord:42", "hi")
    assert sm.send_failed(refused) and "discord" in refused
    assert taken == "sent:discord:42"


async def test_attachments_are_read_off_the_loop_and_carry_their_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    from kazma_gateway.agent_handler import attachments
    from kazma_gateway.agent_handler.graph import make_gateway_send_handler

    loop_thread = threading.get_ident()
    seen: list[int] = []
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4 x")

    def fake_find(text: str, **_: Any) -> list[Path]:
        seen.append(threading.get_ident())
        return [report]

    monkeypatch.setattr(attachments, "find_auto_attach_paths", fake_find)
    mgr = _Manager(True)
    with caplog.at_level(logging.WARNING, logger="kazma_gateway.agent_handler.graph"):
        out = await make_gateway_send_handler(mgr, _Store())(
            "telegram:1", "see report.pdf",
            attachments=[{"filename": "a.txt", "data": b"abc"}, {"path": str(tmp_path / "secret.txt")}],
        )
    assert out == "sent:telegram:1"
    assert seen and seen[0] != loop_thread
    names = [a.filename for a in mgr.sent[0].attachments]
    assert names == ["a.txt", "report.pdf"]  # the path entry is not read
    assert any("has no data" in r.getMessage() for r in caplog.records)


# ── send_file_message reads the file in a thread ─────────────────────────


async def test_send_file_message_reads_off_the_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    loop_thread = threading.get_ident()
    readers: list[int] = []
    real = sm._file_attachment

    def spy(path: str) -> Any:
        readers.append(threading.get_ident())
        return real(path)

    got: dict[str, Any] = {}

    async def backend(target_id: str, text: str, **kw: Any) -> str:
        got.update(kw)
        return f"sent:{target_id}"

    monkeypatch.setattr(sm, "_file_attachment", spy)
    monkeypatch.setitem(sm._message_backends, "testsend", backend)
    f = tmp_path / "out.txt"
    f.write_bytes(b"data")
    assert await sm.send_file_message("testsend:1", "cap", file_path=str(f), backend="testsend") == "sent:testsend:1"
    assert readers and readers[0] != loop_thread
    assert got["attachments"][0]["data"] == b"data"
    missing = await sm.send_file_message("testsend:1", "cap", file_path=str(tmp_path / "no.txt"), backend="testsend")
    assert missing.startswith("Error: file not found")


# ── reminders, scheduled posts, the dispatcher tools ─────────────────────


def _job(target: str) -> Any:
    from kazma_core.cron.scheduler import ScheduledJob

    return ScheduledJob(job_id="job-1", timing="0m", prompt="water the plants",
                        platform="telegram", thread_id="gw-telegram-1", delivery_target=target)


@pytest.mark.parametrize("answer", ["Error: telegram did not take the message for telegram:1", "sent:telegram:1"])
async def test_a_reminder_says_whether_it_was_delivered(
    answer: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    from unittest.mock import MagicMock

    from kazma_core.cron.scheduler import CronScheduler

    async def backend(target_id: str, text: str, **kw: Any) -> str:
        return answer

    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    sched = CronScheduler(store=MagicMock(), graph_builder=lambda: None)
    with caplog.at_level(logging.INFO, logger="kazma_core.cron.scheduler"):
        await sched._deliver(_job("telegram:1"), "Done.")
    failed = [r for r in caplog.records if r.levelname == "CRITICAL" and "delivery FAILED" in r.getMessage()]
    delivered = [r for r in caplog.records if "delivered job-1" in r.getMessage()]
    if answer.startswith("Error"):
        assert failed and not delivered
    else:
        assert delivered and not failed


def _post(target: str) -> Any:
    from kazma_core.x_api.schedule import ScheduledXPost

    return ScheduledXPost({
        "id": 7, "text": "hello", "fire_at": 0.0, "tz": "", "reply_to_id": "", "status": "pending",
        "thread_id": "", "delivery_target": target, "tenant_id": "default", "created_at": 0.0,
        "fired_at": None, "tweet_id": "", "error": "",
    })


async def test_a_refused_post_notice_tries_every_chat_app(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.x_api import scheduled_fire

    async def refusing(target_id: str, text: str, **kw: Any) -> str:
        return "Error: telegram did not take the message for telegram:1"

    fanned: list[Any] = []

    class _Adapter:
        async def send(self, message: Any) -> bool:
            fanned.append(message)
            return False  # nobody took it either

    class _Bus:
        adapter = _Adapter()

    monkeypatch.setitem(sm._message_backends, "telegram", refusing)
    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: _Bus())
    with caplog.at_level(logging.CRITICAL, logger="kazma_core.x_api.scheduled_fire"):
        await scheduled_fire._deliver(_post("telegram:1"), "✅ posted")
    assert len(fanned) == 1  # the fan-out was tried after the refusal
    messages = [r.getMessage() for r in caplog.records]
    assert any("could not deliver notification to telegram:1" in m for m in messages)
    assert any("no chat app took the notification for post 7" in m for m in messages)


async def test_the_dispatcher_tools_return_a_refusal_as_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.chat_platform_dispatcher import tools

    async def refusing(**kw: Any) -> str:
        return "Error: telegram did not take the message for telegram:5"

    monkeypatch.setattr(tools, "_core_send_message", refusing)
    out = await tools.dispatch_notification("telegram", "telegram:5", "hi")
    card = await tools.send_approval_request("telegram", "telegram:5", "Approve?", ["Approve"])
    assert out.startswith("Error:") and card.startswith("Error:")


# ── send_file: the fallback target and a refusal ─────────────────────────


async def test_send_file_without_a_bound_chat_uses_the_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The fallback lookup raised UnboundLocalError (``asyncio`` was imported
    only in the web branch) and a DEBUG line hid it: every send_file from a
    reminder or the CLI answered "no chat channel configured"."""
    from kazma_core.agent.tool_builtins import filesystem
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.ide.workspace_scope import workspace_path_scope

    registry = LocalToolRegistry()
    filesystem.register_filesystem_tools(registry)
    send_file = registry._tools["send_file"].func
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    sent: list[str] = []

    async def fake_send(target_id: str, text: str, *, file_path: str, backend: str = "telegram") -> str:
        sent.append(target_id)
        return answers.pop(0)

    answers = ["sent:telegram:77", "Error: telegram did not take the message for telegram:77"]
    monkeypatch.setattr(sm, "get_current_delivery_target", lambda: None)
    monkeypatch.setattr(sm, "get_current_platform", lambda: "")
    monkeypatch.setattr(filesystem, "_fallback_telegram_target", lambda: "telegram:77")
    monkeypatch.setattr(sm, "send_file_message", fake_send)
    async with workspace_path_scope(tmp_path):
        ok = await send_file("notes.txt")
        refused = await send_file("notes.txt")
    assert ok.startswith("File sent: notes.txt"), ok
    assert refused.startswith("Error: notes.txt was not sent"), refused
    assert sent == ["telegram:77", "telegram:77"]
