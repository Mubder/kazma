"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/documents.js"), "utf8");
const responses = new Map();
const sandbox = {window: {fetch: async (url) => {
  const response = responses.get(url);
  if (response instanceof Error) throw response;
  const status = response?.httpStatus || 200;
  return {ok: status >= 200 && status < 300, status,
    headers: {get: () => response?.contentType || "application/json"},
    json: async () => response?.body === undefined ? response : response.body};
}}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/auth-guard.js"), "utf8"), sandbox);
vm.runInContext(source, sandbox);

async function run() {
  const page = sandbox.documentsPage();
  responses.set("/api/documents/ops/capacity", {ok: true, capacity: {status: "ok"}});
  responses.set("/api/documents/ops/metrics", {ok: true, metrics: {status: "ok"}});
  responses.set("/api/documents/ops/readiness", {ok: true, readiness: {status: "ready", accepting_durable_writes: true}});
  responses.set("/api/documents/ops/audit?limit=25", {ok: true, events: []});
  await page.loadOps();
  assert.equal(page.capacity.status, "ok");
  assert.equal(page.readiness.status, "ready");
  for (const endpoint of ["capacity", "metrics", "readiness"]) {
    responses.set(`/api/documents/ops/${endpoint}`, {ok: false, code: "document_policy_unavailable"});
  }
  await page.loadOps();
  assert.equal(page.capacity.status, "unavailable");
  assert.equal(page.ops, null);
  assert.equal(page.readiness.status, "unavailable");
  assert.equal(page.readiness.accepting_durable_writes, false);
  assert.equal(page.readiness.degraded_reasons[0], "document_policy_unavailable");
  responses.set("/api/documents/ops/readiness", {httpStatus: 503, body: {ok: false, code: "document_policy_unavailable"}});
  await page.loadOps();
  assert.equal(page.readiness.status, "unavailable");
  assert.equal(page.readiness.accepting_durable_writes, false);
  responses.set("/api/documents/ops/readiness", null);
  await page.loadOps();
  assert.equal(page.readiness.status, "unavailable");
  responses.set("/api/documents/ops/readiness", {httpStatus: 502, contentType: "text/html", body: "<html>proxy</html>"});
  await page.loadOps();
  assert.equal(page.readiness.status, "unavailable");
  responses.set("/api/documents/ops/readiness", new Error("test network outage"));
  await page.loadOps();
  assert.equal(page.readiness.status, "unavailable");
  assert.equal(page.readiness.degraded_reasons[0], "readiness_unavailable");
  console.log("Documents ops: successful status is cleared on policy and network outages");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
