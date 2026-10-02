/* Removing an MCP server asks first (2026-10-02).

   A removal also forgets the keys saved for the server (the vault), and the
   card's Remove sent the DELETE at the first click. removeMcpServer now asks
   through kazmaConfirm, naming the server; declining sends nothing. The
   request goes to the card's own (URL-encoded) path. */
"use strict";

const assert = require("assert");
const path = require("path");

const { removeMcpServer } = require(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "mcp.js",
));

function fakeCard(name, endpoint) {
  const card = {
    removed: false,
    remove() { this.removed = true; },
    querySelector: (sel) => (sel === "h3" ? { textContent: `  ${name}  ` } : null),
  };
  const button = {
    disabled: false,
    getAttribute: (attr) => (attr === "hx-delete" ? endpoint : null),
    closest: (sel) => (sel === ".mcp-card" ? card : null),
  };
  return { card, button };
}

(async () => {
  const requests = [];
  global.fetch = async (url, opts) => {
    requests.push([url, opts && opts.method]);
    return { ok: true, status: 200, json: async () => ({ status: "ok" }) };
  };
  const asked = [];
  let answer = false;
  global.window = {
    kazmaConfirm: async (opts) => { asked.push(opts); return answer; },
    showToast: () => {},
  };

  // Declined: asked, with the name; nothing sent; the card stays.
  let { card, button } = fakeCard("my server", "/api/mcp/servers/my%20server");
  await removeMcpServer(button);
  assert.strictEqual(asked.length, 1, "removal asks first");
  assert.ok(asked[0].title.includes("my server"), asked[0].title);
  assert.strictEqual(asked[0].danger, true);
  assert.deepStrictEqual(requests, [], "a declined removal sends nothing");
  assert.strictEqual(card.removed, false);

  // Confirmed: one DELETE to the card's own path; the card goes.
  answer = true;
  ({ card, button } = fakeCard("my server", "/api/mcp/servers/my%20server"));
  await removeMcpServer(button);
  assert.deepStrictEqual(requests, [["/api/mcp/servers/my%20server", "DELETE"]]);
  assert.strictEqual(card.removed, true);

  console.log("MCP removal asks first and sends nothing when declined.");
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
