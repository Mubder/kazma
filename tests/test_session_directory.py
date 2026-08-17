"""Unified session directory — list / resolve / take-over across mouths."""

from __future__ import annotations

import pytest

from kazma_core.config_store import ConfigStore, reset_config_store, set_config_store
from kazma_core.sessions.directory import (
    bind_sender_to_thread,
    create_named_session,
    enrich_summary,
    format_session_list,
    infer_origin,
    list_directory,
    resolve_session,
    stamp_last_platform,
)
from kazma_ui.session_manager import (
    ChatSession,
    get_session_manager,
    reset_session_manager,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    reset_session_manager()
    store = ConfigStore(
        db_path=str(tmp_path / "settings.db"),
        yaml_path=str(tmp_path / "missing.yaml"),
    )
    set_config_store(store)
    yield
    reset_config_store()
    reset_session_manager()


def _seed(title: str, session_id: str, *, platform: str = "web", msgs: int = 1) -> ChatSession:
    sm = get_session_manager()
    sess = ChatSession(session_id=session_id, thread_id=session_id, title=title)
    for i in range(msgs):
        sess.add_message("user", f"hello {i}")
    sm.put(sess)
    stamp_last_platform(session_id, platform)
    return sess


def test_infer_origin_from_gw_prefix():
    assert infer_origin("gw-telegram-1-abc") == "telegram"
    assert infer_origin("gw-discord-chan") == "discord"
    assert infer_origin("gw-slack-u") == "slack"
    assert infer_origin("aaaaaaaa-bbbb") == "web"


def test_list_and_resolve_by_index_and_suffix():
    _seed("Research notes", "sess-aaa11111", platform="web")
    _seed("Telegram chat", "gw-telegram-u-bbb22222", platform="telegram")
    entries = list_directory()
    assert len(entries) >= 2
    by_one = resolve_session("1")
    assert by_one is not None
    by_suffix = resolve_session("bbb22222")
    assert by_suffix is not None
    assert by_suffix.title == "Telegram chat"
    by_hash = resolve_session("#bbb22222")
    assert by_hash is not None
    assert by_hash.title == "Telegram chat"
    by_short = resolve_session(by_suffix.short_id)
    assert by_short is not None
    assert by_short.thread_id == by_suffix.thread_id
    by_title = resolve_session("research")
    assert by_title is not None
    assert by_title.title == "Research notes"


def test_format_list_marks_current():
    _seed("Alpha", "tid-alpha01", platform="web")
    text = format_session_list(list_directory(), current_thread_id="tid-alpha01")
    assert "Alpha" in text
    assert "here" in text
    assert "/session 2" in text or "/session" in text


@pytest.mark.asyncio
async def test_bind_sender_takeover_updates_active_thread_and_delivery():
    _seed("Web draft", "web-thread-xyz", platform="web")

    class FakeStore:
        def __init__(self) -> None:
            self.data: dict = {}

        async def get(self, tid: str) -> dict:
            return dict(self.data.get(tid, {}))

        async def put(self, tid: str, ctx: dict) -> None:
            self.data[tid] = dict(ctx)

    store = FakeStore()
    entry = await bind_sender_to_thread(
        "telegram:42",
        "web-thread-xyz",
        platform="telegram",
        delivery_ctx={"chat_id": 99, "username": "ali"},
        session_store=store,
    )
    assert entry is not None
    assert entry.thread_id == "web-thread-xyz"
    from kazma_core.config_store import get_config_store

    assert get_config_store().get("active_thread.telegram:42") == "web-thread-xyz"
    assert store.data["web-thread-xyz"]["chat_id"] == 99
    summaries = [enrich_summary(s.to_summary()) for s in get_session_manager().list_all()]
    hit = next(s for s in summaries if s["thread_id"] == "web-thread-xyz")
    assert hit["last_platform"] == "telegram"
    assert hit["platform"] == "telegram"


def test_create_named_session_registers_in_directory():
    entry = create_named_session(platform="discord", sender_id="discord:chan", title="Sprint")
    assert entry.session_id.startswith("gw-discord-")
    assert entry.thread_id == entry.session_id
    found = resolve_session("Sprint")
    assert found is not None
    assert found.title == "Sprint"


def test_slash_resolver_falls_through_to_graph():
    from kazma_gateway.slash_commands import resolve_slash_command

    assert resolve_slash_command("/sessions") is None
    assert resolve_slash_command("/seasons") is None
    assert resolve_slash_command("/session 2") is None
    assert resolve_slash_command("/season 2") is None
    assert resolve_slash_command("/switch 1") is None
    assert resolve_slash_command("/new") is None
    help_text = resolve_slash_command("/help")
    assert help_text is not None
    assert "/sessions" in help_text
    assert "/seasons" in help_text
    assert "/session 2" in help_text
    assert "/season" in help_text


def test_session_command_parse():
    from kazma_gateway.agent_handler.session_commands import _parse

    assert _parse("/sessions") == ("list", "")
    assert _parse("/seasons") == ("list", "")
    assert _parse("/session new Notes") == ("new", "Notes")
    assert _parse("/season 2") == ("switch", "2")
    assert _parse("/new") == ("new", "")
    assert _parse("/switch 3") == ("switch", "3")
    assert _parse("/session here") == ("here", "")
    assert _parse("hello") is None
    assert _parse("/session@KazmaBot 40") == ("switch", "40")
    assert _parse("/sessions@KazmaBot") == ("list", "")
    assert _parse("/seasons@KazmaBot") == ("list", "")


def test_find_mouth_thread_prefers_configstore_then_existing_telegram():
    from kazma_core.sessions.directory import find_mouth_thread, remember_sender_thread

    existing = _seed(
        "Telegram · bAlfaris",
        "gw-telegram-bAlfaris-fd44e607",
        platform="telegram",
        msgs=3,
    )
    remember_sender_thread("telegram:99", existing.session_id)
    assert find_mouth_thread("telegram:99", platform="telegram", username="bAlfaris") == existing.session_id

    # Username is not enough to steal another season (that duplicated Hey).
    from kazma_core.config_store import get_config_store

    get_config_store().delete("active_thread.telegram:99")
    found = find_mouth_thread("telegram:99", platform="telegram", username="bAlfaris")
    assert found is None


def test_canonical_web_session_prefers_named_season_over_telegram_twin():
    from kazma_core.sessions.directory import canonical_web_session

    tid = "shared-thread-yolo"
    yolo = _seed("/yolo", "web-yolo-sid", platform="web", msgs=5)
    yolo.thread_id = tid
    get_session_manager().put(yolo)
    twin = _seed("Telegram · bAlfaris", tid, platform="telegram", msgs=2)
    twin.thread_id = tid
    get_session_manager().put(twin)
    picked = canonical_web_session(tid)
    assert picked is not None
    assert picked.session_id == "web-yolo-sid"


def test_prune_twin_sessions_archives_telegram_auto_title():
    from kazma_core.sessions.directory import prune_twin_sessions

    tid = "shared-thread-prune"
    yolo = _seed("/yolo", "web-keep-sid", platform="web", msgs=5)
    yolo.thread_id = tid
    get_session_manager().put(yolo)
    twin = _seed("Telegram · bAlfaris", "gw-telegram-twin", platform="telegram", msgs=2)
    twin.thread_id = tid
    get_session_manager().put(twin)
    gone = prune_twin_sessions(apply=True)
    assert "gw-telegram-twin" in gone
    assert get_session_manager().get("gw-telegram-twin").archived is True
    assert get_session_manager().get("web-keep-sid").archived is False
    live = [s.session_id for s in get_session_manager().list_all(prune_empty=False)]
    assert "web-keep-sid" in live
    assert "gw-telegram-twin" not in live


def test_slash_resolver_strips_telegram_bot_suffix():
    from kazma_gateway.slash_commands import resolve_slash_command

    assert resolve_slash_command("/session@KazmaBot 40") is None
    assert resolve_slash_command("/sessions@KazmaBot") is None
