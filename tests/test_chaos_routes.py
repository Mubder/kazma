"""The chaos routes: absent unless enabled, and a custom injection is checked.

``POST /api/chaos/injections/custom`` passed its ``params`` straight into
``FailureInjection(**params)``, so an unknown key was a TypeError and the
caller got a 500 instead of being told what was wrong (found while writing
docs/docs/ops/chaos-testing.md, 2026-09-28). The routes had no test at all.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core import chaos
from kazma_ui.routes_chaos import register_chaos_routes

BASE = {
    "failure_type": "latency",
    "target": "llm_provider",
    "probability": 0.5,
    "duration_seconds": 30,
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("KAZMA_CHAOS_ENABLED", "1")
    monkeypatch.setattr(chaos, "_injector", chaos.FailureInjector())
    app = FastAPI()
    register_chaos_routes(app)
    with TestClient(app) as c:
        yield c


def _active(client) -> list[dict]:
    return client.get("/api/chaos/injections").json()["injections"]


def test_the_routes_are_absent_unless_enabled(monkeypatch):
    monkeypatch.delenv("KAZMA_CHAOS_ENABLED", raising=False)
    app = FastAPI()
    register_chaos_routes(app)
    with TestClient(app) as c:
        assert c.get("/api/chaos/experiments").status_code == 404
        assert c.post("/api/chaos/injections/custom", json=BASE).status_code == 404


def test_a_custom_injection_is_created_listed_and_stopped(client):
    r = client.post(
        "/api/chaos/injections/custom",
        json={**BASE, "params": {"latency_ms": "250", "severity": "low"}},
    )
    assert r.status_code == 200, r.text
    [active] = _active(client)
    assert active["injection_id"] == r.json()["injection_id"]
    assert active["severity"] == "low"
    assert client.delete("/api/chaos/injections").status_code == 200
    assert _active(client) == []


@pytest.mark.parametrize(
    ("params", "says"),
    [
        ({"latency": 250}, "Unknown params: latency"),
        ({"injection_id": "mine"}, "Unknown params: injection_id"),
        (["latency_ms"], "params must be an object"),
        ({"latency_ms": "slow"}, "Invalid value"),
        ({"error_code": None}, "Invalid value"),
    ],
)
def test_bad_params_are_refused_with_a_400(client, params, says):
    r = client.post("/api/chaos/injections/custom", json={**BASE, "params": params})
    assert r.status_code == 400, r.text
    assert says in r.json()["detail"]
    assert _active(client) == []


def test_an_unknown_key_breaks_the_injection_itself():
    """Negative control: what the route used to hand over raises -- the 500."""
    with pytest.raises(TypeError):
        chaos.FailureInjection(
            failure_type=chaos.FailureType.LATENCY,
            target=chaos.InjectionTarget.LLM_PROVIDER,
            latency=250,
        )
