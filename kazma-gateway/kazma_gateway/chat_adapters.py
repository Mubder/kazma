"""The chat apps' adapters and swarm senders, built from saved settings by one builder.

Boot, Settings' Refresh and every saved setting build Telegram, Discord,
Slack and WhatsApp here. Until 2026-10-01 boot and Refresh each kept a copy of this code,
and the copies drifted:

* a token saved in Settings reached no running adapter -- the adapter kept
  the revoked one, and the Test reported its ``invalid_auth`` for a token
  that worked, until a separate Refresh;
* a Refresh started a platform switched off, dropped Telegram's webhook
  secret and left every adapter fail-closed (the boot copy then let everyone
  in when no allowlist was set), and each old adapter sat out a 5 s stop
  grace;
* the on/off switch in Settings was read by nothing (boot read kazma.yaml);
* the swarm bus kept the bot token and destination it booted with.

``ChatAdapters`` owns the running ones. It listens to the settings store
(``ConfigStore.add_change_listener``) and, whoever wrote the setting -- a
route, a restore, the terminal app, the agent -- rebuilds a platform's
adapter when its adapter settings changed, its swarm sender when its bus
settings did, and re-applies allowlists (and the owner's own ids Kazma
knows, ``allowlists.known_owner_ids``) in place.

Who an adapter answers is ``sender_rule``'s: since 2026-11-11 a chat app
with an empty allowlist is private (its owner's own account Kazma knows, or
no one), and ``KAZMA_GATEWAY_OPEN_TO_EVERYONE`` is the explicit way back to
letting everyone in. Each build says, as a WARNING, who an adapter with an
empty allowlist answers and how to fix it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from kazma_core.env_files import env_credential
from kazma_gateway.allowlists import OWNER_ID_KEYS, OwnerId, apply_adapter_allowlists, known_owner_ids, split_ids
from kazma_gateway.gateway import BaseAdapter, GatewayManager
from kazma_gateway.sender_rule import OPEN_SWITCH, STRICT_SWITCH, open_to_everyone

logger = logging.getLogger(__name__)

__all__ = [
    "CHAT_PLATFORMS",
    "ChatAdapters",
]

CHAT_PLATFORMS: tuple[str, ...] = ("telegram", "discord", "slack", "whatsapp")

_LABELS = {"telegram": "Telegram", "discord": "Discord", "slack": "Slack", "whatsapp": "WhatsApp"}

#: ``connectors.<platform>.<field>`` values a running adapter is built from: a
#: change rebuilds that platform's adapter.
ADAPTER_FIELDS: dict[str, tuple[str, ...]] = {
    "telegram": ("enabled", "token", "webhook_secret"),
    "discord": ("enabled", "token"),
    "slack": ("enabled", "token", "app_token"),
    "whatsapp": ("enabled", "token", "phone_number_id", "app_secret", "verify_token", "api_version"),
}

#: Allowlists: a change is applied to the running adapter in place.
ALLOWLIST_FIELDS: dict[str, tuple[str, ...]] = {
    "telegram": ("allowed_users",),
    "discord": ("allowed_users", "guild_id"),
    "slack": ("allowed_users", "allowed_teams", "allowed_channels"),
    "whatsapp": ("allowed_users",),
}

#: The swarm bus's sender for a platform: switch, bot token, destination.
BUS_FIELDS: dict[str, tuple[str, ...]] = {
    "telegram": ("enabled", "token", "swarm_chat_id"),
    "discord": ("enabled", "token", "swarm_channel_id"),
    "slack": ("enabled", "token", "swarm_channel_id"),
    # No swarm sender: WhatsApp lets a business message a person only within
    # 24 hours of their last message (outside it, approved templates only), so
    # a swarm card could not reach the owner reliably.
    "whatsapp": (),
}

#: Settings outside ``connectors.<platform>.`` that name the owner's own
#: account on a platform: a change re-applies that platform's allowlists.
_OWNER_KEYS: dict[str, str] = {
    key: platform for platform, keys in OWNER_ID_KEYS.items() for key in keys
}

#: Writes come in bursts (a connector save sets five or six keys): apply once,
#: this long after the last.
_SETTLE_S = 0.5

#: How long the Test waits for a rebuilt adapter's first connection attempt.
#: Slack and Telegram answer in a second or two.
FIRST_OUTCOME_WAIT_S = 10.0


def _watched_key(key: str) -> bool:
    """Whether a change to this setting concerns a chat adapter or swarm sender."""
    if key in _OWNER_KEYS:
        return True
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != "connectors" or parts[1] not in CHAT_PLATFORMS:
        return False
    platform, name = parts[1], parts[2]
    return (
        name in ADAPTER_FIELDS[platform]
        or name in ALLOWLIST_FIELDS[platform]
        or name in BUS_FIELDS[platform]
    )


@dataclass(frozen=True)
class _PlatformSettings:
    """What one platform's adapter and swarm sender are built from.

    Credentials are left out of the repr: this object is logged and compared.
    """

    platform: str
    enabled: bool
    token: str = field(default="", repr=False)
    app_token: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    #: WhatsApp: the business phone number's id, the Meta app's secret (it
    #: signs every webhook call), the webhook's verify token, the Graph API version.
    phone_number_id: str = ""
    app_secret: str = field(default="", repr=False)
    verify_token: str = field(default="", repr=False)
    api_version: str = ""
    swarm_target: str = ""
    allowlists: tuple[str, ...] = ()
    #: The settings naming the owner's own account here (OWNER_ID_KEYS).
    owners: tuple[str, ...] = ()

    def adapter_key(self) -> tuple[Any, ...]:
        return (self.enabled, self.token, self.app_token, self.webhook_secret,
                self.phone_number_id, self.app_secret, self.verify_token, self.api_version)

    def sender_key(self) -> tuple[Any, ...]:
        return (self.enabled, self.token, self.swarm_target)


def _text(config_store: Any, key: str) -> str:
    value = config_store.get(key, "")
    return "" if value is None else str(value).strip()


def _switched_on(value: Any) -> bool:
    """``connectors.<platform>.enabled``: on unless it says off."""
    if value is None or value == "":
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def _read_platform_settings(config_store: Any, platform: str) -> _PlatformSettings:
    """One platform's settings, from the store with the environment as the
    fallback for tokens (blocking: a store read)."""
    prefix = f"connectors.{platform}."
    enabled = _switched_on(config_store.get(prefix + "enabled", True))
    allowlists = tuple(_text(config_store, prefix + name) for name in ALLOWLIST_FIELDS[platform])
    owners = tuple(_text(config_store, key) for key in OWNER_ID_KEYS.get(platform, ()))
    if platform == "telegram":
        return _PlatformSettings(
            platform,
            enabled,
            token=_text(config_store, prefix + "token") or env_credential("TELEGRAM_BOT_TOKEN"),
            webhook_secret=(
                _text(config_store, prefix + "webhook_secret")
                or os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
            ),
            swarm_target=_text(config_store, prefix + "swarm_chat_id"),
            allowlists=allowlists,
            owners=owners,
        )
    if platform == "discord":
        return _PlatformSettings(
            platform,
            enabled,
            token=_text(config_store, prefix + "token") or env_credential("DISCORD_BOT_TOKEN"),
            swarm_target=_text(config_store, prefix + "swarm_channel_id"),
            allowlists=allowlists,
            owners=owners,
        )
    if platform == "slack":
        # A form that saved its mask ("***3554") stored no token: such a value
        # is not one, and the environment's is used instead.
        bot = _text(config_store, prefix + "token")
        app = _text(config_store, prefix + "app_token")
        return _PlatformSettings(
            platform,
            enabled,
            token=bot if bot.startswith("xoxb-") else env_credential("SLACK_BOT_TOKEN"),
            app_token=app if app.startswith("xapp-") else env_credential("SLACK_APP_TOKEN"),
            swarm_target=_text(config_store, prefix + "swarm_channel_id"),
            allowlists=allowlists,
            owners=owners,
        )
    if platform == "whatsapp":
        # Settings only: the owner enters Meta's values on the WhatsApp card.
        return _PlatformSettings(
            platform,
            enabled,
            token=_text(config_store, prefix + "token"),
            phone_number_id=_text(config_store, prefix + "phone_number_id"),
            app_secret=_text(config_store, prefix + "app_secret"),
            verify_token=_text(config_store, prefix + "verify_token"),
            api_version=_text(config_store, prefix + "api_version"),
            allowlists=allowlists,
            owners=owners,
        )
    raise ValueError(f"not a chat platform: {platform!r}")


def _say_who_it_answers(
    platform: str, allowed_users: str, owners: list[OwnerId], open_switch: bool
) -> None:
    """One WARNING for an adapter whose allowlist is empty: who it answers
    (sender_rule), and the fix. Nothing when an allowlist is set."""
    if split_ids(allowed_users):
        return
    label = _LABELS[platform]
    where = f"connectors.{platform}.allowed_users"
    if platform == "whatsapp":
        fix = "Put your WhatsApp number in Settings → Adapters & Routes → WhatsApp → Allowed numbers"
    else:
        fix = f"Put your {label} user id in Settings → Adapters & Routes → {label} → Allowed User IDs"
    if open_switch:
        logger.warning(
            "[Gateway] %s lets everyone in: its allowlist is empty (%s) and %s is on. %s, then turn %s off",
            label, where, OPEN_SWITCH, fix, OPEN_SWITCH,
        )
    elif owners:
        known = ", ".join(f"{o.user_id} from {o.source}" for o in owners)
        logger.warning(
            "[Gateway] %s is private: its allowlist is empty (%s), so it answers only the owner's own "
            "account Kazma knows (%s) and refuses everyone else. %s to make it explicit",
            label, where, known, fix,
        )
    else:
        logger.warning(
            "[Gateway] %s answers no one: its allowlist is empty (%s) and Kazma knows no %s account "
            "of its owner's. %s",
            label, where, label, fix,
        )


def _build_chat_adapter(
    settings: _PlatformSettings,
    config_store: Any,
    *,
    voice: Mapping[str, Any] | None = None,
) -> BaseAdapter | None:
    """The adapter *settings* describe, allowlists applied -- or None when the
    platform is switched off or has no token (the log says which). Blocking:
    the allowlists are store reads."""
    platform = settings.platform
    label = _LABELS[platform]
    if not settings.enabled:
        logger.info("[Gateway] %s is switched off (connectors.%s.enabled) -- not started", label, platform)
        return None
    if not settings.token:
        logger.info("[Gateway] No %s token -- %s adapter not started", label, label)
        return None
    allow_all = open_to_everyone()
    adapter: BaseAdapter
    if platform == "telegram":
        from kazma_gateway.adapters.telegram import TelegramAdapter

        voice_cfg = dict(voice or {})
        adapter = TelegramAdapter(
            token=settings.token,
            voice_enabled=voice_cfg.get("enabled", False),
            voice_provider=voice_cfg.get("stt_provider", "openai"),
            stt_api_key=None,  # the STT provider reads its key from the environment
            tts_provider=voice_cfg.get("tts_provider", "edgetts"),
            tts_voice=voice_cfg.get("tts_voice", "default"),
            tts_output_format=voice_cfg.get("tts_output_format", "mp3"),
            stt_language=voice_cfg.get("stt_language", "auto"),
            webhook_secret=settings.webhook_secret or None,
            allow_all=allow_all,
        )
    elif platform == "discord":
        from kazma_gateway.adapters.discord import DiscordAdapter

        adapter = DiscordAdapter(token=settings.token, allow_all=allow_all)
    elif platform == "whatsapp":
        from kazma_gateway.adapters.whatsapp import WhatsAppAdapter
        from kazma_gateway.adapters.whatsapp_api import DEFAULT_API_VERSION

        if not settings.phone_number_id:
            logger.info("[Gateway] No WhatsApp phone number id -- WhatsApp adapter not started")
            return None
        adapter = WhatsAppAdapter(
            settings.token,
            settings.phone_number_id,
            app_secret=settings.app_secret,
            verify_token=settings.verify_token,
            api_version=settings.api_version or DEFAULT_API_VERSION,
            allow_all=allow_all,
        )
        if not (settings.app_secret and settings.verify_token):
            logger.warning(
                "[Gateway] WhatsApp has no %s: Meta's webhook calls are refused until it is saved "
                "(Settings → Adapters & Routes → WhatsApp)",
                "app secret" if not settings.app_secret else "verify token",
            )
    else:
        from kazma_gateway.adapters.slack import SlackAdapter

        adapter = SlackAdapter(
            bot_token=settings.token,
            app_token=settings.app_token or None,
            allow_all=allow_all,
        )
    apply_adapter_allowlists(adapter, config_store)
    _say_who_it_answers(
        platform,
        dict(zip(ALLOWLIST_FIELDS[platform], settings.allowlists)).get("allowed_users", ""),
        known_owner_ids(config_store, platform),
        allow_all,
    )
    if platform == "slack":
        how = "Socket Mode" if settings.app_token else "polling mode -- no app token"
        logger.info("[Gateway] Slack adapter built (%s)", how)
    else:
        logger.info("[Gateway] %s adapter built", label)
    return adapter


def _build_swarm_sender(settings: _PlatformSettings) -> Any | None:
    """The swarm bus's sender for one platform, or None (switched off, no
    token, or -- Discord and Slack -- no destination channel)."""
    if not (settings.enabled and settings.token) or not BUS_FIELDS.get(settings.platform):
        return None
    if settings.platform == "telegram":
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

        return TelegramBusAdapter(bot_token=settings.token, chat_id=settings.swarm_target)
    if not settings.swarm_target:
        return None
    if settings.platform == "discord":
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

        return DiscordBusAdapter(bot_token=settings.token, channel_id=settings.swarm_target)
    from kazma_gateway.adapters.slack_bus import SlackBusAdapter

    return SlackBusAdapter(bot_token=settings.token, channel_id=settings.swarm_target)


def _say_the_open_switch() -> None:
    """At every boot while the open switch is on: a WARNING that a chat app
    with an empty allowlist takes anyone's messages (or that the old strict
    switch, set too, wins)."""
    from kazma_core.security.boot_guard import env_flag

    if not env_flag(OPEN_SWITCH):
        return
    if env_flag(STRICT_SWITCH):
        logger.warning(
            "[Gateway] %s is on, but %s is set too and wins: a chat app with an empty allowlist "
            "stays private. Unset one of them",
            OPEN_SWITCH, STRICT_SWITCH,
        )
        return
    logger.warning(
        "[Gateway] %s is on: a chat app with an empty allowlist answers anyone who can message "
        "its bot. Set each one's Allowed User IDs and turn the switch off",
        OPEN_SWITCH,
    )


class ChatAdapters:
    """The gateway's chat adapters and the swarm bus's senders, kept in step
    with the saved settings.

    *swarm_bus* None leaves the bus alone (under pytest: a test must never
    wire a real sender). The builders are parameters so a test can watch what
    is built without reaching a platform.
    """

    def __init__(
        self,
        gateway: GatewayManager,
        config_store: Any,
        *,
        voice: Mapping[str, Any] | None = None,
        swarm_bus: Any = None,
        build_adapter: Callable[..., BaseAdapter | None] = _build_chat_adapter,
        build_sender: Callable[[_PlatformSettings], Any | None] = _build_swarm_sender,
    ) -> None:
        self._gateway = gateway
        self._store = config_store
        self._voice = dict(voice or {})
        self._bus = swarm_bus
        self._build_adapter = build_adapter
        self._build_sender = build_sender
        self._settings: dict[str, _PlatformSettings] = {}
        self._senders: dict[str, Any] = {}
        self._sender_settings: dict[str, _PlatformSettings] = {}
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: asyncio.TimerHandle | None = None
        self._unsubscribe: Callable[[], None] | None = None

    # ── Boot ──────────────────────────────────────────────────────────

    def build(self) -> dict[str, _PlatformSettings]:
        """Build every platform's adapter onto the gateway, and the swarm
        senders, from the settings now (at boot: blocking, no loop needed)."""
        settings = self._read_all()
        _say_the_open_switch()
        for platform in CHAT_PLATFORMS:
            adapter = self._build_adapter(settings[platform], self._store, voice=self._voice)
            if adapter is not None:
                self._gateway.add_adapter(adapter)
        self._settings = settings
        self._wire_senders(settings)
        return settings

    # ── At run time ───────────────────────────────────────────────────

    async def apply(self, platforms: Iterable[str] | None = None, *, force: bool = False) -> list[str]:
        """Bring the running adapters and senders in line with the settings.

        A platform's adapter is rebuilt when its adapter settings changed (every
        named one with *force*: Settings' Refresh), its allowlists re-applied in
        place when only they changed, and the swarm senders rewired when a
        sender's settings changed. Returns the platforms whose adapter was
        rebuilt. Concurrent calls run one after another.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        names = tuple(platforms or CHAT_PLATFORMS)
        async with self._lock:
            fresh = await asyncio.to_thread(self._read_all)
            rebuilt: list[str] = []
            for platform in names:
                new = fresh[platform]
                old = self._settings.get(platform)
                if force or old is None or old.adapter_key() != new.adapter_key():
                    adapter = await asyncio.to_thread(self._build_adapter, new, self._store, voice=self._voice)
                    if adapter is not None or self._gateway.adapter_named(platform) is not None:
                        await self._gateway.replace_adapter(platform, adapter)
                        rebuilt.append(platform)
                        logger.info(
                            "[Gateway] %s adapter %s from the saved settings",
                            _LABELS[platform],
                            "rebuilt" if adapter is not None else "stopped",
                        )
                elif old.allowlists != new.allowlists or old.owners != new.owners:
                    running = self._gateway.adapter_named(platform)
                    if running is not None:
                        await asyncio.to_thread(apply_adapter_allowlists, running, self._store)
                self._settings[platform] = new
            if self._bus is not None:
                await asyncio.to_thread(self._wire_senders, fresh)
            return rebuilt

    async def first_outcome(self, platform: str, timeout: float = FIRST_OUTCOME_WAIT_S) -> None:
        """Wait until the platform's adapter has connected or met a problem.

        A Test run right after a rebuild otherwise reports a connection that
        has not made its first attempt yet. An adapter that has been failing
        for a while has its problem recorded and is not waited for.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            adapter = self._gateway.adapter_named(platform)
            if adapter is None:
                return
            state, why = adapter.connection_state()
            if state != "connecting" or why:
                return
            await asyncio.sleep(0.2)

    def start_watching(self) -> None:
        """Apply every settings change from now on (call on the running loop)."""
        self.stop_watching()
        self._loop = asyncio.get_running_loop()
        self._unsubscribe = self._store.add_change_listener(self._on_settings_changed)

    def stop_watching(self) -> None:
        """Stop applying settings changes (shutdown)."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        self._loop = None

    # ── Internals ─────────────────────────────────────────────────────

    def _read_all(self) -> dict[str, _PlatformSettings]:
        return {platform: _read_platform_settings(self._store, platform) for platform in CHAT_PLATFORMS}

    def _on_settings_changed(self, keys: frozenset[str] | None) -> None:
        """A settings write committed, on any thread: apply it soon if it is ours."""
        if keys is not None and not any(_watched_key(key) for key in keys):
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._schedule)
        except RuntimeError:
            # The loop closed between the check and the call: shutting down.
            return

    def _schedule(self) -> None:
        loop = self._loop
        if loop is None:
            return
        if self._pending is not None:
            self._pending.cancel()
        self._pending = loop.call_later(_SETTLE_S, self._apply_now)

    def _apply_now(self) -> None:
        from kazma_core.background import spawn_background

        self._pending = None
        # A failure is logged by the background runner, traceback and all.
        spawn_background(self.apply(), name="chat-adapters-apply")

    def _wire_senders(self, settings: Mapping[str, _PlatformSettings]) -> None:
        """Rebuild the senders whose settings changed -- an unchanged one is
        kept, with the approvals it is waiting on -- and give the bus the set."""
        if self._bus is None:
            return
        from kazma_core.swarm.bus import FanOutBusAdapter, NullBusAdapter

        changed = False
        for platform in CHAT_PLATFORMS:
            new = settings[platform]
            old = self._sender_settings.get(platform)
            if old is not None and old.sender_key() == new.sender_key():
                continue
            self._senders.pop(platform, None)
            sender = self._build_sender(new)
            if sender is not None:
                self._senders[platform] = sender
                logger.info("[SwarmBus] %s ready", type(sender).__name__)
            self._sender_settings[platform] = new
            changed = True
        if not changed:
            return
        senders = [self._senders[p] for p in CHAT_PLATFORMS if p in self._senders]
        if len(senders) == 1:
            self._bus.set_adapter(senders[0])
            logger.info("[SwarmBus] Single adapter wired: %s", type(senders[0]).__name__)
        elif senders:
            self._bus.set_adapter(FanOutBusAdapter(senders))
            logger.info(
                "[SwarmBus] FanOutBusAdapter wired with %d platforms: %s",
                len(senders),
                ", ".join(type(s).__name__ for s in senders),
            )
        else:
            self._bus.set_adapter(NullBusAdapter())
            logger.info("[SwarmBus] No platform adapter -- swarm events stay internal (NullBusAdapter)")
