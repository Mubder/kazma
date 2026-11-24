"use strict";
// What the browser kept by a moved chat's old id (Spaces S2-12): the open
// chat and the model chosen for it go to its new id; a message waiting to be
// sent, and how far the old chat's frames were read, end with it. And a tab
// attached to a chat that is gone leaves it: it never attaches to it again.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const JS = path.join(__dirname, "../../kazma-ui/kazma_ui/static/js");
const read = (name) => fs.readFileSync(path.join(JS, name), "utf8").replace(/\r\n/g, "\n");
const chat = read("chat.js");

/** One function declaration of chat.js, by name (its braces matched). */
function declared(name) {
  const at = chat.indexOf("  function " + name + "(");
  assert(at > 0, name + " was not found");
  return chat.slice(at, chat.indexOf("\n  }\n", at) + 5);
}

function browser(stored) {
  const store = Object.assign({}, stored);
  const context = {
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    SESSION_LS_KEY: "kazma.chatSessionId",
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(read("modules/delivery_cursor.js"), context);
  return { store, context };
}

function moved(stored, code) {
  const { store, context } = browser(stored);
  vm.runInContext(declared("_chatModelKey") + (code || declared("_moveBrowserState")) +
                  '\n_moveBrowserState("old", "new");', context);
  return store;
}

const KEPT = {
  "kazma.chatSessionId": "old",
  "kazma.chatModel.old": "openai:gpt-5-mini",
  "kazma_outbox_old": '{"text":"unsent"}',
  "kazma.deliveryCursor.old": "41",
  "kazma.chatModel.other": "groq:llama",
  "kazma.deliveryCursor.other": "7",
};
const AFTER = {
  "kazma.chatSessionId": "new",
  "kazma.chatModel.new": "openai:gpt-5-mini",
  "kazma.chatModel.other": "groq:llama",
  "kazma.deliveryCursor.other": "7",
};
assert.deepStrictEqual(moved(KEPT), AFTER);
// Another chat is open here: it stays open, and no model is invented.
assert.deepStrictEqual(moved({ "kazma.chatSessionId": "other" }), { "kazma.chatSessionId": "other" });
// Negative control: a page that keeps nothing by the new id.
assert.notDeepStrictEqual(moved(KEPT, "function _moveBrowserState() {}"), AFTER);

// The tab's attach to a chat that is gone.
function attachAnswered(data, code) {
  const did = [];
  const context = {
    _sseEpoch: 1, activeStream: {},
    _leaveDeletedChat: (said, text) => did.push(["leave", said, text]),
    _resyncDelivery: (why) => did.push(["resync", why]),
  };
  vm.createContext(context);
  vm.runInContext((code || declared("_defaultAttachCallbacks")) +
                  '\nvar answered = _defaultAttachCallbacks(1);', context);
  context.answered.onError("This chat was deleted.", data);
  return did;
}
assert.deepStrictEqual(attachAnswered({ code: "chat_deleted" }), [["leave", "This chat was deleted.", ""]]);
assert.deepStrictEqual(attachAnswered(undefined), [["resync", "sse-fail"]]);
// Negative control: the callbacks that only resync attach to it again.
const old = declared("_defaultAttachCallbacks").replace("data.code === 'chat_deleted'", "false");
assert.notStrictEqual(old, declared("_defaultAttachCallbacks"));
assert.deepStrictEqual(attachAnswered({ code: "chat_deleted" }, old), [["resync", "sse-fail"]]);

console.log("PASS: a moved chat's browser state, and an attach to a chat that is gone");
