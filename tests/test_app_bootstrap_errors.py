"""A failure while the web app boots surfaces as itself, not as a later symptom.

Regression for the 2026-09-22 audit. A 2026-08-29 edit inserted the ``.env``
ladder methods into the middle of ``_bootstrap_environment``, so ~250 lines of
bootstrap (logging, config, ConfigStore, model registry, agent, workspace, the
FastAPI app itself) became the tail of ``_load_env_files`` — which its caller
wraps in ``try/except`` that logs at DEBUG. A broken config was swallowed and
the next phase failed on a ``None`` app, naming the wrong cause.
"""

from __future__ import annotations

import pytest


class _BootFailure(RuntimeError):
    pass


def test_a_bootstrap_failure_is_raised_with_its_own_cause(monkeypatch):
    import kazma_core.agent as agent_mod
    from kazma_ui.app import KazmaAppBuilder

    def _broken_config(*_args, **_kwargs):
        raise _BootFailure("config could not be loaded")

    monkeypatch.setattr(agent_mod, "load_config", _broken_config)
    with pytest.raises(_BootFailure, match="config could not be loaded"):
        KazmaAppBuilder().build()


def test_a_missing_env_file_is_still_not_fatal(monkeypatch):
    """The ladder itself stays best-effort: only IT is inside the try."""
    import kazma_core.env_files as env_files
    from kazma_ui.app import KazmaAppBuilder

    def _unreadable(**_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(env_files, "load_env_files", _unreadable)
    builder = KazmaAppBuilder()
    calls: list[str] = []
    monkeypatch.setattr(builder, "_bootstrap_services", lambda: calls.append("ran"))
    builder._bootstrap_environment()
    assert calls == ["ran"]
