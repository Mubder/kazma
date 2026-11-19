"""The first-run setup guide's routes (kazma_ui/setup_api.py) and the
install overview the Packages page reads (kazma_core.install_components).

The owner asked, after a clean Windows install (2026-11-07), why there was no
guide in the web UI: the first visit showed one card with a provider list, a
key and a model name to type -- nothing for a model on this computer, no
model list, no test, no next steps. The guide's routes: the status (presets
with where a key is got, the model in use by name), the local model servers,
saving a provider without switching to it, the switch (which keeps a stored
key and an address set in Settings), and a check that sends the chosen model
one message. Each rule with the old behaviour as its control.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from kazma_ui import setup_api as sa


class FakeRegistry:
    def __init__(self, providers=(), active=None):
        self.providers = {p["name"]: dict(p) for p in providers}
        self.upserts: list[dict] = []
        self.active = active or {}

    def get_provider(self, name):
        found = self.providers.get(name)
        return dict(found) if found else None

    def upsert_provider(self, data):
        self.upserts.append(dict(data))
        row = self.providers.setdefault(data["name"], {"name": data["name"]})
        row.update({k: v for k, v in data.items() if k != "name"})
        return dict(row)

    def list_providers(self):
        return [dict(p) for p in self.providers.values()]

    def get_active_profile(self):
        return dict(self.active)


def test_a_fresh_install_is_not_ready_because_it_lists_local_servers():
    """Every install lists Ollama and LM Studio with no key. Counted as set up,
    they made every fresh install "ready" and hid the way to the guide."""
    listed = FakeRegistry(
        [{"name": "deepseek", "api_key": ""}, {"name": "ollama", "api_key": ""},
         {"name": "lm-studio", "api_key": ""}],
        active={"provider": "deepseek", "model": "deepseek-chat", "api_key": ""},
    )
    assert sa.compute_setup_status(listed)["ready"] is False
    # Chosen, a local server is set up; another provider's key counts too.
    listed.active = {"provider": "ollama", "model": "llama3.2", "api_key": ""}
    assert sa.compute_setup_status(listed)["ready"] is True
    keyed = FakeRegistry([{"name": "openai", "api_key": "vault://x"}],
                         active={"provider": "deepseek", "model": "deepseek-chat", "api_key": ""})
    assert sa.compute_setup_status(keyed)["ready"] is True


def test_the_status_names_presets_and_the_model_in_use_never_a_key():
    reg = FakeRegistry(active={"provider": "deepseek", "model": "deepseek-chat", "api_key": "sk-secret-value"})
    status = sa.compute_setup_status(reg)
    assert status["ready"] is True
    assert status["active_provider"] == "deepseek" and status["active_model"] == "deepseek-chat"
    presets = {p["id"]: p for p in status["presets"]}
    assert presets["ollama"]["local"] is True and presets["ollama"]["key_url"] == ""
    assert presets["deepseek"]["local"] is False and presets["deepseek"]["key_url"].startswith("https://")
    # A key is all the guide asks: a fresh install's placeholder ("custom"),
    # Azure and Bedrock need more, and are not offered (the guide chose
    # "custom" while its list showed OpenAI).
    assert presets["deepseek"]["guided"] is True and presets["ollama"]["guided"] is False
    assert not any(presets[pid]["guided"] for pid in ("custom", "azure", "bedrock"))
    assert "sk-secret-value" not in json.dumps(status)
    assert "api_key" not in status and all("api_key" not in p for p in status["presets"])


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def test_local_servers_are_found_without_saving_anything():
    def get(url):
        if ":11434/" in url:
            return _Resp(200, {"data": [{"id": "llama3.2"}, {"id": "qwen3"}]})
        raise httpx.ConnectError("refused")

    servers = {s["id"]: s for s in sa._find_local_servers(get)}
    assert servers["ollama"]["online"] is True and servers["ollama"]["models"] == 2
    assert servers["lm-studio"]["online"] is False and servers["lm-studio"]["error"] == "ConnectError"
    assert servers["lm-studio"]["download"].startswith("https://")


def test_saving_a_provider_keeps_what_settings_set():
    body = sa._ProviderBody
    # Not a provider Kazma knows; a cloud provider with no key and none stored.
    assert sa._save_provider(body(provider="nope"), FakeRegistry())["error"] == "unknown_provider"
    reg = FakeRegistry()
    assert sa._save_provider(body(provider="deepseek"), reg)["error"] == "api_key_required"
    assert reg.upserts == []

    # A new key: saved with the preset's address.
    assert sa._save_provider(body(provider="deepseek", api_key="sk-new"), reg)["ok"] is True
    assert reg.upserts[-1]["api_key"] == "sk-new" and reg.upserts[-1]["base_url"].startswith("https://")

    # The guide run again: an empty key keeps the stored one, and an address
    # set in Settings stays.
    stored = FakeRegistry([{"name": "deepseek", "api_key": "vault://x", "base_url": "https://proxy.example/v1"}])
    assert sa._save_provider(body(provider="deepseek", api_key=""), stored)["ok"] is True
    assert "api_key" not in stored.upserts[-1] and "base_url" not in stored.upserts[-1]

    # A local server needs no key.
    local = FakeRegistry()
    assert sa._save_provider(body(provider="ollama"), local)["ok"] is True
    assert local.upserts[-1]["api_key"] == "local"


def _client(monkeypatch, registry):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import kazma_core.model_registry as mr
    import kazma_core.runtime.model_switch as ms

    switched: list[tuple] = []
    monkeypatch.setattr(mr, "get_model_registry", lambda: registry)
    monkeypatch.setattr(ms, "switch_active_provider", lambda pid, **kw: (
        switched.append((pid, kw.get("model"))), SimpleNamespace(ok=True, error=""))[1])
    app = FastAPI()
    app.include_router(sa.create_setup_router())
    return TestClient(app), switched


def test_the_switch_keeps_a_stored_key(monkeypatch):
    """The guide run again to change the model sends no key: the one stored
    is used. The old route refused it ("api_key_required")."""
    reg = FakeRegistry([{"name": "deepseek", "api_key": "vault://x"}])
    client, switched = _client(monkeypatch, reg)
    out = client.post("/api/setup/bootstrap", json={"provider": "deepseek", "api_key": "", "model": "deepseek-chat"})
    assert out.status_code == 200 and "error" not in out.json(), out.json()
    assert switched == [("deepseek", "deepseek-chat")]
    assert all("api_key" not in u for u in reg.upserts)

    # The negative control: no key stored, none given.
    bare, _ = _client(monkeypatch, FakeRegistry())
    refused = bare.post("/api/setup/bootstrap", json={"provider": "deepseek", "api_key": "", "model": "x"})
    assert refused.json()["error"] == "api_key_required"


class _Client:
    def __init__(self, behaviour="ok", reasoning=True):
        self.behaviour = behaviour
        self.reasoning = reasoning
        self.calls: list[dict] = []

    def can_turn_off_reasoning(self):
        return self.reasoning

    async def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        if self.behaviour == "slow":
            await asyncio.sleep(1.0)
        if self.behaviour == "refused":
            import httpx

            from kazma_core.llm_provider import LLMError

            request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
            raise LLMError("LLM call failed (HTTP 401): invalid key", transient=False) from httpx.HTTPStatusError(
                "401", request=request, response=httpx.Response(401, request=request))
        return SimpleNamespace(content="OK", tool_calls=[])

    async def close(self):
        return None


def test_the_check_uses_the_typed_key_and_saves_nothing(monkeypatch):
    """The guide checks the chosen model with the key as typed, through the
    client chat would build, before anything is saved (providers stage 1):
    it saved the key first, and a refused key stayed saved."""
    from kazma_core import provider_adapters
    from kazma_core import provider_check as pc

    built: list[tuple[str, str]] = []
    client = _Client()

    def build(name, config, entry=None):
        built.append((name, config.api_key))
        return client

    monkeypatch.setattr(provider_adapters, "build_client", build)
    reg = FakeRegistry()
    http, _ = _client(monkeypatch, reg)
    out = http.post("/api/setup/check", json={"provider": "deepseek", "api_key": "sk-typed", "model": "deepseek-chat"})
    assert out.json()["ok"] is True and isinstance(out.json()["ms"], int)
    assert built == [("deepseek", "sk-typed")]
    assert client.calls == [{"max_tokens": pc.CHECK_MAX_TOKENS, "model": "deepseek-chat", "reasoning": "off"}]
    assert reg.upserts == [], "the check saved something"

    client.behaviour = "refused"
    refused = http.post("/api/setup/check", json={"provider": "deepseek", "api_key": "sk-bad", "model": "m"}).json()
    assert refused["ok"] is False and refused["error"] == "key_refused" and reg.upserts == []

    monkeypatch.setattr(pc, "CHECK_TIMEOUT_S", 0.05)
    client.behaviour = "slow"
    monkeypatch.setattr(pc.check_model, "__kwdefaults__", {"timeout": 0.05})
    assert http.post("/api/setup/check", json={"provider": "deepseek", "api_key": "k", "model": "m"}).json()[
        "error"] == "timeout"

    # A cloud provider with no key, none stored: refused before any call.
    assert http.post("/api/setup/check", json={"provider": "deepseek", "model": "m"}).json() == {
        "ok": False, "error": "api_key_required"}


def test_the_models_are_listed_with_the_typed_key_and_nothing_saved(monkeypatch):
    from kazma_core.model_registry import DiscoveryResult

    asked: list[dict] = []
    reg = FakeRegistry()

    async def discover(name, candidate=None):
        asked.append(dict(candidate or {}))
        return DiscoveryResult(True, ["deepseek-chat", "whisper-1"])

    reg.discover = discover
    reg.resolve_provider_credentials = lambda name: ("", "")
    reg.stored_key_is_undecryptable = lambda name: False
    http, _ = _client(monkeypatch, reg)
    out = http.post("/api/setup/models", json={"provider": "deepseek", "api_key": "sk-typed"}).json()
    assert out == {"ok": True, "models": ["deepseek-chat"]}
    assert asked and asked[0]["api_key"] == "sk-typed" and reg.upserts == []


@pytest.mark.parametrize("path,method", [
    ("/setup", "GET"), ("/api/setup/local", "GET"), ("/api/setup/models", "POST"),
    ("/api/setup/bootstrap", "POST"), ("/api/setup/check", "POST"),
])
def test_the_guide_is_the_admins(path, method):
    """It saves a provider's key and switches the model for everyone, as
    Settings does. The status stays readable: the chat page asks it."""
    from kazma_core.security.platform_rbac import role_allows

    assert role_allows("admin", path, method)
    assert not role_allows("operator", path, method)
    assert role_allows("operator", "/api/setup/status", "GET")


# -- Settings -> Packages: the install's parts -----------------------------------------------


def test_the_install_overview_says_chosen_only_with_a_record():
    from kazma_core import install_components as ic

    none = ic.install_overview({})
    assert none["mode"] is None and all(p["chosen"] is None for p in none["parts"])

    record = {"install": {"mode": "express", "components": ic.express_ids(), "at": 1,
                          "results": {"meaning_search": {"ok": True}}}}
    express = ic.install_overview(record)
    parts = {p["id"]: p for p in express["parts"]}
    assert express["mode"] == "express" and express["chosen"] == len(ic.express_ids())
    assert parts["meaning_search"]["chosen"] is True and parts["meaning_search"]["ok"] is True
    assert parts["browser"]["chosen"] is False  # left out of Express: not chosen, never missing
    assert ic._parts_of_extra("rag") == ["meaning_search"] and ic._parts_of_extra("tui") == []


def test_the_packages_page_reads_chosen_and_the_install_command():
    """The badge says "Not chosen" for a part the install left out; the old
    badge said Missing (or Partial for Postgres, whose driver the database
    tools bring) whatever the install was."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    page = (root / "kazma-ui" / "kazma_ui" / "templates" / "settings.html").read_text(encoding="utf-8")
    route = (root / "kazma-ui" / "kazma_ui" / "routes_direct" / "system.py").read_text(encoding="utf-8")
    ops = (root / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_ops.js").read_text(encoding="utf-8")
    assert "extra.chosen === false" in page and "packages.not_chosen_badge" in page
    assert "pkgExtraBadge(extra) === 'not_chosen'" in page and "extra.chosen === false" in ops
    assert 'uv pip install -e ".[all]"' not in page and "kazma setup install --mode full" in page
    assert "annotate_extras(extras_list, install)" in route
    from kazma_core import install_components as ic

    extras = [{"name": "rag"}, {"name": "tui"}]
    ic.annotate_extras(extras, ic.install_overview({}))
    assert extras[0]["install_cmd"] == "kazma setup install --with meaning_search" and "chosen" not in extras[0]
    assert "part" not in extras[1]


# The owner's fresh Full install of 2026-11-19, as its record holds it: the
# Python pieces in place, Docker and the three parts that need it not.
_OWNERS_RECORD = {"install": {
    "mode": "full", "at": 1, "without": [],
    "components": ["meaning_search", "databases", "docker", "document_container", "private_search",
                   "graph_neo4j", "service"],
    "results": {"meaning_search": {"ok": True}, "databases": {"ok": True}, "docker": {"ok": False},
                "document_container": {"ok": False}, "private_search": {"ok": False},
                "graph_neo4j": {"ok": False}, "service": {"ok": True}}}}


def test_the_packages_page_names_the_parts_not_in_place():
    """Every row of Settings -> Packages was green over an install with no
    Docker, SearXNG, Neo4j or document worker: the page listed Python extras
    only, and Neo4j's driver was installed. The parts the last run could not
    put in place are named, and an extra whose part failed is not green."""
    from kazma_core import install_components as ic

    overview = ic.install_overview(_OWNERS_RECORD, "windows")
    assert overview["not_in_place"] == ["docker", "document_container", "private_search", "graph_neo4j"]
    extras = [{"name": "neo4j", "installed": True}, {"name": "rag", "installed": True},
              {"name": "sandbox", "installed": True}]
    ic.annotate_extras(extras, overview)
    neo4j, rag, sandbox = extras
    assert neo4j["part_missing"] == "Neo4j memory graph" and neo4j["chosen"] is True
    assert rag["part_missing"] == "" and rag["chosen"] is True
    assert sandbox["chosen"] is False and sandbox["part_missing"] == ""
    # A record that says nothing of a part (an older one) is not a failure.
    quiet = {"install": {**_OWNERS_RECORD["install"], "results": {}}}
    assert ic.install_overview(quiet, "windows")["not_in_place"] == []


def test_control_the_old_annotation_left_a_failed_part_green():
    """Negative control: the route's annotation before 2026-11-19 (part,
    command, chosen) gives the Neo4j row nothing that is not green."""
    from kazma_core import install_components as ic

    overview = ic.install_overview(_OWNERS_RECORD, "windows")
    chosen_parts = {p["id"] for p in overview["parts"] if p["chosen"]}
    extra = {"name": "neo4j", "installed": True}
    owners = ic._parts_of_extra(extra["name"])
    extra["part"], extra["chosen"] = owners[0], any(o in chosen_parts for o in owners)
    assert extra["installed"] and extra["chosen"] and not extra.get("part_missing")


def test_switching_provider_ends_the_fallback_notice_of_the_one_left(monkeypatch):
    """A fresh install's placeholder provider has no key; saving a provider in
    the guide let Kazma fall back to it, and the "custom has no usable API key"
    notice stayed on every page after the switch: it closed only when custom
    got a key, which a provider moved away from never does."""
    from kazma_core.observability import model_fallback as mf

    mf.reset_fallback_state()
    mf.report_substitution(provider="custom", model="gpt-4o-mini", used_provider="ollama", used_model="m")
    mf.report_substitution(provider="ollama", model="m", used_provider="deepseek", used_model="d")
    assert {"substitution:custom", "substitution:ollama"} <= set(mf._active)

    # The negative control: a key saved for another provider closes nothing.
    mf.report_provider_usable("deepseek")
    assert "substitution:custom" in mf._active

    mf.report_provider_chosen("ollama")
    assert "substitution:custom" not in mf._active
    assert "substitution:ollama" in mf._active  # the provider in use keeps its own
    mf.reset_fallback_state()


def test_a_network_server_in_use_is_ready_by_its_address():
    """The provider in use is a server on the owner's network under a name of
    its own (one the guide found by address): ready. The name rule alone kept
    the setup wall over it (found by the stage 2-4 browser test)."""
    from kazma_ui.setup_api import compute_setup_status

    class _Reg:
        def get_active_profile(self):
            return {"provider": "server-192-168-1-20-8000", "model": "qwen", "api_key": "",
                    "base_url": "http://192.168.1.20:8000/v1"}

        def list_providers(self):
            return []

    assert compute_setup_status(_Reg())["ready"] is True

    class _Cloud(_Reg):
        def get_active_profile(self):
            return {"provider": "cerebras", "model": "m", "api_key": "", "base_url": "https://api.cerebras.ai/v1"}

    assert compute_setup_status(_Cloud())["ready"] is False  # control: a cloud address needs a key
