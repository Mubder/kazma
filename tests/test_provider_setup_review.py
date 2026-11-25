"""Whose key goes where: the review of the provider setup (2026-11-26).

Each test fails on 470f10299: a key of the environment replaced in silence
(M1), a taken name written over and its extra keys sent to the new address
(M2), a thinking model failing its check (M5), a key changed while the rows
are tidied lost with the vault on (M6), X refusing a merged provider (M7), and
the small ones (L5, L6, L8, L12, L14).
"""

from __future__ import annotations

import os

import pytest
from kazma_core.provider_setup import SetupRefused, add_key, rows_at, tidy, tidy_report

KEY_A, KEY_B, KEY_C = "demo-key-aaaaaaaa", "demo-key-bbbbbbbb", "demo-key-cccccccc"
ENV_KEY = "demo-key-of-the-environment"
QWEN = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_core.model_registry import ModelRegistry

    for name in list(os.environ):
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name, raising=False)
    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    set_config_store(store)
    yield ModelRegistry(store)
    store.close()


@pytest.fixture
def vault_env(tmp_path, monkeypatch):
    from kazma_core.security import vault as vault_mod

    monkeypatch.setenv("KAZMA_VAULT_KEY", "unit-test-vault-key-32bytes-long!!")
    vault_mod.reset_vault()
    vault = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "_vault", vault)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    yield vault
    vault_mod.reset_vault()


@pytest.fixture
def api(registry, monkeypatch):
    """The providers' own routes over the same registry."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import providers

    monkeypatch.setattr("kazma_core.model_registry._registry", registry)
    app = FastAPI()
    app.include_router(providers.create_providers_router(registry.config_store))
    return TestClient(app)


def _add(registry, name, url, key="", **more):
    registry.upsert_provider({"name": name, "base_url": url, "api_key": key, "enabled": True, **more})


def _sent(registry, name) -> str:
    return registry.resolve_provider_credentials(name)[1]


# ── M1: a key from the environment ──────────────────────────────────────


@pytest.mark.parametrize("variable", ["MYGATE_API_KEY", "KAZMA_API_KEY"])
def test_a_row_on_an_environment_key_takes_no_key_in_silence(registry, monkeypatch, variable):
    _add(registry, "mygate", "https://gate.example/v1")
    monkeypatch.setenv(variable, ENV_KEY)
    assert _sent(registry, "mygate") == ENV_KEY
    with pytest.raises(SetupRefused) as refused:
        add_key(registry, "mygate", KEY_B)
    assert refused.value.code == "env_key"
    assert _sent(registry, "mygate") == ENV_KEY
    [row] = rows_at(registry, {"name": "x", "base_url": "https://gate.example/v1"})
    assert row["env_key"] is True and ENV_KEY not in str(row)


@pytest.mark.parametrize("active", ["mygate", "DS2"])
def test_a_tidy_leaves_a_row_on_an_environment_key(registry, monkeypatch, active):
    """Merged into, its key would be replaced; merged away, its chats would
    send the other row's key."""
    _add(registry, "mygate", "https://gate.example/v1", models=["m"])
    _add(registry, "DS2", "https://gate.example/v1", KEY_B, models=["m"])
    monkeypatch.setenv("MYGATE_API_KEY", ENV_KEY)
    registry.set_active_provider(active, model="m")
    [group] = tidy_report(registry)["merges"]
    assert group["merge"] == [] and [k["why"] for k in group["kept"]] == ["env_key"]
    with pytest.raises(SetupRefused):
        tidy(registry, merge=["DS2" if active == "mygate" else "mygate"])
    assert (_sent(registry, "mygate"), _sent(registry, "DS2")) == (ENV_KEY, KEY_B)


def test_the_route_says_so_by_code(api, registry, monkeypatch):
    _add(registry, "mygate", "https://gate.example/v1")
    monkeypatch.setenv("MYGATE_API_KEY", ENV_KEY)
    answer = api.post("/api/providers/mygate/keys", json={"api_key": KEY_B})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "env_key"


# ── M2: a name that is taken ────────────────────────────────────────────


def test_a_setup_never_writes_over_a_provider(api, registry):
    _add(registry, "gateway", "https://one.example/v1", KEY_A, api_keys=[{"id": "x", "label": "", "api_key": KEY_B}])
    body = {"name": "Gateway", "base_url": "https://two.example/v1", "api_key": KEY_C, "create": True}
    answer = api.post("/api/providers", json=body)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "name_taken"
    assert registry.get_provider("gateway")["base_url"] == "https://one.example/v1"
    # A catalog row nobody set up is filled in, a merged name is taken.
    assert api.post("/api/providers", json={"name": "openai", "base_url": "https://api.openai.com/v1",
                                            "api_key": KEY_C, "create": True}).status_code == 200
    _add(registry, "kept", "https://three.example/v1", KEY_A, aliases=["old name"])
    assert api.post("/api/providers", json={**body, "name": "Old Name"}).status_code == 409


def test_an_extra_key_is_never_sent_to_another_address(api, registry):
    """The old Add dialog posted ``api_keys: []``; the setup posts none."""
    from kazma_core.provider_keys import keys_of

    _add(registry, "gateway", "https://one.example/v1", KEY_A, api_keys=[{"id": "x", "label": "", "api_key": KEY_B}],
         aws_session_token="demo-token", secret_headers={"X-Org": "demo-header"})
    moved = api.post("/api/providers", json={"name": "gateway", "base_url": "https://two.example/v1", "api_key": KEY_C})
    assert moved.status_code == 200
    row = registry.get_provider("gateway")
    assert [k for _l, k in keys_of(row["api_key"], row)] == [KEY_C]
    assert not row.get("aws_session_token") and not row.get("secret_headers")
    # The same address: what is not posted stays (the control).
    _add(registry, "same", "https://one.example/v1", KEY_A, api_keys=[{"id": "x", "label": "", "api_key": KEY_B}])
    api.post("/api/providers", json={"name": "same", "base_url": "https://one.example/v1/", "api_key": KEY_C})
    row = registry.get_provider("same")
    assert [k for _l, k in keys_of(row["api_key"], row)] == [KEY_C, KEY_B]


@pytest.mark.parametrize("posted", ["", "****aaaa"])
def test_a_key_not_typed_again_stays_with_its_address(api, registry, posted):
    """Edit's empty key means "keep the stored one": with another address it
    was kept, and sent there."""
    _add(registry, "gateway", "https://one.example/v1", KEY_A)
    moved = api.post("/api/providers", json={"name": "gateway", "base_url": "https://two.example/v1", "api_key": posted})
    assert moved.status_code == 400 and "again" in moved.json()["detail"]
    assert registry.get_provider("gateway")["base_url"] == "https://one.example/v1"
    stay = api.post("/api/providers", json={"name": "gateway", "base_url": "https://one.example/v1/", "api_key": posted})
    assert stay.status_code == 200 and registry.get_provider("gateway")["api_key"] == KEY_A


def test_every_writer_of_a_row_drops_them(registry):
    """Not the route alone: any caller that moves a row and names no keys."""
    _add(registry, "gateway", "https://one.example/v1", KEY_A, api_keys=[{"id": "x", "label": "", "api_key": KEY_B}])
    registry.upsert_provider({"name": "gateway", "base_url": "https://two.example/v1", "api_key": KEY_C})
    assert not registry.get_provider("gateway").get("api_keys")


# ── M5: a model that was still thinking ─────────────────────────────────


async def test_a_model_still_thinking_at_the_checks_limit_works(monkeypatch):
    from kazma_core import provider_check
    from kazma_core.llm_provider import LLMResponse

    class _Client:
        config = None
        answer = LLMResponse(content="", reasoning_chars=640, finish_reason="length")

        async def chat(self, messages, **kw):
            return self.answer

    monkeypatch.setattr("kazma_core.provider_adapters._resolve", lambda style: lambda config, **kw: _Client())
    entry = {"name": "local", "base_url": "http://127.0.0.1:9/v1", "api_key": ""}
    found = await provider_check.check_model(entry, "r1")
    assert found["ok"] is True and found["thinking"] is True and found["answer"] == ""
    _Client.answer = LLMResponse(content="", reasoning_chars=0)  # the control: no answer at all
    assert (await provider_check.check_model(entry, "r1"))["error_code"] == "empty"


# ── M6: a key changed meanwhile, with the vault on and off ──────────────


def _two_qwens(registry):
    _add(registry, "Qwen 1", QWEN, KEY_A, models=["qwen-plus"])
    _add(registry, "QwenCloud", QWEN + "/", KEY_B, models=["qwen-max"])
    registry.set_active_provider("Qwen 1", model="qwen-plus")
    # Written once more, as any later write leaves them: every field there.
    registry.set_provider_health("QwenCloud", "healthy")


@pytest.mark.parametrize("vault", [False, True])
@pytest.mark.parametrize("changed", ["QwenCloud", "Qwen 1"])
def test_a_key_changed_while_rows_are_tidied_is_not_lost(registry, request, monkeypatch, vault, changed):
    if vault:
        request.getfixturevalue("vault_env")
    _two_qwens(registry)
    real = registry.update_provider_rows

    def late(updater, **kw):
        registry.upsert_provider({"name": changed, "api_key": KEY_C})
        return real(updater, **kw)

    monkeypatch.setattr(registry, "update_provider_rows", late)
    with pytest.raises(SetupRefused) as refused:
        tidy(registry, merge=["QwenCloud"])
    assert refused.value.code == "plan_changed"
    assert registry.get_provider(changed)["api_key"] == KEY_C
    assert {"Qwen 1", "QwenCloud"} <= {p["name"] for p in registry.list_providers()}
    # Looked at again, it goes through with the key as it is now.
    monkeypatch.setattr(registry, "update_provider_rows", real)
    tidy(registry, merge=["QwenCloud"])
    from kazma_core.provider_keys import keys_of

    row = registry.get_provider("Qwen 1")
    assert KEY_C in [k for _l, k in keys_of(row["api_key"], row)]


@pytest.mark.parametrize("vault", [False, True])
def test_a_key_added_meanwhile_is_not_lost(registry, request, monkeypatch, vault):
    """Two keys added to one row at once: both stay."""
    from kazma_core.provider_keys import keys_of

    if vault:
        request.getfixturevalue("vault_env")
    _two_qwens(registry)
    real = registry.update_provider_rows
    calls = []

    def late(updater, **kw):
        if not calls:
            calls.append(1)
            add_key(registry, "Qwen 1", KEY_C)
        return real(updater, **kw)

    monkeypatch.setattr(registry, "update_provider_rows", late)
    assert add_key(registry, "Qwen 1", KEY_B)["keys"] == 3
    row = registry.get_provider("Qwen 1")
    assert {k for _l, k in keys_of(row["api_key"], row)} == {KEY_A, KEY_B, KEY_C}


# ── M7: what still works after a merge ──────────────────────────────────


def test_x_finds_a_merged_provider_by_its_old_name(registry, monkeypatch):
    from kazma_core.x_api import model_selection

    _two_qwens(registry)
    tidy(registry, merge=["QwenCloud"])
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: registry)
    chosen = {"selection": "explicit", "provider": "QwenCloud", "model": "qwen-max"}
    model_selection.validate_provider(chosen)
    with pytest.raises(model_selection.XModelUnavailableError):  # never a fuzzy match
        model_selection.validate_provider({**chosen, "provider": "Qwen"})
    with pytest.raises(model_selection.XModelUnavailableError):  # found, and not local
        model_selection.validate_provider({**chosen, "local_only": True})


def test_the_report_says_which_rows_are_off_and_hold_a_key(registry):
    _two_qwens(registry)
    registry.toggle_provider("QwenCloud", False)
    [group] = tidy_report(registry)["merges"]
    assert [(m["name"], m["enabled"], m["keys"]) for m in group["merge"]] == [("QwenCloud", False, 1)]


# ── The small ones ──────────────────────────────────────────────────────


def test_a_key_is_added_only_where_it_was_offered(api, registry):  # L5
    _add(registry, "gate", "https://two.example/v1", KEY_A)
    answer = api.post("/api/providers/gate/keys", json={"api_key": KEY_B, "base_url": "https://one.example/v1"})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "row_moved"
    assert not registry.get_provider("gate").get("api_keys")
    assert api.post("/api/providers/gate/keys", json={"api_key": KEY_B, "base_url": "https://TWO.example/v1/"}).status_code == 200


@pytest.mark.parametrize(("one", "two", "same"), [
    ("https://a.example/TeamA/v1", "https://a.example/teama/v1", False),
    ("https://a.example/v1?team=A", "https://a.example/v1?team=a", False),
    ("https://u:Pw@a.example/v1", "https://u:pW@a.example/v1", False),
    ("https://A.example/v1/", "HTTPS://a.example/v1", True),
])
def test_two_accounts_of_one_server_are_not_one_row(registry, one, two, same):  # L6
    _add(registry, "first", one, KEY_A)
    assert bool(rows_at(registry, {"name": "x", "base_url": two})) is same


def test_a_tidy_cut_short_leaves_every_price_in_force(registry, monkeypatch):  # L8
    _two_qwens(registry)
    registry.config_store.set("models.prices", {"qwencloud:qwen-max": {"input": 1.0, "output": 2.0}}, category="models")
    real = registry.update_provider_rows

    def then_dies(updater, **kw):
        real(updater, **kw)
        raise RuntimeError("the process stopped here")

    monkeypatch.setattr(registry, "update_provider_rows", then_dies)
    with pytest.raises(RuntimeError):
        tidy(registry, merge=["QwenCloud"])
    assert "qwen 1:qwen-max" in registry.config_store.get("models.prices")


def test_usage_counts_written_as_text_are_billed():  # L12
    from kazma_core.llm_provider import cost_from_usage

    bill = {"input_cost_per_1m": 1.0, "output_cost_per_1m": 1.0}
    assert cost_from_usage({"prompt_tokens": "100", "completion_tokens": "20"}, **bill) == pytest.approx(120 / 1e6)
    assert cost_from_usage({"prompt_tokens": None, "input_tokens": 100, "output_tokens": 20}, **bill) == pytest.approx(120 / 1e6)
    assert cost_from_usage({"prompt_tokens": "100", "completion_tokens": 0,
                            "prompt_tokens_details": {"cached_tokens": "100"}}, **bill) == pytest.approx(10 / 1e6)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://a.example/v1", "http://a\x00b.example/v1",
                                 "http://a.example/\nv1", "gopher://a.example", "javascript://a.example"])
def test_only_a_web_address_is_looked_at(api, url):  # L14
    answer = api.post("/api/providers/setup/look", json={"preset": "custom", "endpoint": "custom", "base_url": url})
    assert answer.json()["ok"] is False and answer.json()["problems"][0]["field"] == "base_url"


@pytest.mark.parametrize(("path", "body"), [
    ("/api/providers/gate/keys", {"api_key": "demo-" + "k" * 9000}),
    ("/api/providers/setup/models", {"api_key": "demo-" + "k" * 9000}),
    ("/api/providers", {"name": "x", "api_key": {"nested": "demo-secret-value"}}),
])
def test_a_refused_body_is_not_answered_back(api, path, body):  # L14
    answer = api.post(path, json=body)
    assert answer.status_code == 422
    assert "demo-" not in answer.text and "kkkk" not in answer.text
    assert answer.json()["detail"][0]["loc"]  # where, still said
