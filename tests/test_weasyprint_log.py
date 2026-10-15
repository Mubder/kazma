"""WeasyPrint's warnings about its own stylesheet stay out of Kazma's log.

WeasyPrint 70's built-in stylesheet uses ``::note-call``, ``::note-marker``
and ``::note-callback``, which its own parser refuses: every rendering
process logged three WARNINGs about a file Kazma does not ship (live
2026-10-15). Only those are dropped; a selector WeasyPrint refuses in any
other stylesheet is still logged. Negative control: without the filter
they reach the log.
"""

from __future__ import annotations

import logging

import pytest
from kazma_core.documents import weasyprint_log


@pytest.fixture
def weasy_logger():
    logger = logging.getLogger("weasyprint")
    saved = list(logger.filters)
    yield logger
    logger.filters[:] = saved


def _warn_all(logger: logging.Logger) -> None:
    for selector in ("::note-call ", "::note-marker ", "::note-callback ", "::footnote-cal "):
        logger.warning("Invalid or unsupported selector, '%s'", selector)


def test_only_weasyprints_own_selectors_are_dropped(weasy_logger, caplog) -> None:
    weasyprint_log.quiet_own_stylesheet_warnings()
    weasyprint_log.quiet_own_stylesheet_warnings()  # once per process
    assert sum(isinstance(f, weasyprint_log._OwnStylesheetSelectors) for f in weasy_logger.filters) == 1
    with caplog.at_level(logging.WARNING, logger="weasyprint"):
        _warn_all(weasy_logger)
        weasy_logger.warning("Ignored `foo: bar` at 1:1, unknown property.")
    messages = [r.getMessage() for r in caplog.records if r.name == "weasyprint"]
    assert messages == ["Invalid or unsupported selector, '::footnote-cal '",
                        "Ignored `foo: bar` at 1:1, unknown property."], messages


def test_negative_control_without_the_filter_they_reach_the_log(weasy_logger, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="weasyprint"):
        _warn_all(weasy_logger)
    assert len([r for r in caplog.records if r.name == "weasyprint"]) == 4
