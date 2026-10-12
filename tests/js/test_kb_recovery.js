"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/kb.js'), 'utf8');

function setup(fetch) {
  const timers = new Map(), toasts = [];
  let next = 1;
  const window = {
    __KB_STRINGS: {},
    kazmaT: (key, fallback) => ({ 'knowledge.phase_interrupted': 'متوقف' })[key] || fallback,
    kazmaCount: (key, count) => String(count),
    KazmaFormat: { number: String },
    showToast: (...args) => toasts.push(args),
  };
  const context = { window, fetch, AbortController, console,
    setInterval: fn => { const id = next++; timers.set(id, fn); return id; },
    clearInterval: id => timers.delete(id),
    setTimeout: fn => { const id = next++; timers.set(id, fn); return id; },
    clearTimeout: id => timers.delete(id),
    document: { getElementById: () => null, querySelector: () => null },
  };
  vm.runInNewContext(source, context);
  const page = window.knowledgePage();
  page.load = async () => {};
  return { page, timers, toasts };
}

test('reconnect selects durable interrupted work and renders Arabic status', async () => {
  const calls = [];
  const { page, timers, toasts } = setup(async url => {
    calls.push(url);
    return { json: async () => url === '/api/kb/jobs'
      ? { ok: true, jobs: [{ job_id: 'lib:job?1', status: 'interrupted' }] }
      : { ok: true, job: { job_id: 'lib:job?1', phase: 'interrupted', resume_available: true } } };
  });
  await page.restoreCrawl();
  await timers.get(page._pollTimer)();
  assert.equal(calls[1], '/api/kb/jobs/lib%3Ajob%3F1');
  assert.equal(page.jobTitle(), 'متوقف');
  assert.equal(page.jobDone(), true);
  assert.equal(page.activeJob.resume_available, true);
  assert.equal(page._pollTimer, null);
  assert.equal(toasts.length, 0);
});

test('slow status reads stay single and storage failure stays visible', async () => {
  let finish, requests = 0;
  const { page, timers } = setup(async () => {
    requests++;
    await new Promise(resolve => { finish = resolve; });
    return { json: async () => ({ ok: false, error: 'Journal storage unavailable' }) };
  });
  page._pollJob('fixture');
  const tick = timers.get(page._pollTimer);
  const pending = tick();
  await tick();
  assert.equal(requests, 1);
  finish();
  await pending;
  assert.equal(page.activeJob.poll_error, 'Journal storage unavailable');
  assert.equal(page._pollBusy, false);
  assert.notEqual(page._pollTimer, null);
  page.destroy();
  assert.equal(timers.size, 0);
});

test('reconnect restores the latest settled receipt and credits without repeating completion', async () => {
  const { page, timers, toasts } = setup(async url => ({ json: async () => url === '/api/kb/jobs'
    ? { ok: true, jobs: [{ job_id: 'completed', status: 'partial' }] }
    : { ok: true, job: { job_id: 'completed', phase: 'done', fetched: 2, ingested: 4,
      acquisition: { status: 'partial', provider_balances: [{ provider: 'firecrawl', fresh: false, remaining: null }] } } } }));
  await page.restoreCrawl();
  await timers.get(page._pollTimer)();
  assert.equal(page.activeJob.job_id, 'completed');
  assert.equal(page.activeJob.acquisition.provider_balances[0].remaining, null);
  assert.equal(page._pollTimer, null);
  assert.equal(toasts.length, 0);
  page.destroy();
});

test('resume calls the bounded endpoint and prevents a second submission', async () => {
  let requests = 0, finish;
  const { page } = setup(async (url, options) => {
    requests++;
    assert.equal(url, '/api/kb/jobs/lib%3Aone/resume');
    assert.equal(options.method, 'POST');
    await new Promise(resolve => { finish = resolve; });
    return { json: async () => ({ ok: true, job_id: 'lib:one' }) };
  });
  page.activeJob = { job_id: 'lib:one', phase: 'interrupted', resume_available: true };
  const pending = page.resumeCrawl();
  await page.resumeCrawl();
  assert.equal(requests, 1);
  finish();
  await pending;
  assert.equal(page.activeJob.phase, 'starting');
  assert.equal(page._activeJobId, 'lib:one');
  page.destroy();
});

test('destroy aborts reconnect and cannot schedule a late retry', async () => {
  let fail, signal;
  const { page, timers } = setup(async (url, options) => {
    signal = options.signal;
    await new Promise((resolve, reject) => { fail = reject; });
  });
  const pending = page.restoreCrawl();
  page.destroy();
  assert.equal(signal.aborted, true);
  fail(new Error('Restart'));
  await pending;
  assert.equal(timers.size, 0);
  assert.equal(page.activeJob, null);
});

test('credit refresh is explicit and single; regular status reads do not call a provider', async () => {
  const calls = [];
  let finish;
  const { page, timers } = setup(async (url, options = {}) => {
    calls.push([url, options]);
    if (options.method === 'POST') {
      await new Promise(resolve => { finish = resolve; });
      return { json: async () => ({ ok: true, balances: [{ provider: 'firecrawl', remaining: 0, fresh: true }] }) };
    }
    return { json: async () => ({ ok: true, job: { phase: 'crawl', acquisition: {} } }) };
  });
  page._pollJob('fixture');
  await timers.get(page._pollTimer)();
  await timers.get(page._pollTimer)();
  assert.equal(calls.length, 2);
  assert(calls.every(([url, options]) => url === '/api/kb/jobs/fixture' && !options.method));
  const pending = page.refreshCredits();
  await page.refreshCredits();
  assert.equal(calls.length, 3);
  assert.equal(calls[2][0], '/api/kb/services/firecrawl/credits/refresh');
  finish();
  await pending;
  assert.equal(page.activeJob.acquisition.provider_balances[0].remaining, 0);
  page.destroy();
  assert.equal(timers.size, 0);
});

test('incomplete discovery with successful pages never emits a success toast', async () => {
  const { page, timers, toasts } = setup(async () => ({ json: async () => ({ ok: true,
    job: { phase: 'done', fetched: 2, discovered: 2, ingested: 4, failed: 0,
      acquisition: { status: 'partial', metadata_ambiguous: 1 } } }) }));
  page._pollJob('fixture');
  await timers.get(page._pollTimer)();
  assert.equal(toasts.length, 1);
  assert.equal(toasts[0][1], 'warning');
  assert.match(toasts[0][0], /incomplete discovery/);
  page.destroy();
});
