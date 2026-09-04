"""Target-id routing guards (2026-09-04 connector audit).

Two latent bugs found by the in-app audit and confirmed by the deep check:

1. ``resolve_channel_id`` (Discord + Slack) fell back to
   ``split(":", 1)[1]`` — on a dual-colon Discord DM sender id
   (``discord:{user_id}:{channel_id}``) that yields ``user_id:channel_id``,
   an invalid snowflake. The fallback now takes the LAST segment.
2. ``_build_target_id`` ignored ``discord_thread_id``, so replies to a
   Discord thread routed to the parent channel. Threads ARE channels in
   the Discord API — the thread id is the correct target.
"""

from __future__ import annotations

from kazma_gateway.adapters.discord_send import resolve_channel_id as dc_resolve
from kazma_gateway.adapters.slack_send import resolve_channel_id as slack_resolve
from kazma_gateway.agent_handler.store import _build_target_id


class TestResolveChannelId:
    def test_dual_colon_discord_dm_target_resolves_to_channel(self) -> None:
        assert dc_resolve({}, "discord:111222333444:999888777666") == "999888777666"

    def test_single_colon_target_still_resolves(self) -> None:
        assert dc_resolve({}, "discord:999888777666") == "999888777666"
        assert slack_resolve({}, "slack:C0123ABCD") == "C0123ABCD"

    def test_metadata_channel_id_wins_over_target(self) -> None:
        assert dc_resolve({"channel_id": "C1"}, "discord:U1:C2") == "C1"
        assert slack_resolve({"channel_id": "C1"}, "slack:U1") == "C1"

    def test_slack_dual_colon_takes_last_segment(self) -> None:
        # Mirrors the Discord hardening; Slack ids aren't dual-colon today,
        # but the resolver must not misroute if one ever arrives.
        assert slack_resolve({}, "slack:U0123:C0123ABCD") == "C0123ABCD"


class TestBuildTargetId:
    def test_telegram_uses_chat_id(self) -> None:
        assert _build_target_id("telegram", {"chat_id": 1804015016}) == "telegram:1804015016"

    def test_discord_thread_preferred_over_channel(self) -> None:
        """A reply to a Discord thread must land IN the thread (thread ids
        are channel ids in the Discord API), not the parent channel."""
        target = _build_target_id("discord", {
            "channel_id": "999888777666",
            "user_id": "111222333444",
            "discord_thread_id": "1234567890123456789",
        })
        assert target == "discord:1234567890123456789"

    def test_discord_without_thread_uses_channel(self) -> None:
        assert _build_target_id("discord", {"channel_id": "999888777666"}) == "discord:999888777666"

    def test_thread_key_ignored_for_other_platforms(self) -> None:
        # The discord_thread_id key must not hijack non-discord routing.
        assert _build_target_id("slack", {
            "channel_id": "C0123",
            "discord_thread_id": "123",
        }) == "slack:C0123"

    def test_unknown_when_no_ids(self) -> None:
        assert _build_target_id("telegram", {}) == "telegram:unknown"
