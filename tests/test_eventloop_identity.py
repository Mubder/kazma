"""Windows loop helpers: Proactor detection must not be silent."""

from __future__ import annotations

from kazma_core.eventloop import is_proactor_loop, log_running_loop, uvicorn_loop_factory


def test_log_running_loop_returns_class_name() -> None:
    name = log_running_loop(postgres_intended=False)
    assert isinstance(name, str)
    assert name
    assert name != ""


def test_is_proactor_loop_matches_type_name(monkeypatch) -> None:
    class _FakeProactor:
        pass

    _FakeProactor.__name__ = "ProactorEventLoop"
    assert is_proactor_loop(_FakeProactor()) is True

    class _FakeSelector:
        pass

    _FakeSelector.__name__ = "SelectorEventLoop"
    assert is_proactor_loop(_FakeSelector()) is False


def test_uvicorn_loop_factory_on_windows_or_none() -> None:
    import sys

    factory = uvicorn_loop_factory()
    if sys.platform == "win32":
        assert callable(factory)
        loop = factory()
        try:
            assert "Selector" in type(loop).__name__
            assert "Proactor" not in type(loop).__name__
        finally:
            loop.close()
    else:
        assert factory is None
