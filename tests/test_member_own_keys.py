"""Spaces S2-04: a member's own model keys.

Reem keeps a DeepSeek key and an Anthropic key of her own in her personal
space ("Use my own key" in My account); Omar keeps none. The install's keys
are the admin's.

* **A call made for Reem goes with her key alone**, whatever chose it and in
  whichever of her spaces it runs: the real provider clients (``chat`` and
  ``chat_stream`` of the OpenAI-shaped and the Anthropic classes) put her key
  in the header, and the install's key is never sent. Her key refused, the
  call fails as hers: never the install's key in its place, never the
  install's other keys (providers stage 4).
* **Everyone else's calls are unchanged**: Omar's, the admin's, Kazma's own
  work (nothing bound), and every call while Spaces is not enforced (then
  nothing is read).
* **Her spend on her own key does not count against her limit** (the ledger
  marks the call ``own_key``; ``usage_of`` and ``usage_since`` leave it out).
* **The ledger records the call that answered** (the review of S2-04): a
  real supervisor turn and ``resilient_chat`` write the row of the client
  that answered, ``own_key`` from its answer, never worked out from a
  provider's name. Controls: today's rows named the build-time client's
  provider and asked the vault whether she had a key for it, so a call on
  the install's key was recorded as hers and the reverse.
* **Her key never goes through the install's egress proxy** (LiteLLM), and
  neither does the check of a key she types: behind the proxy a key that
  does not work was kept (the proxy's own key answered) and the install
  paid for the check. The check is ledgered as hers, rate limited.
* **The key is not shown to the admin**: the admin's own work cannot read
  it from the vault, and no GET route of the built app answers it to the
  admin. The routes answer Reem whether one is kept, never the key.
* **Past her daily limit a turn on her own key still runs**, on her own keys
  only: a real turn whose model she holds a key for starts and goes with
  her key; a call the turn would make on the install's key (a respond
  synthesis on another provider, a failover) is refused before it is sent.
  Controls: a turn on the install's key refused as at the limit; today's
  refusal (no admission) refusing her own-key turn too; the failover under
  her limit going on the install's key; the respond node without the rule.
  Gate: every graph node holds the rule (a planted node as the control).
* **Gate**: every ``chat``/``chat_stream`` of every provider class that asks
  ``_person_call`` hands the call to the client it returns (a planted class
  that asks and ignores the answer is the control).

Control: the install-scoped provider row (``_person_call`` without the own
key: ``own_keys.key_for_call`` answering nothing) sends the install's key
for Reem.
"""

from __future__ import annotations

import ast
import asyncio
import json
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from kazma_core.config_store import get_config_store
from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider
from kazma_core.observability import llm_ledger
from kazma_core.provider_adapters import build_client
from kazma_core.security import vault as vault_mod
from kazma_core.security.vault import SecretVault
from kazma_core.spaces import identity, own_keys, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, bind_scope

REPO = Path(__file__).resolve().parents[1]
VAULT_KEY = "c" * 64
INSTALL = {"deepseek": "install-deepseek-key", "anthropic": "install-anthropic-key"}
SPARE = "install-deepseek-spare-key"
REEMS = {"deepseek": "reem-deepseek-key", "anthropic": "reem-anthropic-key"}
TEAM = "team-s204"

ROWS: dict[str, dict[str, Any]] = {
    "deepseek": {"name": "deepseek", "preset": "deepseek", "base_url": "https://api.deepseek.invalid/v1",
                 "api_key": INSTALL["deepseek"], "enabled": True},
    "anthropic": {"name": "anthropic", "preset": "anthropic", "base_url": "https://api.anthropic.invalid/v1",
                  "api_key": INSTALL["anthropic"], "enabled": True},
    "ollama": {"name": "ollama", "preset": "ollama", "base_url": "http://localhost:11434/v1", "enabled": True},
}
MODELS = {"deepseek": "deepseek-chat", "anthropic": "claude-test", "ollama": "llama3"}


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    from tests._no_models import finish_turn_memory, keep_models_out

    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


class _Registry:
    """The install's three providers, as the model registry answers them
    (DeepSeek in use), and the clients it builds."""

    def __init__(self, world: World) -> None:
        self.world = world

    def get_provider(self, name: str) -> dict[str, Any] | None:
        row = ROWS.get(str(name))
        return dict(row) if row else None

    def list_providers(self) -> list[dict[str, Any]]:
        return [dict(r) for r in ROWS.values()]

    def split_model_ref(self, ref: str) -> tuple[str, str]:
        head, sep, tail = str(ref or "").partition(":")
        return (head, tail) if sep and head in ROWS else ("", str(ref or ""))

    def find_provider_for_model(self, model: str) -> dict[str, Any] | None:
        return next((dict(ROWS[p]) for p, m in MODELS.items() if m == model), None)

    def get_active_profile(self) -> dict[str, Any]:
        return {"provider": "deepseek", "model": MODELS["deepseek"]}

    def get_client(self, model: str | None = None) -> LLMProvider:
        provider = self.split_model_ref(str(model or ""))[0] or (
            (self.find_provider_for_model(str(model or "")) or {}).get("name") or "deepseek")
        return self.world.client(str(provider))

    def provider_for_client(self, client: Any) -> str:
        return str(getattr(client, "provider_name", "") or "")


def _listed() -> dict[str, Any]:
    return {"default": "deepseek:deepseek-chat", "options": [
        {"ref": f"{p}:{m}", "provider": p, "provider_label": p.title(), "model": m, "local": p == "ollama"}
        for p, m in MODELS.items()]}


class World:
    """Reem (with her own keys), Omar (none), a shared space of both, the
    install's clients, and every request the providers were sent."""

    def __init__(self) -> None:
        self.reem = ""
        self.omar = ""
        self.reem_space = ""
        self.sent: list[dict[str, str]] = []
        self.refuse: set[str] = set()
        self.down: set[str] = set()
        self.registry = _Registry(self)

    def client(self, provider: str) -> LLMProvider:
        """The install's client for *provider*, as the registry builds it:
        the install's key, and its spare key to switch to."""
        config = LLMConfig(base_url=ROWS[provider]["base_url"], api_key=INSTALL[provider],
                           model=MODELS[provider], key_switch=lambda failed, exc: SPARE)
        return build_client(provider, config, ROWS[provider])

    def keys_sent(self) -> list[str]:
        return [s["key"] for s in self.sent]

    def sent_to(self) -> list[tuple[str, str]]:
        """(host, key) of every request."""
        return [(s["host"], s["key"]) for s in self.sent]


def _stream_of(anthropic: bool) -> str:
    """A streamed answer "done" (3 prompt tokens, 1 completion), in the
    provider's own event format."""
    if anthropic:
        events = [
            ("message_start", {"type": "message_start", "message": {
                "id": "m", "type": "message", "role": "assistant", "model": "claude-test", "content": [],
                "stop_reason": None, "usage": {"input_tokens": 3, "output_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "done"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                               "usage": {"output_tokens": 1}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        return "".join(f"event: {name}\ndata: {json.dumps(body)}\n\n" for name, body in events)
    chunks = [
        {"id": "c", "model": "deepseek-chat", "choices": [
            {"index": 0, "delta": {"role": "assistant", "content": "done"}, "finish_reason": None}]},
        {"id": "c", "model": "deepseek-chat", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _transport(world: World) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
        world.sent.append({"host": request.url.host, "path": request.url.path, "key": key})
        if key in world.refuse:
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})
        if key in world.down:
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        try:
            streamed = bool(json.loads(request.content or b"{}").get("stream"))
        except ValueError:
            streamed = False
        if streamed:
            return httpx.Response(200, text=_stream_of(request.url.path.endswith("/messages")),
                                  headers={"content-type": "text/event-stream"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={
                "id": "m", "type": "message", "role": "assistant", "model": "claude-test",
                "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 1}})
        return httpx.Response(200, json={
            "id": "c", "model": "deepseek-chat",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}})

    return httpx.MockTransport(answer)


@pytest.fixture
def world(tmp_path, monkeypatch):
    import kazma_core.model_registry as model_registry
    import kazma_core.models.options as options

    monkeypatch.setenv("KAZMA_VAULT_KEY", VAULT_KEY)
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    vault = SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "_vault", vault)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    vault_mod.reset_posture_cache()
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    w = World()
    monkeypatch.setattr(model_registry, "peek_model_registry", lambda: w.registry)
    monkeypatch.setattr(model_registry, "get_model_registry", lambda: w.registry)
    monkeypatch.setattr(options, "model_options", lambda registry, details=True: _listed())
    transport = _transport(w)
    real_client = httpx.AsyncClient

    def with_transport(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", with_transport)
    w.reem = people.create_person(f"reem-{secrets.token_hex(3)}", "a-long-enough-password",
                                  by=OWNER_ACCOUNT_ID)["id"]
    w.omar = people.create_person(f"omar-{secrets.token_hex(3)}", "a-long-enough-password",
                                  by=OWNER_ACCOUNT_ID)["id"]
    w.reem_space = own_keys._personal_space(w.reem)
    identity.create_space("Team", created_by=OWNER_ACCOUNT_ID, space_id=TEAM)
    people.add_member(TEAM, w.reem, by=OWNER_ACCOUNT_ID)
    people.add_member(TEAM, w.omar, by=OWNER_ACCOUNT_ID)
    for provider, key in REEMS.items():
        own_keys.set_key(w.reem, provider, key, w.registry)
    yield w
    own_keys._forget(w.reem)
    own_keys._forget(w.omar)
    store.delete(identity.REGISTRY_KEY)
    vault.close()
    vault_mod.reset_posture_cache()


def _chat(client: LLMProvider) -> Any:
    return asyncio.run(client.chat([{"role": "user", "content": "hi"}], max_tokens=10))


# ── a call made for Reem goes with her key alone ────────────────────────


@pytest.mark.parametrize("provider", ["deepseek", "anthropic"])
def test_a_call_for_reem_goes_with_her_key_alone(world, provider: str) -> None:
    with bind_scope(world.reem_space, world.reem):
        assert _chat(world.client(provider)).content == "done"
    assert world.keys_sent() == [REEMS[provider]], world.sent


def test_her_key_goes_from_a_shared_space_too(world) -> None:
    with bind_scope(TEAM, world.reem):
        _chat(world.client("deepseek"))
    assert world.keys_sent() == [REEMS["deepseek"]]


def test_control_the_install_scoped_row_sends_the_installs_key_for_her(world, monkeypatch) -> None:
    monkeypatch.setattr(own_keys, "key_for_call", lambda provider: None)
    with bind_scope(world.reem_space, world.reem):
        _chat(world.client("deepseek"))
    assert world.keys_sent() == [INSTALL["deepseek"]]


def test_the_stream_goes_with_her_key(world, monkeypatch) -> None:
    keys: list[str] = []

    async def once(self: LLMProvider, *_a: Any, **_k: Any) -> Any:
        from kazma_core.llm_provider import StreamDelta

        keys.append(str(self.config.api_key))
        yield StreamDelta(content="done")

    monkeypatch.setattr(LLMProvider, "_chat_stream_once", once)

    async def stream() -> None:
        async for _ in world.client("deepseek").chat_stream([{"role": "user", "content": "hi"}]):
            pass

    with bind_scope(world.reem_space, world.reem):
        asyncio.run(stream())
    assert keys == [REEMS["deepseek"]]


def test_her_key_refused_fails_as_hers_and_nothing_else_is_tried(world) -> None:
    world.refuse.add(REEMS["deepseek"])
    with bind_scope(world.reem_space, world.reem), pytest.raises(LLMError) as raised:
        _chat(world.client("deepseek"))
    assert "Your own key for deepseek was refused" in str(raised.value)
    assert world.keys_sent() == [REEMS["deepseek"]], "the install's keys were tried in its place"


def test_control_the_installs_client_switches_to_its_spare_key(world) -> None:
    """The install's own client does switch keys: what her twin must not."""
    world.refuse.add(INSTALL["deepseek"])
    with bind_scope(world.reem_space, world.omar):
        _chat(world.client("deepseek"))
    assert world.keys_sent() == [INSTALL["deepseek"], SPARE]


def test_the_install_rows_secret_headers_never_go_with_her_key(world, monkeypatch) -> None:
    seen: list[dict[str, str]] = []
    real = httpx.AsyncClient

    def headers_of(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        seen.append(dict(kwargs.get("headers") or {}))
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", headers_of)
    client = world.client("deepseek")
    client.config.extra_headers = {"X-Org": "the-owners-org"}
    with bind_scope(world.reem_space, world.reem):
        _chat(client)
    assert seen and all("X-Org" not in h for h in seen), seen


# ── everyone else's calls are unchanged ─────────────────────────────────


def test_omars_and_the_admins_calls_use_the_installs_key(world) -> None:
    with bind_scope(TEAM, world.omar):
        _chat(world.client("deepseek"))
    with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        _chat(world.client("deepseek"))
    _chat(world.client("deepseek"))  # Kazma's own work: nothing bound
    assert world.keys_sent() == [INSTALL["deepseek"]] * 3


def test_with_spaces_off_nothing_is_read(world, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    monkeypatch.setattr(identity, "read_registry", lambda: pytest.fail("a read without Spaces"))
    with bind_scope(world.reem_space, world.reem):
        _chat(world.client("deepseek"))
    assert world.keys_sent() == [INSTALL["deepseek"]]


def test_an_unreadable_store_refuses_the_call_never_the_installs_key(world, monkeypatch) -> None:
    own_keys._forget(world.reem)

    def away() -> Any:
        raise identity.IdentityUnavailable("the accounts store is away")

    from kazma_core.spaces import person_models

    # S2-03's check refuses on an unreadable store too; out of the way, the
    # own-key read is what decides.
    monkeypatch.setattr(person_models, "call_refusal", lambda provider, model: None)
    monkeypatch.setattr(identity, "read_registry", away)
    with bind_scope(world.reem_space, world.reem), pytest.raises(LLMError) as raised:
        _chat(world.client("deepseek"))
    assert raised.value.kind == "own_key_unreadable" and world.sent == []


def test_a_server_on_this_network_takes_no_own_key(world) -> None:
    assert not own_keys._takes_own_key(ROWS["ollama"])
    assert own_keys._takes_own_key(ROWS["deepseek"]) and own_keys._takes_own_key(ROWS["anthropic"])
    assert not own_keys._takes_own_key({"name": "bedrock", "preset": "bedrock"})


# ── her spend on her own key does not count against her limit ───────────


@pytest.fixture
def ledger(tmp_path, monkeypatch) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "llm_calls.db"), check_same_thread=False)
    conn.executescript(llm_ledger._SCHEMA)
    llm_ledger._migrate(conn)
    monkeypatch.setattr(llm_ledger, "_get_conn", lambda db_path=None: conn)
    return conn


def _record(world: World, provider: str, tokens: int) -> None:
    """A call answered on her own key (the answer said so)."""
    llm_ledger.record_llm_call(provider=provider, model=MODELS[provider], prompt_tokens=tokens,
                               cost_usd=1.0, asker=world.reem, space=world.reem_space, own_key=True)


def test_her_own_key_spend_is_not_counted_against_her_limit(world, ledger) -> None:
    _record(world, "deepseek", 900)  # her own key
    llm_ledger.record_llm_call(provider="ollama", model="llama3", prompt_tokens=50, cost_usd=0.0,
                               asker=world.reem, space=world.reem_space)  # the install's server
    marks = dict(ledger.execute("SELECT provider, own_key FROM llm_calls").fetchall())
    assert marks == {"deepseek": 1, "ollama": 0}
    since = datetime.now(UTC) - timedelta(hours=1)
    assert llm_ledger.usage_of(world.reem, since)["tokens"] == 50
    assert llm_ledger.usage_since(since)["tokens"] == 50
    assert [p["asker"] for p in llm_ledger.spend_by_person(since)] == [world.reem]


def test_control_counting_every_row_counts_her_own_key(world, ledger) -> None:
    _record(world, "deepseek", 900)
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    total = ledger.execute("SELECT SUM(total_tokens) FROM llm_calls WHERE asker = ? AND ts >= ?",
                           (world.reem, since)).fetchone()[0]
    assert total == 900


def test_her_limit_is_not_reached_by_her_own_keys_spend(world, ledger, monkeypatch) -> None:
    from kazma_core.spaces import daily_limits

    people.set_daily_limit(world.reem, {"tokens": 100, "usd": 0}, by=OWNER_ACCOUNT_ID)
    monkeypatch.setattr(daily_limits, "_ledger_on", lambda: True)
    _record(world, "deepseek", 5000)
    assert not daily_limits.verdict(world.reem)["over"]
    llm_ledger.record_llm_call(provider="ollama", model="llama3", prompt_tokens=200,
                               asker=world.reem, space=world.reem_space)
    assert daily_limits.verdict(world.reem)["over"]


# ── the key is not shown to the admin ───────────────────────────────────


def test_the_admins_own_work_cannot_read_her_key(world) -> None:
    name = own_keys._secret_name(world.reem_space, "deepseek")
    vault = vault_mod.get_vault()
    with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert vault.retrieve(name) is None
        assert name not in {r["name"] for r in vault.list_secrets()}
    with bind_scope(TEAM, world.omar):
        assert vault.retrieve(name) is None


def test_her_key_is_sealed_in_her_personal_space(world) -> None:
    vault = vault_mod.get_vault()
    row = vault._conn.execute("SELECT tenant_id, key_id, encrypted_value FROM secrets WHERE name = ?",
                              (own_keys._secret_name(world.reem_space, "deepseek"),)).fetchone()
    assert row is not None and row[1], "sealed with her space's key"
    assert REEMS["deepseek"].encode() not in bytes(row[2])


def test_erasing_her_space_takes_her_keys(world) -> None:
    vault = vault_mod.get_vault()
    vault.erase_space(world.reem_space)
    own_keys._forget(world.reem)
    assert vault.for_space(world.reem_space).retrieve(own_keys._secret_name(world.reem_space, "deepseek")) is None


# ── the routes ───────────────────────────────────────────────────────────


@pytest.fixture
def as_reem(world, monkeypatch):
    """Requests signed in as Reem, in her personal space."""
    from kazma_ui import auth, space_binding

    monkeypatch.setattr(auth, "get_request_principal", lambda request: {"stand_in": "reem"})
    monkeypatch.setattr(space_binding, "account_of",
                        lambda principal: world.reem if principal.get("stand_in") == "reem" else
                        (OWNER_ACCOUNT_ID if principal.get("stand_in") == "admin" else None))
    return {space_binding.SPACE_HEADER: world.reem_space}


@pytest.fixture
def app(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.own_keys_api import create_own_keys_router

    checked: list[dict[str, Any]] = []

    async def check(entry: dict[str, Any], model: str, **_k: Any) -> dict[str, Any]:
        checked.append({"key": entry.get("api_key"), "headers": entry.get("secret_headers"), "model": model})
        if entry.get("api_key") == "bad-key":
            return {"ok": False, "error_code": "key_refused", "error": "The provider refused the key (HTTP 401)."}
        return {"ok": True}

    import kazma_core.provider_check as provider_check

    monkeypatch.setattr(provider_check, "check_model", check)
    built = FastAPI()
    built.middleware("http")(auth.create_tenant_middleware())
    built.include_router(create_own_keys_router())
    with TestClient(built) as client:
        client.checked = checked  # type: ignore[attr-defined]
        yield client


def test_the_route_lists_her_providers_and_never_a_key(world, app, as_reem) -> None:
    answer = app.get("/api/account/model-keys", headers=as_reem)
    assert answer.status_code == 200, answer.text
    providers = {p["provider"]: p["own"] for p in answer.json()["providers"]}
    assert providers == {"anthropic": True, "deepseek": True}, "a server on this network takes no key"
    for key in (*REEMS.values(), *INSTALL.values()):
        assert key not in answer.text


def test_a_key_is_kept_only_once_it_answered(world, app, as_reem) -> None:
    refused = app.put("/api/account/model-keys/deepseek", json={"key": "bad-key"}, headers=as_reem)
    assert refused.status_code == 400 and refused.json()["code"] == "key_refused"
    own_keys._forget(world.reem)
    assert own_keys._read_key(world.reem, "deepseek") == REEMS["deepseek"], "nothing was kept"
    kept = app.put("/api/account/model-keys/deepseek", json={"key": "reem-new-key"}, headers=as_reem)
    assert kept.status_code == 200 and {k: kept.json()[k] for k in ("ok", "provider", "own")} == {
        "ok": True, "provider": "deepseek", "own": True}
    assert "reem-new-key" not in kept.text
    assert app.checked[-1] == {"key": "reem-new-key", "headers": {}, "model": "deepseek-chat"}
    assert own_keys._read_key(world.reem, "deepseek") == "reem-new-key"


def test_removing_her_key_brings_back_the_installs(world, app, as_reem) -> None:
    gone = app.delete("/api/account/model-keys/deepseek", headers=as_reem)
    assert gone.status_code == 200 and gone.json()["removed"] is True
    with bind_scope(world.reem_space, world.reem):
        _chat(world.client("deepseek"))
    assert world.keys_sent() == [INSTALL["deepseek"]]


def test_the_routes_refuse_the_admin_and_an_unknown_provider(world, app, as_reem, monkeypatch) -> None:
    from kazma_ui import auth

    assert app.put("/api/account/model-keys/ollama", json={"key": "k"}, headers=as_reem).json()["code"] \
        == "unknown_provider"
    monkeypatch.setattr(auth, "get_request_principal", lambda request: {"stand_in": "admin"})
    owner = {"X-Kazma-Space": OWNER_SPACE_ID}
    answer = app.get("/api/account/model-keys", headers=owner)
    assert answer.status_code == 409 and answer.json()["code"] == "admin_uses_install_keys"


def test_with_spaces_off_the_routes_refuse(world, app, as_reem, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    answer = app.get("/api/account/model-keys")
    assert answer.status_code == 409 and answer.json()["code"] == "spaces_off"


# ── the gate: every provider class hands the call on ────────────────────

_ASK = "_person_call"


def _ignored_answers(sources: dict[str, str]) -> list[str]:
    """``file:Class.method`` for each ``chat``/``chat_stream`` of a provider
    class that asks ``_person_call`` and does not hand the call to the
    client it returned (the name it was bound to, passed to a call)."""
    missing: list[str] = []
    for rel, text in sources.items():
        for cls in (n for n in ast.parse(text).body if isinstance(n, ast.ClassDef)):
            bases = {b.id for b in cls.bases if isinstance(b, ast.Name)}
            if cls.name != "LLMProvider" and "LLMProvider" not in bases:
                continue
            for fn in cls.body:
                if not isinstance(fn, ast.AsyncFunctionDef) or fn.name not in ("chat", "chat_stream"):
                    continue
                bound = {
                    t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                    and isinstance(n.value, ast.Await) and isinstance(n.value.value, ast.Call)
                    and getattr(n.value.value.func, "attr", "") == _ASK
                    for t in n.targets if isinstance(t, ast.Name)
                }
                if not bound:
                    continue
                handed = any(
                    isinstance(c, ast.Call) and getattr(c.func, "attr", "") == fn.name
                    and c.args and isinstance(c.args[0], ast.Name) and c.args[0].id in bound
                    for c in ast.walk(fn))
                if not handed:
                    missing.append(f"{rel}:{cls.name}.{fn.name}")
    return sorted(missing)


def _provider_sources() -> dict[str, str]:
    from tests.test_person_models import _product_provider_sources

    return _product_provider_sources()


def test_every_provider_class_hands_the_call_to_the_persons_client() -> None:
    sources = _provider_sources()
    asking = [r for r, t in sources.items() if _ASK in t]
    assert any("anthropic_llm.py" in r for r in asking) and any("bedrock_llm.py" in r for r in asking)
    assert _ignored_answers(sources) == []


def test_control_a_class_that_asks_and_ignores_the_answer_is_found() -> None:
    planted = {"planted.py": (
        "class LLMProvider:\n    pass\n"
        "class Planted(LLMProvider):\n"
        "    async def chat(self, messages, model=None):\n"
        "        own = await self._person_call(model)\n"
        "        return await self._send(messages)\n"
    )}
    assert _ignored_answers(planted) == ["planted.py:Planted.chat"]


# ── what the page shows ─────────────────────────────────────────────────


def test_the_card_is_a_members_and_never_the_installs() -> None:
    page = (REPO / "kazma-ui" / "kazma_ui" / "templates" / "settings.html").read_text(encoding="utf-8")
    card = page.index('id="own-model-keys"')
    guard = page.rfind("{% if not install_area %}", 0, card)
    assert guard != -1 and page.find("{% endif %}", guard) > card
    script = (REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_ops.js").read_text(encoding="utf-8")
    assert "/api/account/model-keys" in script
    assert json.dumps("key") in script.replace("'", '"') or "key: key" in script


# ── no GET route answers her key to the admin ───────────────────────────

WALK_SECRET = "own-key-walk-test-secret"
SENTINEL = "SENTINELOWNKEY4d2a"


def test_no_get_route_answers_her_key_to_the_admin(tmp_path, monkeypatch) -> None:
    """Every GET ``/api`` route of the built app, called as the admin (the
    shared secret), with Reem's own key planted in her personal space: none
    answers it. The walk of ``test_no_get_route_answers_a_provider_key``."""
    import socket

    from fastapi.testclient import TestClient
    from kazma_core.model_registry import reset_model_registry

    from tests.test_no_get_route_answers_a_provider_key import _LOCAL, NOT_WALKED, _get_routes

    monkeypatch.setenv("KAZMA_SECRET", WALK_SECRET)
    monkeypatch.setenv("KAZMA_VAULT_KEY", VAULT_KEY)
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    vault = SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "_vault", vault)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    vault_mod.reset_posture_cache()
    real_connect, real_resolve = socket.create_connection, socket.getaddrinfo

    def local_only(address: Any, *args: Any, **kwargs: Any) -> Any:
        if str(address[0]) not in _LOCAL:
            raise OSError(f"no network in this test: {address[0]}")
        return real_connect(address, *args, **kwargs)

    def local_names_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None and str(name) not in _LOCAL:
            raise socket.gaierror(socket.EAI_NONAME, f"no network in this test: {name}")
        return real_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", local_only)
    monkeypatch.setattr(socket, "getaddrinfo", local_names_only)
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    reset_model_registry()
    from kazma_ui.app import create_app

    try:
        with TestClient(create_app(), headers={"X-Kazma-Secret": WALK_SECRET},
                        raise_server_exceptions=False) as client:
            monkeypatch.setattr(scope, "_enforced", True)
            reem = people.create_person(f"reem-{secrets.token_hex(3)}", "a-long-enough-password",
                                        by=OWNER_ACCOUNT_ID)["id"]
            space = own_keys._personal_space(reem)
            vault.for_space(space).store(own_keys._secret_name(space, "deepseek"), f"{SENTINEL}-key")
            assert vault.for_space(space).retrieve(own_keys._secret_name(space, "deepseek")) == f"{SENTINEL}-key"
            leaks = []
            for path in _get_routes(client.app):
                if path in NOT_WALKED:
                    continue
                answer = client.get(path, headers={"X-Kazma-Space": OWNER_SPACE_ID})
                if SENTINEL in answer.text:
                    leaks.append(f"{path} ({answer.status_code})")
            assert not leaks, "a GET route answers a member's own key to the admin:\n  " + "\n  ".join(leaks)
    finally:
        store.delete(identity.REGISTRY_KEY)
        reset_model_registry()
        vault.close()
        vault_mod.reset_posture_cache()


# ── the ledger records the call that answered (the review of S2-04) ─────


def _rows(conn: sqlite3.Connection, account: str) -> list[tuple[str, int]]:
    return [(str(p), int(o)) for p, o in conn.execute(
        "SELECT provider, own_key FROM llm_calls WHERE asker = ? AND status = 'ok' ORDER BY id", (account,))]


def _counted(account: str) -> int:
    return int(llm_ledger.usage_of(account, datetime.now(UTC) - timedelta(hours=1))["tokens"])


def _only_her_key_for(world: World, provider: str) -> None:
    for other in REEMS:
        if other != provider:
            own_keys.remove_key(world.reem, other, world.registry)
    own_keys._forget(world.reem)


def _her_default(world: World, ref: str) -> None:
    people.set_models(world.reem, [f"{p}:{m}" for p, m in MODELS.items() if p != "ollama"], ref,
                      by=OWNER_ACCOUNT_ID)


def _turn(world: World, space: str, *, ok: bool = True) -> Any:
    """One real supervisor turn for Reem in *space*, its graph built over
    the install's DeepSeek client (the install's default); its result."""
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.turn import run_agent_turn
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.spaces import threads
    from kazma_core.tracing import KazmaTracer

    graph = build_supervisor_graph(
        llm=world.client("deepseek"), system_prompt="You are Kazma.", tool_definitions=[], tool_executor=None,
        cost_breaker=create_cost_breaker(), authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
    )
    thread_id = f"t-own-key-{uuid.uuid4().hex[:8]}"
    threads.record_thread(thread_id, space, world.reem)
    result = asyncio.run(run_agent_turn(graph=graph, thread_id=thread_id,
                                        state={"messages": [{"role": "user", "content": "what time is it?"}]},
                                        persist=False))
    if ok:
        assert not result.error, result.error
    return result


def test_a_turn_on_the_installs_key_is_recorded_as_the_installs(world, ledger) -> None:
    """Her own key is DeepSeek's (the install's default); she picks
    Anthropic, which goes out on the install's key: the row names Anthropic,
    not her own key, and counts against her limit. Today's row said
    ``deepseek, own_key=1`` and nothing was counted."""
    _only_her_key_for(world, "deepseek")
    _her_default(world, "anthropic:claude-test")
    _turn(world, world.reem_space)
    assert world.keys_sent() == [INSTALL["anthropic"]], world.sent
    rows = _rows(ledger, world.reem)
    assert rows and set(rows) == {("anthropic", 0)}, rows
    assert _counted(world.reem) > 0


def test_a_turn_on_her_own_key_is_not_counted_against_her_limit(world, ledger) -> None:
    """The reverse: her own key is Anthropic's, the turn runs on it. Today's
    row said ``deepseek, own_key=0`` and counted her own spend."""
    _only_her_key_for(world, "anthropic")
    _her_default(world, "anthropic:claude-test")
    _turn(world, TEAM)
    assert world.keys_sent() == [REEMS["anthropic"]], world.sent
    rows = _rows(ledger, world.reem)
    assert rows and set(rows) == {("anthropic", 1)}, rows
    assert _counted(world.reem) == 0


def test_a_provider_moved_to_this_network_is_the_installs_whatever_she_kept(world, ledger, monkeypatch) -> None:
    """Her DeepSeek key is still kept, but the row now names a server on
    this network, which takes no own key: the call goes on the install's
    key, and is counted. Today's ledger asked the vault alone and said it
    was hers."""
    monkeypatch.setitem(ROWS["deepseek"], "base_url", "http://localhost:8000/v1")
    _turn(world, world.reem_space)
    assert world.keys_sent() == [INSTALL["deepseek"]], world.sent
    assert set(_rows(ledger, world.reem)) == {("deepseek", 0)}
    assert _counted(world.reem) > 0


@pytest.mark.parametrize(("own", "expected"), [("anthropic", ("anthropic", 1)), ("deepseek", ("anthropic", 0))])
def test_resilient_chat_records_the_answer_it_was_given(world, ledger, own, expected) -> None:
    from kazma_core.agent.resilient_chat import resilient_chat

    _only_her_key_for(world, own)
    with bind_scope(world.reem_space, world.reem):
        asyncio.run(resilient_chat(world.client("anthropic"), messages=[{"role": "user", "content": "go"}],
                                   max_attempts=1))
    assert _rows(ledger, world.reem) == [expected]


# ── never through the install's egress proxy ────────────────────────────

GATEWAY = "litellm.gateway.invalid"
GATEWAY_KEY = "install-gateway-master-key"


@pytest.fixture
def egress(monkeypatch):
    """The install's calls go through a LiteLLM proxy with its own key."""
    monkeypatch.delenv("KAZMA_LITELLM", raising=False)
    monkeypatch.setenv("KAZMA_LITELLM_URL", f"https://{GATEWAY}/v1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", GATEWAY_KEY)


def test_her_key_never_goes_through_the_egress_proxy(world, egress) -> None:
    with bind_scope(TEAM, world.omar):
        _chat(world.client("deepseek"))
    with bind_scope(world.reem_space, world.reem):
        _chat(world.client("deepseek"))
    assert world.sent_to() == [(GATEWAY, GATEWAY_KEY), ("api.deepseek.invalid", REEMS["deepseek"])], world.sent


def test_the_check_of_her_key_goes_direct_with_her_key_alone(world, egress, ledger) -> None:
    from kazma_core.provider_check import check_model

    world.refuse.add("reem-wrong-key")
    entry = {**ROWS["deepseek"], "api_key": "reem-wrong-key"}
    refused = asyncio.run(check_model(entry, MODELS["deepseek"], own_key_of=(world.reem_space, world.reem)))
    assert refused["ok"] is False and refused["error_code"] == "key_refused", refused
    good = asyncio.run(check_model({**entry, "api_key": "reem-good-key"}, MODELS["deepseek"],
                                   own_key_of=(world.reem_space, world.reem)))
    assert good["ok"] is True, good
    assert world.sent_to() == [("api.deepseek.invalid", "reem-wrong-key"),
                               ("api.deepseek.invalid", "reem-good-key")], world.sent
    calls = ledger.execute("SELECT provider, status, own_key, asker, space FROM llm_calls ORDER BY id").fetchall()
    assert calls == [("deepseek", "error", 0, world.reem, world.reem_space),
                     ("deepseek", "ok", 1, world.reem, world.reem_space)], calls


@pytest.fixture
def real_check_app(world, monkeypatch):
    """The routes with the providers' real check."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.own_keys_api import create_own_keys_router

    built = FastAPI()
    built.middleware("http")(auth.create_tenant_middleware())
    built.include_router(create_own_keys_router())
    with TestClient(built) as client:
        yield client


def test_behind_the_egress_proxy_a_key_that_does_not_work_is_not_kept(world, egress, as_reem,
                                                                     real_check_app) -> None:
    world.refuse.add("reem-wrong-key")
    refused = real_check_app.put("/api/account/model-keys/deepseek", json={"key": "reem-wrong-key"},
                                 headers=as_reem)
    assert refused.status_code == 400 and refused.json()["code"] == "key_refused", refused.text
    own_keys._forget(world.reem)
    assert own_keys._read_key(world.reem, "deepseek") == REEMS["deepseek"], "the refused key was kept"
    assert world.sent_to() == [("api.deepseek.invalid", "reem-wrong-key")], world.sent


def test_the_key_route_is_rate_limited(world, app, as_reem, monkeypatch) -> None:
    from kazma_ui import rate_limit

    monkeypatch.setenv("KAZMA_SECRET", "own-key-rate-limit-secret")
    monkeypatch.setattr(rate_limit, "_windows", type(rate_limit._windows)())
    answers = [app.put("/api/account/model-keys/deepseek", json={"key": f"reem-key-{n}"}, headers=as_reem)
               for n in range(7)]
    assert [a.status_code for a in answers] == [200] * 6 + [429]


# ── every answered row says whether it was on the person's own key ──────

_WRITERS = ("arecord_llm_call", "record_llm_call", "_record")


def _rows_without_own_key(sources: dict[str, str]) -> list[str]:
    """``file:line`` of each ledger write of an answered call (``usage=``)
    that does not say whether it went out on the person's own key."""
    missing: list[str] = []
    for rel, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            words = {k.arg for k in node.keywords}
            if name in _WRITERS and "usage" in words and "own_key" not in words:
                missing.append(f"{rel}:{node.lineno}")
    return sorted(missing)


def _product_sources() -> dict[str, str]:
    found = {}
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway"):
        for path in (REPO / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if any(w + "(" in text for w in _WRITERS):
                found[str(path.relative_to(REPO))] = text
    return found


def test_every_answered_row_says_whether_it_was_on_the_persons_own_key() -> None:
    sources = _product_sources()
    assert any("graph_supervisor.py" in r for r in sources) and any("resilient_chat.py" in r for r in sources)
    assert _rows_without_own_key(sources) == []


def test_control_a_row_that_does_not_say_is_found() -> None:
    planted = {"planted.py": "async def f(r):\n    await arecord_llm_call(model='m', usage=r.usage)\n"}
    assert _rows_without_own_key(planted) == ["planted.py:2"]


# ── past her daily limit, a turn on her own key still runs ──────────────


def _past_her_limit(world: World, monkeypatch) -> None:
    """Her limit is 100 tokens a day, and the install's key has spent 200
    for her today."""
    from kazma_core.spaces import daily_limits

    people.set_daily_limit(world.reem, {"tokens": 100, "usd": 0}, by=OWNER_ACCOUNT_ID)
    monkeypatch.setattr(daily_limits, "_ledger_on", lambda: True)
    llm_ledger.record_llm_call(provider="ollama", model="llama3", prompt_tokens=200,
                               asker=world.reem, space=world.reem_space)
    assert daily_limits.verdict(world.reem)["over"]


def test_past_her_limit_a_turn_on_her_own_key_runs_on_it(world, ledger, monkeypatch) -> None:
    """Past her limit, the turn's model is Anthropic, which she holds a key
    for: the turn starts, its call goes with her key alone, is marked hers
    and is not counted, and the turn keeps to her own keys."""
    _only_her_key_for(world, "anthropic")
    _her_default(world, "anthropic:claude-test")
    _past_her_limit(world, monkeypatch)
    result = _turn(world, world.reem_space)
    assert world.keys_sent() == [REEMS["anthropic"]], world.sent
    assert result.text.strip() == "done", result.text
    assert result.state.get("own_keys_only") is True
    assert set(_rows(ledger, world.reem)) == {("ollama", 0), ("anthropic", 1)}
    assert _counted(world.reem) == 200


def test_control_past_her_limit_a_turn_on_the_installs_key_is_refused(world, ledger, monkeypatch) -> None:
    """Her own key is Anthropic's, but the turn's model is DeepSeek, on the
    install's key: refused before any call, as at the limit."""
    _only_her_key_for(world, "anthropic")
    _her_default(world, "deepseek:deepseek-chat")
    _past_her_limit(world, monkeypatch)
    result = _turn(world, world.reem_space)
    assert world.sent == []
    assert "daily limit" in result.text, result.text


def test_control_todays_refusal_refuses_her_own_key_turn_too(world, ledger, monkeypatch) -> None:
    """Today's rule (no admission on her own key): the same turn on
    Anthropic is refused. What lets it in is ``on_own_key``."""
    from kazma_core.spaces import daily_limits

    _only_her_key_for(world, "anthropic")
    _her_default(world, "anthropic:claude-test")
    _past_her_limit(world, monkeypatch)
    monkeypatch.setattr(daily_limits, "on_own_key", lambda provider: False)
    result = _turn(world, world.reem_space)
    assert world.sent == []
    assert "daily limit" in result.text, result.text


def _failover_to_deepseek(monkeypatch) -> None:
    """One attempt, then the failover chain's DeepSeek (the install's key)."""
    from dataclasses import replace

    import kazma_core.agent.graph_supervisor as graph_supervisor
    import kazma_core.agent.nonstop as nonstop
    import kazma_core.retry as retry
    from kazma_core.agent.nonstop import FailoverConfig

    real = nonstop.get_nonstop_config
    monkeypatch.setattr(nonstop, "get_nonstop_config", lambda *a, **k: replace(
        real(*a, **k), failover=FailoverConfig(enabled=True, chain=["deepseek-chat"], cooldown_seconds=0.0)))
    monkeypatch.setattr(retry, "load_retry_config",
                        lambda: {"max_attempts": 1, "min_wait": 0.0, "max_wait": 0.0})
    monkeypatch.setattr(graph_supervisor, "_failover_clients", {})
    monkeypatch.setattr(graph_supervisor, "_failover_cooldowns", {})


def test_past_her_limit_a_failover_never_goes_on_the_installs_key(world, ledger, monkeypatch) -> None:
    """Her Anthropic call fails (503) and the failover chain names DeepSeek,
    on the install's key: within a turn let in on her own key it is not
    sent, and the turn fails as hers."""
    _only_her_key_for(world, "anthropic")
    _her_default(world, "anthropic:claude-test")
    _failover_to_deepseek(monkeypatch)
    world.down.add(REEMS["anthropic"])
    _past_her_limit(world, monkeypatch)
    _turn(world, world.reem_space, ok=False)
    assert world.keys_sent() == [REEMS["anthropic"]], world.sent


def test_control_under_her_limit_the_failover_goes_on_the_installs_key(world, ledger, monkeypatch) -> None:
    _only_her_key_for(world, "anthropic")
    _her_default(world, "anthropic:claude-test")
    _failover_to_deepseek(monkeypatch)
    world.down.add(REEMS["anthropic"])
    result = _turn(world, world.reem_space, ok=False)
    assert world.keys_sent() == [REEMS["anthropic"], INSTALL["deepseek"]], world.sent
    assert result.text.strip() == "done", result.text


def _synthesis_state(world: World, **extra: Any) -> dict[str, Any]:
    """A turn's state that the respond node must finish with a synthesis
    call, on the install's DeepSeek (the turn's last model)."""
    return {"messages": [{"role": "user", "content": "hi"}], "force_synthesis": True,
            "last_model": "deepseek-chat", "thread_id": f"t-synth-{uuid.uuid4().hex[:8]}",
            "iteration": 3, "max_iterations": 15, "tenant_id": world.reem_space, **extra}


@pytest.mark.parametrize(("held", "sent"), [(True, []), (False, [INSTALL["deepseek"]])])
def test_the_respond_node_keeps_to_her_own_keys(world, held: bool, sent: list[str]) -> None:
    """The respond node runs in a context of its own: it reads the turn's
    rule from the state. Held, its synthesis on the install's DeepSeek is
    not sent; not held (the control), it is."""
    from kazma_core.agent.graph_respond import respond_node

    _only_her_key_for(world, "anthropic")
    with bind_scope(world.reem_space, world.reem):
        asyncio.run(respond_node(_synthesis_state(world, own_keys_only=held), llm=world.client("deepseek")))
    assert world.keys_sent() == sent, world.sent


def test_held_to_her_own_keys_the_installs_key_is_refused_chat_and_stream(world) -> None:
    from kazma_core.retry import friendly_llm_error
    from kazma_core.spaces import daily_limits

    _only_her_key_for(world, "anthropic")

    async def calls() -> None:
        daily_limits.hold_to_own_keys({"own_keys_only": True})
        with pytest.raises(LLMError) as refused:
            await world.client("deepseek").chat([{"role": "user", "content": "hi"}], max_tokens=10)
        assert refused.value.kind == "daily_limit" and not refused.value.transient
        assert "daily limit" in friendly_llm_error(refused.value)
        with pytest.raises(LLMError) as streamed:
            async for _ in world.client("deepseek").chat_stream([{"role": "user", "content": "hi"}]):
                pass
        assert streamed.value.kind == "daily_limit"
        await world.client("anthropic").chat([{"role": "user", "content": "hi"}], max_tokens=10)

    with bind_scope(world.reem_space, world.reem):
        asyncio.run(calls())
    assert world.keys_sent() == [REEMS["anthropic"]], world.sent
    assert daily_limits.own_keys_only() is False  # the rule stayed in that run


def test_a_store_that_cannot_say_is_no(world, monkeypatch) -> None:
    from kazma_core.spaces import daily_limits

    def away(provider: str) -> Any:
        raise own_keys.OwnKeyUnreadable("away")

    monkeypatch.setattr(own_keys, "key_for_call", away)
    with bind_scope(world.reem_space, world.reem):
        assert daily_limits.on_own_key("anthropic") is False


# Every graph node reads the turn's rule: LangGraph runs each node in a copy
# of its caller's context, so a node that does not would use the install's
# key past the limit.

_HOLD = "hold_to_own_keys"


def _nodes_without_the_hold(builder: str, sources: dict[str, str]) -> list[str]:
    """Each ``*_node`` function a graph node of *builder* calls whose
    definition (in *sources*) does not call :data:`_HOLD`."""
    tree = ast.parse(builder)
    added = {getattr(c.args[1], "id", "") for c in ast.walk(tree) if isinstance(c, ast.Call)
             and getattr(c.func, "attr", "") == "add_node" and len(c.args) == 2}
    called: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef) and fn.name in added:
            called |= {getattr(c.func, "id", "") for c in ast.walk(fn) if isinstance(c, ast.Call)
                       and getattr(c.func, "id", "").endswith("_node")}
    missing = []
    for name in sorted(called):
        defs = [fn for text in sources.values() for fn in ast.walk(ast.parse(text))
                if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef) and fn.name == name]
        holds = any(getattr(c.func, "id", "") == _HOLD for fn in defs for c in ast.walk(fn)
                    if isinstance(c, ast.Call))
        if not defs or not holds:
            missing.append(name)
    return missing


def _agent_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8")
            for p in (REPO / "kazma-core" / "kazma_core" / "agent").glob("graph_*.py")}


def test_every_graph_node_holds_the_turn_to_her_own_keys() -> None:
    sources = _agent_sources()
    assert _nodes_without_the_hold(sources["graph_builder.py"], sources) == []
    builder = ast.parse(sources["graph_builder.py"])
    assert sum(getattr(c.func, "attr", "") == "add_node" for c in ast.walk(builder)
               if isinstance(c, ast.Call)) == 3


def test_control_a_node_that_does_not_hold_is_found() -> None:
    builder = ("def build(graph):\n"
               "    async def _a(state):\n        return await a_node(state)\n"
               "    async def _b(state):\n        return await b_node(state)\n"
               "    graph.add_node('a', _a)\n    graph.add_node('b', _b)\n")
    nodes = {"n.py": ("async def a_node(state):\n    hold_to_own_keys(state)\n"
                      "async def b_node(state):\n    return state\n")}
    assert _nodes_without_the_hold(builder, nodes) == ["b_node"]
