"""Explicit cached acquisition inspection; one joined read per panel."""
from __future__ import annotations

import asyncio
import logging
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Static

from kazma_core.web_acquire import operations
from kazma_core.web_acquire.crawl_jobs import call
from kazma_core.web_acquire.result import AcquisitionError
from kazma_core.config_availability import _storage_failures

logger = logging.getLogger(__name__)

_LABELS = operations.PANEL_LABELS


class AcquisitionStatus(Vertical):
    DEFAULT_CSS = 'AcquisitionStatus { height: auto; } AcquisitionStatus Static { height: auto; }'

    def __init__(self, *, language='en'):
        super().__init__()
        self._language = 'ar' if language == 'ar' else 'en'
        self._loading = False
        self._report = None
        self._error = None
        self._error_label = None

    def compose(self) -> ComposeResult:
        labels = _LABELS[self._language]
        yield Static(labels['title'], id='acquisition-title', markup=False)
        yield Input(placeholder=labels['job'], id='acquisition-job')
        yield Button(labels['refresh'], id='acquisition-refresh')
        yield Button(labels['credits'], id='acquisition-credits')
        yield Static(labels['hint'], id='acquisition-result', markup=False)

    def set_language(self, language):
        self._language = 'ar' if language == 'ar' else 'en'
        labels = _LABELS[self._language]
        self.query_one('#acquisition-title', Static).update(labels['title'])
        self.query_one('#acquisition-job', Input).placeholder = labels['job']
        self.query_one('#acquisition-refresh', Button).label = labels['refresh']
        self.query_one('#acquisition-credits', Button).label = labels['credits']
        self._render_status()

    def _render_status(self):
        labels = _LABELS[self._language]
        content = operations.format_cached_status(self._report, language=self._language) if self._report else labels['hint']
        if self._error_label:
            content = labels[self._error_label]
        elif self._error:
            content = self._error
        self.query_one('#acquisition-result', Static).update(labels['loading'] if self._loading else content)

    @on(Button.Pressed, '#acquisition-credits')
    @on(Button.Pressed, '#acquisition-refresh')
    def _read_clicked(self, event):
        event.stop()
        if self._loading:
            return
        self._loading = True
        self._report = self._error = self._error_label = None
        self.query_one('#acquisition-refresh', Button).disabled = True
        self.query_one('#acquisition-credits', Button).disabled = True
        self.query_one('#acquisition-result', Static).update(_LABELS[self._language]['loading'])
        job_id = self.query_one('#acquisition-job', Input).value.strip() or None
        self.run_worker(self._read_status(job_id, refresh_credits=event.button.id == 'acquisition-credits'), group='acquisition-cached-read')

    async def _read_status(self, job_id, *, refresh_credits=False):
        try:
            if refresh_credits:
                from kazma_core.web_acquire.credits import refresh_firecrawl_balance
                await refresh_firecrawl_balance()
            self._report = await call(operations.cached_status, job_id)
        except AcquisitionError as exc:
            self._error = str(exc)
        except (_storage_failures() + (ValueError, TypeError, KeyError)) as exc:
            logger.warning('Cached acquisition status failed (%s)', type(exc).__name__)
            self._error_label = 'failure'
        except asyncio.CancelledError:
            self._error_label = 'cancelled'
            raise
        finally:
            self._loading = False
            if self.is_mounted:
                self._render_status()
                self.query_one('#acquisition-refresh', Button).disabled = False
                self.query_one('#acquisition-credits', Button).disabled = False
