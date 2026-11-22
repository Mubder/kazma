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
* **The key is not shown to the admin**: the admin's own work cannot read
  it from the vault, and no GET route of the built app answers it to the
  admin. The routes answer Reem whether one is kept, never the key.
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
    """The install's three providers, as the model registry answers them."""

    def get_provider(self, name: str) -> dict[str, Any] | None:
        row = ROWS.get(str(name))
        return dict(row) if row else None

    def list_providers(self) -> list[dict[str, Any]]:
        return [dict(r) for r in ROWS.values()]


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
        self.registry = _Registry()

    def client(self, provider: str) -> LLMProvider:
        """The install's client for *provider*, as the registry builds it:
        the install's key, and its spare key to switch to."""
        config = LLMConfig(base_url=ROWS[provider]["base_url"], api_key=INSTALL[provider],
                           model=MODELS[provider], key_switch=lambda failed, exc: SPARE)
        return build_client(provider, config, ROWS[provider])

    def keys_sent(self) -> list[str]:
        return [s["key"] for s in self.sent]


def _transport(world: World) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
        world.sent.append({"path": request.url.path, "key": key})
        if key in world.refuse:
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})
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
    llm_ledger.record_llm_call(provider=provider, model=MODELS[provider], prompt_tokens=tokens,
                               cost_usd=1.0, asker=world.reem, space=world.reem_space)


def test_her_own_key_spend_is_not_counted_against_her_limit(world, ledger) -> None:
    from datetime import UTC, datetime, timedelta

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
    from datetime import UTC, datetime, timedelta

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
    assert kept.status_code == 200 and kept.json() == {"ok": True, "provider": "deepseek", "own": True}
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
