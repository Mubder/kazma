"""The guided provider setup (kazma_core.provider_setup, provider_endpoints):
every address and field is checked before anything is sent or saved, one
service is one row, and rows that repeat each other are tidied without losing
a key or a name that is used."""

from __future__ import annotations

import re

import pytest
from kazma_core.provider_endpoints import ENDPOINTS, FIELDS, FORM_FIELDS, GROUPS, KEY_SHAPES, endpoint_fields
from kazma_core.provider_setup import (
    SetupRefused,
    add_key,
    endpoint_address,
    field_problems,
    _identity_of,
    rows_at,
    tidy,
    tidy_report,
)
from kazma_core.providers import PROVIDER_PRESETS, catalog

KEY_A, KEY_B, KEY_C = "demo-key-aaaaaaaa", "demo-key-bbbbbbbb", "demo-key-cccccccc"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    import os

    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_core.model_registry import ModelRegistry

    for name in list(os.environ):
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name, raising=False)
    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    set_config_store(store)
    yield ModelRegistry(store)
    store.close()


def _add(registry, name, url, key="", **more):
    registry.upsert_provider({"name": name, "base_url": url, "api_key": key, "enabled": True, **more})


# ── The data ────────────────────────────────────────────────────────────


def test_every_provider_has_a_group_and_the_setup_reads_it_from_the_catalog():
    rows = {r["id"]: r for r in catalog()}
    assert set(rows) == set(PROVIDER_PRESETS)
    assert {r["group"] for r in rows.values()} == set(GROUPS)
    assert rows["openrouter"]["group"] == "host" and rows["ollama"]["group"] == "local"
    assert rows["azure"]["group"] == "platform" and rows["custom"]["group"] == "other"


def test_a_presets_own_address_is_its_first_endpoint():
    for pid, endpoints in ENDPOINTS.items():
        own = PROVIDER_PRESETS[pid]["base_url"]
        if own and "ACCOUNT_ID" not in own:
            assert endpoints[0][1] == own, pid
        assert len({eid for eid, _u in endpoints}) == len(endpoints)
        for _eid, url in endpoints:
            assert url.startswith("https://") and set(endpoint_fields(url)) <= set(FIELDS)


def test_every_form_and_field_is_known():
    forms = {str(m["form"]) for m in PROVIDER_PRESETS.values()}
    assert set(FORM_FIELDS) == forms
    for field, rule in FIELDS.items():
        example = rule["example"].replace(".", "A") if field == "aws_access_key_id" else rule["example"]
        assert re.fullmatch(rule["pattern"], example), field
    assert {p for _pattern, ids in KEY_SHAPES for p in ids} <= set(PROVIDER_PRESETS)


# ── Addresses and fields ────────────────────────────────────────────────


def test_an_address_is_filled_and_a_value_cannot_name_another_host():
    assert endpoint_address("azure", "azure_public", {"resource": "my-res"}) == "https://my-res.openai.azure.com"
    got = endpoint_address("dashscope", "workspace", {"workspace_id": "llm-1a2b", "workspace_region": "ap-southeast-1"})
    assert got == "https://llm-1a2b.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    for value in ("evil.example/", "a@b", "x.y", "a#", "a b", "", "../x", "a?b"):
        with pytest.raises(SetupRefused):
            endpoint_address("azure", "azure_public", {"resource": value})
    with pytest.raises(SetupRefused):
        endpoint_address("azure", "no_such", {})


@pytest.mark.parametrize(("entry", "problems"), [
    ({"name": "bedrock", "region": "us-east-1"}, []),
    ({"name": "bedrock", "region": ""}, [("region", "missing")]),
    ({"name": "aws", "preset": "bedrock", "region": "Virginia"}, [("region", "malformed")]),
    ({"name": "google", "google_mode": "ai_studio"}, []),
    ({"name": "google", "google_mode": "vertex_ai", "project_id": "", "location": ""},
     [("project_id", "missing"), ("location", "missing")]),
    ({"name": "google", "google_mode": "vertex_ai", "project_id": "my-project-1", "location": "me-central2"}, []),
    ({"name": "google", "google_mode": "vertex_ai", "project_id": "My Project", "location": "x"},
     [("project_id", "malformed"), ("location", "malformed")]),
    ({"name": "azure", "base_url": "https://r.openai.azure.com", "api_version": ""}, []),
    ({"name": "azure", "base_url": "https://r.openai.azure.com", "api_version": "yesterday"},
     [("api_version", "malformed")]),
    ({"name": "custom", "base_url": "https://{resource}.example.com/v1"}, [("base_url", "malformed")]),
    ({"name": "custom", "base_url": "not an address"}, [("base_url", "malformed")]),
])
def test_each_types_own_fields_are_checked(entry, problems):
    assert [(p["field"], p["code"]) for p in field_problems(entry)] == problems


def test_a_value_a_row_already_has_is_not_judged():
    stored = {"name": "azure", "base_url": "https://r.openai.azure.com", "api_version": "odd"}
    assert field_problems(dict(stored), stored) == []
    assert field_problems({**stored, "api_version": "odder"}, stored)


def test_a_check_is_refused_before_anything_is_sent(registry):
    from kazma_core.provider_check import CandidateRefused, candidate_entry

    with pytest.raises(CandidateRefused) as refused:
        candidate_entry(registry, "aws", typed={"preset": "bedrock", "region": "nowhere!",
                                                "aws_access_key_id": "A" * 20, "aws_secret_access_key": "s"})
    assert refused.value.code == "field_malformed"


# ── One row per provider and address ────────────────────────────────────


def test_rows_at_an_address_whatever_its_spelling(registry):
    _add(registry, "Qwen 1", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/", KEY_A)
    typed = {"name": "dashscope", "base_url": "https://DASHSCOPE-intl.aliyuncs.com/compatible-mode/v1"}
    same = rows_at(registry, typed)
    assert [(r["name"], r["has_key"], r["keys"]) for r in same] == [("Qwen 1", True, 1)]
    assert "demo-key" not in str(same)
    # Another region is another provider; a catalog row nobody set up is none.
    assert rows_at(registry, {**typed, "base_url": "https://dashscope-us.aliyuncs.com/compatible-mode/v1"}) == []
    assert rows_at(registry, {"name": "openai", "base_url": "https://api.openai.com/v1"}) == []


def test_what_makes_two_rows_one_provider():
    azure = {"name": "a", "preset": "azure", "base_url": "https://r.openai.azure.com", "api_version": "2024-10-21"}
    assert _identity_of(azure) != _identity_of({**azure, "api_version": "2025-01-01"})
    bedrock = {"name": "b", "preset": "bedrock", "region": "us-east-1"}
    assert _identity_of(bedrock) == _identity_of({**bedrock, "name": "c"})
    assert _identity_of(bedrock) != _identity_of({**bedrock, "region": "eu-west-1"})
    anthropic = {"name": "anthropic", "base_url": "https://api.anthropic.com/v1"}
    assert _identity_of(anthropic) != _identity_of({"name": "x", "base_url": "https://api.anthropic.com/v1"})
    assert _identity_of({"name": "x"}) is None


def test_a_key_is_added_once_and_the_runtime_tries_it(registry):
    from kazma_core.provider_keys import keys_of

    _add(registry, "deepseek", "https://api.deepseek.com/v1", KEY_A)
    assert add_key(registry, "deepseek", KEY_B, "second account") == {"added": True, "keys": 2}
    assert add_key(registry, "deepseek", KEY_B) == {"added": False, "keys": 2}
    row = registry.get_provider("deepseek")
    assert keys_of(row["api_key"], row) == [("", KEY_A), ("second account", KEY_B)]
    # Never plain in the settings store where a vault is there; the other rows stay.
    assert registry.get_provider("openai") is not None
    for bad in ("", "****", "sk-****1234"):
        with pytest.raises(SetupRefused):
            add_key(registry, "deepseek", bad)
    with pytest.raises(SetupRefused) as refused:
        add_key(registry, "nobody", KEY_C)
    assert refused.value.code == "unknown_provider"


def test_a_row_with_no_key_takes_it_as_its_own_and_is_switched_on(registry):
    registry.upsert_provider({"name": "Qwen", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                              "enabled": False})
    add_key(registry, "Qwen", KEY_A)
    row = registry.get_provider("Qwen")
    assert (row["api_key"], row["enabled"], row.get("api_keys")) == (KEY_A, True, None)


# ── Tidying ─────────────────────────────────────────────────────────────

QWEN = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def _three_qwens(registry):
    _add(registry, "Qwen 1", QWEN, KEY_A, models=["qwen-plus"])
    _add(registry, "QwenCloud", QWEN + "/", KEY_B, models=["qwen-max"])
    registry.upsert_provider({"name": "Qwen", "base_url": "https://old.example/v1", "enabled": False})
    registry.set_active_provider("Qwen 1", model="qwen-plus")


def test_duplicates_become_one_row_and_nothing_is_lost(registry):
    from kazma_core.provider_keys import keys_of

    _three_qwens(registry)
    registry.config_store.set("models.prices", {"qwencloud:qwen-max": {"input": 1.0, "output": 2.0}}, category="models")
    report = tidy_report(registry)
    [group] = report["merges"]
    assert (group["keep"], [m["name"] for m in group["merge"]]) == ("Qwen 1", ["QwenCloud"])
    assert [u["name"] for u in report["unused"]] == ["Qwen"]
    assert "demo-key" not in str(report)

    done = tidy(registry, merge=["QwenCloud"], remove=["Qwen"])
    assert done == {"merged": 1, "removed": 1, "kept": ["Qwen 1"]}
    names = {p["name"] for p in registry.list_providers()}
    assert "Qwen 1" in names and not names & {"QwenCloud", "Qwen"}
    row = registry.get_provider("Qwen 1")
    assert [k for _label, k in keys_of(row["api_key"], row)] == [KEY_A, KEY_B]
    assert row["models"] == ["qwen-max", "qwen-plus"]
    # A chat, a backup or a profile that names the merged row still finds it.
    assert registry.get_provider("QwenCloud")["name"] == "Qwen 1"
    assert registry.split_model_ref("QwenCloud:qwen-max") == ("Qwen 1", "qwen-max")
    assert registry.find_provider_for_model("qwencloud:qwen-max")["name"] == "Qwen 1"
    assert registry.config_store.get("models.prices") == {"qwen 1:qwen-max": {"input": 1.0, "output": 2.0}}
    assert tidy_report(registry)["merges"] == []


def test_a_key_that_cannot_be_read_is_never_lost(registry, vault_env):
    """The old way of merging by hand (delete the second row) loses its key;
    a row whose stored key does not open is not merged."""
    _three_qwens(registry)
    assert vault_env.delete("cfg:providers.list.QwenCloud.api_key", every_scope=True)
    registry.config_store._cache.clear()
    [group] = tidy_report(registry)["merges"]
    assert [(k["name"], k["why"]) for k in group["kept"]] == [("QwenCloud", "key_undecryptable")]
    with pytest.raises(SetupRefused):
        tidy(registry, merge=["QwenCloud"])


def test_a_list_that_changed_meanwhile_changes_nothing(registry, monkeypatch):
    _three_qwens(registry)
    before = registry.list_providers()
    real = registry.update_provider_rows

    def late(updater, **kw):
        registry.upsert_provider({"name": "QwenCloud", "api_key": KEY_C})
        return real(updater, **kw)

    monkeypatch.setattr(registry, "update_provider_rows", late)
    with pytest.raises(SetupRefused) as refused:
        tidy(registry, merge=["QwenCloud"], remove=["Qwen"])
    assert refused.value.code == "plan_changed"
    assert {p["name"] for p in registry.list_providers()} == {p["name"] for p in before}
    assert registry.get_provider("QwenCloud")["api_key"] == KEY_C


def test_only_what_the_report_offers_is_tidied(registry):
    _three_qwens(registry)
    for merge, remove in ((["Qwen 1"], []), ([], ["Qwen 1"]), ([], ["openai"]), ([], [])):
        with pytest.raises(SetupRefused):
            tidy(registry, merge=merge, remove=remove)


def test_a_row_a_person_is_given_is_kept(registry, monkeypatch):
    from kazma_core import provider_setup

    _three_qwens(registry)
    monkeypatch.setattr(provider_setup, "_person_lists", lambda: {"qwencloud"})
    [group] = tidy_report(registry)["merges"]
    assert [(k["name"], k["why"]) for k in group["kept"]] == [("QwenCloud", "person_list")]


def test_a_member_may_hold_a_key_under_the_rows_name(registry, monkeypatch):
    from kazma_core import provider_setup

    _three_qwens(registry)
    monkeypatch.setattr(provider_setup, "_enforced", lambda: True)
    [group] = tidy_report(registry)["merges"]
    assert [k["why"] for k in group["kept"]] == ["members_may_hold_keys"]


def test_rows_with_their_own_headers_are_not_merged(registry):
    _add(registry, "Gate A", "https://gate.example/v1", KEY_A, secret_headers={"X-Org": "one"})
    _add(registry, "Gate B", "https://gate.example/v1", KEY_B, secret_headers={"X-Org": "two"})
    [group] = tidy_report(registry)["merges"]
    assert [k["why"] for k in group["kept"]] == ["own_headers"]


def test_a_catalog_row_merged_away_stays_away(registry):
    _add(registry, "Z.AI", "https://api.z.ai/api/paas/v4", KEY_A)
    [group] = tidy_report(registry)["merges"]
    assert (group["keep"], [m["name"] for m in group["merge"]]) == ("Z.AI", ["zai"])
    tidy(registry, merge=["zai"])
    assert "zai" not in {p["name"] for p in registry.list_providers()}
    row = registry.get_provider("zai")
    assert row["name"] == "Z.AI" and row["preset"] == "zai"


# ── With the vault on, and on Postgres ──────────────────────────────────


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


def _merged(registry):
    """Two rows merged, a key added: every key read back by a fresh registry,
    and the rows as they are stored."""
    from kazma_core.model_registry import ModelRegistry
    from kazma_core.model_registry_store import load_providers_unresolved
    from kazma_core.provider_keys import keys_of

    _three_qwens(registry)
    add_key(registry, "QwenCloud", KEY_C, "third")
    tidy(registry, merge=["QwenCloud"], remove=["Qwen"])
    row = registry.get_provider("Qwen 1")
    assert [k for _l, k in keys_of(row["api_key"], row)] == [KEY_A, KEY_B, KEY_C]
    # A row added later under the merged row's name takes nothing back.
    _add(registry, "QwenCloud", "https://another.example/v1", "demo-key-dddddddd")
    again = ModelRegistry(registry.config_store)
    row = again.get_provider("Qwen 1")
    assert [k for _l, k in keys_of(row["api_key"], row)] == [KEY_A, KEY_B, KEY_C]
    assert again.get_provider("QwenCloud")["base_url"] == "https://another.example/v1"
    return [r for r in load_providers_unresolved(registry.config_store) if r.get("name") == "Qwen 1"][0]


def test_moved_keys_are_in_the_vault(registry, vault_env):
    stored = _merged(registry)
    held = [stored["api_key"], *(k["api_key"] for k in stored["api_keys"])]
    assert len(held) == 3 and all(str(v).startswith("vault://") for v in held)
    assert len(set(held)) == 3 and "demo-key" not in str(stored)


@pytest.mark.postgres
def test_on_postgres_too(registry):
    from kazma_core.db.backend import is_postgres

    if not is_postgres():
        pytest.skip("a Postgres run")
    from kazma_core.model_registry_store import REMOVED_PRESETS_KEY

    for key in ("providers.list", REMOVED_PRESETS_KEY, "models.prices"):
        registry.config_store.delete(key)
    _merged(registry)
