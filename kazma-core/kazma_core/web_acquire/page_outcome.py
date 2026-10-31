"""What happened to one crawled page, and what the owner can do about it.

A crawl's verdict was a single word, and "partial" meant a failed page, a
page robots.txt kept out, a page cap the owner chose and an uncertain fetch
alike: nearly every real crawl ended "partial", and a real failure could not
be told from a policy (live 2026-10-31). Every page now ends in one outcome:

- ``ingested`` / ``unchanged``: its text is in the library.
- ``excluded``: a rule kept it out -- robots.txt, the crawl's scope, a login
  wall, a page the site no longer has, a page larger than Kazma reads (its
  text past ``document.PAGE_TEXT_MAX``; a part is never stored). Not a
  failure; listed as a note.
- ``failed``: something went wrong that might be fixed or retried.

Each refusal also has a reason code (``reason``) the report shows with a
hint in the page's language.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Fetch error codes that are a rule, not a fault (FetchErrorCode values).
_EXCLUDED_CODES = {
    'robots_disallowed': 'robots',
    'scope_blocked': 'out_of_scope',
    'login_required': 'login',
}
#: HTTP statuses that say the page is gone: a stale sitemap entry.
_GONE = {404, 410}
#: Codes worth trying again within the crawl (a network or server hiccup).
TRANSIENT_CODES = frozenset({
    'connect_error', 'read_error', 'timeout', 'acquisition_deadline', 'origin_throttled',
    'robots_unavailable', 'backend_unavailable',
})
#: HTTP statuses worth trying again: the server's own "try later".
TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class PageVerdict:
    outcome: str  # 'excluded' | 'failed'
    reason: str   # a stable code the report words


def judge(error_code: str | None, http_status: int | None, *, truncated: bool = False,
          empty: bool = False, reasons: tuple[str, ...] = ()) -> PageVerdict:
    """The outcome of a page that was not stored. *reasons* are a cut
    page's ``truncation_reasons``: a size limit (``body_limit``,
    ``text_limit``...) is Kazma's own rule; anything else (links a browser
    could not complete) is an incomplete page, a failure."""
    code = str(error_code or '')
    if code in _EXCLUDED_CODES:
        return PageVerdict('excluded', _EXCLUDED_CODES[code])
    if http_status in _GONE:
        return PageVerdict('excluded', 'gone')
    if truncated:  # Kazma's own size limit, as much a rule as robots.txt
        if all(reason.endswith('_limit') for reason in reasons):
            return PageVerdict('excluded', 'too_large')
        return PageVerdict('failed', 'incomplete')
    if empty:
        return PageVerdict('failed', 'no_text')
    if http_status is not None and http_status >= 400:
        return PageVerdict('failed', 'server_error' if http_status >= 500 else 'http_error')
    if code in TRANSIENT_CODES:
        return PageVerdict('failed', 'network')
    if code == 'security_blocked':
        return PageVerdict('failed', 'blocked_address')
    if code == 'challenge_page':
        return PageVerdict('failed', 'bot_wall')
    if code in {'unsupported_content', 'decoding_error'}:
        return PageVerdict('failed', 'unreadable')
    return PageVerdict('failed', 'other')


def worth_retrying(error_code: str | None, http_status: int | None, *, transient: bool = False) -> bool:
    """A failure that a wait may clear: tried again inside the crawl.

    ``security_blocked`` is here on purpose: a name that resolved to a private
    address once, when the connection dropped (the router answered 10.0.0.1,
    live 2026-10-31), is resolved and checked again -- a retry re-runs every
    check, so a host that really points inside is refused again.
    """
    code = str(error_code or '')
    if transient or code in TRANSIENT_CODES or code == 'security_blocked':
        return True
    return http_status in TRANSIENT_HTTP


#: What a crawl's notes mean: limits and rules, never failures.
NOTES = ('excluded', 'page_cap', 'link_walk_bounded', 'sitemap_bounded', 'nothing_stored')
