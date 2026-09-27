"""The user's "About me" reaches the model on every call (plan C1, 2026-09-27).

Measured before building: the live install held 6 current user-stated facts
about the user, all subscription reset dates and 4 of them past -- a profile
assembled from facts would have put stale dates in front of the model every
turn. The owner chose a short text the user writes in Settings instead
(``memory/profile.py``), never inferred, placed right after the system prompt
and the personality on every call (``graph_helpers._ensure_about_user``).

Held here: the store (trimmed, refused -- not cut -- past the limit, cleared by
an empty text, per tenant); the placement; a real supervisor call that shows
the text to the model, and none without it (negative control); the routes and
the export. The routes' tenant rule is in ``test_memory_routes_tenant_scope.py``.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.agent.graph_helpers import _PERSONALITY_MARKER, _ensure_about_user
from kazma_core.memory import profile

ABOUT = "I'm Sara, a product manager in Dubai. I prefer short answers."


# ── the store ─────────────────────────────────────────────────────────────


def test_saved_text_comes_back_trimmed():
    assert profile.set_about("default", "  " + ABOUT + "\n")["ok"]
    got = profile.get_about("default")
    assert got["about"] == ABOUT and got["updated_at"]


def test_too_long_is_refused_not_cut():
    profile.set_about("default", ABOUT)
    out = profile.set_about("default", "x" * (profile.ABOUT_MAX_CHARS + 1))
    assert out["ok"] is False and str(profile.ABOUT_MAX_CHARS) in out["error"]
    assert profile.get_about("default")["about"] == ABOUT  # the saved text stands


def test_an_empty_text_clears_it():
    profile.set_about("default", ABOUT)
    profile.set_about("default", "   ")
    assert profile.get_about("default") == {"about": "", "updated_at": None}


def test_each_tenant_has_its_own():
    profile.set_about("alpha", "alpha's own words")
    profile.set_about("beta", "beta's own words")
    assert profile.get_about("alpha")["about"] == "alpha's own words"
    assert profile.get_about("gamma")["about"] == ""


# ── the placement ─────────────────────────────────────────────────────────


def _msgs(personality: bool = False) -> list[dict]:
    head = [{"role": "system", "content": "You are Kazma."}]
    if personality:
        head.append({"role": "system", "content": f"{_PERSONALITY_MARKER}\nBe warm."})
    return head + [{"role": "user", "content": "hi"}]


def test_it_sits_after_the_system_prompt_and_the_personality():
    block = profile.about_block(ABOUT)
    out = _ensure_about_user(_msgs(personality=True), block)
    assert [m["content"][:12] for m in out[:3]] == ["You are Kazm", _PERSONALITY_MARKER[:12], block[:12]]
    assert out[3]["role"] == "user"


def test_an_edit_replaces_the_old_copy_and_clearing_removes_it():
    old = _ensure_about_user(_msgs(), profile.about_block("old words"))
    new = _ensure_about_user(old, profile.about_block("new words"))
    blocks = [m for m in new if profile.ABOUT_MARKER in m["content"]]
    assert len(blocks) == 1 and "new words" in blocks[0]["content"]
    assert not any(profile.ABOUT_MARKER in m["content"] for m in _ensure_about_user(new, ""))


# ── the model sees it ─────────────────────────────────────────────────────


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls: list = []
        self.model = "fake-model"
        self.usage = {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
        self.cost_usd = 0.0


class _LLM:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def chat(self, messages=None, tools=None, model=None, **kwargs):
        self.calls.append(list(messages or []))
        return _Response("Hello!")


class _Fake:
    compactor = type("C", (), {"retrieve_memories": staticmethod(lambda *a, **k: [])})()

    def should_halt(self) -> bool:
        return False

    def record_cost(self, cost: float) -> None:
        pass

    def trace_llm_call(self, **kwargs) -> None:
        pass

    async def check_and_enforce(self, state):
        return state


async def _run(tenant: str = "default", messages: list | None = None) -> list[dict]:
    from kazma_core.agent.graph_builder import supervisor_node

    llm = _LLM()
    fake = _Fake()
    await supervisor_node(
        {"messages": messages or _msgs(), "iteration": 0, "max_iterations": 5, "tenant_id": tenant},
        llm=llm, system_prompt="You are Kazma.", tool_definitions=[], tool_executor=None,
        cost_breaker=fake, authority=fake, tracer=fake,
    )
    return llm.calls[0]


@pytest.mark.asyncio
async def test_the_model_is_shown_the_users_about_me():
    profile.set_about("default", ABOUT)
    sent = await _run()
    shown = [m["content"] for m in sent if profile.ABOUT_MARKER in str(m.get("content"))]
    assert len(shown) == 1 and ABOUT in shown[0]


@pytest.mark.asyncio
async def test_without_one_the_model_is_shown_none():
    """Negative control -- and a stale copy in the history goes too."""
    stale = _ensure_about_user(_msgs(), profile.about_block("cleared since"))
    sent = await _run(messages=stale)
    assert not any(profile.ABOUT_MARKER in str(m.get("content")) for m in sent)


@pytest.mark.asyncio
async def test_the_turn_reads_its_own_tenants_text():
    profile.set_about("alpha", "ALPHA words")
    sent = await _run(tenant="beta")
    assert "ALPHA words" not in json.dumps(sent)


# ── the routes and the export ─────────────────────────────────────────────


@pytest.fixture()
def client():
    from kazma_ui.memory_api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_settings_routes_round_trip(client):
    assert client.get("/api/memory/v2/profile").json() == {
        "ok": True, "about": "", "updated_at": None, "max_chars": profile.ABOUT_MAX_CHARS}
    assert client.put("/api/memory/v2/profile", json={"about": ABOUT}).json() == {"ok": True, "about": ABOUT}
    assert client.get("/api/memory/v2/profile").json()["about"] == ABOUT
    assert client.put("/api/memory/v2/profile", json={"text": "wrong key"}).json()["ok"] is False


def test_the_export_carries_it(client):
    client.put("/api/memory/v2/profile", json={"about": ABOUT})
    exported = json.loads(client.get("/api/memory/v2/export").content)
    assert [a["about"] for a in exported["about_me"]] == [ABOUT]
