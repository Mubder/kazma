"""Bounded provider observations; tokens never imply charges or a balance.

Jina Reader's maintained crawler emits X-Usage-Tokens in response headers:
https://github.com/jina-ai/reader/blob/main/src/api/crawler.ts
"""

_FIXED = dict(provider='jina', unit='tokens', source='X-Usage-Tokens', scope='service_response')
_MAX = 999_999_999_999


def _from_jina(headers):
    values = headers.get_list('x-usage-tokens')
    if len(values) != 1:
        return None
    value = values[0].strip()
    if not 1 <= len(value) <= 12 or not value.isascii() or not value.isdecimal():
        return None
    return {**_FIXED, 'reported': int(value)}


def _valid(value):
    return (isinstance(value, dict) and set(value) == {*_FIXED, 'reported'}
        and all(value[key] == expected for key, expected in _FIXED.items())
        and type(value['reported']) is int and 0 <= value['reported'] <= _MAX)


def _summary(rows, *, complete=True):
    """Keep each observed response distinct; do not invent a complete bill."""
    observations = [{'attempt': identity, **value} for identity, value in rows if _valid(value)]
    if not observations:
        return None
    return dict(scope='observed_service_responses', observations=observations,
        complete=complete and len(observations) == len(rows))


def _observations(summary):
    if (not isinstance(summary, dict) or summary.get('scope') != 'observed_service_responses'
            or type(summary.get('complete')) is not bool or not isinstance(summary.get('observations'), list)
            or len(summary['observations']) > 256):
        return ()
    return tuple(value for value in summary['observations'] if isinstance(value, dict)
        and _valid({key: entry for key, entry in value.items() if key != 'attempt'}))


def jina_token_budget():
    """Provider-enforced Reader content budget; never a money/balance estimate.

    Contract: https://github.com/jina-ai/reader (Reader only; search ignores it).
    """
    import os
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
    raw = os.environ.get('KAZMA_JINA_TOKEN_BUDGET', '50000')
    if not 1 <= len(raw) <= 5 or not raw.isascii() or not raw.isdecimal() or not 500 <= int(raw) <= 50000:
        raise AcquisitionError(FetchErrorCode.POLICY, 'Jina Reader token budget must be an integer from 500 to 50000; dispatch refused.')
    return int(raw)
