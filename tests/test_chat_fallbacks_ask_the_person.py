"""Stage 3 review, M6: a fallback to a configured chat never sends someone
else's work to the owner's chat.

``send_file``'s fallback (``filesystem._fallback_telegram_target``) and the
document intent handler's (``document._resolve_delivery_target``) fell back
to ``connectors.telegram.swarm_chat_id`` or the first allowed user, whoever
the work was for: a member's swarm worker with no inherited context (a
resumed pipeline, the durable path) sent its files to the owner's Telegram.
Once Spaces is enforced both answer the bound person's own address
(``send_message.own_address``: theirs, the owner's operator Telegram for
the owner, else nothing).

The class gate: every product function that reads a configured chat id
(``connectors.<app>.swarm_chat_id`` or ``.allowed_users``) asks
``own_address`` or is declared with why it is no fallback for someone's
work. Negative controls: the fallback of before, and a planted reader.
"""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_apps, chat_links, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope

PASSWORD = "a-long-enough-password"
ROOT = Path(__file__).resolve().parents[1]
OWNER_CHAT = "7202"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    store.set("connectors.telegram.swarm_chat_id", OWNER_CHAT, category="connectors")
    dana = people.create_person(f"dana-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    omar = people.create_person(f"omar-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)

    def link(reg: dict) -> None:
        reg["links"].append({"platform": "telegram", "user_id": "6202", "account": dana["id"],
                             "target": "telegram:6202"})

    identity.update_registry(link)
    chat_links.refresh_links()
    yield dana, omar
    store.delete("connectors.telegram.swarm_chat_id")
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_links_for_tests()


def _as(person: dict, fn, *args):
    with bind_scope(str(person["personal_space"]), person["id"]):
        return fn(*args)


def test_send_file_s_fallback_is_the_person_s_own_chat(world) -> None:
    from kazma_core.agent.tool_builtins.filesystem import _fallback_telegram_target

    dana, omar = world
    assert _as(omar, _fallback_telegram_target) == "", "a member with no link was sent to the owner's chat"
    assert _as(dana, _fallback_telegram_target) == "telegram:6202"


def test_the_document_handler_s_fallback_is_the_person_s_own_chat(world) -> None:
    from kazma_core.agent.intent.handlers.document import _resolve_delivery_target

    dana, omar = world
    assert _as(dana, _resolve_delivery_target, "telegram") == "telegram:6202"
    assert _as(omar, _resolve_delivery_target, "telegram") == "", (
        "a member with no link was sent to the owner's chat")


def test_before_spaces_the_owner_s_chat_as_always(world, monkeypatch) -> None:
    from kazma_core.agent.tool_builtins.filesystem import _fallback_telegram_target

    monkeypatch.setattr(scope, "_enforced", False)
    assert _fallback_telegram_target() == f"telegram:{OWNER_CHAT}"


# ── the class gate ──────────────────────────────────────────────────────

_KEYS = ("swarm_chat_id", "allowed_users")
#: (file, function) that read a configured chat id and are no fallback for
#: someone's work, with why.
DECLARED = {
    ("kazma-core/kazma_core/tools/send_message.py", "operator_telegram_target"):
        "the owner's operator Telegram: own_address answers it for the owner only",
    ("kazma-core/kazma_core/observability/ops_alerts.py", "*"):
        "the install's own alerts: the admin's, never a person's work",
    ("kazma-core/kazma_core/spaces/preconditions.py", "_listed_senders_note"):
        "a note of --check, sends nothing",
    ("kazma-core/kazma_core/spaces/chat_links.py", "_was_the_owner"):
        "the sender rule of before, for the switch; sends nothing",
    ("kazma-gateway/kazma_gateway/allowlists.py", "*"):
        "the allowlists themselves, before Spaces",
    ("kazma-ui/kazma_ui/providers.py", "*"): "Settings shows the setting",
    ("kazma-ui/kazma_ui/settings.py", "*"): "Settings shows the setting",
}
PRODUCT = ("kazma-core/kazma_core", "kazma-gateway/kazma_gateway", "kazma-ui/kazma_ui",
           "kazma-skills/kazma_skills", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui")


def _readers(source: str) -> list[tuple[str, bool]]:
    """(function, asks own_address) for each function reading a chat-id key."""
    tree = ast.parse(source)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        reads = False
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("get", "_cs_get"):
                if any(isinstance(a, (ast.Constant, ast.JoinedStr)) and "connectors" in ast.unparse(a)
                       and any(k in ast.unparse(a) for k in _KEYS) for a in node.args):
                    reads = True
        if reads:
            asks = any(isinstance(n, ast.Name) and n.id == "own_address" for n in ast.walk(fn)) or any(
                isinstance(n, ast.alias) and n.name == "own_address" for n in ast.walk(fn))
            out.append((fn.name, asks))
    return out


def _findings(files: dict[str, str]) -> list[str]:
    found = []
    for rel, source in files.items():
        for name, asks in _readers(source):
            if asks or (rel, name) in DECLARED or (rel, "*") in DECLARED:
                continue
            found.append(f"{rel}:{name}")
    return found


def _product_files() -> dict[str, str]:
    files = {}
    for top in PRODUCT:
        for path in (ROOT / top).rglob("*.py"):
            files[path.relative_to(ROOT).as_posix()] = path.read_text(encoding="utf-8")
    return files


def test_every_reader_of_a_configured_chat_asks_the_person_or_is_declared() -> None:
    assert _findings(_product_files()) == [], "a fallback to a configured chat asks nothing of the person"


def test_negative_control_a_planted_reader() -> None:
    planted = '''
def fallback():
    from kazma_core.config_store import get_config_store
    return "telegram:" + str(get_config_store().get("connectors.telegram.swarm_chat_id"))
'''
    assert _findings({"kazma-core/kazma_core/planted.py": planted}) == ["kazma-core/kazma_core/planted.py:fallback"]


def test_negative_control_the_fallback_of_before(world, monkeypatch) -> None:
    """Planted: the fallback of before, the owner's chat whoever asks."""
    from kazma_core.tools import send_message

    monkeypatch.setattr(send_message, "own_address", lambda: f"telegram:{OWNER_CHAT}")
    with pytest.raises(AssertionError, match="sent to the owner's chat"):
        test_send_file_s_fallback_is_the_person_s_own_chat(world)
