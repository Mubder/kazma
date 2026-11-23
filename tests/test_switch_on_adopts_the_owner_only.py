"""Stage 3 review, M1: switching Spaces on adopts only what was the owner.

``known_owner_ids`` names the guard's pager, the swarm chat and
``KAZMA_GATEWAY_ADMINS`` entries, whatever the allowlist said. With the
allowlist holding the owner (111) and the pager pointed at a colleague
(222), switching on made 222 the owner and the chat apps' admin, the
owner's own 111 was not linked, and ``--check`` named neither.

Now a known id is adopted only when it was the owner before the switch
(``chat_links.owner_link_plan``): the sender rule of before let it chat --
on the allowlist, or the allowlist empty, or the open switch on -- and the
admin rule of before made it admin (``KAZMA_GATEWAY_ADMINS`` when set, else
the allowlist, else the known ids). A Telegram group (a negative id) in
``KAZMA_GATEWAY_ADMINS`` is never anyone's link. ``--check`` names every id
it will adopt and every known id it will not, with why.

Negative control: the plan of before, every known id adopted -- the
colleague's pager becomes the owner.
"""

from __future__ import annotations

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_apps, chat_links, identity, preconditions, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

KEYS = ("guard.telegram.chat_id", "connectors.telegram.swarm_chat_id",
        "connectors.telegram.allowed_users", "connectors.discord.allowed_users")


@pytest.fixture
def store(monkeypatch):
    s = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY, *KEYS):
        s.delete(key)
    monkeypatch.delenv(chat_links.GATEWAY_ADMINS_ENV, raising=False)
    monkeypatch.delenv("KAZMA_GATEWAY_OPEN_TO_EVERYONE", raising=False)
    monkeypatch.setattr(scope, "_enforced", False)
    yield s
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY, *KEYS):
        s.delete(key)
    chat_links._forget_links_for_tests()


def _owner_links() -> set[tuple[str, str]]:
    return {(link["platform"], str(link["user_id"])) for link in identity.read_registry()["links"]
            if link.get("account") == OWNER_ACCOUNT_ID}


def test_a_colleague_s_pager_is_not_adopted(store) -> None:
    store.set("connectors.telegram.allowed_users", "111", category="connectors")
    store.set("guard.telegram.chat_id", "222", category="guard")
    chat_links.adopt_owner_ids(by=OWNER_ACCOUNT_ID)
    assert ("telegram", "222") not in _owner_links(), "the colleague's pager became the owner"


def test_the_owner_s_own_pager_is_adopted(store) -> None:
    store.set("connectors.telegram.allowed_users", "111,333", category="connectors")
    store.set("guard.telegram.chat_id", "111", category="guard")
    chat_links.adopt_owner_ids(by=OWNER_ACCOUNT_ID)
    assert _owner_links() == {("telegram", "111")}


def test_an_empty_allowlist_adopts_the_known_ids(store) -> None:
    store.set("guard.telegram.chat_id", "111", category="guard")
    chat_links.adopt_owner_ids(by=OWNER_ACCOUNT_ID)
    assert _owner_links() == {("telegram", "111")}


def test_the_environment_list_decides_who_was_admin(store, monkeypatch) -> None:
    monkeypatch.setenv(chat_links.GATEWAY_ADMINS_ENV, "telegram:444,discord:555")
    store.set("guard.telegram.chat_id", "111", category="guard")
    chat_links.adopt_owner_ids(by=OWNER_ACCOUNT_ID)
    assert _owner_links() == {("telegram", "444"), ("discord", "555")}, (
        "with the environment list set, the pager was not an admin before")


def test_a_telegram_group_in_the_environment_list_is_no_one_s_link(store, monkeypatch) -> None:
    monkeypatch.setenv(chat_links.GATEWAY_ADMINS_ENV, "telegram:-100123456,telegram:111")
    chat_links.adopt_owner_ids(by=OWNER_ACCOUNT_ID)
    assert _owner_links() == {("telegram", "111")}
    assert chat_links.address_of(OWNER_ACCOUNT_ID) == "telegram:111"


def test_check_names_what_it_adopts_and_what_it_leaves(store) -> None:
    store.set("connectors.telegram.allowed_users", "111", category="connectors")
    store.set("guard.telegram.chat_id", "222", category="guard")
    store.set("connectors.telegram.swarm_chat_id", "111", category="connectors")
    said = " ".join(preconditions.notes())
    assert "Telegram 111" in said and "connectors.telegram.swarm_chat_id" in said, said
    assert "Telegram 222" in said and "not on the allowlist" in said, said


def test_a_list_typed_allowlist_is_read_as_ids(store) -> None:
    store.set("connectors.telegram.allowed_users", [111, 333], category="connectors")
    said = " ".join(preconditions.notes())
    assert "Telegram 111" in said and "Telegram 333" in said, said
    assert "[" not in said and "]" not in said, "the list was read as its printed form"


def test_negative_control_every_known_id_adopted(store, monkeypatch) -> None:
    """Planted: the plan of before, every known id adopted whatever the
    allowlist said. The colleague's pager becomes the owner: the check
    above fails on its own assertion."""
    monkeypatch.setattr(chat_links, "_was_the_owner", lambda *a, **k: "")
    with pytest.raises(AssertionError, match="the colleague's pager became the owner"):
        test_a_colleague_s_pager_is_not_adopted(store)


# ── the open switch counts only with no allowlist (the re-check's B) ─────


def test_the_open_switch_never_overrides_a_set_allowlist(store, monkeypatch) -> None:
    """Before Spaces a set allowlist decided alone (``sender_rule.refusal``):
    with it set, an id in ``KAZMA_GATEWAY_ADMINS`` but not on it could not
    chat, the open switch on or not. It is not adopted as the owner."""
    monkeypatch.setenv("KAZMA_GATEWAY_OPEN_TO_EVERYONE", "1")
    monkeypatch.setenv(chat_links.GATEWAY_ADMINS_ENV, "telegram:111,telegram:666")
    store.set("connectors.telegram.allowed_users", "111", category="connectors")
    chat_links.adopt_owner_ids(by=OWNER_ACCOUNT_ID)
    assert ("telegram", "666") not in _owner_links(), (
        "an id off a set allowlist was adopted as the owner through the open switch")
    assert ("telegram", "111") in _owner_links()


def test_with_no_allowlist_the_open_switch_and_the_admin_list_adopt(store, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_GATEWAY_OPEN_TO_EVERYONE", "1")
    monkeypatch.setenv(chat_links.GATEWAY_ADMINS_ENV, "telegram:666")
    chat_links.adopt_owner_ids(by=OWNER_ACCOUNT_ID)
    assert ("telegram", "666") in _owner_links()


def test_negative_control_today_s_was_the_owner(store, monkeypatch) -> None:
    """Planted: today's ``_was_the_owner``, where the open switch overrode a
    set allowlist. The id off the allowlist is adopted as the owner."""
    import os

    from kazma_core.security.boot_guard import env_flag
    from kazma_core.spaces.chat_links import GATEWAY_ADMINS_ENV, platform_user_id, split_ids

    def before(s, platform, uid):
        listed = [x for x in (platform_user_id(platform, v) for v in split_ids(
            s.get(f"connectors.{platform}.allowed_users", "") or "")) if x]
        open_switch = env_flag("KAZMA_GATEWAY_OPEN_TO_EVERYONE") and not env_flag(
            "KAZMA_GATEWAY_STRICT_ALLOWLIST")
        if listed and uid not in listed and not open_switch:
            return "it is not on the allowlist"
        env_raw = str(os.environ.get(GATEWAY_ADMINS_ENV, "") or "").strip()
        if env_raw:
            for part in split_ids(env_raw):
                if ":" in part:
                    plat, ident = (x.strip() for x in part.split(":", 1))
                    if plat.lower() == platform and platform_user_id(platform, ident) == uid:
                        return ""
                elif part == uid:
                    return ""
            return f"it is not in {GATEWAY_ADMINS_ENV}"
        return "it is not on the allowlist" if listed and uid not in listed else ""

    monkeypatch.setattr(chat_links, "_was_the_owner", before)
    with pytest.raises(AssertionError, match="adopted as the owner through the open switch"):
        test_the_open_switch_never_overrides_a_set_allowlist(store, monkeypatch)
