"""Email manager — sandbox, router, HITL, analyze heuristics."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS, requires_approval


@pytest.fixture()
def sandbox_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "sandbox_emails.db"
    monkeypatch.setenv("EMAIL_DEFAULT_PROVIDER", "sandbox")
    # Force clean env so auto → sandbox
    for k in (
        "EMAIL_GMAIL_ADDRESS",
        "EMAIL_GMAIL_APP_PASSWORD",
        "EMAIL_MS_ACCESS_TOKEN",
        "EMAIL_MS_REFRESH_TOKEN",
        "EMAIL_ADDRESS",
        "EMAIL_PASSWORD",
    ):
        monkeypatch.delenv(k, raising=False)
    from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend

    return SandboxBackend(db_path=db)


@pytest.mark.asyncio
async def test_sandbox_list_and_get(sandbox_db) -> None:
    from kazma_skills.native.email_manager.models import ListQuery

    msgs = await sandbox_db.list_messages(ListQuery(folder="INBOX", limit=10))
    assert len(msgs) >= 5
    boss = [m for m in msgs if "boss@corp.com" in m.from_addr]
    assert boss
    full = await sandbox_db.get_message(boss[0].id)
    assert "Friday" in full.body or "slides" in full.body.lower()


@pytest.mark.asyncio
async def test_sandbox_send_delete_categorize(sandbox_db) -> None:
    from kazma_skills.native.email_manager.models import (
        CategorizeRequest,
        SendRequest,
    )

    res = await sandbox_db.send(
        SendRequest(
            action="send",
            to=["contact@kazma.ai"],
            subject="Hello",
            body="Test body",
        )
    )
    assert res.ok
    assert res.message_id
    await sandbox_db.categorize(
        CategorizeRequest(message_id=res.message_id, mark_read=True, star=True)
    )
    msg = await sandbox_db.get_message(res.message_id)
    assert msg.starred is True
    await sandbox_db.delete(res.message_id, permanent=False)
    trashed = await sandbox_db.get_message(res.message_id)
    assert trashed.folder == "Trash"


@pytest.mark.asyncio
async def test_tools_list_banner(sandbox_db, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from kazma_skills.native.email_manager import tools as t
    from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend

    monkeypatch.setattr(
        "kazma_skills.native.email_manager.router.get_backend",
        lambda provider=None: sandbox_db,
    )
    out = await t.email_list(folder="INBOX", limit=5, provider="sandbox")
    assert "[sandbox mode]" in out or "sandbox" in out.lower()
    assert "sbx-" in out or "Emails" in out


@pytest.mark.asyncio
async def test_email_analyze_phishing(sandbox_db, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.email_manager import tools as t

    monkeypatch.setattr(
        "kazma_skills.native.email_manager.router.get_backend",
        lambda provider=None: sandbox_db,
    )
    # Find phishing sample
    from kazma_skills.native.email_manager.models import ListQuery

    msgs = await sandbox_db.list_messages(ListQuery(folder="INBOX", limit=50))
    phish = next((m for m in msgs if "lottery" in m.subject.lower() or "won" in m.subject.lower()), None)
    assert phish is not None
    out = await t.email_analyze(message_id=phish.id, focus="security", provider="sandbox")
    assert "analysis" in out.lower() or "risk" in out.lower()
    assert "sandbox" in out.lower() or "[" in out


def test_hitl_email_mutators() -> None:
    for name in ("email_send", "email_delete", "email_categorize"):
        assert name in CANONICAL_DANGER_TOOLS
        assert requires_approval(
            name, {"enabled": True, "require_approval_for": list(CANONICAL_DANGER_TOOLS)}
        )


def test_router_auto_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ):
        if k.startswith("EMAIL_"):
            monkeypatch.delenv(k, raising=False)
    from kazma_skills.native.email_manager.router import detect_available_provider, resolve_provider

    assert detect_available_provider() == "sandbox"
    assert resolve_provider("auto") == "sandbox"
    assert resolve_provider("microsoft") in ("microsoft", "sandbox")  # no creds → may still request ms


def test_native_loader_registers_email(tmp_path: Path) -> None:
    from kazma_core.agent.tool_registry import LocalToolRegistry

    reg = LocalToolRegistry()
    names = set(reg._tools.keys())
    for tname in (
        "email_list",
        "email_get",
        "email_send",
        "email_delete",
        "email_categorize",
        "email_analyze",
    ):
        assert tname in names, f"missing {tname} — is email_manager skill loaded?"


@pytest.mark.asyncio
async def test_analyze_heuristic() -> None:
    from kazma_skills.native.email_manager.analyze import _heuristic_analyze

    data = _heuristic_analyze(
        "URGENT: You won $1,000,000",
        "prizes@evil.ru",
        "Click bit.ly and enter password to claim. Act within 2 hours. Wire fees.",
    )
    assert data["security"]["risk_level"] in ("medium", "high")
    assert data["security"]["phishing_signals"]
