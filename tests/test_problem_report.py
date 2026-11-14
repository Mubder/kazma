"""Problem reports (2026-11-14, REMAINING 3.1; ``kazma_core.support.problem_report``).

What goes in (and what never does), what the person sees first, where it goes
and what happens when it cannot: on a stand-in inbox, the real routes, the
CLI and the chat command. The inbox itself is ``deploy/observer/src/reports.js``
(its own Node tests).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from kazma_core.support import problem_report as pr

REPO = Path(__file__).resolve().parents[1]


def test_a_report_keeps_nothing_private() -> None:
    home = str(Path.home())
    text = (f"key sk-proj-{'a' * 40} at {home}\\kazma-data\\vault.db from owner@example.com "
            "via 203.0.113.7 and 2001:db8:0:0:0:0:0:1 bot 123456789:AAH" + "x" * 33)
    out = pr.redacted(text)
    assert "sk-proj-" + "a" * 40 not in out
    assert home not in out and "~" in out
    assert "owner@example.com" not in out and "<email>" in out
    assert "203.0.113.7" not in out and "2001:db8" not in out and "<ip>" in out


def test_the_report_holds_its_parts_and_no_chat_or_setting(tmp_path, monkeypatch) -> None:
    log = tmp_path / "kazma.log"
    log.write_text("\n".join([
        '{"level": "INFO", "message": "a private chat message: my salary is 9000"}',
        f'{{"level": "ERROR", "message": "crash reading {Path.home()}\\\\notes.txt for owner@example.com"}}',
        "Traceback (most recent call last):",
    ]), encoding="utf-8")
    monkeypatch.setattr(pr, "_readiness", lambda port: {"answered": True, "status": 200, "checks": {"db": "ok"}})
    monkeypatch.setattr(pr, "_problems", lambda: ["alert: Backups are late"])
    report = pr.collect("The PDF came out empty", where="/documents", log=log)
    assert set(report) == {"kind", "version", "commit", "where", "note", "system", "install", "readiness",
                           "open_problems", "recent_errors", "made_at"}
    assert report["kind"] == "kazma-problem-report" and report["note"] == "The PDF came out empty"
    text = pr.preview(report)
    assert "salary" not in text, "an INFO line (a chat's words) never goes in"
    assert len(report["recent_errors"]) == 2
    assert "owner@example.com" not in text and str(Path.home()) not in text


class Inbox:
    """A stand-in for the inbox: answers as status.kazma.ai/v1/report does."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.got: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.got.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "inbox_full_today"})
        return httpx.Response(200, json={"ok": True, "id": "k7m2p9q4rt"})


@pytest.fixture
def inbox(monkeypatch, tmp_path):
    from kazma_core.config_store import get_config_store

    get_config_store().delete(pr._SENT_KEY)
    box = Inbox()
    real = httpx.Client

    def client(*a: Any, **k: Any) -> httpx.Client:
        k.pop("verify", None)
        return real(*a, transport=httpx.MockTransport(box.handler), **k)

    monkeypatch.setattr(httpx, "Client", client)
    return box


def _report() -> dict[str, Any]:
    return {"kind": "kazma-problem-report", "version": "0.21.2", "note": "x", "recent_errors": []}


def test_a_report_is_sent_and_numbered(inbox) -> None:
    result = pr.send(_report(), url="https://status.kazma.example/v1/report", now=1000.0)
    assert result.sent and result.report_id == "k7m2p9q4rt"
    assert inbox.got[0]["kind"] == "kazma-problem-report"
    assert "k7m2p9q4rt" in pr.describe(result)


def test_a_report_that_cannot_leave_is_saved_and_says_where(inbox, tmp_path) -> None:
    inbox.status = 429
    refused = pr.send(_report(), url="https://status.kazma.example/v1/report", now=1000.0)
    assert not refused.sent and "429" in refused.why and Path(refused.saved).is_file()
    for url, why in (("", "no inbox"), ("http://status.kazma.example/v1/report", "not https")):
        result = pr.send(_report(), url=url, now=1000.0)
        assert not result.sent and why in result.why
        assert json.loads(Path(result.saved).read_text(encoding="utf-8"))["kind"] == "kazma-problem-report"
    assert len(inbox.got) == 1, "nothing goes to an empty or plain-http inbox"


def test_at_most_ten_reports_leave_in_an_hour(inbox) -> None:
    sent = [pr.send(_report(), url="https://status.kazma.example/v1/report", now=5000.0 + i).sent
            for i in range(pr.MAX_PER_HOUR + 1)]
    assert sent == [True] * pr.MAX_PER_HOUR + [False]
    assert pr.send(_report(), url="https://status.kazma.example/v1/report", now=5000.0 + 3700).sent


def test_the_routes_preview_and_send_and_are_an_administrators(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.security.platform_rbac import role_allows
    from kazma_ui import support_api

    monkeypatch.setattr(pr, "collect", lambda note, where="", **k: {**_report(), "note": note, "where": where})
    monkeypatch.setattr(pr, "send", lambda report, **k: pr.SendResult(sent=True, report_id="abc123defg"))
    app = FastAPI()
    app.include_router(support_api.create_support_router())
    client = TestClient(app)
    shown = client.post("/api/support/report/preview", json={"note": "broken", "where": "/chat"}).json()
    assert '"note": "broken"' in shown["preview"] and '"where": "/chat"' in shown["preview"]
    assert client.post("/api/support/report", json={"note": "broken"}).json() == {
        "sent": True, "id": "abc123defg", "saved": "", "why": ""}
    assert client.post("/api/support/report", json={"note": "x" * 4001}).status_code == 422
    assert not role_allows("operator", "/api/support/report", "POST")
    assert not role_allows("viewer", "/api/support/report/preview", "POST")
    assert role_allows("admin", "/api/support/report", "POST")


def test_kazma_report_shows_it_first_and_sends_only_on_yes(monkeypatch) -> None:
    from kazma_cli import report as cli

    sent: list[Any] = []
    monkeypatch.setattr(pr, "collect", lambda note, where="", **k: {**_report(), "note": note})
    monkeypatch.setattr(pr, "send", lambda report, **k: sent.append(report) or pr.SendResult(True, "abc123defg"))
    said: list[str] = []
    assert cli.main(["--dry-run", "it", "broke"], say=said.append) == 0 and not sent
    assert any('"note": "it broke"' in s for s in said)
    assert cli.main(["it", "broke"], ask=lambda _q: "n", say=said.append) == 2 and not sent
    assert cli.main(["it", "broke"], ask=lambda _q: "y", say=said.append) == 0 and len(sent) == 1


def test_report_in_a_chat_shows_then_sends(monkeypatch) -> None:
    from kazma_gateway.agent_handler.report_command import _pending, try_report_command
    from kazma_gateway.gateway import IncomingMessage
    from kazma_gateway.slash_commands import changes_global_config

    _pending.clear()
    monkeypatch.setattr(pr, "_readiness", lambda port: {"answered": False, "why": "ConnectError"})
    monkeypatch.setattr(pr, "_problems", lambda: [])
    sent: list[Any] = []
    monkeypatch.setattr(pr, "send", lambda report, **k: sent.append(report) or pr.SendResult(True, "abc123defg"))
    replies: list[str] = []

    async def say(text: str) -> None:
        replies.append(text)

    def msg(text: str) -> IncomingMessage:
        return IncomingMessage(platform="telegram", sender_id="telegram:7", text=text,
                               context_metadata={"chat_id": 7})

    async def run() -> None:
        assert await try_report_command(msg("/report send"), say)
        assert await try_report_command(msg("/report The PDF was empty"), say)
        assert not sent
        assert await try_report_command(msg("/report send"), say)
        assert not await try_report_command(msg("/reports"), say)

    asyncio.run(run())
    assert "Nothing to send" in replies[0]
    assert "The PDF was empty" in replies[1] and "/report send" in replies[1]
    assert sent[0]["note"] == "The PDF was empty" and sent[0]["where"] == "chat:telegram"
    assert "abc123defg" in replies[2]
    assert changes_global_config("/report it broke") and changes_global_config("/report send")


def test_the_inbox_kazma_sends_to_is_the_worker_route() -> None:
    index = (REPO / "deploy" / "observer" / "src" / "index.js").read_text(encoding="utf-8")
    reports = (REPO / "deploy" / "observer" / "src" / "reports.js").read_text(encoding="utf-8")
    assert pr.DEFAULT_REPORT_URL == "https://status.kazma.ai/v1/report"
    assert 'route === "POST /v1/report"' in index and f'const KIND = "{pr.REPORT_KIND}"' in reports
