"""Real shipped Alpine/template rendering against local recovery API fixtures."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

from kazma_ui.i18n import TRANSLATIONS, plural_forms, t

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'kazma-ui/kazma_ui/static/js'


def _html(lang, job, legacy):
    base = '<!doctype html><html lang="{{ lang }}" dir="{{ direction }}"><head><style>[x-cloak]{display:none!important}body{font:16px Arial;max-width:100vw}*{box-sizing:border-box}</style></head><body>{% block content %}{% endblock %}</body></html>'
    env = Environment(loader=ChoiceLoader([DictLoader({'base.html': base}),
        FileSystemLoader(str(ROOT / 'kazma-ui/kazma_ui/templates'))]), autoescape=select_autoescape())
    env.globals.update(t=lambda key: t(key, lang=lang), plural_forms=lambda key: plural_forms(key, lang=lang), js_version=lambda: 'fixture')
    html = env.get_template('knowledge_base.html').render(lang=lang, direction='rtl' if lang == 'ar' else 'ltr')
    html = html.replace('<script src="/static/js/kb.js?v=fixture"></script>', '')
    catalog = {key: entry.get(lang) or entry['en'] for key, entry in TRANSLATIONS.items()}
    bootstrap = '''window.fixtureCalls=[];window.fixtureToasts=[];
window.kazmaT=(key,fallback)=>window.fixtureCatalog[key]||fallback;
window.kazmaCount=(key,n)=>KazmaFormat.count(Object.fromEntries(['zero','one','two','few','many','other'].map(k=>[k,fixtureCatalog[key+'.'+k]])),n);
window.showToast=(...args)=>fixtureToasts.push(args);
window.fetch=async(url,options={})=>{fixtureCalls.push([url,options.method||'GET']);let body;
if(url==='/api/kb/libraries')body={ok:true,libraries:[]};
else if(url==='/api/kb/jobs')body={ok:true,jobs:[{job_id:fixtureJob.job_id,status:'interrupted'}]};
else if(url==='/api/kb/jobs/'+encodeURIComponent(fixtureJob.job_id))body={ok:true,job:fixtureJob};
else if(url==='/api/kb/services/firecrawl/credits/refresh')body={ok:true,balances:[{provider:'firecrawl',remaining:0,fresh:true,status:'observed'}]};
else throw Error('Unexpected fixture request '+url);
return {json:async()=>body};};'''
    scripts = ['window.fixtureCatalog=' + json.dumps(catalog) + ';window.fixtureJob=' + json.dumps(job) + ';' + bootstrap]
    scripts += [(STATIC / name).read_text(encoding='utf-8') for name in ('locale_format.js', 'icons.js', 'kb.js', 'alpine.min.js')]
    if legacy:
        scripts = [source.replace('    _creditsAbort: null,\n', '') for source in scripts]
    return html.replace('</body>', ''.join('<script>' + source.replace('</script', '<\\/script') + '</script>' for source in scripts) + '</body>')


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != 'nt', reason='Contained Windows Chromium UI qualification; Linux uses the separate boundary harness')
@pytest.mark.parametrize('lang', ['en', 'ar'])
@pytest.mark.parametrize('legacy', [False, True], ids=['fixed', 'original-uninitialized-state'])
async def test_recovery_credit_unknowns_and_explicit_refresh_render_in_real_alpine(lang, legacy, tmp_path):
    pytest.importorskip('playwright.async_api')
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.web_acquire.browser_driver import BrowserDriver

    job = dict(job_id='lib:interrupted?version=1', phase='interrupted', resume_available=True,
        message='Fixture: uncertain metadata request', fetched=2, ingested=4, failed=0,
        acquisition=dict(status='interrupted', pending=1, ambiguous=0, metadata_ambiguous=1,
            discovery_ambiguous=0, last_receipt=dict(backend='httpx', attempts=[]),
            usage=dict(observations=[dict(provider='jina', reported=42)], complete=False),
            allowances=dict(job_reserved=2, job_limit=256, day_reserved=6, day_limit=1024),
            provider_balances=[dict(provider='firecrawl', remaining=None, fresh=False,
                status='not_checked', observed_at=None, dashboard='https://www.firecrawl.dev/app'),
                dict(provider='jina', remaining=None, fresh=False, status='dashboard_only', observed_at=None)]))
    errors, requests, timings = [], [], []
    async def verify():
        started = time.monotonic()
        def phase(name):
            timings.append({'phase': name, 'elapsed_s': round(time.monotonic() - started, 3)})
        driver = BrowserDriver()
        try:
            phase('driver_start')
            runtime = await driver.start()
            if not Path(runtime.chromium.executable_path).is_file():
                pytest.skip('Chromium executable is not installed')
            phase('browser_launch')
            browser = await runtime.chromium.launch(headless=True, chromium_sandbox=True)
            phase('page_create')
            page = await browser.new_page(viewport={'width': 1000, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            await page.route('**/*', lambda route: route.abort())
            phase('render_fixture')
            await page.set_content(_html(lang, job, legacy), timeout=10000)
            await page.get_by_text(t('knowledge.phase_interrupted', lang=lang), exact=True).wait_for(timeout=5000)
            assert await page.get_by_text(t('knowledge.resume', lang=lang), exact=True).is_visible()
            assert await page.get_by_text(t('knowledge.reported_token_usage', lang=lang), exact=False).is_visible()
            assert await page.get_by_text(t('knowledge.credits_unavailable', lang=lang), exact=True).count() == 2
            assert await page.get_by_text(t('knowledge.discovery_uncertain', lang=lang), exact=False).is_visible()
            assert await page.evaluate('document.documentElement.dir') == ('rtl' if lang == 'ar' else 'ltr')
            assert not await page.evaluate('fixtureCalls.some(c=>c[1]==="POST")')
            phase('capture')
            await page.screenshot(path=str(tmp_path / ('kb-recovery-' + lang + '.png')), full_page=True, timeout=10000)
            phase('refresh_click')
            await page.get_by_text(t('knowledge.credits_refresh', lang=lang), exact=True).click()
            phase('refresh_render')
            await page.wait_for_function('fixtureCalls.filter(c=>c[1]==="POST").length===1 && document.body.innerText.includes("observed")', timeout=10000)
            assert await page.evaluate('fixtureToasts.length') == 0
            assert not requests
            assert errors == (['_creditsAbort is not defined'] if legacy else [])
            await page.evaluate('Alpine.destroyTree(document.querySelector("[x-data]"))')
            assert await page.evaluate('__kazma_inflight_checkers.length') == 0
            phase('browser_close')
            await browser.close()
        finally:
            phase('driver_stop')
            try:
                await driver.stop()
            finally:
                phase('finished')
                (tmp_path / 'kb-recovery-timings.json').write_text(json.dumps(timings), encoding='utf-8')
    try:
        # This verifies rendering/cleanup, not a 20-second product deadline.
        # The old outer budget was shorter than a single default UI wait and
        # included contained Node/Chromium startup, screenshot and teardown.
        await run_in_browser_loop(verify(), timeout=60)
    except BaseException:
        print('KB BROWSER PHASES', json.dumps(timings), flush=True)
        raise
