"""Kazma's container engine is chosen, pinned, and never switched by accident.

2026-11-12: a Windows update restarted the PC and nobody signed in; Docker
Desktop starts only at a sign-in, so every container stayed down for six
hours. Kazma's own engine (Docker Engine in the ``kazma-engine`` WSL distro,
started at boot by the ``KazmaEngine`` task) comes back with nobody signed
in. Setup installs it when container parts are chosen; Docker Desktop stays
the owner's choice (``containers.engine: desktop``).

The rules held here (``docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md``):

* No choice pins nothing. An install is never switched to an engine it has
  not moved to: the first version pinned Kazma's engine whenever its context
  existed, and on the owner's PC -- context made, containers not yet moved --
  the next reload would have looked for SearXNG and Neo4j on an empty engine
  (an independent review caught it before it shipped). Negative control:
  that rule, reproduced here, pins.
* A choice is pinned in ``DOCKER_CONTEXT`` for the process and everything it
  starts; the operator's own ``DOCKER_CONTEXT`` or ``DOCKER_HOST`` wins.
* The first ``find_docker_cli`` of a Kazma process applies the stored
  choice, so the server, the CLI and scripts use one engine; the guard's copy
  of the module (loaded by path) never reads settings.
* A Windows folder is mounted as Kazma's engine sees it (``/g/...``) when that
  engine is in force -- decided from the context, never from a cached query.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from kazma_core import container_engine, docker_cli

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def contexts(tmp_path, monkeypatch):
    """A private docker config folder; ``make(name)`` creates a context."""
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "docker"))
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(docker_cli, "_applied", None)
    monkeypatch.setattr(docker_cli, "_choice_applied", False)

    def make(name: str) -> None:
        digest = hashlib.sha256(name.encode()).hexdigest()
        meta = tmp_path / "docker" / "contexts" / "meta" / digest / "meta.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"Name": name, "Endpoints": {}}), encoding="utf-8")

    return make


# ── which context ─────────────────────────────────────────────────────


def test_a_context_is_found_from_the_cli_files(contexts):
    assert not docker_cli._context_exists("kazma-engine")
    contexts("kazma-engine")
    assert docker_cli._context_exists("kazma-engine")
    assert not docker_cli._context_exists("desktop-linux")


def test_no_choice_pins_nothing_even_with_kazmas_engine_on_the_machine(contexts):
    contexts("kazma-engine")
    contexts("desktop-linux")
    assert docker_cli.normalize_engine(None) == ""
    assert docker_cli.normalize_engine("something-else") == ""
    assert docker_cli.apply_engine_choice("") is None
    assert "DOCKER_CONTEXT" not in os.environ
    # Negative control: the first rule ("kazma" whenever its context exists)
    # would have pinned the empty engine on the owner's PC.
    def first_rule(engine):
        if docker_cli.normalize_engine(engine) == "desktop":
            return "desktop-linux"
        return "kazma-engine" if docker_cli._context_exists("kazma-engine") else None

    assert first_rule("") == "kazma-engine"


def test_a_choice_is_pinned_and_follows_changes(contexts):
    contexts("kazma-engine")
    contexts("desktop-linux")
    assert docker_cli.apply_engine_choice("kazma") == "kazma-engine"
    assert os.environ["DOCKER_CONTEXT"] == "kazma-engine"
    assert docker_cli.apply_engine_choice("desktop") == "desktop-linux"
    assert os.environ["DOCKER_CONTEXT"] == "desktop-linux"
    assert docker_cli.apply_engine_choice("") is None
    assert "DOCKER_CONTEXT" not in os.environ


def test_a_chosen_engine_without_its_context_is_said_and_not_pinned(contexts, caplog, monkeypatch):
    caplog.set_level("WARNING", logger=docker_cli.logger.name)
    monkeypatch.setattr(docker_cli, "_reported", set())
    assert docker_cli.apply_engine_choice("kazma") is None
    assert "DOCKER_CONTEXT" not in os.environ
    assert any("kazma setup engine" in r.getMessage() for r in caplog.records)


def test_the_operators_own_context_or_host_wins(contexts, monkeypatch):
    contexts("kazma-engine")
    monkeypatch.setenv("DOCKER_CONTEXT", "my-remote")
    assert docker_cli.apply_engine_choice("kazma") == "my-remote"
    assert os.environ["DOCKER_CONTEXT"] == "my-remote"
    monkeypatch.delenv("DOCKER_CONTEXT")
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.5:2376")
    assert docker_cli.apply_engine_choice("kazma") is None
    assert "DOCKER_CONTEXT" not in os.environ


# ── the stored choice ─────────────────────────────────────────────────


def _setting(value):
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    if value is None:
        store.delete(docker_cli.ENGINE_KEY)
    else:
        store.set(docker_cli.ENGINE_KEY, value)


def _record(entry):
    from kazma_core.paths import data_dir

    path = data_dir() / "setup_record.json"
    path.write_text(json.dumps({"version": 1, **({"container_engine": entry} if entry else {})}),
                    encoding="utf-8")


def test_the_setting_wins_then_what_setup_installed(contexts):
    _setting(None)
    _record(None)
    assert container_engine.engine_setting() == ""
    _record({"kind": "kazma", "distro": "kazma-engine"})
    assert container_engine.engine_setting() == "kazma", "setup installed Kazma's engine"
    _setting("desktop")
    assert container_engine.engine_setting() == "desktop", "the owner's choice wins"
    _setting(None)
    _record(None)


def test_the_first_docker_lookup_applies_the_stored_choice(contexts):
    contexts("desktop-linux")
    _setting("desktop")
    try:
        docker_cli.find_docker_cli()
        assert os.environ.get("DOCKER_CONTEXT") == "desktop-linux"
    finally:
        _setting(None)


def test_the_guards_copy_never_reads_settings(contexts, monkeypatch):
    """The guard loads docker_cli.py by path: its copy must not import the
    app's settings (AGENTS.md section 39)."""
    called = []
    monkeypatch.setattr(container_engine, "apply_engine_setting", lambda: called.append(1))
    spec = importlib.util.spec_from_file_location(
        "kazma_guard_docker_cli_probe", ROOT / "kazma-core" / "kazma_core" / "docker_cli.py")
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)
    copy.find_docker_cli()
    assert called == []
    # Control: the package's own module does apply it.
    docker_cli.find_docker_cli()
    assert called == [1]


def test_a_settings_write_reapplies_the_choice(contexts, monkeypatch):
    seen = []
    monkeypatch.setattr(container_engine, "apply_engine_setting", lambda: seen.append(1))

    class _Thread:
        def __init__(self, target, **_kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(container_engine.threading, "Thread", _Thread)
    container_engine._on_settings_written({"memory.enabled"})
    assert seen == []
    container_engine._on_settings_written({docker_cli.ENGINE_KEY})
    container_engine._on_settings_written(None)
    assert seen == [1, 1]


# ── paths ─────────────────────────────────────────────────────────────


def test_windows_folders_as_kazmas_engine_mounts_them(contexts, monkeypatch):
    monkeypatch.setattr(docker_cli, "_drive_is_fixed", lambda drive: True)
    monkeypatch.setenv("DOCKER_CONTEXT", "kazma-engine")
    assert docker_cli.engine_host_path(r"G:\GitHubRepos\kazma", windows=True) == "/g/GitHubRepos/kazma"
    assert docker_cli.engine_host_path("C:\\", windows=True) == "/c"
    assert docker_cli.engine_host_path(r"\\server\share\x", windows=True) is None
    monkeypatch.setattr(docker_cli, "_drive_is_fixed", lambda drive: False)
    assert docker_cli.engine_host_path(r"Z:\mapped", windows=True) is None


def test_docker_desktop_and_other_systems_keep_the_path(contexts, monkeypatch):
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-linux")
    assert docker_cli.engine_host_path(r"G:\x", windows=True) == r"G:\x"
    monkeypatch.delenv("DOCKER_CONTEXT")
    assert docker_cli.engine_host_path(r"G:\x", windows=True) == r"G:\x"
    monkeypatch.setenv("DOCKER_CONTEXT", "kazma-engine")
    assert docker_cli.engine_host_path("/home/u/x", windows=False) == "/home/u/x"


def test_the_sandbox_mounts_the_translated_folder(contexts, monkeypatch, tmp_path):
    from kazma_core.tools import code_exec

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(docker_cli, "engine_host_path", lambda path, **_kw: "/g/translated")
    args = code_exec._workspace_mounts(ws)
    assert any("src=/g/translated" in a for a in args), args
    monkeypatch.setattr(docker_cli, "engine_host_path", lambda path, **_kw: None)
    with pytest.raises(RuntimeError, match="cannot mount"):
        code_exec._workspace_mounts(ws)
