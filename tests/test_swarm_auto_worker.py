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


# ── the autoscaler: a catch-all template, and edits kept out of the checkout ──


def _scaler(tmp_path, templates: list[dict]):
    import json
    from unittest.mock import MagicMock

    from kazma_core.swarm.autoscaler import AutoScaler

    path = tmp_path / "templates.json"
    path.write_text(json.dumps(templates), encoding="utf-8")
    scaler = AutoScaler(MagicMock(), templates_path=path)
    scaler.load_templates()
    spawned: list[str] = []

    def fake_instantiate(template):
        spawned.append(template.name)
        return type("W", (), {"name": template.name + "-1"})()

    scaler._instantiate = fake_instantiate  # the engine is not under test here
    return scaler, spawned


_SHIPPED = [
    {"name": "coder", "capabilities": {"expertise": ["python", "debug"]}},
    {"name": "generalist", "capabilities": {"expertise": ["explain", "help"]}, "catch_all": True},
]


def test_a_prompt_that_matches_no_template_goes_to_the_catch_all(tmp_path):
    """Live 2026-09-28: "In one sentence: what is idempotency in HTTP APIs?"
    matched no template's words and the Auto task failed."""
    scaler, spawned = _scaler(tmp_path, _SHIPPED)
    assert scaler.maybe_scale("In one sentence: what is idempotency in HTTP APIs?") is not None
    assert spawned == ["generalist"]


def test_a_matching_template_still_wins(tmp_path):
    scaler, spawned = _scaler(tmp_path, _SHIPPED)
    scaler.maybe_scale("debug this python function")
    assert spawned == ["coder"]


def test_without_a_catch_all_nothing_is_spawned(tmp_path):
    """Negative control: the templates as they shipped until 2026-09-28."""
    old = [dict(t, catch_all=False) for t in _SHIPPED]
    scaler, spawned = _scaler(tmp_path, old)
    assert scaler.maybe_scale("In one sentence: what is idempotency in HTTP APIs?") is None
    assert spawned == []


def test_the_shipped_generalist_takes_any_task():
    import json

    from kazma_core.swarm.autoscaler import _default_templates_path

    shipped = json.loads(_default_templates_path().read_text(encoding="utf-8"))
    assert [t["name"] for t in shipped if t.get("catch_all") is True] == ["generalist"]


def test_the_flag_survives_a_save_and_a_reload(tmp_path):
    from kazma_core.swarm.autoscaler import WorkerTemplate

    scaler, _ = _scaler(tmp_path, _SHIPPED)
    scaler.register_template(WorkerTemplate.from_dict({"name": "any", "catch_all": True}))
    scaler.save_templates()
    scaler.load_templates()
    assert scaler._templates["any"].catch_all is True
    assert WorkerTemplate.from_dict({"name": "x", "catch_all": "yes"}).catch_all is False


def test_edits_go_to_the_data_folder_not_the_checkout(tmp_path, monkeypatch):
    """The shipped file is tracked; an edit written into it left a modified
    tree the next update's pull refuses."""
    import json
    from unittest.mock import MagicMock

    from kazma_core.swarm import autoscaler as mod

    shipped = tmp_path / "install" / "swarm_templates.json"
    shipped.parent.mkdir()
    shipped.write_text(json.dumps(_SHIPPED), encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setattr(mod, "_default_templates_path", lambda: shipped)
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: data)

    scaler = mod.AutoScaler(MagicMock())
    scaler.load_templates()
    assert set(scaler._templates) == {"coder", "generalist"}
    scaler.register_template(mod.WorkerTemplate.from_dict({"name": "mine"}))
    scaler.save_templates()
    assert json.loads(shipped.read_text(encoding="utf-8")) == _SHIPPED  # untouched
    saved = json.loads((data / "swarm_templates.json").read_text(encoding="utf-8"))
    assert {t["name"] for t in saved} == {"coder", "generalist", "mine"}

    # A new process reads the operator's copy.
    again = mod.AutoScaler(MagicMock())
    again.load_templates()
    assert "mine" in again._templates
