"""Audit 2026-09-16 F-8 — answering an approval is admin-grade everywhere.

Audit H-8 made *package installation* admin-grade on Telegram, Discord and
Slack, and stopped there. That left the privilege model upside down: pressing
**Approve** on a `shell_exec`, `vault_retrieve`, `email_send` or `git_push`
card was LESS privileged than installing a package — even though approval is
the button that converts "the agent wants to" into "the agent did".

It only mattered in the `allow_all` posture, which is a documented, supported
option: every member of the group chat / guild / workspace could answer the
operator's approvals. These tests pin the fix on all three adapters, in both
directions (a non-admin is refused; an admin still gets through), so nobody
"simplifies" the gate away later.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SWARM_INTERACTION = {
    "id": "ia_1",
    "token": "tok_1",
    "data": {"custom_id": "swarm_approve_task_1"},
    "message": {"content": "Approve?"},
    "user": {"id": "outsider_9"},
}


def _discord_adapter():
    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="fake", allow_all=True)
    acks: list[dict] = []

    async def mock_post(url, json=None, **kwargs):
        acks.append(json)
        resp = MagicMock()
        resp.status_code = 200
        return resp

    adapter._http = MagicMock()
    adapter._http.post = AsyncMock(side_effect=mock_post)
    return adapter, acks


@pytest.mark.asyncio
async def test_discord_non_admin_cannot_approve(monkeypatch):
    """allow_all + a configured admin ⇒ everyone else is refused."""
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "discord:the_operator")
    adapter, acks = _discord_adapter()

    with patch(
        "kazma_gateway.adapters.discord_callbacks.route_swarm_bus",
        return_value="task_1",
    ) as routed:
        await adapter._handle_interaction(SWARM_INTERACTION)

    # The approval must never reach the bus.
    routed.assert_not_called()
    assert acks, "the user should get a refusal, not silence"
    assert "Admin privilege required" in acks[0]["data"]["content"]


@pytest.mark.asyncio
async def test_discord_admin_can_still_approve(monkeypatch):
    """The gate must not break the operator it exists to protect."""
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "discord:outsider_9")
    adapter, acks = _discord_adapter()

    with patch(
        "kazma_gateway.adapters.discord_callbacks.route_swarm_bus",
        return_value="task_1",
    ):
        await adapter._handle_interaction(SWARM_INTERACTION)

    assert acks
    assert "Admin privilege required" not in acks[0]["data"]["content"]


@pytest.mark.asyncio
async def test_telegram_non_admin_cannot_answer_hitl(monkeypatch):
    from kazma_gateway.adapters.telegram import TelegramAdapter

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:the_operator")
    adapter = TelegramAdapter(token="fake", allow_all=True)

    answers: list[tuple] = []

    async def fake_answer(cb_id, text=None, *a, **kw):
        answers.append((cb_id, text))

    adapter._answer_callback_query = fake_answer  # type: ignore[assignment]
    adapter._spawn = lambda coro, **kw: __import__("asyncio").ensure_future(coro)

    queued: list = []
    adapter._queue = MagicMock()
    adapter._queue.put = AsyncMock(side_effect=lambda m: queued.append(m))

    await adapter._handle_callback_query(
        {
            "id": "cb_1",
            "data": "hitl:approve:req_1",
            "message": {"chat": {"id": 1}, "message_id": 2},
            "from": {"id": 424242},
        }
    )
    # Let the spawned answer coroutine run.
    import asyncio

    await asyncio.sleep(0)

    assert not queued, "a non-admin approval must not enqueue an agent turn"
    assert any(
        txt and "Not authorized" in txt for _cb, txt in answers
    ), f"expected a refusal, got {answers!r}"


@pytest.mark.parametrize(
    "admins,sender,platform,expected,why",
    [
        ("telegram:12345", "telegram:12345", "telegram", True, "qualified, same platform"),
        # The bug: `_candidate_tokens` expanded `telegram:12345` to its bare
        # tail `12345`, so a Discord snowflake that happened to equal 12345
        # became admin on a platform the operator never named. Telegram and
        # Discord ids are both numeric, so this is reachable.
        ("telegram:12345", "discord:12345", "discord", False, "qualified, OTHER platform"),
        ("telegram:12345", "telegram:999", "telegram", False, "different id"),
        # An unqualified entry is the operator saying "this id anywhere".
        ("12345", "telegram:12345", "telegram", True, "bare entry, any platform"),
        ("12345", "discord:12345", "discord", True, "bare entry, any platform"),
        ("12345", "discord:999", "discord", False, "different id"),
    ],
)
def test_admin_ids_are_platform_scoped(
    monkeypatch, admins, sender, platform, expected, why
):
    """A `platform:id` admin entry grants admin on that platform only."""
    from kazma_gateway.allowlists import is_gateway_admin

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", admins)
    assert is_gateway_admin(sender, platform) is expected, why


def test_gate_covers_every_adapter():
    """All three adapters gate `hitl`/`swarm`, not just installs.

    A source check, deliberately: the Slack branch lives inside a deeply
    nested Socket-Mode reader that is impractical to drive end-to-end, and
    "we only tested two of the three platforms" is how this class of gap
    appeared in the first place.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in (
        "kazma-gateway/kazma_gateway/adapters/telegram.py",
        "kazma-gateway/kazma_gateway/adapters/discord.py",
        "kazma-gateway/kazma_gateway/adapters/slack.py",
    ):
        src = (root / rel).read_text(encoding="utf-8", errors="replace")
        assert 'action.kind in ("hitl", "swarm")' in src, (
            f"{rel} does not admin-gate approval callbacks (audit F-8). "
            "Approving a danger tool must be at least as privileged as "
            "installing a package."
        )
        assert "is_gateway_admin" in src
