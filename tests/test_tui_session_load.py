"""TUI `/session N` must submit and load the listed season transcript."""

from __future__ import annotations

from kazma_tui.season_load import (
    coerce_visible_messages,
    load_season_messages,
    message_text,
    session_messages_url,
)
from kazma_tui.slash_complete import (
    enter_completes_autocomplete,
    has_args,
    slash_matches,
)
from kazma_ui.session_manager import ChatSession, get_session_manager, reset_session_manager

_COMMANDS = [
    ("/help", "help"),
    ("/sessions", "list"),
    ("/session", "switch"),
    ("/switch", "switch"),
]


def test_session_12_is_not_eaten_by_sessions_autocomplete():
    typed = "/session 12"
    assert has_args(typed)
    matches = slash_matches(typed, _COMMANDS)
    assert matches == []
    assert enter_completes_autocomplete(typed, [("/sessions", "list")]) is False


def test_partial_session_still_suggests_both_commands():
    matches = slash_matches("/ses", _COMMANDS)
    assert [c for c, _ in matches] == ["/sessions", "/session"]
    assert enter_completes_autocomplete("/ses", matches) is True


def test_exact_session_command_submits_instead_of_filling_sessions():
    matches = slash_matches("/session", _COMMANDS)
    assert "/session" in [c for c, _ in matches]
    assert enter_completes_autocomplete("/session", matches) is False


def test_sessions_exact_command_submits():
    matches = slash_matches("/sessions", _COMMANDS)
    assert matches == [("/sessions", "list")]
    assert enter_completes_autocomplete("/sessions", matches) is False


def test_message_text_flattens_content_parts():
    assert message_text({"content": "plain"}) == "plain"
    assert message_text({
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
    }) == "hello\nworld"


def test_coerce_visible_messages_skips_empty_and_tools():
    rows = coerce_visible_messages([
        {"role": "tool", "content": "nope"},
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [{"text": "hi"}]},
    ])
    assert rows == [
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "hi"},
    ]


def test_session_messages_url_encodes_id():
    url = session_messages_url(
        "http://127.0.0.1:9090",
        "gw-telegram-bAlfaris-fd44e607",
    )
    assert url.endswith("/api/chat/sessions/gw-telegram-bAlfaris-fd44e607/messages")
    spaced = session_messages_url("http://127.0.0.1:9090/", "id with space")
    assert "id%20with%20space" in spaced


def test_load_season_messages_uses_local_store_first(monkeypatch):
    reset_session_manager()
    sm = get_session_manager()
    sid = "gw-telegram-bAlfaris-fd44e607"
    sess = ChatSession(session_id=sid, thread_id=sid, title="Telegram · bAlfaris")
    sess.add_message("user", "hello from telegram")
    sess.add_message("assistant", "got it")
    sm.put(sess)

    def _fail_http(_sid: str, **_kw):
        raise AssertionError("HTTP must not run when local history exists")

    monkeypatch.setattr("kazma_tui.season_load.fetch_season_messages_http", _fail_http)
    rows = load_season_messages(sid, sid)
    assert len(rows) == 2
    assert rows[0]["content"] == "hello from telegram"
    reset_session_manager()
