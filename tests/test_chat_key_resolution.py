"""Chat must read the API key from the same place Settings > Test reads it.

The live symptom, reported after four commits that each claimed to fix a
provider/key bug:

    ⚠️ No API key configured for https://api.deepseek.com/v1.
       Please go to Settings > Models, enter your API key, and click Save.

...while Settings > Test on that same provider returned success, and the key
was sitting in the registry the whole time.

Isolated against the running server by sending two otherwise identical turns:

    {"message": "..."}                          -> the error
    {"message": "...", "model": "deepseek-flash"} -> streams fine

The pre-stream key check read ``_get_llm()`` when the turn pinned no model.
That is the *agent's* provider, which can carry a refreshed ``base_url`` with a
stale, empty ``api_key`` — hence an error naming the correct URL for a provider
whose key was configured. The model-pinned branch already went through the
registry and was always correct.

It surfaced when it did because ``abe1099d`` stopped ``chat.js`` echoing a
stale localStorage model on every turn. That fix was right; it just moved
traffic onto the branch that was already broken.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_ui.sse_chat import create_sse_chat_router

CLOUD_URL = "https://api.deepseek.com/v1"


@dataclass
class _Config:
    base_url: str
    api_key: str
    model: str = "deepseek-flash"


class _Provider:
    """Stand-in for LLMProvider — only ``.config`` is read by the key check."""

    def __init__(self, api_key: str) -> None:
        self.config = _Config(base_url=CLOUD_URL, api_key=api_key)


class _Registry:
    """The registry holds the key the operator actually saved."""

    def __init__(self, api_key: str = "sk-real-deepseek-key") -> None:
        self.api_key = api_key
        self.calls: list[Any] = []

    def get_client(self, model: Any = None) -> _Provider:
        self.calls.append(model)
        return _Provider(self.api_key)


@pytest.fixture(autouse=True)
def _isolated_sessions(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_ui.session_manager import reset_session_manager

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    reset_session_manager()


def _client(
    registry: _Registry, mounted_key: str, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Mount chat with a keyless agent provider and a keyed registry.

    That split is the bug's shape: ``_get_llm()`` returns the former, the
    registry holds the latter.

    ``monkeypatch`` is not optional here. Assigning ``get_model_registry``
    directly leaks the stub into every later test in the session — it broke 16
    unrelated cases in ``test_settings.py`` the first time this file was
    written, which is its own small lesson about global mutation in fixtures.
    """
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry", lambda: registry
    )
    router = create_sse_chat_router(
        graph=None,
        llm_provider=_Provider(mounted_key),
        llm_provider_getter=lambda: _Provider(mounted_key),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _key_error(
    body: dict[str, Any],
    registry: _Registry,
    mounted_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> str | None:
    resp = _client(registry, mounted_key, monkeypatch).post("/api/chat/stream", json=body)
    assert resp.status_code == 200
    return "No API key configured" if "No API key configured" in resp.text else None


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("no model pinned", {"message": "say OK", "session_id": "t-bare"}),
        ("model pinned", {"message": "say OK", "session_id": "t-pin", "model": "deepseek-flash"}),
    ],
)
def test_a_configured_key_is_found_however_the_turn_arrives(
    label: str, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bare turn is the regression; the pinned one is the control.

    Both must find the registry's key. Before the fix the bare turn read the
    mounted provider's empty key and refused to start.
    """
    registry = _Registry()
    assert _key_error(body, registry, "", monkeypatch) is None, (
        f"{label}: chat refused to start despite a key in the registry"
    )


def test_the_bare_turn_consults_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not just 'it worked' — it must actually ask the registry.

    Passing because the mounted provider happened to hold a key would hide the
    bug the moment that provider went stale again.
    """
    registry = _Registry()
    _client(registry, "", monkeypatch).post(
        "/api/chat/stream", json={"message": "say OK", "session_id": "t-consult"}
    )
    assert registry.calls, "the bare turn never asked the registry for a client"


def test_a_genuinely_missing_key_is_still_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control.

    Routing through the registry must not swallow the real 'no key' case —
    that message is the honest one when nothing is configured anywhere.
    """
    registry = _Registry(api_key="")
    assert (
        _key_error(
            {"message": "say OK", "session_id": "t-nokey"}, registry, "", monkeypatch
        )
        == "No API key configured"
    )


def test_the_placeholder_key_still_counts_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``not-needed`` is what a local-provider profile carries.

    Against a cloud URL it means "never configured", and must not be sent to
    DeepSeek as if it were a credential.
    """
    registry = _Registry(api_key="not-needed")
    assert (
        _key_error(
            {"message": "say OK", "session_id": "t-placeholder"}, registry, "", monkeypatch
        )
        == "No API key configured"
    )
