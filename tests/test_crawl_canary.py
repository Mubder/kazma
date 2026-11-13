"""The daily crawl canary (2026-10-31): a real crawl, told the day it fails.

The canary crawls one known page through the product's whole crawl into a
private store, and alerts at once when the page is not stored or does not say
what it should. It never touches the owner's libraries or crawl history.
Since 2026-11-13 it also renders the page in Kazma's own browser (the crawl
reaches Chromium only when plain HTTP fails, so a broken browser went
unseen): the crawl tests below stub that leg, and the browser-leg tests drive
it (a real Chromium where one is installed).
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

    async def browser_leg_not_under_test(url, marker):
        return "skipped", "the browser leg has its own tests"

    monkeypatch.setattr(canary, "_render_leg", browser_leg_not_under_test)
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


# -- the browser leg ---------------------------------------------------------------------------


def _browser(monkeypatch, *, unavailable=None, result=None, raises=None):
    """The browser leg's seams: whether a browser can start here, the public
    address check, and the render itself (on the browser loop)."""
    from kazma_core import playwright_loop
    from kazma_core.web_acquire import browser, browser_isolation

    calls = []
    monkeypatch.setattr(browser_isolation, "browser_unavailable", lambda: unavailable)

    async def public(url, **kw):
        calls.append(("validated", url))

    async def render(url, *, challenge, profile="generic"):
        calls.append(("rendered", url))
        if raises is not None:
            raise raises
        return browser.RenderedDocument(result=result)

    async def on_the_browser_loop(coro, timeout=180.0, **kw):
        return await coro

    monkeypatch.setattr(canary, "_public_check", public)
    monkeypatch.setattr(browser, "render_document", render)
    monkeypatch.setattr(playwright_loop, "run_in_browser_loop", on_the_browser_loop)
    return calls


@pytest.fixture
def browser_leg(live, monkeypatch):
    """The live fixture with the real browser leg back in place."""
    monkeypatch.setattr(canary, "_render_leg", _REAL_RENDER_LEG)
    return live


_REAL_RENDER_LEG = canary._render_leg


@pytest.mark.asyncio
async def test_a_browser_that_cannot_render_fails_the_canary(browser_leg, monkeypatch):
    from kazma_core.web_acquire.result import FetchErrorCode, FetchResult

    settings, alerts = browser_leg
    calls = _browser(monkeypatch, result=FetchResult(ok=False, url=URL, error_code=FetchErrorCode.UNAVAILABLE,
                                                     error="the driver ended"))
    with cr.replaying(monkeypatch, _site()):
        outcome = await canary.run_canary(alert=_alert(alerts))
    assert outcome["ok"] is False and "Kazma's browser did not render it" in outcome["reason"]
    assert outcome["render"]["status"] == "failed"
    assert [key for key, _severity, _detail in alerts] == ["acquisition.canary_failed"]
    assert calls == [("validated", URL), ("rendered", URL)]


@pytest.mark.asyncio
async def test_a_rendered_page_without_its_words_fails_too(browser_leg, monkeypatch):
    from kazma_core.web_acquire.result import FetchResult

    settings, alerts = browser_leg
    _browser(monkeypatch, result=FetchResult(ok=True, url=URL, text="a challenge page"))
    with cr.replaying(monkeypatch, _site()):
        outcome = await canary.run_canary(alert=_alert(alerts))
    assert outcome["ok"] is False and "rendered does not hold" in outcome["reason"]


@pytest.mark.asyncio
async def test_a_browser_that_renders_passes_and_is_recorded(browser_leg, monkeypatch):
    from kazma_core.web_acquire.result import FetchResult

    settings, alerts = browser_leg
    _browser(monkeypatch, result=FetchResult(ok=True, url=URL, text="Reserved Top Level DNS Names ..."))
    with cr.replaying(monkeypatch, _site()):
        outcome = await canary.run_canary(alert=_alert(alerts))
    assert outcome["ok"] is True and outcome["render"] == {"status": "passed", "reason": ""}
    assert canary.state()["render"]["status"] == "passed" and alerts == []


@pytest.mark.asyncio
async def test_no_browser_installed_is_said_and_passes(browser_leg, monkeypatch):
    settings, alerts = browser_leg
    calls = _browser(monkeypatch, unavailable="Kazma's browser is not installed (kazma setup install --with browser)")
    with cr.replaying(monkeypatch, _site()):
        outcome = await canary.run_canary(alert=_alert(alerts))
    assert outcome["ok"] is True and outcome["render"]["status"] == "skipped"
    assert "--with browser" in outcome["render"]["reason"] and calls == [] and alerts == []


@pytest.mark.asyncio
async def test_negative_control_the_crawl_alone_passes_over_a_broken_browser(browser_leg, monkeypatch):
    """The canary as it was (no browser leg): the same broken browser, a pass."""
    from kazma_core.web_acquire.result import FetchErrorCode, FetchResult

    settings, alerts = browser_leg
    _browser(monkeypatch, result=FetchResult(ok=False, url=URL, error_code=FetchErrorCode.UNAVAILABLE,
                                             error="the driver ended"))

    async def the_old_canary(url, marker):
        return "skipped", "not part of the canary then"

    monkeypatch.setattr(canary, "_render_leg", the_old_canary)
    with cr.replaying(monkeypatch, _site()):
        outcome = await canary.run_canary(alert=_alert(alerts))
    assert outcome["ok"] is True and alerts == []


def test_the_install_and_the_canary_ask_one_rule_for_chromium(tmp_path, monkeypatch):
    from kazma_cli import install
    from kazma_core.web_acquire import browser_isolation

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert install._playwright_chromium() is browser_isolation.chromium_installed() is False
    (tmp_path / "chromium-1234").mkdir()
    assert install._playwright_chromium() is browser_isolation.chromium_installed() is True


@pytest.mark.asyncio
async def test_a_real_chromium_renders_the_replayed_page(browser_leg, monkeypatch):
    """Where Kazma's browser is installed and may start, the leg drives a real
    Chromium through the request broker, against the replayed site."""
    from kazma_core.web_acquire import browser_isolation

    why_not = browser_isolation.browser_unavailable()
    if why_not:
        pytest.skip(why_not)
    settings, alerts = browser_leg
    with cr.replaying(monkeypatch, _site()):
        outcome = await canary.run_canary(alert=_alert(alerts))
    assert outcome["render"] == {"status": "passed", "reason": ""}, outcome
    assert outcome["ok"] is True
