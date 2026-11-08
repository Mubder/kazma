"""A model server on the owner's own network is a server Kazma may use with
no key (providers stage 1, 2026-11-08).

"Local" was decided by text in the address (localhost, 127.0.0.1, :11434):
a keyless vLLM or llama.cpp on 192.168.1.20 was taken for a cloud provider
with no key -- swapped for another provider, refused by Test, hidden by the
chat's model picker -- and discovery refused every private address but
loopback, so it could not list its models. Now one rule says what is on the
owner's network: loopback, private ranges, Tailscale's shared space, a host
name with no dot or a network suffix; never link-local (169.254.169.254) or
a cloud metadata address. The setup guide finds such a server by address.
"""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.runtime.live_llm import url_is_local
from kazma_core.security import ssrf


@pytest.mark.parametrize("url", [
    "http://192.168.1.20:8000/v1", "http://10.1.2.3/v1", "http://172.16.0.9:8080",
    "http://gpu-box:8000/v1", "http://llm.lan/v1", "http://host.docker.internal:8000/v1",
    "http://100.101.102.103:8000/v1", "http://[fd00::1]:8000/v1",
])
def test_an_address_on_the_owners_network_needs_no_key(url):
    assert url_is_local(url)
    # The control: the old rule read text in the address.
    assert not any(tok in url for tok in ("localhost", "127.0.0.1", "0.0.0.0", "lmstudio", "lm-studio", ":11434"))


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data", "http://100.100.100.200/", "http://[fd00:ec2::254]/",
    "https://api.deepseek.com/v1", "https://8.8.8.8/v1", "",
])
def test_metadata_and_cloud_addresses_are_not_local(url):
    assert not url_is_local(url)


def test_discovery_asks_only_an_address_that_resolves_to_the_owners_network(monkeypatch):
    answers = {"gpu-box": ["192.168.1.20"], "rebind": ["192.168.1.20", "169.254.169.254"],
               "cloud": ["93.184.216.34"], "gone": []}
    monkeypatch.setattr(ssrf, "_resolve_host_ips", lambda host: answers.get(host, []))
    assert ssrf.resolves_to_own_network("http://gpu-box:8000/v1")
    assert ssrf.resolves_to_own_network("http://[::1]:8000")
    assert not ssrf.resolves_to_own_network("http://rebind:8000")  # one address elsewhere: no
    assert not ssrf.resolves_to_own_network("http://cloud/v1")
    assert not ssrf.resolves_to_own_network("http://gone/v1")
    assert not ssrf.own_network_ip(ipaddress.ip_address("169.254.169.254"))
    # The control: the SSRF check's allow_private skips every check, metadata too.
    assert ssrf.validate_url("http://169.254.169.254/", allow_private=True) == ()


class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return self._body


def test_the_guide_finds_a_server_by_address_and_tries_v1():
    from kazma_ui import setup_api as sa

    asked: list[str] = []

    def get(url: str) -> _Resp:
        asked.append(url)
        if url == "http://192.168.1.20:8000/v1/models":
            return _Resp(200, {"data": [{"id": "qwen2.5-32b"}, {"id": "llama-3.1-8b"}]})
        return _Resp(404, {})

    found = sa._network_server("192.168.1.20:8000", get=get, own_network=lambda url: True)
    assert found == {"ok": True, "base_url": "http://192.168.1.20:8000/v1", "label": "192.168.1.20:8000", "models": 2}
    assert asked == ["http://192.168.1.20:8000/models", "http://192.168.1.20:8000/v1/models"]
    assert sa._network_server("api.example.com", get=get, own_network=lambda url: False) == {
        "ok": False, "error": "not_own_network"}
    assert sa._network_server("http://user:pw@10.0.0.2", get=get, own_network=lambda url: True)["error"] == "bad_address"
    assert sa._network_server("10.0.0.3:9000", get=lambda url: _Resp(404, {}),
                              own_network=lambda url: True)["error"] == "no_server"


class _Registry:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = {r["name"]: dict(r) for r in rows or []}

    def get_provider(self, name: str) -> dict[str, Any] | None:
        row = self.rows.get(name)
        return dict(row) if row else None

    def upsert_provider(self, data: dict[str, Any]) -> dict[str, Any]:
        self.rows.setdefault(data["name"], {}).update(data)
        return dict(self.rows[data["name"]])


def test_a_found_server_is_saved_and_needs_no_key():
    from kazma_ui import setup_api as sa

    registry = _Registry()
    name = sa._register_network_server(
        {"base_url": "http://192.168.1.20:8000/v1", "label": "192.168.1.20:8000", "models": 2}, registry)
    assert name == "server-192-168-1-20-8000"
    assert registry.rows[name]["enabled"] is True and registry.rows[name]["base_url"].endswith("/v1")
    saved = sa._save_provider(sa._ProviderBody(provider=name), registry)
    assert saved == {"ok": True, "provider": name}
    # A stored cloud provider with no key is still refused; an unknown name too.
    cloud = _Registry([{"name": "acme", "base_url": "https://api.acme.example/v1", "api_key": ""}])
    assert sa._save_provider(sa._ProviderBody(provider="acme"), cloud)["error"] == "api_key_required"
    assert sa._save_provider(sa._ProviderBody(provider="nope"), _Registry())["error"] == "unknown_provider"


def test_finding_a_server_is_the_admins():
    from kazma_core.security.platform_rbac import role_allows

    assert role_allows("admin", "/api/setup/local/address", "POST")
    assert not role_allows("operator", "/api/setup/local/address", "POST")


def test_the_providers_list_says_which_are_local(monkeypatch):
    import kazma_ui.providers as ui_providers
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    rows = [{"name": "server-gpu", "base_url": "http://192.168.1.20:8000/v1", "api_key": "", "enabled": True},
            {"name": "deepseek", "base_url": "https://api.deepseek.com/v1", "api_key": "", "enabled": True}]
    fake = SimpleNamespace(
        list_providers=lambda: rows, get_discovered_models=lambda n: [], get_selected_models=lambda n: [],
        get_visible_models=lambda n: [],
    )
    monkeypatch.setattr(ui_providers, "get_model_registry", lambda: fake)
    app = FastAPI()
    app.include_router(ui_providers.create_providers_router(SimpleNamespace(get=lambda *a, **k: None)))
    out = {p["name"]: p["local"] for p in TestClient(app).get("/api/providers").json()}
    assert out == {"server-gpu": True, "deepseek": False}
