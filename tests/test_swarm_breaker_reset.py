"""The Swarm page can reset a worker's circuit breaker, and names every
breaker state in the page's language (2026-09-28).

The reset route existed and nothing called it: an open breaker could only be
waited out. The badge's label looked its key up from the state's value, and
"half-open" has a hyphen where the key has an underscore, so a half-open
breaker showed the raw key. The page's behaviour is tested under node
(tests/js/test_swarm_breaker.js); this file holds the server-side half.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_every_breaker_state_has_its_words() -> None:
    from kazma_core.swarm.reliability import CircuitState
    from kazma_ui.i18n import TRANSLATIONS

    for state in CircuitState:
        key = "swarm.cb_" + state.value.replace("-", "_")
        assert key in TRANSLATIONS, key
        assert TRANSLATIONS[key]["en"] and TRANSLATIONS[key]["ar"], key


def test_the_card_looks_the_state_up_by_its_key_and_offers_reset() -> None:
    html = (REPO / "kazma-ui/kazma_ui/templates/swarm.html").read_text(encoding="utf-8")
    card = html[html.index('id="worker-card-{{ w.name }}"'):]
    card = card[: card.index('data-action="start"')]
    assert "|replace('-', '_')" in card
    assert 'data-action="reset-breaker"' in card and 'data-cb-reset="{{ w.name }}"' in card
    js = (REPO / "kazma-ui/kazma_ui/static/js/swarm.js").read_text(encoding="utf-8")
    assert "btn.dataset.action === 'reset-breaker') resetBreaker(workerName)" in js


@pytest.mark.parametrize("known", [True, False])
def test_the_reset_route(monkeypatch: pytest.MonkeyPatch, known: bool) -> None:
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.swarm.reliability import CircuitBreaker
    from kazma_ui.swarm_panel import routes_workers

    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state.value == "open"

    class Engine:
        def get_worker(self, name):
            return object() if known else None

        def reset_circuit_breaker(self, name):
            breaker.reset()
            return breaker

    class Service:
        def _get_engine(self):
            return Engine()

        def has_swarm_core(self):
            return True

    monkeypatch.setattr(routes_workers, "get_swarm_service", lambda: Service())
    router = APIRouter()
    routes_workers.register_workers_routes(router, templates=None)
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post("/api/swarm/workers/coder/circuit-breaker/reset")
    if known:
        assert r.status_code == 200 and r.json()["circuit_breaker"]["state"] == "closed"
    else:
        assert r.status_code == 404
