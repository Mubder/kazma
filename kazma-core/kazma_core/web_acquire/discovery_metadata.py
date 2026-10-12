"""Persist metadata effects and parsed links before moving discovery forward.

Bodies stay ephemeral. Completed and uncertain requests are never replayed
when an interrupted job resumes; only work without a receipt is dispatched.
"""
from __future__ import annotations

from kazma_core.web_acquire import crawl_jobs
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


def _valid(records):
    if not isinstance(records, dict) or len(records) > 96:
        return False
    for url, record in records.items():
        if not isinstance(url, str) or len(url) > 8192 or not isinstance(record, dict):
            return False
        if record.get('status') not in {'running', 'completed', 'ambiguous'}:
            return False
        result = record.get('result')
        if record['status'] != 'completed':
            if result is not None:
                return False
            continue
        if not isinstance(result, dict) or set(result) != {'pages', 'children', 'bytes'}:
            return False
        if type(result['bytes']) is not int or not 0 <= result['bytes'] <= 5_000_000:
            return False
        for key, limit in (('pages', 4000), ('children', 64)):
            values = result[key]
            if not isinstance(values, list) or len(values) > limit or any(not isinstance(value, str) or len(value) > 8192 for value in values):
                return False
    return True


def _settle(row):
    for record in row.get('metadata', {}).values():
        if record['status'] == 'running':
            record['status'] = 'ambiguous'


async def _acquire(url, fetch_and_parse):
    job = crawl_jobs.current_crawl.get()
    if job is None:
        return await fetch_and_parse()

    def begin(row):
        records = row.setdefault('metadata', {})
        if url in records:
            return False, records[url]['result']
        if len(records) >= 96 or not isinstance(url, str) or len(url) > 8192:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Metadata discovery reached its durable request/URL budget.')
        records[url] = dict(status='running', result=None)
        return True, None

    admitted, result = await crawl_jobs.call(job.update, begin)
    if not admitted:
        return result
    result = await fetch_and_parse()
    if not _valid({url: dict(status='completed', result=result)}):
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Parsed metadata exceeds its durable link/byte budget; discovery stopped.')

    def end(row):
        row['metadata'][url] = dict(status='completed', result=result)

    await crawl_jobs.call(job.update, end)
    return result
