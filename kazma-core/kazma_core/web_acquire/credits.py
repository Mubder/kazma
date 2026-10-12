"""Explicit, metered Firecrawl balance refresh; local reads never poll a service.

Contract: https://docs.firecrawl.dev/api-reference/endpoint/credit-usage
GET /v2/team/credit-usage, data.remainingCredits. This is a team balance
observed through one credential, never the charge for Kazma's last request.
No supported Jina/proxy balance contract is assumed.
"""
from __future__ import annotations

import asyncio
import math
import os
import time
import uuid

import httpx

from kazma_core.web_acquire import accounting, crawl_jobs
from kazma_core.web_acquire.endpoint_transport import EndpointAsyncTransport
from kazma_core.web_acquire.policy import acquisition_policy
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


def _configuration():
    key = (os.environ.get('KAZMA_FIRECRAWL_API_KEY') or '').strip()
    base = (os.environ.get('KAZMA_FIRECRAWL_URL') or 'https://api.firecrawl.dev').rstrip('/')
    return key, base, accounting.account_fingerprint('firecrawl', base + key)


def _claim(account):
    now, claim = time.time(), uuid.uuid4().hex
    def change(state):
        balances = state['balances']
        row = balances.get(account)
        if row and now - row['attempted_at'] < 60:
            return None
        if account not in balances and len(balances) >= 16:
            del balances[min(balances, key=lambda identity: balances[identity]['attempted_at'])]
        balances[account] = dict(claim=claim, attempted_at=now, observed_at=None,
            status='refreshing', remaining=None, error=None)
        return claim
    return accounting._mutate(change)


def _save(account, claim, *, remaining=None, error=None):
    def change(state):
        row = state['balances'].get(account)
        if row is not None and row['claim'] == claim:
            row.update(status='unavailable' if error else 'observed', remaining=remaining,
                observed_at=None if error else time.time(), error=error)
    accounting._mutate(change)


def snapshot():
    key, _, account = _configuration()
    policy = acquisition_policy()
    firecrawl = dict(provider='firecrawl', account=account, scope='authenticated_team_via_credential',
        unit='credits', remaining=None, observed_at=None, fresh=False, status='disabled',
        dashboard='https://www.firecrawl.dev/app')
    if policy.permits('firecrawl'):
        if key:
            row = accounting._read_evidence()['balances'].get(account)
            if row:
                firecrawl.update({k: v for k, v in row.items() if k not in {'claim'}})
                fresh = row['observed_at'] is not None and 0 <= time.time() - row['observed_at'] < 300
                firecrawl['fresh'] = fresh
                if row['status'] == 'observed' and not fresh:
                    firecrawl['status'] = 'stale'
            else:
                firecrawl['status'] = 'not_checked'
        else:
            firecrawl['status'] = 'not_configured'
    return [firecrawl, dict(provider='jina', status='dashboard_only', remaining=None,
        fresh=False, observed_at=None, dashboard='https://jina.ai/reader/'),
        dict(provider='proxy', status='not_supported', remaining=None, fresh=False, observed_at=None)]


async def refresh_firecrawl_balance():
    """An explicit action; concurrency/cadence controlled in the durable ledger."""
    if not acquisition_policy().permits('firecrawl'):
        raise AcquisitionError(FetchErrorCode.POLICY, 'Firecrawl credit refresh is disabled by acquisition policy.')
    key, base, account = _configuration()
    if not key:
        raise AcquisitionError(FetchErrorCode.POLICY, 'Configure a Firecrawl API key before refreshing its credit balance.')
    claim = await crawl_jobs.call(_claim, account)
    if claim is None:
        return await crawl_jobs.call(snapshot)
    from kazma_core.http_tls import shared_ssl_context
    from kazma_core.web_acquire.http import read_response_capped
    from kazma_core.web_acquire.resources import acquisition_capacity

    try:
        with accounting.remote_scope():
            async with asyncio.timeout(15), await acquisition_capacity().acquire(), httpx.AsyncClient(
                    transport=EndpointAsyncTransport(verify=shared_ssl_context()), verify=shared_ssl_context(), trust_env=False,
                    timeout=10, follow_redirects=False,
                    event_hooks=accounting.httpx_hooks('firecrawl', base + key, asynchronous=True)) as client:
                async with client.stream('GET', base + '/v2/team/credit-usage',
                        headers={'Authorization': 'Bearer ' + key, 'Accept-Encoding': 'identity'}) as response:
                    receipt, _, truncated = await read_response_capped(response, 32768)
        if receipt.status_code != 200:
            hint = ('Check the API key and team access.' if receipt.status_code in {401, 403}
                else 'Check account credits in the provider dashboard.' if receipt.status_code == 402
                else 'Rate limited; wait before refreshing again.' if receipt.status_code == 429
                else 'Service unavailable; retry later.' if receipt.status_code >= 500
                else 'Check the provider dashboard and endpoint configuration.')
            await crawl_jobs.call(_save, account, claim,
                error=f'Firecrawl balance query failed (HTTP {receipt.status_code}). {hint}')
            return await crawl_jobs.call(snapshot)
        if truncated:
            await crawl_jobs.call(_save, account, claim,
                error='Firecrawl balance response exceeded its read limit; credits unavailable.')
            return await crawl_jobs.call(snapshot)
        data = receipt.json()
        remaining = data['data']['remainingCredits'] if data.get('success') is True else None
        if type(remaining) not in (int, float) or not math.isfinite(remaining):
            raise ValueError('Invalid balance')
        await crawl_jobs.call(_save, account, claim, remaining=remaining)
    except (ValueError, KeyError, TypeError, AttributeError):
        await crawl_jobs.call(_save, account, claim, error='Invalid Firecrawl balance response; credits unavailable.')
    except (AcquisitionError, httpx.HTTPError, TimeoutError) as exc:
        # Never persist an exception containing credentials/endpoint query data.
        await crawl_jobs.call(_save, account, claim, error='Firecrawl balance refresh unavailable (' + type(exc).__name__ + ').')
        if isinstance(exc, AcquisitionError) and exc.code in {FetchErrorCode.STORAGE, FetchErrorCode.LIMIT}:
            raise
    return await crawl_jobs.call(snapshot)
