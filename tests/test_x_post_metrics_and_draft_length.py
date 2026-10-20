"""X posts' public counts are readable, and a draft's X length is told.

Live 2026-10-20 (Telegram), tests 20 and 21:

- "Show me my last 5 posts on X with their like counts": ``x_status`` had no
  counts, and the agent fetched each post's public page (views only) and
  answered "likes unknown". The X client already reads posts (mentions,
  replies) on the X read budget; ``x_status(metrics=true)`` now reads each
  listed post's ``public_metrics`` in ONE request.
- "Draft a post about Kuwait's summer heat": the agent asked the operator to
  approve a ``python_exec`` whose only job was ``print(len(draft))``.
  ``save_proposal`` now gives each X draft's length as X counts it.
"""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    import kazma_core.x_api.ledger as ledger_mod

    led = ledger_mod.XPostLedger(tmp_path / "x_posts.db")
    for n in range(3):
        led.record(tweet_id=f"21110{n}", text=f"post number {n}")
    monkeypatch.setattr(ledger_mod, "_ledger", led)
    return led


@pytest.fixture
def configured(monkeypatch):
    from kazma_skills.native.x_publisher import tools

    real = tools.get_x_config

    def config():
        cfg = real()
        monkeypatch.setattr(type(cfg.credentials), "complete", lambda self: True)
        return cfg

    monkeypatch.setattr(tools, "get_x_config", config)


def _client_answers(monkeypatch, answer):
    """X answers the metrics read with *answer* (a body, or an exception)."""
    from kazma_core.x_api.client import XClient

    requests: list[str] = []

    async def request(self, method, path, **kw):
        requests.append(f"{method} {path}")
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(XClient, "_request", request)
    return requests


async def _status(**kw) -> dict:
    from kazma_skills.native.x_publisher.tools import x_status

    return json.loads(await x_status(**kw))


@pytest.mark.anyio
async def test_metrics_true_reads_every_listed_posts_counts_in_one_request(ledger, configured, monkeypatch):
    requests = _client_answers(monkeypatch, {"data": [
        {"id": "211100", "public_metrics": {"like_count": 4, "retweet_count": 1, "impression_count": 113}},
        {"id": "211102", "public_metrics": {"like_count": 0, "retweet_count": 0, "impression_count": 6}},
    ]})
    page = await _status(recent=3, metrics=True)
    assert len(requests) == 1 and "tweet.fields=public_metrics" in requests[0]
    assert all(i in requests[0] for i in ("211100", "211101", "211102"))
    by_id = {p["tweet_id"]: p for p in page["recent_posts"]}
    assert by_id["211100"]["metrics"]["like_count"] == 4
    assert by_id["211102"]["metrics"]["like_count"] == 0
    # A post X did not return is said, never shown as zero.
    assert "metrics" not in by_id["211101"] and page["metrics_missing"] == ["211101"]


@pytest.mark.anyio
async def test_a_failed_read_says_why_and_shows_no_counts(ledger, configured, monkeypatch):
    from kazma_core.x_api.client import XApiError

    _client_answers(monkeypatch, XApiError("X read budget is exhausted", outcome="not_sent"))
    page = await _status(recent=3, metrics=True)
    assert "budget" in page["metrics_error"]
    assert all("metrics" not in p for p in page["recent_posts"])


@pytest.mark.anyio
async def test_negative_control_without_metrics_nothing_is_read_from_x(ledger, configured, monkeypatch):
    requests = _client_answers(monkeypatch, {"data": []})
    page = await _status(recent=3)
    assert requests == [] and all("metrics" not in p for p in page["recent_posts"])


def test_the_manifest_offers_metrics():
    import inspect

    from kazma_skills.native.x_publisher.tools import x_status

    assert inspect.signature(x_status).parameters["metrics"].default is False


# -- a draft's X length ---------------------------------------------------

DRAFT = ("Kuwait in summer isn't just hot — it's one of the hottest places on Earth.\n\n"
         "Mean June–September highs run 44–46 °C.")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    import kazma_core.agent.artifacts as artifacts_mod

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "kazma-data"))
    monkeypatch.setenv("KAZMA_ARTIFACTS_DB", str(tmp_path / "kazma-data" / "agent_artifacts.db"))
    artifacts_mod.reset_artifact_store()
    yield artifacts_mod.get_artifact_store()
    artifacts_mod.reset_artifact_store()


def _save():
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return registry._tools["save_proposal"].func


def test_an_x_draft_is_saved_with_its_length_as_x_counts_it(store):
    from kazma_core.x_api.text_length import validate_text

    out = asyncio.run(_save()("tweets", [DRAFT, "x" * 300]))
    assert f"[X length {validate_text(DRAFT).weighted}/280]" in out
    assert "[X length 300/280, TOO LONG]" in out


def test_other_drafts_get_no_x_length(store):
    out = asyncio.run(_save()("emails", [DRAFT]))
    assert "X length" not in out
