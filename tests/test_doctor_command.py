"""``kazma doctor`` must report what will ACTUALLY happen.

It exists because the 2026-09-16 Telegram failure took a log-dig to explain:
the registry silently substituted Z.AI for a keyless deepseek, and a pinned
model rode onto it. Three facts, individually logged, that no command showed
together.

The trap these tests guard is the one the first draft fell into: doctor
re-derived the key check from the provider row while the registry judges it per
model PROFILE, so doctor printed "has a usable key" for the provider the
registry had already rejected. A diagnostic that disagrees with the system it
diagnoses is worse than none.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kazma_cli import doctor


def _provider(name, url, key="sk-real", enabled="True"):
    return {"name": name, "base_url": url, "api_key": key, "enabled": enabled}


@pytest.fixture
def store(monkeypatch):
    cfg = {
        "registry.active_model": "deepseek-flash",
        "registry.active_provider": "deepseek",
        "registry.discovered_models": {
            "deepseek": ["deepseek-flash", "deepseek-v4-pro"],
            "Z.AI": ["glm-5.3-flash", "glm-4.6"],
        },
    }
    monkeypatch.setattr(
        "kazma_core.config_store.get_config_store",
        lambda: SimpleNamespace(get=lambda k, d=None: cfg.get(k, d)),
    )
    monkeypatch.setattr(
        "kazma_core.model_registry_store.load_providers",
        lambda _cs: [
            _provider("deepseek", "https://api.deepseek.com/v1"),
            _provider("Z.AI", "https://api.z.ai/api/paas/v4/"),
        ],
    )
    return cfg


def _registry_returning(url, model):
    return lambda: SimpleNamespace(
        get_client=lambda _m: SimpleNamespace(
            config=SimpleNamespace(base_url=url, model=model)
        )
    )


def test_reports_the_substitution_that_caused_the_live_failure(store, monkeypatch):
    """deepseek has no usable key -> registry swaps in Z.AI. Doctor must say so."""
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        _registry_returning("https://api.z.ai/api/paas/v4", "glm-5.3-flash"),
    )
    rows = doctor.collect()
    text = "\n".join(r for _s, r in rows)

    assert any(s == doctor.BAD for s, _ in rows), "a substitution must FAIL the check"
    assert "SUBSTITUTES" in text
    assert "api.z.ai" in text and "api.deepseek.com" in text, (
        "must name both endpoints — which model goes WHERE is the whole question"
    )
    assert "no usable API key" in text, "must name the trigger, not just the symptom"


def test_clean_setup_passes(store, monkeypatch):
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        _registry_returning("https://api.deepseek.com/v1", "deepseek-flash"),
    )
    rows = doctor.collect()
    assert not any(s == doctor.BAD for s, _ in rows), "\n".join(r for _s, r in rows)


def test_catalog_is_checked_against_the_RESOLVED_provider(store, monkeypatch):
    """After a substitution, the model must be checked against the NEW endpoint.

    Checking it against the configured-but-unused provider would report a
    healthy pair while the wire carries a broken one.
    """
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        _registry_returning("https://api.z.ai/api/paas/v4", "deepseek-flash"),
    )
    rows = doctor.collect()
    text = "\n".join(r for _s, r in rows)
    assert "does not offer 'deepseek-flash'" in text or "SUBSTITUTES" in text


def test_registry_failure_is_reported_not_swallowed(store, monkeypatch):
    def _boom():
        raise RuntimeError("registry exploded")

    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", _boom)
    rows = doctor.collect()
    text = "\n".join(r for _s, r in rows)
    assert "could not ask the registry" in text
    assert "registry exploded" in text


def test_exit_code_is_nonzero_when_broken(store, monkeypatch, capsys):
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        _registry_returning("https://api.z.ai/api/paas/v4", "glm-5.3-flash"),
    )
    assert doctor.run([]) == 1
    assert "FAIL" in capsys.readouterr().out
