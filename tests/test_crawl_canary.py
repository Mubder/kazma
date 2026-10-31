"""The daily crawl canary (2026-10-31): a real crawl, told the day it fails.

The canary crawls one known page through the product's whole crawl into a
private store, and alerts at once when the page is not stored or does not say
what it should. It never touches the owner's libraries or crawl history.
"""
from __future__ import annotations

import pytest
from kazma_core import config_store
from kazma_core.security import vault as vault_mod
from kazma_core.web_acquire import canary, crawl_jobs

from tests import _crawl_replay as cr

URL = "https://www.rfc-editor.org/rfc/rfc2606.html"
PAGE = ("<html><head><title>RFC 2606: Reserved Top Level DNS Names</title></head><body>"
        "<h1>Reserved Top Level DNS Names</h1>" + "<p>To reduce the likelihood of conflict and confusion, "
        "a few top level domain names are reserved for use in private testing.</p>" * 20 + "</body></html>")


def _site(page=PAGE, status=200):
    return {"responses": {
        "https://www.rfc-editor.org/robots.txt": {"status": 404, "headers": {}, "text": ""},
        URL: {"status": status, "headers": {"content-type": "text/html; charset=utf-8"}, "text": page},
    }}


@pytest.fixture
def live(tmp_path, monkeypatch):
    """The settings store as production has it (the vault on), and an
    owner's Knowledge store the canary must never open."""
    from kazma_core.stores import knowledge, knowledge_index
    from kazma_core.stores import knowledge_ingest as ki

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))  # each test its own private store
    monkeypatch.setenv("KAZMA_VAULT_KEY", "canary-test-vault-key-32-bytes-long!")
    vault_mod.reset_vault()
    monkeypatch.setattr(vault_mod, "_vault", vault_mod.SecretVault(db_path=str(tmp_path / "vault.db")))
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    settings = config_store.ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: settings)

    def owners_store():
        raise AssertionError("the canary opened the owner's Knowledge store")

    for module in (ki, knowledge):
        monkeypatch.setattr(module, "get_knowledge_store", owners_store)
    for module in (ki, knowledge_index):
        monkeypatch.setattr(module, "get_knowledge_index", owners_store)
    from kazma_core.memory import vector_store_global as vsg

    # The canary's library is searchable by meaning, as the install's: a toy
    # embedder here (no model under pytest).
    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: cr.ToyEmbedder())
    alerts = []
    yield settings, alerts
    settings.close()
    vault_mod.reset_vault()


def _alert(alerts):
    return lambda key, title, detail, **kw: alerts.append((key, kw.get("severity"), detail))


@pytest.mark.asyncio
async def test_the_canary_passes_quietly_and_leaves_no_crawl_behind(live, monkeypatch):
    settings, alerts = live
    with cr.replaying(monkeypatch, _site()) as transport:
        outcome = await canary.run_canary(alert=_alert(alerts))
    assert outcome["ok"] is True and outcome["reason"] == "", outcome
    assert alerts == [] and transport.missing == []
    assert crawl_jobs.inspect_jobs() == []  # no row in the owner's crawl history
    assert canary.state()["ok"] is True
    from kazma_core.observability.cadence import last_run

    assert last_run(canary._CADENCE_KEY) == pytest.approx(outcome["ran_at"])


@pytest.mark.asyncio
async def test_a_page_without_its_words_is_a_same_day_error_then_a_recovery(live, monkeypatch):
    settings, alerts = live
    with cr.replaying(monkeypatch, _site(page=PAGE.replace("Reserved Top Level DNS Names", "Something else"))):
        failed = await canary.run_canary(alert=_alert(alerts))
    assert failed["ok"] is False and "Reserved Top Level DNS Names" in failed["reason"]
    assert [(key, severity) for key, severity, _ in alerts] == [("acquisition.canary_failed", "error")]
    with cr.replaying(monkeypatch, _site()):
        passed = await canary.run_canary(alert=_alert(alerts))
    assert passed["ok"] is True
    assert alerts[-1][:2] == ("acquisition.canary_recovered", "info")


@pytest.mark.asyncio
async def test_a_page_that_cannot_be_fetched_names_what_failed(live, monkeypatch):
    settings, alerts = live
    with cr.replaying(monkeypatch, _site(status=500)):
        outcome = await canary.run_canary(alert=_alert(alerts))
    assert outcome["ok"] is False and "ended partial" in outcome["reason"] or "ended failed" in outcome["reason"]
    assert alerts and alerts[0][0] == "acquisition.canary_failed"


@pytest.mark.asyncio
async def test_without_its_private_store_the_canary_would_write_the_owners(live, monkeypatch):
    """Negative control: the seam ignored, the crawl reaches the owner's store."""
    from kazma_core.stores import knowledge_ingest as ki

    settings, alerts = live
    monkeypatch.setattr(ki, "_knowledge_store", lambda: ki.get_knowledge_store())
    with cr.replaying(monkeypatch, _site()), pytest.raises(AssertionError, match="owner's Knowledge store"):
        await canary.run_canary(alert=_alert(alerts))


@pytest.mark.asyncio
async def test_a_seed_on_a_private_network_is_refused_before_anything_is_made(live, monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

    settings, alerts = live

    async def private(seed):
        raise AcquisitionError(FetchErrorCode.SECURITY, "the address is on a private network")

    monkeypatch.setattr(ki, "_admit_public_seed", private)
    outcome = await canary.run_canary(alert=_alert(alerts))
    assert outcome["ok"] is False and "private network" in outcome["reason"]
    store, _index = canary._private_knowledge()
    try:
        assert store.get_library(canary.LIBRARY) is None and crawl_jobs.inspect_jobs() == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_later_day_finds_the_page_unchanged_and_passes(live, monkeypatch):
    settings, alerts = live
    for _day in range(2):
        with cr.replaying(monkeypatch, _site()):
            outcome = await canary.run_canary(alert=_alert(alerts))
        assert outcome["ok"] is True, outcome
    assert alerts == []


def test_the_settings_are_validated():
    assert canary.validate_settings(dict(canary._DEFAULTS)) == canary._DEFAULTS
    for bad in (dict(canary._DEFAULTS, url="file:///etc/passwd"), dict(canary._DEFAULTS, marker=""),
                dict(canary._DEFAULTS, enabled="yes"), {"url": URL}):
        with pytest.raises(ValueError):
            canary.validate_settings(bad)


def test_the_monitor_never_shows_the_canary(live, monkeypatch, tmp_path):
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import monitor

    monkeypatch.setattr(research_session, "_db_path", lambda: tmp_path / "research.db")
    job = crawl_jobs.start(canary.LIBRARY, URL, dict(mode="local_only", robots=True, max_pages=1, scope="exact",
                                                     delay_ms=0), kind="canary")
    job.finish("failed")
    assert all(entry["key"] != "job:" + job.job_id for entry in monitor._collect()[0])
