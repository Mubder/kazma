"""Acquisition diagnostics, without a running web server.

``status`` and ``credits`` read; ``purge-finished`` is the operator's purge of
the crawl journal (Knowledge audit KB-E04): it removes this install's
finished crawl records, never a running one.
"""
from __future__ import annotations

import argparse
import json


def run(args):
    from kazma_core.web_acquire.operations import cached_status, format_cached_status
    from kazma_core.web_acquire.result import AcquisitionError
    from kazma_core.config_availability import _storage_failures

    parser = argparse.ArgumentParser(prog='kazma acquisition', description='Inspect locally cached crawler errors, usage and credits.')
    parser.add_argument('action', choices=['status', 'credits', 'purge-finished'])
    parser.add_argument('--job', help='Retained crawl job ID')
    parser.add_argument('--json', action='store_true', help='Print structured cached evidence')
    parser.add_argument('--language', choices=['en', 'ar'], default='en')
    options = parser.parse_args(args)
    try:
        if options.action == 'purge-finished':
            from kazma_core.web_acquire.crawl_jobs import purge_terminal

            removed = purge_terminal()
            report = dict(ok=True, removed=removed)
            if not options.json:
                print(f'Removed finished crawl records: {removed}')
                return 0
        else:
            if options.action == 'credits':
                import asyncio
                from kazma_core.web_acquire.credits import refresh_firecrawl_balance
                asyncio.run(refresh_firecrawl_balance())
            report = cached_status(options.job)
    except AcquisitionError as exc:
        report = dict(ok=False, error=str(exc), error_code=str(exc.code), transient=exc.transient)
    except (_storage_failures() + (ValueError, TypeError, KeyError)) as exc:
        report = dict(ok=False, error=f'Acquisition status unavailable ({type(exc).__name__}); check storage logs.',
                      error_code='status_unavailable')
    if options.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report.get('error'):
        print('Error: ' + report['error'])
    else:
        print(format_cached_status(report, language=options.language))
    return 0 if report.get('ok') else 1
