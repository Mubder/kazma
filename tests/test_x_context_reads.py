"""Optional context fields negotiate only reads and never hide a missing field."""

from __future__ import annotations

import pytest
from kazma_core.x_api.client import XApiError, XClient
from kazma_core.x_api.config import XCredentials


async def test_current_long_form_response_keeps_quote_context(monkeypatch):
    async def request(*args, **kwargs):
        return {"data": {"id": "1", "text": "short", "note_post": {"text": "full"},
                         "referenced_posts": [{"type": "quoted", "id": "2"}]},
                "includes": {"posts": [{"id": "2", "text": "quoted"}]}}
    client = XClient(XCredentials("k", "ks", "t", "ts"))
    monkeypatch.setattr(client, "_request", request)
    post, includes = await client.get_tweet("1")
    assert post["note_tweet"]["text"] == "full" and post["referenced_tweets"][0]["id"] == "2"
    assert includes["tweets"][0]["text"] == "quoted"


async def test_field_negotiation_is_bounded_and_flags_unavailable_context(monkeypatch):
    paths = []
    async def request(method, path, **kwargs):
        assert method == "GET"
        paths.append(path)
        if len(paths) < 3:
            raise XApiError("Invalid tweet.fields parameter", status=400)
        return {"data": [{"id": "1", "text": "source"}]}
    client = XClient(XCredentials("k", "ks", "t", "ts"))
    monkeypatch.setattr(client, "_request", request)
    posts, _ = await client.get_mentions("1")
    assert len(paths) == 3 and "note_tweet" in paths[0] and "note_post" in paths[1]
    assert "note_" not in paths[2] and posts[0]["_kazma_context_incomplete"]


async def test_auth_error_is_never_retried_as_field_negotiation(monkeypatch):
    calls = []
    async def request(*args, **kwargs):
        calls.append(1)
        raise XApiError("Read permission denied", status=403)
    client = XClient(XCredentials("k", "ks", "t", "ts"))
    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(XApiError):
        await client.get_mentions("1")
    assert len(calls) == 1
