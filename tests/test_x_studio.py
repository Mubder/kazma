"""X Studio: policy preview, publish choke, drafts inbox, CSRF on post-now."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.x_api.config import XConfig, XCredentials
from kazma_core.x_api.ledger import XPostLedger
from kazma_core.x_api.policy import evaluate_post

_CSRF = {"X-Requested-With": "XMLHttpRequest"}


def _cfg(**overrides) -> XConfig:
    creds = XCredentials("k", "ks", "t", "ts")
    base = dict(
        enabled=True,
        handle="@kazma",
        credentials=creds,
        max_posts_per_day=8,
        max_posts_per_month=80,
        max_mentions=2,
        max_cashtags=1,
        max_hashtags=4,
        max_chars=280,
        duplicate_window_days=30,
        kill_switch=False,
    )
    base.update(overrides)
    return XConfig(**base)


@pytest.fixture()
def studio_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import kazma_core.x_api.config as config_mod
    import kazma_core.x_api.ledger as ledger_mod
    import kazma_core.x_api.policy as policy_mod

    ledger = XPostLedger(tmp_path / "x_posts.db")
    cfg = _cfg()
    monkeypatch.setattr(config_mod, "get_x_config", lambda: cfg)
    monkeypatch.setattr(ledger_mod, "get_ledger", lambda: ledger)
    monkeypatch.setattr(policy_mod, "get_ledger", lambda: ledger)
    return {"cfg": cfg, "ledger": ledger, "tmp": tmp_path}


@pytest.fixture()
def client(studio_env):
    from kazma_ui.x_api import protected_router, router

    app = FastAPI()
    app.include_router(router)
    app.include_router(protected_router)
    with TestClient(app) as c:
        yield c


def test_preview_allows_short_text(client) -> None:
    resp = client.post("/api/x/preview", json={"text": "hello from kazma"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["allow"] is True
    assert data["chars"] == len("hello from kazma")


def test_preview_denies_281_chars(client) -> None:
    """Negative control: over-length must fail closed, not be grepped in source."""
    resp = client.post("/api/x/preview", json={"text": "x" * 281})
    assert resp.status_code == 200
    data = resp.json()
    assert data["allow"] is False
    assert data["chars"] == 281


def test_preview_denies_too_many_mentions(client) -> None:
    resp = client.post("/api/x/preview", json={"text": "hi @a @b @c friends"})
    assert resp.json()["allow"] is False


@pytest.mark.asyncio
async def test_publish_does_not_call_api_when_denied(studio_env, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.x_api.booking import publish_x_post

    called = {"n": 0}

    class _Boom:
        async def create_tweet(self, *a, **k):
            called["n"] += 1
            raise AssertionError("must not hit X when policy denies")

    monkeypatch.setattr("kazma_core.x_api.client.XClient", lambda *a, **k: _Boom())
    ok, payload = await publish_x_post(text="x" * 281)
    assert ok is False
    assert payload["posted"] is False
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_publish_records_ledger_on_success(studio_env, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.x_api.booking import publish_x_post

    class _Ok:
        async def create_tweet(self, text, reply_to_id=""):
            return {"id": "123", "text": text}

    monkeypatch.setattr("kazma_core.x_api.client.XClient", lambda *a, **k: _Ok())
    ok, payload = await publish_x_post(text="studio post")
    assert ok is True
    assert payload["tweet_id"] == "123"
    assert studio_env["ledger"].count_since(0) == 1


def test_post_now_csrf_required(client) -> None:
    resp = client.post("/api/x/post", json={"text": "hello"})
    assert resp.status_code == 403


def test_post_now_policy_deny_is_400(client, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = client.post("/api/x/post", json={"text": "x" * 281}, headers=_CSRF)
    assert resp.status_code == 400
    assert resp.json()["posted"] is False


def test_list_proposals_returns_saved_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.agent.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path / "artifacts.db")
    payload = store.save_proposal("default", "thread-a", "tweets", ["draft one", "draft two"])
    rows = store.list_proposals(tenant_id="default")
    texts = {r["text"] for r in rows}
    assert "draft one" in texts
    assert "draft two" in texts
    assert all(r["proposal_id"] == payload["proposal_id"] for r in rows)


def test_post_now_rewrites_to_stored_proposal(
    client, studio_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kazma_core.agent.artifacts import ArtifactStore
    import kazma_core.agent.artifacts as art_mod

    store = ArtifactStore(tmp_path / "artifacts.db")
    saved = store.save_proposal("default", "thread-a", "tweets", ["canonical draft"])
    item_id = saved["items"][0]["id"]
    monkeypatch.setattr(art_mod, "get_artifact_store", lambda: store)

    captured: dict[str, str] = {}

    class _Ok:
        async def create_tweet(self, text, reply_to_id=""):
            captured["text"] = text
            return {"id": "99", "text": text}

    monkeypatch.setattr("kazma_core.x_api.client.XClient", lambda *a, **k: _Ok())
    resp = client.post(
        "/api/x/post",
        json={"text": "edited in the composer", "proposal_id": item_id},
        headers=_CSRF,
    )
    assert resp.status_code == 200, resp.text
    assert captured["text"] == "canonical draft"
    assert resp.json()["tweet_id"] == "99"
    remaining = store.list_proposals(tenant_id="default")
    assert remaining == []


def test_post_now_unknown_proposal_is_400(client) -> None:
    resp = client.post(
        "/api/x/post",
        json={"text": "hello", "proposal_id": "prop_missing:1"},
        headers=_CSRF,
    )
    assert resp.status_code == 400
    assert "proposal_id" in resp.json()["error"]


def test_delete_csrf_required(client) -> None:
    resp = client.post("/api/x/delete", json={"tweet_id": "1"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_does_not_call_api_when_disabled(
    studio_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kazma_core.x_api.booking import delete_x_post

    monkeypatch.setattr(
        "kazma_core.x_api.config.get_x_config",
        lambda: _cfg(enabled=False),
    )
    called = {"n": 0}

    class _Boom:
        async def delete_tweet(self, *a, **k):
            called["n"] += 1
            raise AssertionError("must not hit X when connector is off")

    monkeypatch.setattr("kazma_core.x_api.client.XClient", lambda *a, **k: _Boom())
    ok, payload = await delete_x_post(tweet_id="123")
    assert ok is False
    assert payload["deleted"] is False
    assert called["n"] == 0


def test_studio_page_and_sidebar_are_wired() -> None:
    root = Path(__file__).resolve().parents[1]
    sidebar = (root / "kazma-ui" / "kazma_ui" / "templates" / "components" / "sidebar.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/x"' in sidebar
    assert (root / "kazma-ui" / "kazma_ui" / "templates" / "x_studio.html").is_file()
    assert (root / "kazma-ui" / "kazma_ui" / "static" / "js" / "x_studio.js").is_file()
    html = (root / "kazma-ui" / "kazma_ui" / "templates" / "x_studio.html").read_text(encoding="utf-8")
    js = (root / "kazma-ui" / "kazma_ui" / "static" / "js" / "x_studio.js").read_text(encoding="utf-8")
    assert "x-cloak" in html
    assert "two-col-grid" in html
    assert "replyToId" in js
    assert "proposal_id" in js
    assert "reschedule" in js
    assert "/api/x/delete" in js
    assert ":dir=" in html
    assert "displayBody" in js
    assert "textDir" in js
    assert "unicode-bidi: plaintext" not in html
    css = (root / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css").read_text(
        encoding="utf-8"
    )
    assert ".xs-row .txt[dir=\"rtl\"]" in css
    assert "direction: rtl" in css.split(".xs-row .txt[dir=\"rtl\"]")[1][:400]


def test_bidi_js_pins_arabic_tweets_rtl() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "tests" / "js" / "test_bidi_post_dir.js"
    )
    out = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=20
    )
    assert out.returncode == 0, out.stdout + out.stderr
