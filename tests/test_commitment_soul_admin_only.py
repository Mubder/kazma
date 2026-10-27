"""AUD-104: the Soul confirm queue is admin-only with platform users, and its
failures answer with a real status.

A confirmed Soul delta is guidance in every future system prompt, for every
user of the install. With platform users an ``operator`` could confirm (or
reject) what the admin's ``soul_requires_confirm`` held back: the routes were
not among ``platform_rbac._ADMIN_PREFIXES``. And a refused or failed confirm
answered 200, with ``applied: false`` or an ``error`` in the body.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "path, method",
    [
        ("/api/commitment/soul/pending", "GET"),
        ("/api/commitment/soul/cmt_1/confirm", "POST"),
        ("/api/commitment/soul/cmt_1/reject", "POST"),
    ],
)
def test_only_an_admin_reaches_the_soul_queue(path: str, method: str) -> None:
    from kazma_core.security.platform_rbac import role_allows

    assert role_allows("admin", path, method) is True
    assert role_allows("operator", path, method) is False
    assert role_allows("viewer", path, method) is False


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "1")
    monkeypatch.setenv("KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM", "1")
    from kazma_ui.commitment_api import create_commitment_router

    app = FastAPI()
    app.include_router(create_commitment_router())
    return TestClient(app)


def test_an_apply_that_raises_is_a_500_not_a_200(client, monkeypatch) -> None:
    from kazma_core.skills import self_improvement
    from kazma_core.skills.self_improvement import mint_soul_commitment

    cid = mint_soul_commitment("learn tool X", agent_id="a1")

    def boom(*_a, **_k):
        raise RuntimeError("store at /srv/private/agent_evolution unavailable")

    monkeypatch.setattr(self_improvement, "apply_agent_mutation", boom)
    r = client.post(f"/api/commitment/soul/{cid}/confirm")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "apply_failed"
    assert body["applied"] is False
    assert "/srv/private" not in r.text


def test_an_apply_that_refuses_is_a_409(client, monkeypatch) -> None:
    from kazma_core.skills import self_improvement
    from kazma_core.skills.self_improvement import mint_soul_commitment

    cid = mint_soul_commitment("learn tool X", agent_id="a1")
    monkeypatch.setattr(self_improvement, "apply_agent_mutation", lambda *a, **k: False)
    r = client.post(f"/api/commitment/soul/{cid}/confirm")
    assert r.status_code == 409
    assert r.json()["code"] == "not_applied"


def test_rejecting_an_unknown_commitment_is_a_404(client) -> None:
    r = client.post("/api/commitment/soul/cmt_nonexistent/reject")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_the_settings_page_words_each_code_in_both_languages() -> None:
    from pathlib import Path

    from kazma_ui.i18n.catalog.settings import TRANSLATIONS

    js = (
        Path(__file__).resolve().parents[1]
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_agent.js"
    ).read_text(encoding="utf-8")
    for code, key in (
        ("not_found", "settings.soul_not_found"),
        ("not_confirmed", "settings.soul_not_confirmed"),
        ("not_applied", "settings.soul_not_applied"),
        ("apply_failed", "settings.soul_apply_failed"),
    ):
        assert f"{code}: '{key}'" in js
        assert TRANSLATIONS[key]["en"] and TRANSLATIONS[key]["ar"]
