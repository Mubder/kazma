"""``config_read`` never hands the model a secret (audit 2026-10-26 AUD-005).

The tool is read tier (no approval card) and judged secrecy by the requested
key's NAME only, so ``config_read("providers.list")`` returned every
provider's API key -- ConfigStore.get decrypts ``vault://`` pointers inside
dicts and lists -- and ``memory.backends.state.url`` the Postgres password.
It now masks with the Settings API's masker (``security/config_mask.py``).
"""

from __future__ import annotations

import json

import pytest

from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.config_store import get_config_store

_KEY = "sk-live-0123456789abcdef"
_DSN = "postgresql://kazma:Pa55word-Zq9@db.internal:5432/kazma"


@pytest.fixture
def config_read():
    return LocalToolRegistry(include_builtins=True)._tools["config_read"].func


@pytest.fixture
def seeded():
    store = get_config_store()
    store.set("providers.list", [{"name": "deepseek", "api_key": _KEY, "model": "deepseek-chat"}])
    store.set("memory.backends.state.url", _DSN)
    yield store
    store.delete("providers.list")
    store.delete("memory.backends.state.url")


def test_a_nested_api_key_is_hidden(config_read, seeded) -> None:
    out = json.loads(config_read("providers.list"))
    assert out["status"] == "set"
    assert _KEY not in json.dumps(out)
    assert "deepseek-chat" in out["value"]  # the rest is still readable
    assert "hidden" in out["message"]


def test_a_url_password_is_hidden(config_read, seeded) -> None:
    out = json.loads(config_read("memory.backends.state.url"))
    assert "Pa55word-Zq9" not in json.dumps(out)
    assert "db.internal" in out["value"]


def test_the_old_name_only_rule_returned_both(seeded) -> None:
    """Negative control: the removed display rule on the same stored values."""

    def old_display(key: str) -> str:
        secret_markers = ("api_key", "apikey", "token", "secret", "password",
                          "passwd", "private_key", "credentials", "auth")
        assert not any(m in key.lower() for m in secret_markers)
        val = seeded.get(key)
        return val if isinstance(val, (str, int, float, bool)) else str(val)

    assert _KEY in old_display("providers.list")
    assert "Pa55word-Zq9" in old_display("memory.backends.state.url")


def test_the_ui_masker_is_the_core_masker() -> None:
    from kazma_core.security import config_mask
    from kazma_ui import settings

    assert settings.mask_deep is config_mask.mask_deep
    assert settings.key_is_secret is config_mask.key_is_secret
