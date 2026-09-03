"""Delivery & Routing v3 (2026-09-03) — behavioral tests.

The v2 card shipped two dead wires these tests keep from coming back:

1. ``notifications.swarm.routes`` had a UI selector and NO consumer —
   checkboxes that did nothing.
2. The ops-alert channel filter read ``getattr(adapter, "name", "")`` while
   no bus adapter had a ``name`` attribute — selecting any channel
   silently dropped the alert from the bus.

Plus the origin-dedupe regression: the old outer
``if not _output_target_is_origin(...)`` guard skipped the ENTIRE send when
dispatching from the configured group, which with route fan-out would have
starved every other selected route.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from kazma_gateway.agent_handler.swarm_dispatch import (
    _maybe_send_to_output_target,
    _swarm_route_config,
    _swarm_routes,
    _target_is_origin_chat,
)
from kazma_gateway.gateway import IncomingMessage


# ── Fakes ────────────────────────────────────────────────────────────────


class _FakeStore:
    """ConfigStore double: plain dict behind get()."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


@pytest.fixture
def config_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    store = _FakeStore()
    monkeypatch.setattr(
        "kazma_core.config_store.get_config_store", lambda: store
    )
    return store


def _msg(platform: str = "telegram", chat_id: str = "111") -> IncomingMessage:
    return IncomingMessage(
        platform=platform,
        sender_id=f"{platform}:1",
        text="hi",
        context_metadata={"chat_id": chat_id},
    )


# ── Route resolution ─────────────────────────────────────────────────────


class TestSwarmRoutesResolution:
    def test_routes_parse_csv_and_list(self, config_store: _FakeStore) -> None:
        config_store._data["notifications.swarm.routes"] = (
            "Telegram, telegram-group ,discord,"
        )
        assert _swarm_routes() == ["telegram", "telegram-group", "discord"]
        config_store._data["notifications.swarm.routes"] = ["slack"]
        assert _swarm_routes() == ["slack"]

    def test_routes_empty_when_unset(self, config_store: _FakeStore) -> None:
        assert _swarm_routes() == []

    def test_group_route_resolves_output_target(
        self, config_store: _FakeStore
    ) -> None:
        config_store._data["swarm.output_target"] = {
            "platform": "telegram",
            "chat_id": -100123,
            "enabled": True,
            "bot_token": "987:abc",
        }
        cfg = _swarm_route_config("telegram-group")
        assert cfg is not None
        assert cfg["chat_id"] == -100123
        assert cfg["bot_token"] == "987:abc"

    def test_group_route_none_when_disabled(
        self, config_store: _FakeStore
    ) -> None:
        config_store._data["swarm.output_target"] = {
            "chat_id": -100123,
            "enabled": False,
        }
        assert _swarm_route_config("telegram-group") is None

    def test_platform_routes_resolve_their_channel_keys(
        self, config_store: _FakeStore
    ) -> None:
        config_store._data["connectors.telegram.swarm_chat_id"] = "1804"
        config_store._data["connectors.discord.swarm_channel_id"] = "999"
        config_store._data["connectors.slack.swarm_channel_id"] = "C123"

        tg = _swarm_route_config("telegram")
        assert tg == {"platform": "telegram", "chat_id": "1804", "enabled": True}
        assert _swarm_route_config("discord")["chat_id"] == "999"
        assert _swarm_route_config("slack")["chat_id"] == "C123"

    def test_unconfigured_route_is_none_not_fallback(
        self, config_store: _FakeStore
    ) -> None:
        """A selected route with no saved chat id is SKIPPED, never silently
        re-routed to another platform."""
        assert _swarm_route_config("discord") is None
        assert _swarm_route_config("bogus-platform") is None


# ── Fan-out behaviour ────────────────────────────────────────────────────


@dataclass
class _SendCapture:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, manager: Any, text: str, cfg: Any, **kw: Any) -> bool:
        self.calls.append({"text": text, "cfg": cfg, **kw})
        return True


class TestMaybeSendToOutputTarget:
    def test_selected_routes_each_receive_the_report(
        self, config_store: _FakeStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_store._data["notifications.swarm.routes"] = [
            "telegram",
            "telegram-group",
            "discord",
        ]
        config_store._data["connectors.telegram.swarm_chat_id"] = "1804"
        config_store._data["connectors.discord.swarm_channel_id"] = "999"
        config_store._data["swarm.output_target"] = {
            "platform": "telegram",
            "chat_id": -100123,
            "enabled": True,
        }
        cap = _SendCapture()
        monkeypatch.setattr(
            "kazma_gateway.agent_handler.swarm_dispatch.send_swarm_output", cap
        )

        sent = asyncio.run(
            _maybe_send_to_output_target(None, "report", origin=_msg())
        )
        assert sent is True
        assert [c["cfg"]["chat_id"] for c in cap.calls] == [
            "1804",
            -100123,
            "999",
        ]

    def test_origin_chat_route_is_skipped_but_others_send(
        self, config_store: _FakeStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dispatching FROM the group must not starve the other selected
        routes (the old outer guard skipped the entire send)."""
        config_store._data["notifications.swarm.routes"] = [
            "telegram-group",
            "discord",
        ]
        config_store._data["swarm.output_target"] = {
            "platform": "telegram",
            "chat_id": -100123,
            "enabled": True,
        }
        config_store._data["connectors.discord.swarm_channel_id"] = "999"
        cap = _SendCapture()
        monkeypatch.setattr(
            "kazma_gateway.agent_handler.swarm_dispatch.send_swarm_output", cap
        )

        sent = asyncio.run(
            _maybe_send_to_output_target(
                None, "report", origin=_msg(chat_id="-100123")
            )
        )
        assert sent is True
        assert [c["cfg"]["chat_id"] for c in cap.calls] == ["999"]

    def test_override_suffix_wins_over_selected_routes(
        self, config_store: _FakeStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_store._data["notifications.swarm.routes"] = ["telegram"]
        config_store._data["connectors.telegram.swarm_chat_id"] = "1804"
        cap = _SendCapture()
        monkeypatch.setattr(
            "kazma_gateway.agent_handler.swarm_dispatch.send_swarm_output", cap
        )
        override = {"platform": "telegram", "chat_id": 4242, "enabled": True}

        asyncio.run(
            _maybe_send_to_output_target(None, "r", override, origin=_msg())
        )
        assert len(cap.calls) == 1
        assert cap.calls[0]["cfg"] is override

    def test_no_routes_falls_back_to_legacy_output_target(
        self, config_store: _FakeStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_store._data["swarm.output_target"] = {
            "platform": "telegram",
            "chat_id": -100123,
            "enabled": True,
        }
        cap = _SendCapture()
        monkeypatch.setattr(
            "kazma_gateway.agent_handler.swarm_dispatch.send_swarm_output", cap
        )

        sent = asyncio.run(_maybe_send_to_output_target(None, "r", origin=_msg()))
        assert sent is True
        assert cap.calls[0]["cfg"]["chat_id"] == -100123

    def test_target_is_origin_chat_matches_platform_and_id(self) -> None:
        cfg = {"platform": "telegram", "chat_id": "-100123"}
        assert _target_is_origin_chat(cfg, _msg(chat_id="-100123")) is True
        assert _target_is_origin_chat(cfg, _msg(chat_id="111")) is False
        discord_cfg = {"platform": "discord", "chat_id": "-100123"}
        assert _target_is_origin_chat(discord_cfg, _msg(chat_id="-100123")) is False
        assert _target_is_origin_chat(None, _msg()) is False


# ── Bus adapter names (ops-alert channel selection) ─────────────────────


class _FakeConnectorStore:
    """ConfigStore double for the connectors router (get/set/get_all)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get_all(self) -> dict[str, dict[str, Any]]:
        return {"connectors": dict(self._data)}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any, category: str = "connectors") -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class TestMaskedExtrasRoundTrip:
    def test_masked_app_token_sent_back_does_not_clobber_the_secret(self) -> None:
        """GET /api/connectors masks secret extras (slack app_token →
        ``****abcd``). The old per-platform dialog POSTed that mask back on
        every save, silently replacing the real credential with the mask
        string. The card v4 round-trips through the same endpoints, so the
        server must skip masked extras."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kazma_ui.providers import create_providers_router

        store = _FakeConnectorStore()
        app = FastAPI()
        app.include_router(create_providers_router(store))  # type: ignore[arg-type]
        client = TestClient(app)

        # Save a real slack connector with an app token.
        resp = client.post("/api/connectors", json={
            "name": "slack", "token": "xoxb-real", "enabled": True,
            "extras": {"app_token": "xapp-1-real-secret", "workspace": "acme"},
        })
        assert resp.status_code == 200

        # Load it back the way the UI does — the app token comes masked.
        entry = next(c for c in client.get("/api/connectors").json()
                     if c["name"] == "slack")
        assert entry["extras"]["app_token"].startswith("****")

        # Re-save the entry unchanged (what a form submit does).
        resp = client.post("/api/connectors", json={
            "name": "slack", "token": entry["token"], "enabled": entry["enabled"],
            "extras": entry["extras"],
        })
        assert resp.status_code == 200

        # The stored credential survived the masked round-trip.
        assert store.get("connectors.slack.app_token") == "xapp-1-real-secret"
        # Non-secret extras still update normally.
        assert store.get("connectors.slack.workspace") == "acme"


class TestBusAdapterNames:
    def test_every_bus_adapter_has_a_routing_name(self) -> None:
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

        assert TelegramBusAdapter("1:a", 1).name == "telegram"
        assert DiscordBusAdapter("d", "9").name == "discord"
        assert SlackBusAdapter("xoxb-x", "C1").name == "slack"

    def test_ops_alerts_channel_filter_selects_by_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The v1/v2 filter read a name attribute no adapter had — any
        selection matched nothing and the alert was silently dropped."""
        from kazma_core.observability import ops_alerts

        sent: list[str] = []

        class _Named:
            def __init__(self, name: str) -> None:
                self.name = name

            async def send(self, message: Any) -> None:
                sent.append(self.name)

        import kazma_core.swarm.bus as bus_mod

        fan = bus_mod.FanOutBusAdapter(
            [_Named("telegram"), _Named("discord")]
        )

        class _Bus:
            adapter = fan

        monkeypatch.setattr(bus_mod, "get_message_bus", lambda: _Bus())
        monkeypatch.setattr(ops_alerts, "_ops_channels", lambda: ["discord"])

        delivered = asyncio.run(ops_alerts._deliver("x"))
        assert delivered is True
        assert sent == ["discord"]
