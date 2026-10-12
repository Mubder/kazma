"""Research page effects on the shared bounded acquisition journal.

Bodies stay out of settings. A page is owned before acquisition/publication;
its summary and newly discovered pending URLs settle in one conditional write.
An interrupted page is ambiguous and is never automatically replayed.
"""
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager

from kazma_core.playwright_loop import finish_owned
from kazma_core.web_acquire import crawl_jobs
from kazma_core.web_acquire.policy import AcquisitionPolicyError, acquisition_policy, policy_scope
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


def valid(row):
    """Validate restored research state before trusting pending work."""
    policy = row['policy']
    if (type(policy.get('max_pages')) is not int or not 1 <= policy['max_pages'] <= 50
            or type(policy.get('max_depth')) is not int or not 0 <= policy['max_depth'] <= 5
            or not isinstance(policy.get('workspace'), str)
            or not isinstance(policy.get('path_prefix'), str) or len(policy['path_prefix']) > 8192
            or type(row.get('frontier_limited', False)) is not bool
            or len(row['pages']) > policy['max_pages'] * 4):
        return False
    urls = set()
    for page in row['pages']:
        if (page['url'] in urls or type(page.get('depth')) is not int
                or not 0 <= page['depth'] <= policy['max_depth']):
            return False
        urls.add(page['url'])
        summary = page.get('summary')
        if summary is not None:
            if (not isinstance(summary, dict) or type(summary.get('chars')) is not int
                    or not 0 <= summary['chars'] <= 20_000_000
                    or type(summary.get('depth')) is not int or summary['depth'] != page['depth']
                    or not isinstance(summary.get('url'), str) or len(summary['url']) > 8192
                    or not isinstance(summary.get('status'), str) or len(summary['status']) > 64
                    or any(not isinstance(summary.get(key), str) or len(summary[key]) > 2000
                           for key in ('path', 'error', 'acquisition'))):
                return False
    return True


class _ResearchCrawl:
    def __init__(self, job):
        self.job = job
        self.finished = False

    async def state(self):
        row = await crawl_jobs.call(self.job.read)
        pending = [(page['url'], page['depth']) for page in row['pages'] if page['status'] == 'pending']
        results = [page.get('summary') or dict(url=page['url'], depth=page['depth'], status=page['status'],
            chars=0, path='', error=page.get('error', 'Uncertain acquisition/publication; automatic replay refused.'))
            for page in row['pages'] if page['status'] != 'pending']
        return pending, {page['url'] for page in row['pages']}, results, row.get('frontier_limited', False)

    async def begin(self, url):
        if not await crawl_jobs.call(self.job.begin_page, url):
            raise AcquisitionError(FetchErrorCode.POLICY, 'Research page is already settled; replay refused.')

    async def receipt(self, url, result):
        await crawl_jobs.call(self.job.receipt, result, url=url)

    async def settle(self, url, summary, pending, limited):
        def change(row):
            page = next(page for page in row['pages'] if page['url'] == url)
            if page['status'] != 'running':
                raise AcquisitionError(FetchErrorCode.POLICY, 'Research page outcome already settled.')
            # Only the fixed public index fields, never raw text or HTML.
            saved = {key: summary.get(key, '') for key in ('url', 'depth', 'status', 'chars', 'path', 'error', 'acquisition')}
            for key in ('path', 'error', 'acquisition'):
                saved[key] = str(saved[key])[:2000]
            successful = summary['status'] in {'ok', 'partial'}
            page.update(status='completed' if successful else 'failed', summary=saved)
            row['fetched' if successful else 'failed'] += 1
            existing = {page['url'] for page in row['pages']}
            for candidate, depth in pending:
                if candidate not in existing:
                    row['pages'].append(dict(url=candidate, depth=depth, status='pending', receipt=None))
                    existing.add(candidate)
            if len(existing) > row['policy']['max_pages'] * 4:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Research frontier exceeded its persisted URL budget.')
            row['frontier_limited'] = bool(limited)
            if not valid(row):
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Research summary/frontier exceeds its durable budget.')
        await crawl_jobs.call(self.job.update, change)

    async def finish(self, partial):
        def finalize():
            self.job.finish('partial' if partial else 'completed')
            self.finished = True
        await crawl_jobs.call(finalize)


@asynccontextmanager
async def research_job(seed, workspace, options, *, resume_job_id=None):
    try:
        policy = acquisition_policy()
    except AcquisitionPolicyError as exc:
        raise AcquisitionError(FetchErrorCode.POLICY, str(exc)) from exc
    frozen = dict(options, kind='research_crawl', workspace=str(workspace), mode=policy.mode,
                  providers=sorted(policy.providers))
    if not valid(dict(policy=frozen, pages=[])):
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Research options exceed their durable budget.')
    library = 'research-crawl:' + hashlib.sha256(str(workspace).encode()).hexdigest()[:32]
    job = await crawl_jobs.call(crawl_jobs.start, library, seed, frozen, job_id=resume_job_id,
        resume=resume_job_id is not None, kind='research_crawl',
        on_cancel=lambda owned: owned.finish('interrupted'))
    owned = _ResearchCrawl(job)
    token = crawl_jobs.current_crawl.set(job)
    try:
        def initialize(row):
            if not row['discovered']:
                row.update(discovered=True, pages=[dict(url=seed, depth=0, status='pending', receipt=None)],
                           frontier_limited=False)
        await crawl_jobs.call(job.update, initialize)
        with policy_scope(policy):
            yield owned
    finally:
        crawl_jobs.current_crawl.reset(token)
        if not owned.finished:
            await finish_owned(crawl_jobs.call(job.finish, 'interrupted'))
