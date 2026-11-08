"""No switched-on cloud provider without a key, and two rows at one address
said (providers stage 1, 2026-11-08).

On the live install "Z.AI" was switched on with no key (every message to it
failed, and Kazma could fall back to it), beside a "zai" row at the same
address, and two Cohere rows. A save left a keyless provider switched on --
new providers are switched on by default -- and the toggle switched one on
whatever it held. Now a cloud provider with no usable key is saved switched
off, and switching one on is refused (409 ``no_key``); a row at the address
of another is reported, not refused (a second account of a vendor is saved
that way until providers have several keys).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class _Registry:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {r["name"]: dict(r) for r in rows}
        self.toggles: list[tuple[str, bool]] = []

    def get_provider(self, name):
        row = self.rows.get(name)
        return dict(row) if row else None

    def list_providers(self):
        return [dict(r) for r in self.rows.values()]

    def resolve_provider_credentials(self, name):
        row = self.rows.get(name) or {}
        return row.get("base_url", ""), row.get("api_key", "")

    def upsert_provider(self, data):
        row = self.rows.setdefault(data["name"], {"name": data["name"], "enabled": True})
        row.update({k: v for k, v in data.items() if v is not None})
        return dict(row)

    def toggle_provider(self, name, enabled):
        self.toggles.append((name, enabled))
        self.rows[name]["enabled"] = enabled

    def get_discovered_models(self, name):
        return []

    def get_selected_models(self, name):
        return []

    def get_visible_models(self, name):
        return []


ROWS = [
    {"name": "zai", "base_url": "https://api.z.ai/api/paas/v4", "api_key": "", "enabled": False},
    {"name": "deepseek", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-deepseek-real", "enabled": True},
]


def _http(monkeypatch, registry: _Registry):
    import kazma_ui.providers as ui
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(ui, "get_model_registry", lambda: registry)
    app = FastAPI()
    app.include_router(ui.create_providers_router(SimpleNamespace(get=lambda *a, **k: None)))
    return TestClient(app)


def test_a_cloud_provider_saved_without_a_key_is_left_off_and_its_twin_named(monkeypatch):
    registry = _Registry(ROWS)
    http = _http(monkeypatch, registry)
    out = http.post("/api/providers", json={"name": "Z.AI", "base_url": "https://api.z.ai/api/paas/v4",
                                            "api_key": "", "enabled": True}).json()
    assert out["enabled"] is False and out["switched_off"] == "no_key"
    assert registry.rows["Z.AI"]["enabled"] is False
    assert out["same_address_as"] == ["zai"]


def test_a_server_on_this_network_is_saved_on_with_no_key(monkeypatch):
    registry = _Registry(ROWS)
    http = _http(monkeypatch, registry)
    out = http.post("/api/providers", json={"name": "gpu", "base_url": "http://192.168.1.20:8000/v1",
                                            "api_key": "", "enabled": True}).json()
    assert out["enabled"] is True and "switched_off" not in out


def test_switching_on_a_cloud_provider_with_no_key_is_refused(monkeypatch):
    registry = _Registry(ROWS)
    http = _http(monkeypatch, registry)
    refused = http.post("/api/providers/zai/toggle", json={"enabled": True})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "no_key"
    assert registry.toggles == []
    # Off is always allowed; a provider with a key switches on.
    assert http.post("/api/providers/zai/toggle", json={"enabled": False}).status_code == 200
    assert http.post("/api/providers/deepseek/toggle", json={"enabled": True}).status_code == 200


def test_the_list_names_rows_at_one_address(monkeypatch):
    registry = _Registry([*ROWS, {"name": "Z.AI", "base_url": "https://api.z.ai/api/paas/v4/", "api_key": "",
                                  "enabled": True}])
    http = _http(monkeypatch, registry)
    twins = {p["name"]: p["same_address_as"] for p in http.get("/api/providers").json()}
    assert twins == {"zai": ["Z.AI"], "deepseek": [], "Z.AI": ["zai"]}


def test_control_without_the_rule_the_provider_stays_on(monkeypatch):
    """What a save did: a new provider is switched on by default."""
    registry = _Registry(ROWS)
    row = registry.upsert_provider({"name": "Z.AI", "base_url": "https://api.z.ai/api/paas/v4", "api_key": ""})
    assert row["enabled"] is True
