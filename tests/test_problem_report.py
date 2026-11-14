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


# -- what the plan promised besides (REMAINING 3.1, the rest) --

ROOT = Path(__file__).resolve().parents[1]


def test_a_chat_goes_in_only_when_given_and_cut(monkeypatch) -> None:
    from kazma_core.support import problem_report as pr

    monkeypatch.setattr(pr, "_readiness", lambda port: {})
    monkeypatch.setattr(pr, "_problems", lambda: [])
    monkeypatch.setattr(pr, "_install_parts", lambda: {})
    assert "chat" not in pr.collect("it broke")
    messages = [{"role": "system", "content": "SYSTEM PROMPT"}, {"role": "tool", "content": "TOOL OUTPUT"}]
    messages += [{"role": "user", "content": f"question {n} from me@example.com"} for n in range(15)]
    messages.append({"role": "assistant", "content": "x" * 5000})
    chat = pr.collect("it broke", chat=messages)["chat"]
    assert len(chat) <= pr.MAX_CHAT_MESSAGES and chat[-1]["role"] == "assistant"
    assert len(chat[-1]["text"]) == pr.MAX_CHAT_CHARS
    text = repr(chat)
    assert "SYSTEM PROMPT" not in text and "TOOL OUTPUT" not in text and "me@example.com" not in text


def test_the_page_sends_the_chat_only_when_ticked(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import support_api

    seen: list[str] = []
    monkeypatch.setattr(support_api, "_chat_messages",
                        lambda sid: (seen.append(sid) or [{"role": "user", "content": "the chat's own words"}])
                        if sid else [])
    monkeypatch.setattr("kazma_core.support.problem_report._readiness", lambda port: {})
    monkeypatch.setattr("kazma_core.support.problem_report._problems", lambda: [])
    app = FastAPI()
    app.include_router(support_api.create_support_router())
    client = TestClient(app)
    plain = client.post("/api/support/report/preview", json={"note": "n"}).json()["preview"]
    assert "the chat's own words" not in plain
    ticked = client.post("/api/support/report/preview", json={"note": "n", "chat_session": "s-1"}).json()["preview"]
    assert "the chat's own words" in ticked and seen[-1] == "s-1"


def test_every_promised_place_offers_a_report() -> None:
    js = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js"
    chat = (js / "chat.js").read_text(encoding="utf-8")
    assert "turn-header-report" in chat and "kazmaReportProblem" in chat, "a failed turn offers it"
    assert "/^\\/report(\\s|$)/i" in chat, "/report in the web chat opens the dialog"
    assert "kazmaReportProblem" in (js / "runs_panel.js").read_text(encoding="utf-8"), "stuck or failed work"
    assert "kazmaReportProblem" in (js / "modules" / "util.js").read_text(encoding="utf-8"), "an error toast"
    dialog = (ROOT / "kazma-ui" / "kazma_ui" / "templates" / "components" / "report_dialog.html").read_text(encoding="utf-8")
    assert 'id="report-include-chat"' in dialog and 'id="report-save"' in dialog
    from kazma_core.agent.command_catalog import COMMANDS

    assert next(c for c in COMMANDS if c.name == "report").web, "listed in the web chat's / menu"


def test_doctor_says_how_to_report_when_a_check_fails(monkeypatch, capsys) -> None:
    from kazma_cli import doctor

    monkeypatch.setattr(doctor, "collect", lambda: [(doctor.BAD, "  x a check")])
    monkeypatch.setattr(doctor, "_system_rows", lambda: [])
    monkeypatch.setattr(doctor, "_database_service_rows", lambda: [])
    assert doctor.run([]) == 1
    assert 'kazma report "what happened"' in capsys.readouterr().out


def test_the_guard_status_names_the_report_only_when_kazma_is_unwell(monkeypatch, capsys) -> None:
    import importlib.util
    import sys

    path = ROOT / "scripts" / "service" / "kazma_guard.py"
    spec = importlib.util.spec_from_file_location("kazma_guard_report_hint", path)
    guard = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = guard
    try:
        spec.loader.exec_module(guard)
        for name in ("KAZMA_GUARD_HEALTH_URL", "KAZMA_PORT", "KAZMA_ENV_FILE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(guard, "_read_state", lambda: {})
        monkeypatch.setattr(guard, "_guard_alive", lambda: True)
        monkeypatch.setattr(guard, "_port_holder_pid", lambda port: None)
        monkeypatch.setattr(guard, "read_pause", lambda: None)
        answers = {
            "healthy": guard.ProbeResult(True, "ready"),
            "setup": guard.ProbeResult(False, "provider", answered=True, needs_setup=("provider",)),
            "down": guard.ProbeResult(False, "connection refused", answered=False),
        }
        said = {}
        for name, answer in answers.items():
            monkeypatch.setattr(guard, "probe", lambda url, timeout, a=answer: a)
            guard._cmd_status()
            said[name] = capsys.readouterr().out
        assert guard.REPORT_HINT not in said["healthy"]
        assert guard.REPORT_HINT not in said["setup"]
        assert guard.REPORT_HINT in said["down"]
    finally:
        sys.modules.pop(spec.name, None)
