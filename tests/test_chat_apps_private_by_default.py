"""A chat app with an empty allowlist is private by default (2026-11-11).

Until then the builder forced ``allow_all`` on every adapter unless
``KAZMA_GATEWAY_STRICT_ALLOWLIST`` was set: a bot with no allowlist answered
anyone who could reach it. The owner's decision:

* an empty allowlist answers only the owner's own account Kazma already knows
  (``allowlists.known_owner_ids``: a private Telegram chat Kazma pages the
  owner in, a ``platform:id`` entry of ``KAZMA_GATEWAY_ADMINS``), and refuses
  everyone else (``no_allowlist``, recorded and logged);
* a refused sender in a direct conversation is told once a day that the bot
  is private and what their id is, in their language;
* ``KAZMA_GATEWAY_OPEN_TO_EVERYONE`` brings the old posture back, warned at
  every boot; ``KAZMA_GATEWAY_STRICT_ALLOWLIST`` set too wins;
* every adapter's accept path asks the one rule (``sender_rule.SenderGate``).

Each behaviour runs through the real adapters' accept paths; the old default
(the builder's ``not STRICT``) is the negative control.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from kazma_core import config_store as config_store_module
from kazma_core.config_store import ConfigStore
from kazma_core.language_lock import detect_user_language
from kazma_gateway import sender_rule
from kazma_gateway.allowlists import apply_adapter_allowlists, is_gateway_admin, known_owner_ids
from kazma_gateway.chat_adapters import ChatAdapters, _PlatformSettings
from kazma_gateway.gateway import GatewayManager
from kazma_gateway.sender_rule import NO_ALLOWLIST, NOT_LISTED, SenderGate, refusal

REPO = Path(__file__).resolve().parents[1]
GATEWAY = REPO / "kazma-gateway" / "kazma_gateway"

OWNER = 777001  # the owner's Telegram user id (a private chat's id)
STRANGER = 666002

_ENV = (
    "KAZMA_GATEWAY_OPEN_TO_EVERYONE",
    "KAZMA_GATEWAY_STRICT_ALLOWLIST",
    "KAZMA_GATEWAY_ADMINS",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
)


@pytest.fixture
def store(tmp_path, monkeypatch) -> ConfigStore:
    """A private settings store as the process's one (the notice record and
    the owner's ids are read through ``get_config_store``)."""
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    cs = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    monkeypatch.setattr(config_store_module, "_config_store", cs)
    sender_rule._forget_told_for_tests()
    yield cs
    sender_rule._forget_told_for_tests()


# ── the rule ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("uid, allowed, owners, open_, expected", [
    ("1", [], [], False, NO_ALLOWLIST),          # private, the owner unknown: no one
    ("1", [], ["1"], False, ""),                 # the owner's own account
    ("2", [], ["1"], False, NO_ALLOWLIST),       # anyone else
    ("2", [], [], True, ""),                     # the open switch
    ("", [], [], True, NO_ALLOWLIST),            # a sender with no id is never "everyone"
    ("2", [1], ["2"], True, NOT_LISTED),         # a set allowlist wins over owners and the switch
    ("1", [1], [], False, ""),                   # ints (Telegram) compare as text
])
def test_the_rule(uid, allowed, owners, open_, expected) -> None:
    assert refusal(uid, allowed=allowed, owners=owners, open_to_everyone=open_) == expected


def test_the_switches_read_through_env_flag(monkeypatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    assert sender_rule.open_to_everyone() is False, "private is the default"
    monkeypatch.setenv("KAZMA_GATEWAY_OPEN_TO_EVERYONE", "on")
    assert sender_rule.open_to_everyone() is True
    monkeypatch.setenv("KAZMA_GATEWAY_STRICT_ALLOWLIST", "1")
    assert sender_rule.open_to_everyone() is False, "the old strict switch, set too, wins"
    monkeypatch.delenv("KAZMA_GATEWAY_OPEN_TO_EVERYONE")
    assert sender_rule.open_to_everyone() is False, "strict alone is the default now"


# ── the owner's own ids Kazma knows ─────────────────────────────────────


def test_known_owner_ids_are_private_chats_and_qualified_admins(store, monkeypatch) -> None:
    store.batch_set([
        ("guard.telegram.chat_id", str(OWNER), "guard"),
        ("connectors.telegram.swarm_chat_id", "-1001234567890", "connectors"),  # a group: not a person
    ])
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "discord:4242:999, slack:U77, 31337, telegram:888")
    assert [(o.user_id, o.source) for o in known_owner_ids(store, "telegram")] == [
        (str(OWNER), "guard.telegram.chat_id"), ("888", "KAZMA_GATEWAY_ADMINS")]
    assert [o.user_id for o in known_owner_ids(store, "discord")] == ["4242"]
    assert [o.user_id for o in known_owner_ids(store, "slack")] == ["U77"]
    # An @name is a public channel, never the owner.
    store.set("guard.telegram.chat_id", "@owner_channel", "guard")
    monkeypatch.delenv("KAZMA_GATEWAY_ADMINS")
    assert known_owner_ids(store, "telegram") == []


def test_the_owner_known_is_admin_only_while_the_allowlist_is_empty(store) -> None:
    store.set("guard.telegram.chat_id", str(OWNER), "guard")
    assert is_gateway_admin(f"telegram:{OWNER}", "telegram")
    assert not is_gateway_admin(f"telegram:{STRANGER}", "telegram")
    store.set("connectors.telegram.allowed_users", "123", "connectors")
    assert not is_gateway_admin(f"telegram:{OWNER}", "telegram"), "a set allowlist decides alone"


# ── Telegram: the real update path ──────────────────────────────────────


def _telegram(store: ConfigStore, sent: list[Any], *, allow_all: bool = False):
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:abc", allow_all=allow_all)
    apply_adapter_allowlists(adapter, store)

    async def no_announce(update: dict) -> None:
        return None

    async def no_reaction(*args: Any, **kwargs: Any) -> None:
        return None

    async def record(outbound: Any) -> bool:
        sent.append(outbound)
        return True

    adapter._announce_if_added_to_group = no_announce  # type: ignore[method-assign]
    adapter._set_reaction = no_reaction  # type: ignore[method-assign]
    adapter.send = record  # type: ignore[method-assign]
    adapter._running = True  # a connection that is up: a notice can be sent
    return adapter


def _update(uid: int, mid: int, user: int, *, text: str = "hi", chat: dict | None = None) -> dict:
    return {"update_id": uid, "message": {
        "message_id": mid, "chat": chat or {"id": user, "type": "private"},
        "from": {"id": user, "is_bot": False, "first_name": "B"}, "date": 1, "text": text,
    }}


async def _settle() -> None:
    """Let the background notices (a thread for the record, then the send) finish."""
    for _ in range(250):
        await asyncio.sleep(0.02)
        if not any("private-notice" in t.get_name() for t in asyncio.all_tasks() if not t.done()):
            return
    raise AssertionError("a private notice never finished")


def test_telegram_answers_the_owner_and_refuses_a_stranger_once_a_day(store, caplog) -> None:
    store.set("guard.telegram.chat_id", str(OWNER), "guard")
    sent: list[Any] = []
    adapter = _telegram(store, sent)
    queue: asyncio.Queue = asyncio.Queue()

    async def scenario() -> None:
        await adapter._process_update(_update(1, 1, OWNER), queue)
        await adapter._process_update(_update(2, 2, STRANGER, text="hello there"), queue)
        await adapter._process_update(_update(3, 3, STRANGER, text="hello again"), queue)
        await _settle()

    with caplog.at_level(logging.INFO, logger="kazma_gateway"):
        asyncio.run(scenario())
    assert queue.qsize() == 1 and queue.get_nowait().context_metadata["user_id"] == OWNER
    snap = adapter.diagnostics()
    assert snap["dropped"] == {NO_ALLOWLIST: 2} and snap["owner_ids"] == [str(OWNER)]
    assert len(sent) == 1, "one notice a day, however often the stranger writes"
    notice = sent[0]
    assert notice.target_id == f"telegram:{STRANGER}" and str(STRANGER) in notice.text
    assert "private" in notice.text and "Allowed User IDs" in notice.text
    assert any("was not answered" in r.getMessage() and str(STRANGER) in r.getMessage()
               for r in caplog.records)

    # A restart forgets what this process told; the settings store does not.
    sender_rule._forget_told_for_tests()
    again = _telegram(store, sent)
    asyncio.run(_run_one(again, _update(4, 4, STRANGER)))
    assert len(sent) == 1, "a restart the same day does not tell them again"


async def _run_one(adapter: Any, update: dict) -> None:
    await adapter._process_update(update, asyncio.Queue())
    await _settle()


def test_telegram_tells_a_stranger_in_their_language(store) -> None:
    sent: list[Any] = []
    asyncio.run(_run_one(_telegram(store, sent), _update(1, 1, STRANGER, text="مرحبا، كيف حالك؟")))
    assert len(sent) == 1 and detect_user_language(sent[0].text) == "ar"
    assert str(STRANGER) in sent[0].text


def test_telegram_tells_no_one_in_a_group(store) -> None:
    sent: list[Any] = []
    adapter = _telegram(store, sent)
    asyncio.run(_run_one(adapter, _update(1, 1, STRANGER, chat={"id": -100777, "type": "supergroup"})))
    assert adapter.diagnostics()["dropped"] == {NO_ALLOWLIST: 1}
    assert sent == [], "a stranger in a group is refused without a reply"


def test_a_listed_user_passes_and_an_unlisted_one_is_told(store) -> None:
    store.set("connectors.telegram.allowed_users", f"{OWNER}", "connectors")
    sent: list[Any] = []
    adapter = _telegram(store, sent)
    queue: asyncio.Queue = asyncio.Queue()

    async def scenario() -> None:
        await adapter._process_update(_update(1, 1, OWNER), queue)
        await adapter._process_update(_update(2, 2, STRANGER), queue)
        await _settle()

    asyncio.run(scenario())
    assert queue.qsize() == 1
    assert adapter.diagnostics()["dropped"] == {NOT_LISTED: 1}
    assert len(sent) == 1 and str(STRANGER) in sent[0].text


def test_a_telegram_button_from_a_stranger_is_refused(store) -> None:
    store.set("guard.telegram.chat_id", str(OWNER), "guard")
    adapter = _telegram(store, [])
    assert adapter.actor_allowed(OWNER) and not adapter.actor_allowed(STRANGER)


# ── Discord and Slack: the real accept paths ───────────────────────────


def _discord(store: ConfigStore, sent: list[Any]):
    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="fake:token")
    apply_adapter_allowlists(adapter, store)

    async def record(outbound: Any) -> bool:
        sent.append(outbound)
        return True

    adapter.send = record  # type: ignore[method-assign]
    adapter._running = True
    return adapter


def _discord_msg(mid: str, author: str, *, guild: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"id": mid, "channel_id": f"dm-{author}", "content": "hello",
                            "author": {"id": author, "username": f"n-{author}", "bot": False}}
    if guild:
        data["guild_id"] = guild
    return data


def test_discord_answers_a_qualified_admin_and_tells_a_stranger_in_a_dm(store, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "discord:4242")
    sent: list[Any] = []
    adapter = _discord(store, sent)

    async def scenario() -> list[Any]:
        got = [
            adapter._accept_message(_discord_msg("m1", "4242")),
            adapter._accept_message(_discord_msg("m2", "5555")),
            adapter._accept_message(_discord_msg("m3", "6666", guild="g1")),
        ]
        await _settle()
        return got

    owner, stranger, in_server = asyncio.run(scenario())
    assert owner is not None and stranger is None and in_server is None
    assert adapter.diagnostics()["dropped"] == {NO_ALLOWLIST: 2}
    assert [s.target_id for s in sent] == ["discord:dm-5555"], "told in the DM only, not in the server"


def _slack(store: ConfigStore, sent: list[Any]):
    from kazma_gateway.adapters.slack import SlackAdapter

    adapter = SlackAdapter(bot_token="xoxb-t", app_token="xapp-t")
    apply_adapter_allowlists(adapter, store)

    async def record(outbound: Any) -> bool:
        sent.append(outbound)
        return True

    adapter.send = record  # type: ignore[method-assign]
    adapter._running = True
    return adapter


def test_slack_refuses_a_stranger_on_both_paths_and_tells_them_in_a_dm(store) -> None:
    sent: list[Any] = []
    adapter = _slack(store, sent)

    async def scenario() -> Any:
        got = adapter._accept_event(
            {"type": "message", "ts": "1.0", "user": "U5", "channel": "D5", "text": "hi"})
        await adapter._handle_message("C1", {"ts": "2.0", "user": "U6", "text": "hi"})
        await _settle()
        return got

    assert asyncio.run(scenario()) is None
    assert adapter.diagnostics()["dropped"] == {NO_ALLOWLIST: 2}
    assert [s.target_id for s in sent] == ["slack:D5"], "a channel message gets no reply"
    assert "U5" in sent[0].text


# ── the open switch, the boot warning and the Test ──────────────────────


def _built(store: ConfigStore) -> dict[str, Any]:
    store.batch_set([
        ("connectors.telegram.token", "123:demo-telegram-token", "connectors"),
        ("connectors.discord.token", "demo-discord-token", "connectors"),
        ("connectors.slack.token", "xoxb-demo-bot-token", "connectors"),
        ("connectors.slack.app_token", "xapp-demo-app-token", "connectors"),
    ])
    gateway = GatewayManager()
    ChatAdapters(gateway, store).build()
    return {a.name: a for a in gateway.adapters}


def test_the_builder_makes_every_adapter_private_and_says_so(store, caplog) -> None:
    store.set("guard.telegram.chat_id", str(OWNER), "guard")
    with caplog.at_level(logging.WARNING, logger="kazma_gateway.chat_adapters"):
        built = _built(store)
    assert set(built) == {"telegram", "discord", "slack"}
    assert not any(a._allow_all for a in built.values())
    assert built["telegram"].actor_allowed(OWNER) and not built["telegram"].actor_allowed(STRANGER)
    assert not built["discord"].actor_allowed("5555"), "no owner known on Discord: no one"
    said = {r.getMessage().split(" ")[1]: r.getMessage() for r in caplog.records
            if r.name == "kazma_gateway.chat_adapters"}
    assert set(said) == {"Telegram", "Discord", "Slack"}, "one WARNING per adapter"
    assert "is private" in said["Telegram"] and f"{OWNER} from guard.telegram.chat_id" in said["Telegram"]
    assert "answers no one" in said["Discord"] and "answers no one" in said["Slack"]
    assert all("Allowed User IDs" in line for line in said.values())


def test_the_open_switch_lets_everyone_in_and_warns(store, caplog, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_GATEWAY_OPEN_TO_EVERYONE", "1")
    with caplog.at_level(logging.WARNING, logger="kazma_gateway.chat_adapters"):
        built = _built(store)
    assert all(a._allow_all for a in built.values())
    assert built["telegram"].actor_allowed(STRANGER)
    lines = [r.getMessage() for r in caplog.records if r.name == "kazma_gateway.chat_adapters"]
    assert any("KAZMA_GATEWAY_OPEN_TO_EVERYONE is on" in line for line in lines), lines
    assert sum("lets everyone in" in line for line in lines) == 3


def test_negative_control_the_old_default_let_a_stranger_in(store, monkeypatch) -> None:
    """The builder before 2026-11-11: open unless STRICT was set."""
    from kazma_core.security.boot_guard import env_flag

    monkeypatch.setattr("kazma_gateway.chat_adapters.open_to_everyone",
                        lambda: not env_flag("KAZMA_GATEWAY_STRICT_ALLOWLIST"))
    built = _built(store)
    assert built["telegram"].actor_allowed(STRANGER), "the old default answered anyone"


def test_a_saved_owner_chat_reaches_the_running_adapter(store) -> None:
    """§48: the owner's chat saved later reaches the built adapter in place."""
    built = _built(store)
    telegram = built["telegram"]
    assert not telegram.actor_allowed(OWNER)
    gateway = GatewayManager()
    gateway.add_adapter(telegram)
    adapters = ChatAdapters(gateway, store)
    adapters._settings = adapters._read_all()
    store.set("guard.telegram.chat_id", str(OWNER), "guard")
    assert asyncio.run(adapters.apply()) == [], "applied in place, not rebuilt"
    assert gateway.adapter_named("telegram") is telegram and telegram.actor_allowed(OWNER)


def test_the_owner_settings_are_watched() -> None:
    from kazma_gateway.chat_adapters import _watched_key

    assert _watched_key("guard.telegram.chat_id")
    assert _watched_key("connectors.telegram.swarm_chat_id")


def _tg_test(**kw: Any) -> dict[str, Any]:
    from kazma_gateway.adapters.telegram_diagnose import diagnose

    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"ok": False}))
    result = asyncio.run(diagnose("123:abc", transport=transport, **kw))
    return next(c for c in result["checks"] if c["key"] == "allowed")


@pytest.mark.parametrize("kw, words", [
    ({"owner_ids": [str(OWNER)]}, f"answers only the owner's own Telegram account it already knows ({OWNER})"),
    ({}, "every message is refused"),
    ({"open_to_everyone": True}, "KAZMA_GATEWAY_OPEN_TO_EVERYONE is on"),
])
def test_the_test_shows_an_empty_allowlist_as_a_problem_with_its_fix(kw, words) -> None:
    check = _tg_test(allowed_users=[], **kw)
    assert check["ok"] is False and words in check["detail"]
    assert "Put your Telegram user id in Allowed User IDs" in check["detail"]


def test_the_test_passes_a_set_allowlist() -> None:
    assert _tg_test(allowed_users=["5"], owner_ids=["9"])["ok"] is True


# ── the class gate: every accept path asks the one rule ─────────────────


def _rule_reads(source: str) -> list[int]:
    """Lines where *source* reads an allowlist attribute itself, outside the
    constructor and the allowlist setter."""
    found: list[int] = []

    def walk(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if (
                isinstance(child, ast.Attribute)
                and child.attr in ("_allowed_users", "_allow_all")
                and isinstance(child.ctx, ast.Load)
                and fn not in ("__init__", "set_allowed_users")
            ):
                found.append(child.lineno)
            walk(child, name)

    walk(ast.parse(source), "")
    return found


def _gateway_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "kazma-gateway/kazma_gateway"], cwd=REPO, capture_output=True, text=True,
        check=True,
    ).stdout.split()
    return [f for f in out if f.endswith(".py")]


def test_every_accept_path_asks_the_one_rule() -> None:
    files = _gateway_files()
    assert any(f.endswith("adapters/telegram.py") for f in files)
    offenders = {
        rel: lines for rel in files
        if rel != "kazma-gateway/kazma_gateway/sender_rule.py"
        and (lines := _rule_reads((REPO / rel).read_text(encoding="utf-8")))
    }
    assert offenders == {}, f"ask SenderGate.sender_refusal / actor_allowed instead: {offenders}"

    from kazma_gateway.adapters.discord import DiscordAdapter
    from kazma_gateway.adapters.slack import SlackAdapter
    from kazma_gateway.adapters.telegram import TelegramAdapter

    for adapter in (TelegramAdapter, DiscordAdapter, SlackAdapter):
        assert issubclass(adapter, SenderGate), adapter
        assert "actor_allowed" not in vars(adapter) and "sender_refusal" not in vars(adapter), adapter


@pytest.mark.parametrize("planted", [
    "def accept(self, uid):\n    if not self._allowed_users and not self._allow_all:\n        return None\n",
    "def ok(adapter, uid):\n    return uid in adapter._allowed_users\n",
    'reason = "user_not_allowed" if adapter._allowed_users or adapter._allow_all else "no_allowlist"\n',
])
def test_the_gate_sees_a_planted_second_rule(planted: str) -> None:
    """Negative control: each shape the adapters used before 2026-11-11."""
    assert _rule_reads(planted)


def test_the_gate_lets_the_constructor_and_setter_write() -> None:
    assert not _rule_reads(
        "class A:\n    def __init__(self):\n        self._allowed_users = set()\n        self._allow_all = False\n"
        "    def set_allowed_users(self, ids):\n        self._allowed_users = set(ids)\n"
    )


def test_a_fake_adapter_spec_stays_buildable() -> None:
    """The settings snapshot carries the owner settings (a change re-applies)."""
    assert "owners" in _PlatformSettings.__dataclass_fields__
