"""A stopped app takes back what it put in the process's service container.

The app registers its settings store, model registry, agent, swarm manager,
gateway, chat adapters and session store in the process-wide service
container (``kazma_core.service_container``). Nothing took them out: a later
app in the same process -- every test that builds one -- found the stopped
app's chat adapters there, bound to that app's settings database, and a chat
app's Test ran against them and failed "no such table: settings" (CI,
2026-11-13, order-dependent). Each case runs the real app; the control is the
app without the release.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from kazma_core.service_container import ServiceContainer, get_container


def _entries_of(builder) -> list[tuple[object, object]]:
    return list(builder._container_entries)


def _held(entries) -> list[str]:
    """The keys still registered by *entries*' registrations."""
    tokens = get_container().snapshot()[2]
    return [getattr(key, "__name__", str(key)) for key, token in entries if tokens.get(key) is token]


# -- the container ----------------------------------------------------------------------


def test_release_removes_an_entry_only_while_it_is_the_same_registration() -> None:
    container = ServiceContainer()
    shared = object()  # two apps share the settings store: the same object
    older = container.register("svc", shared)
    newer = container.register("svc", shared)
    assert container.release("svc", older) is False
    assert container.get("svc") is shared
    assert container.release("svc", newer) is True
    assert not container.has("svc")


# -- the app ----------------------------------------------------------------------------


def test_a_stopped_app_releases_everything_it_registered() -> None:
    from kazma_ui.app import create_app

    app = create_app()
    builder = app.state.kazma_runtime
    with TestClient(app):
        entries = _entries_of(builder)
        names = {getattr(key, "__name__", "") for key, _ in entries}
        assert {"ConfigStore", "ModelRegistry", "KazmaAgent"} <= names
        assert _held(entries) == [getattr(k, "__name__", str(k)) for k, _ in entries]
    assert _held(entries) == [], "a stopped app left entries in the container"
    assert builder._container_entries == []


def test_an_entry_a_newer_app_registered_stays() -> None:
    from kazma_core.config_store import ConfigStore
    from kazma_ui.app import create_app

    older = create_app().state.kazma_runtime
    newer = create_app().state.kazma_runtime
    assert get_container().get(ConfigStore) is newer.config_store
    older._release_container_entries()
    assert get_container().get(ConfigStore) is newer.config_store
    assert _held(_entries_of(newer)) == [getattr(k, "__name__", str(k)) for k, _ in _entries_of(newer)]
    newer._release_container_entries()


def test_negative_control_without_the_release_the_entries_stay(monkeypatch) -> None:
    from kazma_ui.app import KazmaAppBuilder, create_app

    monkeypatch.setattr(KazmaAppBuilder, "_release_container_entries", lambda self: None)
    app = create_app()
    builder = app.state.kazma_runtime
    with TestClient(app):
        entries = _entries_of(builder)
    assert _held(entries), "the control should leave the stopped app's entries"
