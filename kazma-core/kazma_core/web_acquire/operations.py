"""Shared cached acquisition operations for CLI and terminal surfaces."""
from __future__ import annotations

from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

PANEL_LABELS = {
    'en': dict(credits='Refresh Firecrawl credits', title='Web acquisition', refresh='Read cached status', job='Job ID (optional)',
        hint='Inspect saved errors, usage and credits without contacting providers.', loading='Reading cached status…',
        failure='Status unavailable; check storage logs.', cancelled='Cached status read cancelled.'),
    'ar': dict(credits='تحديث رصيد Firecrawl', title='جلب الويب', refresh='قراءة الحالة المحفوظة', job='معرّف المهمة (اختياري)',
        hint='عرض الأخطاء والاستخدام والرصيد المحفوظ دون الاتصال بالمزودين.', loading='قراءة الحالة المحفوظة…',
        failure='الحالة غير متاحة؛ راجع سجلات التخزين.', cancelled='أُلغيت قراءة الحالة المحفوظة.'),
}

_LABELS = {
    'en': dict(title='Cached web acquisition status', mode='Mode', providers='Permitted remote services',
        unknown='unavailable', none='none', usage='Reported service-response tokens',
        charges='Request charges', reservations='Reserved request upper bounds (not charges)',
        day='UTC day', job='Job', balance='Cached account credits', observed='observed', fresh='fresh',
        jobs='Retained jobs', status='status', fetched='fetched', failed='failed', ambiguous='uncertain',
        errors='Readiness errors', local='Local status read; no provider call or balance refresh.',
        capacity='Live capacity belongs to this Python process only.', pending='pending', service='Last service', error='Last acquisition error'),
    'ar': dict(title='حالة جلب الويب المحفوظة', mode='الوضع', providers='الخدمات الخارجية المسموح بها',
        unknown='غير متاح', none='لا يوجد', usage='الرموز المبلّغ عنها لاستجابة الخدمة',
        charges='تكلفة الطلبات', reservations='الحد الأعلى للطلبات المحجوزة (ليس تكلفة)',
        day='اليوم بتوقيت UTC', job='المهمة', balance='رصيد الحساب المحفوظ', observed='وقت الرصد', fresh='حديث',
        jobs='المهام المحتفظ بها', status='الحالة', fetched='تم جلبه', failed='فشل', ambiguous='غير مؤكد',
        errors='أخطاء الجاهزية', local='قراءة محلية؛ لا اتصال بمزود ولا تحديث للرصيد.',
        capacity='السعة المباشرة تخص عملية Python الحالية فقط.', pending='قيد الانتظار', service='آخر خدمة', error='آخر خطأ في الجلب'),
}


def cached_status(job_id: str | None = None):
    """Read persisted observations even when current policy forbids egress."""
    from kazma_core.diagnostic_scope import read_only_diagnostic

    with read_only_diagnostic('cached acquisition status'):
        return _cached_status(job_id)


def _cached_status(job_id):
    from kazma_core.tools.research_readiness import research_readiness
    from kazma_core.web_acquire import accounting, credits

    if job_id is not None and (not isinstance(job_id, str) or not 1 <= len(job_id) <= 200):
        raise AcquisitionError(FetchErrorCode.INVALID_URL, 'Invalid retained acquisition job identity.')
    report = research_readiness(probe_search=False, job_id=job_id)
    acquisition = report.get('acquisition')
    if acquisition is None:
        return report
    if job_id is not None and acquisition['crawl_jobs'] is None:
        journal = next(check for check in report['checks'] if check['id'] == 'durable_crawls')
        if journal['ok']:
            raise AcquisitionError(FetchErrorCode.INVALID_URL, 'Retained acquisition job was not found; check its ID or retention.')
    allowance = accounting.snapshot(job_id)
    balances = credits.snapshot()
    from kazma_core.web_acquire.artifact_storage import snapshot as artifact_snapshot
    acquisition['retained_storage'] = artifact_snapshot()
    allowance['provider_balances'] = balances
    acquisition.update(allowances=allowance, usage=allowance['provider_usage'],
        usage_available=allowance['provider_usage'] is not None,
        provider_balances=balances, credits=None, credits_available=any(row['fresh'] for row in balances),
        resources_scope='current_python_process', status_read='cached_only')
    return report


def format_cached_status(report, *, language='en'):
    """Plain EN/AR text; zero observations remain distinct from unknown bills."""
    labels = _LABELS['ar' if language == 'ar' else 'en']
    evidence = report.get('acquisition') or {}
    unknown = labels['unknown']
    lines = [labels['title'], labels['local'],
        f"{labels['mode']}: {evidence.get('mode', unknown)}",
        f"{labels['providers']}: {', '.join(evidence.get('remote_providers', [])) or labels['none']}"]
    allowance = evidence.get('allowances') or {}
    usage = evidence.get('usage') or {}
    for row in usage.get('observations', []):
        lines.append(f"{row['provider']}: {labels['usage']}={row['reported']}; {labels['charges']}={unknown}")
    if not usage:
        lines.append(f"{labels['usage']}: {unknown}")
    lines.append(f"{labels['charges']}: {unknown}")
    if allowance:
        job_reserved = allowance.get('job_reserved')
        job_limit = allowance.get('job_limit')
        lines.append(f"{labels['reservations']}: {labels['job']}="
            f"{job_reserved if job_reserved is not None else unknown}/{job_limit if job_limit is not None else unknown}; "
            f"{labels['day']}={allowance['day_reserved']}/{allowance['day_limit']}")
    for balance in evidence.get('provider_balances', []):
        value = balance['remaining'] if balance['fresh'] else unknown
        lines.append(f"{balance['provider']}: {labels['balance']}={value}; {labels['status']}={balance['status']}; "
            f"{labels['observed']}={balance.get('observed_at') or unknown}; {labels['fresh']}={balance['fresh']}")
        if balance.get('error'):
            lines.append(f"{balance['provider']}: {labels['error']}: {balance['error']}")
    jobs = evidence.get('crawl_jobs')
    if isinstance(jobs, dict):
        last = jobs.get('acquisition') or {}
        lines.append(f"{labels['job']}: {jobs['job_id']}; {labels['status']}={last.get('status', unknown)}; "
            f"{labels['pending']}={last.get('pending', unknown)}; {labels['ambiguous']}={last.get('ambiguous', unknown)}")
        receipt = last.get('last_receipt') or {}
        lines.append(f"{labels['service']}: {receipt.get('backend') or unknown}; {labels['failed']}={jobs['failed']}")
        if receipt.get('error_code') or receipt.get('error'):
            lines.append(f"{labels['error']}: {receipt.get('error_code') or unknown}; {receipt.get('error') or unknown}")
    elif isinstance(jobs, list):
        lines.append(f"{labels['jobs']}: {len(jobs)}")
        for job in jobs:
            lines.append(f"{job['job_id']}: {labels['status']}={job['status']}; {labels['fetched']}={job['fetched']}; "
                f"{labels['failed']}={job['failed']}; {labels['ambiguous']}={job['ambiguous']}")
    retained = evidence.get('retained_storage') or {}
    if retained:
        title = 'التخزين المحجوز للتقارير' if language == 'ar' else 'Retained report storage reservation'
        lines.append(f"{title}: {retained['reserved_bytes']}/{retained['limit_bytes']} bytes; {retained['retained_directories']} reports")
    lines.append(labels['capacity'])
    errors = [check['message'] for check in report.get('checks', []) if check.get('level') == 'error']
    if errors:
        lines.append(labels['errors'] + ':\n' + '\n'.join(errors))
    return '\n'.join(lines)
