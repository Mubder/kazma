"""A Studio post has its own length; replies keep theirs.

Posts took the reply length (two sentences by default), and the safety check
rejected a three-sentence Studio post for it (live 2026-10-26). Settings -> X
-> Post length (``connectors.x.drafting.post_length``: short, standard, full;
full by default) now sets a post's length in the language contract the
drafter and every check read. The reply contract is untouched. The negative
control drafts with the post length not applied, as before, and the
drafter's contract says two sentences.
"""

from __future__ import annotations

import dataclasses

import pytest

from tests.test_x_drafting_references import drafting  # noqa: F401 -- the fixture


def _set_post_length(monkeypatch, length: str) -> None:
    from kazma_core.x_api import stance

    cfg = dataclasses.replace(stance.get_reply_config(), post_length=length)
    monkeypatch.setattr("kazma_core.x_api.stance.get_reply_config", lambda: cfg)


async def test_a_post_is_drafted_and_checked_at_full_length_by_default(drafting):  # noqa: F811
    from kazma_core.x_api.post_drafting import draft_posts

    result = await draft_posts("A post.")
    assert "length=full" in drafting["messages"][0]["content"]
    assert result.reviews[0]["effective_policy"]["reply_style"]["length"] == "full"


async def test_the_setting_chooses_the_length(drafting, monkeypatch):  # noqa: F811
    from kazma_core.x_api.post_drafting import draft_posts

    _set_post_length(monkeypatch, "short")
    await draft_posts("A post.")
    assert "length=short" in drafting["messages"][0]["content"]


async def test_negative_control_without_the_post_length_a_post_gets_the_reply_length(drafting, monkeypatch):  # noqa: F811
    from kazma_core.x_api.post_drafting import draft_posts

    monkeypatch.setattr("kazma_core.x_api.reply_style.with_post_length", lambda subject, length: subject)
    await draft_posts("A post.")
    assert "length=standard" in drafting["messages"][0]["content"]


def test_replies_keep_the_reply_length():
    from kazma_core.x_api.reply_style import bind_style, style_contract, with_post_length
    from kazma_core.x_api.stance import ReplyConfig, implicit_voice_subject

    cfg = ReplyConfig(enabled=False, mode="draft", summoners=(), trigger="", max_replies_per_day=5,
                      max_replies_per_target_per_day=1, cooldown_per_thread_s=0, min_target_followers=0,
                      poll_interval_s=60, post_length="full")
    reply = bind_style(implicit_voice_subject(), cfg)
    assert "length=standard" in style_contract(reply)
    assert "length=full" in style_contract(with_post_length(reply, cfg.post_length))
    assert "length=full" in style_contract(with_post_length(reply, "an unknown length"))


def test_settings_save_the_post_length_and_refuse_an_unknown_one(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_ui.x_reply_api import protected_router, router

    async def no_loop():
        pass

    monkeypatch.setattr("kazma_core.x_api.mentions_fire.ensure_mentions_loop", no_loop)
    app = FastAPI()
    app.include_router(router)
    app.include_router(protected_router)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    with TestClient(app) as client:
        assert client.get("/api/x/reply").json()["post_length"] == "full"
        response = client.put("/api/x/reply", json={"post_length": "standard"}, headers=headers)
        assert response.status_code == 200 and response.json()["post_length"] == "standard"
        client.put("/api/x/reply", json={}, headers=headers)
        assert client.get("/api/x/reply").json()["post_length"] == "standard"
        assert client.put("/api/x/reply", json={"post_length": "epic"}, headers=headers).status_code == 400
    assert get_config_store().get("connectors.x.drafting.post_length") == "standard"


@pytest.mark.parametrize("length", ["short", "standard", "full"])
def test_every_post_length_is_a_valid_style(length):
    from kazma_core.x_api.reply_style import normalize_style

    assert normalize_style({"length": length})["length"] == length
