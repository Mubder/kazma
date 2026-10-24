"""A public X post's video reaches the user: ``x_download_media`` + ``send_file``.

Live 2026-10-24 (Telegram): "download this X video and send it here". The
agent read the post's page (``read_url``: text, no media file), found the
shell off, ``python_exec`` without network and the browser refused, and said
it could not -- then searched the owner's Downloads folder through six
approval cards for a file nobody had made. Kazma's X connector already reads
posts through the official API; ``XClient.get_media`` reads a post's media
(the MP4 variants X serves a video as) and the tool saves the files in the
workspace, where ``send_file`` takes them.
"""

from __future__ import annotations

import json

import pytest

from kazma_core.agent.tool_refusal import KnownEffectError, RefusedBeforeEffect

POST = "https://x.com/someone/status/2113970901829968151?s=46"
TWEET_ID = "2113970901829968151"


@pytest.fixture
def configured(monkeypatch):
    from kazma_skills.native.x_publisher import tools

    real = tools.get_x_config

    def config():
        cfg = real()
        monkeypatch.setattr(type(cfg.credentials), "complete", lambda self: True)
        return cfg

    monkeypatch.setattr(tools, "get_x_config", config)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import kazma_core.workspace.binding as binding

    monkeypatch.setattr(binding, "resolve_active_root", lambda: tmp_path)
    return tmp_path


def _x_answers(monkeypatch, answer):
    """X answers the media read with *answer* (a body, or an exception)."""
    from kazma_core.x_api.client import XClient

    requests: list[str] = []

    async def request(self, method, path, **kw):
        requests.append(f"{method} {path}")
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(XClient, "_request", request)
    return requests


class _Got:
    def __init__(self, data: bytes, *, status: int = 200, truncated: bool = False):
        self.response = type("R", (), {"status_code": status, "content": data})()
        self.truncated = truncated


def _media_server(monkeypatch, files: dict[str, _Got]):
    """X's media hosts answer each URL in *files*; anything else fails the test."""
    import kazma_core.web_acquire.http as http

    fetched: list[str] = []

    async def fetch_public(url, **kw):
        fetched.append(url)
        assert kw["scope"](url), "fetched outside X's media hosts"
        return files[url]

    monkeypatch.setattr(http, "fetch_public", fetch_public)
    return fetched


VIDEO = {
    "type": "video",
    "duration_ms": 30000,
    "variants": [
        {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/v/pl.m3u8"},
        {"content_type": "video/mp4", "bit_rate": 632000, "url": "https://video.twimg.com/v/320.mp4"},
        {"content_type": "video/mp4", "bit_rate": 2176000, "url": "https://video.twimg.com/v/1280.mp4"},
        {"content_type": "video/mp4", "bit_rate": 950000, "url": "https://video.twimg.com/v/480.mp4"},
    ],
}


async def _download(post: str = POST) -> dict:
    from kazma_skills.native.x_publisher.tools import x_download_media

    return json.loads(await x_download_media(post))


@pytest.mark.anyio
async def test_a_posts_video_is_saved_in_the_workspace_at_its_best_quality(configured, workspace, monkeypatch):
    requests = _x_answers(monkeypatch, {"data": {"id": TWEET_ID}, "includes": {"media": [VIDEO]}})
    fetched = _media_server(monkeypatch, {"https://video.twimg.com/v/1280.mp4": _Got(b"MP4-1280")})
    out = await _download()
    assert out["ok"] is True and len(requests) == 1
    assert f"/2/tweets/{TWEET_ID}?" in requests[0] and "variants" in requests[0]
    assert fetched == ["https://video.twimg.com/v/1280.mp4"]
    saved = out["saved"][0]["path"]
    assert saved == str(workspace / "downloads" / "x" / f"{TWEET_ID}.mp4")
    assert (workspace / "downloads" / "x" / f"{TWEET_ID}.mp4").read_bytes() == b"MP4-1280"
    assert "send_file" in out["next"]


@pytest.mark.anyio
async def test_send_file_may_take_the_saved_file(configured, workspace, monkeypatch):
    from kazma_core.workspace.path_policy import check_path_access

    _x_answers(monkeypatch, {"includes": {"media": [VIDEO]}})
    _media_server(monkeypatch, {"https://video.twimg.com/v/1280.mp4": _Got(b"MP4")})
    out = await _download()
    assert check_path_access(out["saved"][0]["path"], "read").allowed


@pytest.mark.anyio
async def test_a_video_over_the_chat_limit_falls_to_the_next_quality(configured, workspace, monkeypatch):
    _x_answers(monkeypatch, {"includes": {"media": [VIDEO]}})
    fetched = _media_server(monkeypatch, {
        "https://video.twimg.com/v/1280.mp4": _Got(b"x" * 10, truncated=True),
        "https://video.twimg.com/v/480.mp4": _Got(b"MP4-480"),
    })
    out = await _download()
    assert fetched == ["https://video.twimg.com/v/1280.mp4", "https://video.twimg.com/v/480.mp4"]
    assert (workspace / "downloads" / "x" / f"{TWEET_ID}.mp4").read_bytes() == b"MP4-480"
    assert out["ok"] is True


@pytest.mark.anyio
async def test_photos_are_each_saved_and_a_foreign_host_is_never_fetched(configured, workspace, monkeypatch):
    _x_answers(monkeypatch, {"includes": {"media": [
        {"type": "photo", "url": "https://pbs.twimg.com/media/A.jpg"},
        {"type": "photo", "url": "https://evil.example/media/B.jpg"},
        {"type": "photo", "url": "https://pbs.twimg.com/media/C.png"},
    ]}})
    fetched = _media_server(monkeypatch, {
        "https://pbs.twimg.com/media/A.jpg?name=orig": _Got(b"JPG"),
        "https://pbs.twimg.com/media/C.png?name=orig": _Got(b"PNG"),
    })
    out = await _download(TWEET_ID)
    assert not any("evil" in url for url in fetched)
    names = sorted(p.name for p in (workspace / "downloads" / "x").iterdir())
    assert names == [f"{TWEET_ID}-1.jpg", f"{TWEET_ID}-2.png"]
    assert out["not_saved"] and "photo" in out["not_saved"][0]


@pytest.mark.anyio
async def test_a_post_without_media_says_so_and_saves_nothing(configured, workspace, monkeypatch):
    _x_answers(monkeypatch, {"data": {"id": TWEET_ID}})
    with pytest.raises(KnownEffectError) as err:
        await _download()
    assert "no photo, video or GIF" in str(err.value)
    assert not (workspace / "downloads").exists()


@pytest.mark.anyio
async def test_a_failed_x_read_is_a_known_effect_never_unknown(configured, workspace, monkeypatch):
    from kazma_core.x_api.client import XApiError

    _x_answers(monkeypatch, XApiError("X read budget is exhausted", outcome="not_sent"))
    with pytest.raises(KnownEffectError) as err:
        await _download()
    assert "budget" in str(err.value)


@pytest.mark.anyio
async def test_a_link_with_no_post_is_refused_before_anything_is_read(configured, workspace, monkeypatch):
    requests = _x_answers(monkeypatch, {"includes": {"media": [VIDEO]}})
    with pytest.raises(RefusedBeforeEffect):
        await _download("https://x.com/someone")
    assert requests == []


@pytest.mark.parametrize("link", [
    POST,
    f"https://twitter.com/someone/status/{TWEET_ID}",
    f"https://mobile.twitter.com/someone/statuses/{TWEET_ID}",
    f"https://x.com/i/web/status/{TWEET_ID}",
    f"https://x.com/i/status/{TWEET_ID}",
    f"x.com/someone/status/{TWEET_ID}/video/1",
    TWEET_ID,
])
def test_every_shape_of_post_link_names_its_post(link):
    from kazma_skills.native.x_publisher.tools import _post_id

    assert _post_id(link) == TWEET_ID


def test_negative_control_the_only_post_read_before_asked_for_no_media():
    """Before 2026-10-24 Kazma's one read of another account's post
    (``get_tweet``) asked for no media fields: no file to download existed."""
    import inspect

    from kazma_core.x_api.client import XClient

    assert "variants" not in inspect.getsource(XClient.get_tweet)
    assert "variants" in inspect.getsource(XClient.get_media)


def test_the_tool_is_registered_everywhere_a_tool_must_be():
    from kazma_core.safety.hitl import TOOL_TIERS
    from kazma_core.safety.side_effects import EffectKind, get_effect_profile
    from kazma_core.store_registry import TOOL_WRITES

    assert TOOL_TIERS["x_download_media"] == "write"
    assert get_effect_profile("x_download_media").effect is EffectKind.WRITE_FS
    assert TOOL_WRITES["x_download_media"].targets == ("workspace",)
