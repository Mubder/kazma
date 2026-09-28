"""The Swarm page can send a task to "auto", and says the swarm is ready when it is.

``workers=["auto"]`` is the engine's own route: the capability router picks
the best registered worker, or the autoscaler spawns one from a template
(``swarm/dispatch_inner.py``). On live 2026-09-28 it answered in 3 s through
the API -- but the page never offered it: with no registered worker both
pickers said "No workers registered", dispatch refused ("Select at least one
worker"), and the status card read "Stopped" over a swarm that worked.
"""

from __future__ import annotations

import re

import pytest

from kazma_ui.services import SwarmService
from tests.test_swarm_ui_panel import _add_workers, _build_client


def _auto_boxes(html: str) -> dict[str, bool]:
    """name -> checked, for every "auto" checkbox on the page."""
    boxes = {}
    for tag in re.findall(r"<input[^>]*value=\"auto\"[^>]*>", html):
        name = re.search(r'name="([\w-]+)"', tag).group(1)
        boxes[name] = " checked" in tag
    return boxes


@pytest.fixture
def three_templates(monkeypatch):
    monkeypatch.setattr(SwarmService, "template_count", lambda self: 3)


def test_the_status_counts_the_templates(three_templates):
    data = _build_client().get("/api/swarm/status").json()
    assert data["templates"] == 3


def test_with_nothing_registered_auto_is_offered_and_chosen(three_templates):
    html = _build_client().get("/swarm").text
    assert _auto_boxes(html) == {"selected_workers": True, "play_selected_workers": True}
    card = re.search(r'id="metric-swarm-status"[^>]*>([^<]*)<', html).group(1)
    assert card == "On demand"
    assert "Auto spawns one from a template" in html


def test_with_a_registered_worker_auto_is_offered_not_chosen(three_templates):
    """Negative control: a registered (not started) worker is a stopped swarm,
    and the operator's own worker is not overridden by a pre-ticked Auto."""
    client = _build_client()
    _add_workers(client, "alpha")
    html = client.get("/swarm").text
    assert _auto_boxes(html) == {"selected_workers": False, "play_selected_workers": False}
    card = re.search(r'id="metric-swarm-status"[^>]*>([^<]*)<', html).group(1)
    assert card == "Stopped"


def test_without_templates_nothing_is_ready(monkeypatch):
    monkeypatch.setattr(SwarmService, "template_count", lambda self: 0)
    html = _build_client().get("/swarm").text
    card = re.search(r'id="metric-swarm-status"[^>]*>([^<]*)<', html).group(1)
    assert card == "Stopped"


def test_an_autoscaler_that_failed_to_start_counts_no_templates(monkeypatch):
    """The engine turns a failed start into None (and alerts it)."""
    monkeypatch.setattr(SwarmService, "get_autoscaler", lambda self: None)
    assert SwarmService().template_count() == 0


def test_the_real_templates_are_counted():
    """The shipped swarm_templates.json (coder, researcher, generalist)."""
    client = _build_client()
    assert client.get("/api/swarm/status").json()["templates"] >= 3
