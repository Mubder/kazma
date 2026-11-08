"""The providers page, kept short (2026-11-08, the owner: "why the providers
page is too long").

Live's page was some 2,000 lines: Model details listed every model of every
working provider (OpenRouter's 425 among them), the provider list showed all
26 rows (20 of them off with no key), and Model prices offered to price the
1 November audit's ``fault-*`` drill models, which no provider serves.

- Model details marks the models called in the last 30 days (``used``): the
  page lists those, the default and the ones you changed, and searches the
  rest. Default model, details and prices are their own Models sub-tab.
- Model prices offers only a model a working provider still serves with no
  price known.
- Each provider says whether chat could call it now (``callable``): the page
  folds away the ones that are off and cannot be.

Each with the old behaviour as its control.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def page(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import ModelRegistry
    from kazma_ui.providers import create_providers_router
    from kazma_ui.settings import create_settings_router

    tag = uuid.uuid4().hex[:8]
    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    rows: list[dict[str, Any]] = [
        {"name": "deepseek", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-demo-deepseek-key",
         "models": [f"chat-{tag}", f"priced-{tag}", f"quiet-{tag}"], "enabled": True},
        {"name": "mistral", "base_url": "https://api.mistral.ai/v1", "models": [], "enabled": False},
    ]
    store.set("providers.list", rows, category="providers")
    registry = ModelRegistry(store)
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: registry)
    monkeypatch.setattr("kazma_ui.providers.get_model_registry", lambda: registry)
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(MagicMock(), store, Jinja2Templates(directory=str(tmp_path / "templates"))))
    app.include_router(create_providers_router(store))
    try:
        yield TestClient(app), store, tag
    finally:
        store.close()


def _call(model: str, *, provider: str = "deepseek", priced: bool = False) -> None:
    from kazma_core.observability.llm_ledger import record_llm_call

    record_llm_call(provider=provider, model=model, prompt_tokens=10, completion_tokens=2, priced=priced)


def test_prices_offer_only_what_a_working_provider_serves_unpriced(page):
    from kazma_core.observability.llm_ledger import unpriced_models

    client, store, tag = page
    for model in (f"chat-{tag}", f"priced-{tag}", f"fault-503-{tag}"):
        _call(model, provider="LLMProvider")
    store.set("models.prices", {f"priced-{tag}": {"input": 0.27, "output": 1.1}}, category="models")

    offered = {u["model"] for u in client.get("/api/settings/model-prices").json()["unpriced"]}
    assert f"chat-{tag}" in offered
    assert f"fault-503-{tag}" not in offered, "a model no provider serves was offered to price"
    assert f"priced-{tag}" not in offered, "a model with a price was offered to price again"
    # Control: the ledger's own list, which the card showed as it was.
    raw = {u["model"] for u in unpriced_models(30)}
    assert {f"chat-{tag}", f"priced-{tag}", f"fault-503-{tag}"} <= raw


def test_details_say_which_models_were_used(page):
    client, _store, tag = page
    _call(f"chat-{tag}")
    _call(f"chat-{tag}")
    rows = {r["ref"]: r for r in client.get("/api/settings/model-details").json()["models"]}
    assert rows[f"deepseek:chat-{tag}"]["used"] == 2
    assert rows[f"deepseek:quiet-{tag}"]["used"] == 0


def test_each_provider_says_whether_chat_could_call_it(page):
    client, _store, _tag = page
    listed = {p["name"]: p for p in client.get("/api/providers").json()}
    assert listed["deepseek"]["callable"] is True
    assert listed["mistral"]["callable"] is False


def _slice(source: str, start: str, end: str) -> str:
    a = source.index(start)
    return source[a:source.index(end, a)]


def test_the_page_lists_the_short_list_and_folds_what_cannot_be_called():
    """The page's half, read from the files the browser test drives
    (tests/e2e/test_provider_settings_browser.py runs them)."""
    js = (ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_hub.js").read_text(encoding="utf-8")
    html = (ROOT / "kazma-ui" / "kazma_ui" / "templates" / "settings.html").read_text(encoding="utf-8")
    shown = _slice(js, "shownModelDetails() {", "sortedModelDetails(rows) {")
    assert "Number(m.used) > 0" in shown and "this.defaultModelRef" in shown
    assert "!this.modelDetailsShowAll" in shown and "modelDetailsProvider" in shown
    folded = _slice(js, "providerFoldedAway(p) {", "foldedProviderCount() {")
    assert "p.callable !== false" in folded and "p.enabled !== false" in folded
    assert 'x-for="m in shownModelDetails()"' in html, "the card lists every model again"
    assert 'x-for="m in modelDetails" :key="m.ref"' not in html
    # The model cards are the Models sub-tab, apart from the providers.
    models = html.index("hubSubtab !== 'models'")
    assert html.index('id="model-details"') > models and html.index('id="model-prices"') > models
    assert html.index('id="provider-add"') < models
