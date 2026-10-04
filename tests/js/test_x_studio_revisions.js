/* Stale browser responses and confirmations cannot change publication intent. */
"use strict";
const assert = require("assert");
const path = require("path");
global.window = globalThis;
global.document = { documentElement: { lang: "en", getAttribute: () => "ltr" } };
global.showToast = () => {};
require(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/x_studio.js"));
const response = (data, ok = true) => ({ ok, json: async () => data });
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

(async () => {
  let page = xStudioPage();
  page.drafts = [{ id: "last-good" }];
  global.fetch = async () => response({ ok: false, error: "Database unavailable" }, false);
  await page.loadDrafts();
  assert.strictEqual(page.drafts[0].id, "last-good");
  assert.strictEqual(page.loadStates.drafts.error, "Database unavailable");
  assert.strictEqual(page.loadStates.drafts.loading, false);

  const older = deferred(), newer = deferred();
  let calls = 0;
  global.fetch = () => (++calls === 1 ? older.promise : newer.promise);
  const first = page.loadDrafts(), second = page.loadDrafts();
  newer.resolve(response({ ok: true, drafts: [{ id: "new" }] }));
  await second;
  older.resolve(response({ ok: true, drafts: [{ id: "old" }] }));
  await first;
  assert.strictEqual(page.drafts[0].id, "new");

  page = xStudioPage();
  const previewOld = deferred(), previewNew = deferred();
  calls = 0;
  global.fetch = () => (++calls === 1 ? previewOld.promise : previewNew.promise);
  page._previewSequence = 1;
  const p1 = page.refreshPreview();
  page._previewSequence = 2;
  const p2 = page.refreshPreview();
  previewNew.resolve(response({ ok: true, chars: 22 }));
  await p2;
  previewOld.resolve(response({ ok: true, chars: 11 }));
  await p1;
  assert.strictEqual(page.preview.chars, 22);

  page = xStudioPage();
  page.text = "Reviewed post";
  const confirm = deferred();
  let confirmations = 0, sends = 0;
  global.kazmaConfirm = () => { confirmations++; return confirm.promise; };
  page._mutating = async () => { sends++; return response({ ok: true }); };
  const pending = page.postNow();
  await page.postNow();
  assert.strictEqual(page.busy, true);
  assert.strictEqual(confirmations, 1);
  page.text = "Changed during confirmation";
  confirm.resolve(true);
  await pending;
  assert.strictEqual(sends, 0);
  assert.strictEqual(page.busy, false);

  page = xStudioPage();
  const approveConfirm = deferred();
  global.kazmaConfirm = () => approveConfirm.promise;
  const row = { summon_id: "123", approval_token: "old-revision", reply: "Reviewed reply" };
  const requests = [];
  page._mutating = async (method, url, body) => { requests.push({ method, url, body }); return response({ ok: false, error: "Stale revision" }, false); };
  page.loadConversations = async () => {};
  const approving = page.convAction("approve", row);
  await page.convAction("approve", row);
  row.approval_token = "new-revision";
  approveConfirm.resolve(true);
  await approving;
  assert.strictEqual(requests.length, 1);
  assert.strictEqual(requests[0].body.approval_token, "old-revision");
  assert.strictEqual(page.convBusy, "");

  page = xStudioPage();
  const cancelConfirm = deferred();
  global.kazmaConfirm = () => cancelConfirm.promise;
  const booking = { id: "operation", version: 1, can_cancel: true, summary: "Reviewed booking" };
  let sentVersion;
  page._mutating = async (method, url, body) => { sentVersion = body.expected_version; return response({ ok: false }, false); };
  const cancelling = page.cancel(booking);
  booking.version = 2;
  cancelConfirm.resolve(true);
  await cancelling;
  assert.strictEqual(sentVersion, 1);

  page = xStudioPage();
  global.fetch = async () => response({ ok: true, count: 2, next_cursor: "next", items: [
    { id: "held", due_at: 1, state: "awaiting_approval", can_cancel: true },
    { id: "unknown", due_at: 2, state: "outcome_unknown", needs_reconciliation: true },
  ] });
  await page.loadQueue();
  assert.strictEqual(page.queue.length, 2);
  assert.strictEqual(page.queue[1].state, "outcome_unknown");
  assert.strictEqual(page.queueCount, 2);
  assert.strictEqual(page.queueNext, "next");

  page = xStudioPage();
  const threadConfirm = deferred();
  global.kazmaConfirm = () => threadConfirm.promise;
  const thread = { id: "thread", revision: 7, approval_token: "reviewed-token",
    segments: [{ index: 0, text: "Reviewed segment", state: "awaiting_approval" }] };
  const threadRequests = [];
  global.fetch = async (url, options) => {
    if (options) threadRequests.push({ url, body: JSON.parse(options.body) });
    return response(options ? { ok: true } : { ok: true, threads: [] });
  };
  const publishing = page.threadAction(thread, "publish");
  await page.threadAction(thread, "publish");
  assert.strictEqual(page.threadBusy, true);
  thread.revision = 8; thread.approval_token = "changed-token";
  threadConfirm.resolve(true);
  await publishing;
  assert.strictEqual(threadRequests.length, 1);
  assert.strictEqual(threadRequests[0].body.expected_revision, 7);
  assert.strictEqual(threadRequests[0].body.approval_token, "reviewed-token");
  assert.strictEqual(page.threadBusy, false);
  console.log("X Studio retains stale data, ignores old responses, and binds confirmations to exact revisions.");
})().catch(error => { console.error(error); process.exitCode = 1; });
