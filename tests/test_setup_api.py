"""Hands 0.11 WP2 — first-run status + bootstrap (no secret leak)."""

from __future__ import annotations

from pathlib import Path

from kazma_ui.setup_api import compute_setup_status

_CHAT = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
_V5 = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.v5.css"


class _Reg:
    def __init__(self, provider="", model="", api_key="", providers=None):
        self._profile = {
            "provider": provider,
            "model": model,
            "api_key": "***" if api_key else "",
        }
        self._providers = providers or []

    def get_active_profile(self):
        return dict(self._profile)

    def list_providers(self):
        return list(self._providers)


def test_status_not_ready_without_provider_or_model() -> None:
    st = compute_setup_status(_Reg())
    assert st["ready"] is False
    assert st["has_provider"] is False
    assert st["has_model"] is False
    assert "api_key" not in st
    assert any(p["id"] == "openai" for p in st["presets"])


def test_status_ready_with_masked_key_and_model() -> None:
    st = compute_setup_status(_Reg(provider="openai", model="gpt-4.1", api_key="sk-test"))
    assert st["has_provider"] is True
    assert st["has_model"] is True
    assert st["ready"] is True
    assert "sk-test" not in str(st)


def test_local_ollama_counts_as_provider() -> None:
    st = compute_setup_status(_Reg(provider="ollama", model="llama3"))
    assert st["has_provider"] is True
    assert st["ready"] is True


def test_chat_overlay_has_cloak_no_inline_display() -> None:
    html = _CHAT.read_text(encoding="utf-8")
    assert 'id="setup-wall"' in html
    assert "x-cloak" in html
    assert "x-show=\"show\"" in html
    wall = html.split('id="setup-wall"')[1].split("</div>")[0]
    assert "style=" not in wall or "display" not in wall
    assert 'id="chat-input"' in html
    v5 = _V5.read_text(encoding="utf-8")
    assert ".setup-wall" in v5
