"""Research crawl keeps a finite frontier and refuses incomplete publications."""
import importlib
from collections import deque

import pytest

from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode, FetchResult

wr = importlib.import_module('kazma_core.tools.web_research')


@pytest.fixture
def controlled(monkeypatch):
    async def public(*args, **kwargs):
        return ('93.184.216.34',)
    monkeypatch.setattr('kazma_core.web_acquire.dns_worker.validate_public_url', public)


@pytest.mark.asyncio
async def test_fanout_and_duplicate_pending_links_have_a_hard_frontier_bound(controlled, monkeypatch):
    sizes, calls = [], []
    class Observed(deque):
        def append(self, value):
            super().append(value)
            sizes.append(len(self))
    async def fetch(url, **kwargs):
        calls.append(url)
        # A large repeated candidate list used to keep adding the same pending
        # child on every page. Distinct query bytes still name distinct URLs.
        links = ('https://fixture:secret@docs.example/private',) + ('https://docs.example/child?q=1',) * 2000 + tuple(
            f'https://docs.example/child?q={i}' for i in range(2, 2002))
        return FetchResult(ok=True, url=url, text='article', links=links, links_complete=True)
    monkeypatch.setattr(wr, 'deque', Observed)
    monkeypatch.setattr(wr, '_central_fetch', fetch)
    report = await wr.crawl_site('https://docs.example/', max_pages=3, max_depth=2,
        delay_ms=0, save=False, respect_robots=False)
    assert max(sizes) <= 3 * 4
    assert len(calls) == len(set(calls)) == 3
    assert calls[1:] == ['https://docs.example/child?q=1', 'https://docs.example/child?q=2']
    assert 'Coverage partial' in report and 'URL cap=12' in report
    from kazma_core.web_acquire.crawl_jobs import inspect_jobs

    latest = max(inspect_jobs(), key=lambda row: row['updated_at'])
    persisted = inspect_jobs(latest['job_id'])
    assert len(persisted['pages']) == 12
    assert len({page['url'] for page in persisted['pages']}) == 12
    assert persisted['frontier_limited'] and persisted['status'] == 'partial'
    assert all('fixture:secret' not in page['url'] for page in persisted['pages'])


@pytest.mark.asyncio
async def test_partial_acquisition_is_reported_without_publishing_a_file(controlled, monkeypatch, tmp_path):
    async def fetch(url, **kwargs):
        return FetchResult(ok=True, url=url, text='partial hostile fixture', truncated=True,
            truncation_reasons=('body_limit',), links_complete=False)
    monkeypatch.setattr(wr, '_central_fetch', fetch)
    monkeypatch.setattr(wr, '_workspace_root', lambda: tmp_path)
    monkeypatch.setattr(wr, '_safe_workspace_path', lambda name, source: tmp_path / name)
    report = await wr.crawl_site('https://docs.example/', max_pages=1, delay_ms=0,
        save=True, respect_robots=False)
    assert 'Partial extract was not saved' in report and 'body_limit' in report
    assert not list(tmp_path.rglob('*.md'))


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [FetchErrorCode.DEADLINE, FetchErrorCode.LIMIT, FetchErrorCode.SECURITY])
async def test_validation_failures_keep_their_actual_classification(controlled, monkeypatch, code):
    validations, requests = [], []
    async def public(url, **kwargs):
        validations.append(url)
        if len(validations) > 1:
            raise AcquisitionError(code, 'fixture refused before dispatch')
        return ('93.184.216.34',)
    async def fetch(*args, **kwargs):
        requests.append(args)
        raise AssertionError('validation failure reached fetch')
    monkeypatch.setattr('kazma_core.web_acquire.dns_worker.validate_public_url', public)
    monkeypatch.setattr(wr, '_central_fetch', fetch)
    report = await wr.crawl_site('https://docs.example/', max_pages=1, delay_ms=0,
        save=False, respect_robots=False)
    assert str(code) in report and 'fixture refused before dispatch' in report
    assert 'blocked_ssrf' not in report and not requests


@pytest.mark.asyncio
async def test_switching_active_workspace_during_fetch_keeps_crawl_publication_bound(controlled, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from kazma_core.workspace import binding
    from kazma_core.ide.workspace_scope import current_workspace_path

    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(binding, '_WORKSPACE_ROOT', first)
    monkeypatch.setattr('kazma_core.stores.get_workspace_store', lambda:
        SimpleNamespace(get_active_workspace=lambda: None))
    async def fetch(url, **kwargs):
        binding.configure_workspace(str(second))
        return FetchResult(ok=True, url=url, text='Complete article', links_complete=True)
    monkeypatch.setattr(wr, '_central_fetch', fetch)
    report = await wr.crawl_site('https://docs.example/', max_pages=1, delay_ms=0,
        save=True, respect_robots=False)
    assert 'save_error' not in report
    assert len(list(first.rglob('*.md'))) == 1
    assert not list(second.rglob('*.md'))
    assert current_workspace_path() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [{'max_pages': 'invalid'}, {'max_depth': 'invalid'},
    {'delay_ms': 'invalid'}, {'start_url': 'https://docs.example/' + 'x' * 8192}])
async def test_invalid_crawl_budget_refuses_before_dns_or_fetch(monkeypatch, options):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Invalid input reached acquisition')
    monkeypatch.setattr('kazma_core.web_acquire.dns_worker.validate_public_url', forbidden)
    monkeypatch.setattr(wr, '_central_fetch', forbidden)
    report = await wr.crawl_site(**{'start_url': 'https://docs.example/', **options})
    assert report.startswith('Error:')
