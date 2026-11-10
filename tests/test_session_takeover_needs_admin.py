"""A chat-app sender sees and takes over only their own seasons.

Security audit 2026-11-10, SEC-04. ``/sessions`` listed every chat of the
install and ``/switch <n>`` bound the sender to any chat with no owner on
record -- which is every chat started on the Web. With an empty allowlist
(the adapters then accept everyone) a stranger who found the bot could list
the operator's chats, take one over and read or continue it. Now the list and
the switch are the sender's own seasons unless the sender is a gateway admin.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kazma_core.config_store import ConfigStore, reset_config_store, set_config_store
from kazma_core.sessions import directory
from kazma_core.sessions.directory import (
    list_directory,
    record_thread_owner,
    resolve_session,
    sender_may_take_over,
    stamp_last_platform,
)
from kazma_gateway.agent_handler import session_commands
from kazma_ui.session_manager import ChatSession, get_session_manager, reset_session_manager

STRANGER = "telegram:666"
OWNER = "telegram:111"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    reset_session_manager()
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    monkeypatch.delenv("KAZMA_SESSION_OPEN_TAKEOVER", raising=False)
    monkeypatch.delenv("KAZMA_GATEWAY_ADMINS", raising=False)
    reset_session_manager()
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "missing.yaml"))
    set_config_store(store)
    yield
    reset_config_store()
    reset_session_manager()


def _seed(title: str, session_id: str, *, owner: str = "") -> None:
    sess = ChatSession(session_id=session_id, thread_id=session_id, title=title)
    sess.add_message("user", "the operator's private question")
    get_session_manager().put(sess)
    stamp_last_platform(session_id, "web")
    if owner:
        assert record_thread_owner(session_id, owner)


def _seed_all() -> None:
    _seed("Payroll review", "web-aaaa1111")                    # started on the Web: no owner
    _seed("Owner on Telegram", "gw-telegram-111")               # the operator's own DM
    _seed("Stranger's own", "gw-telegram-666")                  # the stranger's own DM


def _run(text: str, sender: str, monkeypatch, *, admin: bool):
    sent: list[str] = []
    bound: list[tuple[str, str]] = []

    class _Manager:
        async def send(self, message) -> None:
            sent.append(message.text)

    async def _bind(sender_id, thread_id, **kwargs) -> None:
        bound.append((sender_id, thread_id))

    monkeypatch.setattr(directory, "bind_sender_to_thread", _bind)
    monkeypatch.setattr("kazma_gateway.allowlists.is_gateway_admin", lambda s, p="": admin)
    monkeypatch.setattr(session_commands, "_build_target_id", lambda platform, ctx: "t")
    msg = SimpleNamespace(text=text, platform="telegram", context_metadata={})
    sessions_map: dict[str, str] = {}
    handled = asyncio.run(session_commands.try_session_command(
        msg, thread_id=f"gw-{sender.replace(':', '-')}", sender=sender, store=None,
        manager=_Manager(), sessions_map=sessions_map,
        prepare_outbound=lambda m, text, ctx: (text, ctx),
    ))
    assert handled
    return "\n".join(sent), bound, sessions_map


# ── the rule ─────────────────────────────────────────────────────────────


def test_a_non_admin_lists_only_their_own_seasons():
    _seed_all()
    titles = [e.title for e in list_directory(only_for=STRANGER)]
    assert titles == ["Stranger's own"]
    assert {e.title for e in list_directory()} == {"Payroll review", "Owner on Telegram", "Stranger's own"}


def test_a_number_means_the_list_the_sender_was_shown():
    _seed_all()
    hit = resolve_session("1", only_for=STRANGER)
    assert hit is not None and hit.title == "Stranger's own"
    assert resolve_session("web-aaaa1111", only_for=STRANGER) is None
    assert resolve_session("web-aaaa1111") is not None


def test_an_unowned_season_is_an_admins():
    _seed_all()
    assert sender_may_take_over(STRANGER, "web-aaaa1111") == (False, "")
    assert sender_may_take_over(OWNER, "web-aaaa1111", admin=True) == (True, "")
    assert sender_may_take_over(STRANGER, "gw-telegram-666") == (True, "")
    allowed, owner = sender_may_take_over(STRANGER, "gw-telegram-111", admin=True)
    assert (allowed, owner) == (False, OWNER)       # an admin still cannot take another user's


def test_the_shared_team_switch_still_opens_it(monkeypatch):
    _seed_all()
    monkeypatch.setenv("KAZMA_SESSION_OPEN_TAKEOVER", "1")
    assert sender_may_take_over(STRANGER, "web-aaaa1111") == (True, "")
    assert len(list_directory(only_for=STRANGER)) == 3


# ── through the command, as each sender ──────────────────────────────────


def test_a_stranger_cannot_list_or_take_the_operators_chat(monkeypatch):
    _seed_all()
    listing, _, _ = _run("/sessions", STRANGER, monkeypatch, admin=False)
    assert "Payroll review" not in listing and "Owner on Telegram" not in listing
    assert "Stranger's own" in listing

    for pick in ("web-aaaa1111", "aaaa1111", "Payroll", "2", "3"):
        reply, bound, sessions_map = _run(f"/switch {pick}", STRANGER, monkeypatch, admin=False)
        assert bound == [] and sessions_map == {}, (pick, reply)
        assert "Taken over" not in reply


def test_an_admin_still_lists_and_takes_over_a_web_chat(monkeypatch):
    _seed_all()
    listing, _, _ = _run("/sessions", OWNER, monkeypatch, admin=True)
    assert "Payroll review" in listing
    reply, bound, sessions_map = _run("/switch web-aaaa1111", OWNER, monkeypatch, admin=True)
    assert bound == [(OWNER, "web-aaaa1111")]
    assert sessions_map == {OWNER: "web-aaaa1111"}
    assert "Taken over" in reply


def test_control_the_old_rule_handed_the_chat_over(monkeypatch):
    """Without the admin rule (every sender counted as one), the stranger gets it."""
    _seed_all()
    listing, _, _ = _run("/sessions", STRANGER, monkeypatch, admin=True)
    assert "Payroll review" in listing
    _, bound, _ = _run("/switch web-aaaa1111", STRANGER, monkeypatch, admin=True)
    assert bound == [(STRANGER, "web-aaaa1111")]
