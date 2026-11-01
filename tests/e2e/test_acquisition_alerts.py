"""Real app, bell, persistent completion and saved result in both languages."""
import pytest

pytest.importorskip('playwright')
pytest.importorskip('uvicorn')
from tests.e2e._unified_turn_harness import unified_turn_server

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.mark.parametrize('language', ['en', 'ar'])
def test_completion_alert_opens_saved_result_and_settings(language, monkeypatch):
    from playwright.sync_api import sync_playwright
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import monitor
    from kazma_core.observability import ops_alerts

    def no_external_delivery(*args, on_delivery=None, **kwargs):
        if on_delivery:
            on_delivery(False)
        return True
    monkeypatch.setattr(ops_alerts, 'alert', no_external_delivery)
    with unified_turn_server() as h:
        session = research_session.create_session('Completion fixture')
        monitor.tick()
        research_session.update_session(session.id, status='done', sources=3, summary='Saved fixture result, not a regenerated answer.')
        monitor.tick()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies([dict(name='kazma-lang', value=language, url=h.base)])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            try:
                page.goto(h.base + '/research')
                page.wait_for_function('() => !!window.Alpine && !!window.KazmaResearch')
                page.evaluate("() => Alpine.store('notifications').refresh()")
                page.wait_for_function("() => Alpine.store('notifications').items.some(a => a.status === 'done')")
                row = page.evaluate("() => Alpine.store('notifications').items.find(a => a.status === 'done')")
                assert row['dispatch'] == 'failed'
                assert row['title'] == ('اكتمل البحث' if language == 'ar' else 'Research completed')
                assert row['callback_id'] == 'link:/research?session=' + session.id
                # A finished run is a success: a green toast whose Open button
                # goes to the saved result, never the yellow attention banner
                # (87f46a6d; this test still looked for the banner's link).
                toast = page.locator('.toast-action')
                toast.wait_for()
                assert toast.inner_text() == ('فتح' if language == 'ar' else 'Open')
                assert not page.locator('.system-alerts-banner').is_visible()
                toast.click()
                page.wait_for_url('**/research?session=' + session.id)
                page.wait_for_function("() => document.getElementById('research-detail-output').textContent.includes('Saved fixture result')")
                assert 'Completion fixture' in page.locator('#research-detail-title').inner_text()
                # The alert and saved text survive a new document, not merely a toast.
                page.reload()
                page.wait_for_function("() => document.getElementById('research-detail-output').textContent.includes('Saved fixture result')")
                page.goto(h.base + '/settings?tab=system')
                page.locator('#acquisition-operations button').filter(has_text='قراءة إعدادات التنبيهات' if language == 'ar' else 'Read alert settings').click()
                page.wait_for_function("() => !!Alpine.$data(document.querySelector('[x-data=\"settingsApp()\"]')).acquisitionMonitor")
                cfg = page.evaluate("() => Alpine.$data(document.querySelector('[x-data=\"settingsApp()\"]')).acquisitionMonitor")
                assert cfg == dict(enabled=True, stall_seconds=180, low_credit_threshold=100, notify_completed=True)
                assert not errors
            finally:
                context.close()
                browser.close()
