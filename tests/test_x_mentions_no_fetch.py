"""A mention's pasted link is read from X's own entities, never fetched
(audit 2026-10-26 AUD-002).

``mentions_fire`` followed a t.co link from the mention text with a raw
``httpx.get(url, follow_redirects=True)`` -- before the "already handled"
check and the summoner allowlist. Anyone who mentioned the account chose a
URL the server fetched (t.co redirects wherever the link points): no
public-address check, no body cap, no total deadline, and a slow answer
stalled the sequential poller. X reports where each t.co link points in the
post's ``entities``; the poller asks for them and reads them.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from kazma_core.x_api import mentions_fire

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_X_API = _ROOT / "kazma-core" / "kazma_core" / "x_api"

_STATUS = "https://x.com/someone/status/1850000000000000001"


@pytest.fixture
def no_network(monkeypatch):
    import httpx

    def refuse(*_a, **_k):
        raise AssertionError("a mention made a network call")

    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.setattr(httpx, "Client", refuse)
    monkeypatch.setattr(httpx, "AsyncClient", refuse)


def test_a_pasted_status_link_is_read_from_the_entities(no_network) -> None:
    tweet = {
        "text": "@KazmaAI what about this https://t.co/AbCdEf",
        "entities": {"urls": [{"url": "https://t.co/AbCdEf", "expanded_url": _STATUS}]},
    }
    assert mentions_fire._tweet_id_from_post(tweet, tweet["text"]) == "1850000000000000001"


def test_a_long_post_s_entities_are_read_too(no_network) -> None:
    tweet = {
        "text": "@KazmaAI see https://t.co/x",
        "note_tweet": {"entities": {"urls": [{"unwound_url": _STATUS}]}},
    }
    assert mentions_fire._tweet_id_from_post(tweet, tweet["text"]) == "1850000000000000001"


def test_a_link_to_anywhere_else_is_no_status_and_no_fetch(no_network) -> None:
    tweet = {
        "text": "@KazmaAI https://t.co/evil",
        "entities": {"urls": [{"expanded_url": "http://169.254.169.254/latest/meta-data"}]},
    }
    assert mentions_fire._tweet_id_from_post(tweet, tweet["text"]) == ""


def test_the_mentions_request_asks_for_entities() -> None:
    src = (_X_API / "client.py").read_text(encoding="utf-8")
    line = next(ln for ln in src.splitlines()
                if "tweet.fields=author_id,conversation_id,referenced_tweets" in ln)
    assert "entities" in line


def _raw_http_calls(path: pathlib.Path) -> list[str]:
    """Network calls a module makes itself: module-level httpx helpers,
    httpx clients, urllib/requests."""
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        name = node.func.attr
        if isinstance(owner, ast.Name) and owner.id == "httpx" and name in {
            "get", "post", "put", "head", "delete", "request", "stream", "Client", "AsyncClient",
        }:
            found.append(f"{path.name}:{node.lineno} httpx.{name}")
        if isinstance(owner, ast.Name) and owner.id == "requests":
            found.append(f"{path.name}:{node.lineno} requests.{name}")
        if name == "urlopen":
            found.append(f"{path.name}:{node.lineno} urlopen")
    return found


def test_only_the_x_client_talks_to_the_network() -> None:
    """The X API client (fixed host, no redirects) is x_api's one network door."""
    calls = [c for p in sorted(_X_API.rglob("*.py")) for c in _raw_http_calls(p)]
    assert [c for c in calls if not c.startswith("client.py:")] == []


def test_the_gate_sees_the_old_resolver(tmp_path) -> None:
    """Negative control: the removed t.co resolver is caught."""
    old = tmp_path / "mentions_fire.py"
    old.write_text(
        "import httpx\n"
        "def _resolve(url):\n"
        "    r = httpx.get(url, follow_redirects=True, timeout=8.0)\n"
        "    return str(r.url)\n",
        encoding="utf-8",
    )
    assert _raw_http_calls(old) == ["mentions_fire.py:3 httpx.get"]
