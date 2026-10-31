"""Twenty real sites, recorded once, crawled the way production crawls them.

Every bug the live install found on 2026-10-31 passed the tests: the robots
claim the vault swallowed (the tests had no vault), the sitemap past the
parser's bound (the tests had small sitemaps), the 100,000-character cut
(the tests had short pages). These crawls replay what twenty real sites
answered (``tests/_crawl_replay.py``) through the product's whole crawl, with
the settings store as production has it -- the vault on -- and hold each
site's outcome, page by page, to ``tests/fixtures/crawl_corpus/expected.json``
(``python scripts/crawl_corpus.py lock`` after a reviewed change).
"""
from __future__ import annotations

import os

import pytest
from kazma_core import config_store
from kazma_core.security import vault as vault_mod

from tests import _crawl_replay as cr

SITES = cr.sites()
EXPECTED = cr.expected()
#: Of every page the corpus holds, the share that must end ingested,
#: unchanged or excluded by a rule (the crawl target, 2026-10-31).
TARGET = 0.99


@pytest.fixture
def production_settings(tmp_path, monkeypatch):
    """The settings store with the vault on, as on every live install."""
    monkeypatch.setenv("KAZMA_VAULT_KEY", "corpus-test-vault-key-32-bytes-long!")
    vault_mod.reset_vault()
    monkeypatch.setattr(vault_mod, "_vault", vault_mod.SecretVault(db_path=str(tmp_path / "vault.db")))
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    settings = config_store.ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: settings)
    yield settings
    settings.close()
    vault_mod.reset_vault()


async def _replay(site, tmp_path, monkeypatch, **kwargs):
    with cr.replaying(monkeypatch, cr.load(site["name"])) as transport:
        outcome = await cr.crawl(site, tmp_path / "kb", monkeypatch, **kwargs)
    assert transport.missing == [], (
        f"the recording of {site['name']} is stale; run: python scripts/crawl_corpus.py record {site['name']}")
    return outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("site", SITES, ids=[site["name"] for site in SITES])
async def test_a_recorded_site_crawls_as_locked(site, production_settings, tmp_path, monkeypatch):
    assert await _replay(site, tmp_path, monkeypatch) == EXPECTED[site["name"]]


def test_every_site_is_recorded_and_locked():
    names = {site["name"] for site in SITES}
    assert len(names) == len(SITES) >= 20
    assert names == set(EXPECTED)
    assert [name for name in sorted(names) if not cr.recording_path(name).exists()] == []


def test_the_corpus_meets_the_target():
    outcomes = [outcome for site in EXPECTED.values() for outcome, _reason in site["pages"].values()]
    good = sum(outcome in ("ingested", "unchanged", "excluded") for outcome in outcomes)
    assert outcomes and good / len(outcomes) >= TARGET, (good, len(outcomes))


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["python-missing", "robots-google-search"])
async def test_the_old_judgment_fails_the_pages_a_rule_keeps_out(name, production_settings, tmp_path, monkeypatch):
    """Negative control: every unfetched page a failure, as before 2026-10-31."""
    from kazma_core.web_acquire import page_outcome

    monkeypatch.setattr(page_outcome, "judge", lambda *a, **k: page_outcome.PageVerdict("failed", "other"))
    site = next(site for site in SITES if site["name"] == name)
    outcome = await _replay(site, tmp_path, monkeypatch)
    assert outcome != EXPECTED[name] and outcome["status"] != "completed"


@pytest.mark.asyncio
async def test_a_recorded_site_with_a_real_vector_store(production_settings, tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    site = next(site for site in SITES if site["name"] == "python-tutorial")
    outcome = await _replay(site, tmp_path, monkeypatch, vectors=cr.ToyEmbedder())
    vectors = outcome.pop("vectors")
    assert outcome == EXPECTED[site["name"]]
    assert vectors == outcome["chunks"] > 0  # every chunk searchable by meaning


@pytest.mark.postgres
@pytest.mark.skipif(os.environ.get("KAZMA_DB_BACKEND") != "postgres", reason="needs the Postgres test database")
@pytest.mark.asyncio
async def test_a_recorded_site_on_postgres(tmp_path, monkeypatch):
    """The settings store on Postgres, the vault on: the live install's shape."""
    monkeypatch.setenv("KAZMA_VAULT_KEY", "corpus-test-vault-key-32-bytes-long!")
    vault_mod.reset_vault()
    monkeypatch.setattr(vault_mod, "_vault", vault_mod.SecretVault(db_path=str(tmp_path / "vault.db")))
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    settings = config_store.ConfigStore(db_path=str(tmp_path / "unused.db"), yaml_path=str(tmp_path / "none.yaml"))
    assert settings._use_postgres()
    monkeypatch.setattr(config_store, "get_config_store", lambda: settings)
    from kazma_core.web_acquire import crawl_jobs

    try:
        for name in ("python-tutorial", "robots-google-search"):
            site = next(site for site in SITES if site["name"] == name)
            assert await _replay(site, tmp_path / name, monkeypatch) == EXPECTED[name]
    finally:
        crawl_jobs.purge_terminal()
        settings.close()
        vault_mod.reset_vault()
