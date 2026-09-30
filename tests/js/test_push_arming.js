/**
 * Web Push is armed from the send gesture, and a first "Allow" subscribes.
 *
 * Web Push reaches a tab the browser discarded (plan P5). Its only arming
 * call lived in the chat store's WebSocket send path, and turns have been
 * sent over SSE since 2026-08-25, so no browser ever subscribed (found
 * 2026-09-30 while removing that path, AUD-026). It is armed now by
 * KazmaTurnVisibility.armPermission, which chat.js calls on every send: when
 * permission is already granted, and when the user grants the prompt it
 * shows. Second defect: ensureSubscribed marked itself tried BEFORE the
 * permission check, so the first send (permission still "default") used the
 * page's one try and the "Allow" that followed subscribed nothing.
 *
 * Runs the REAL turn_visibility.js and push_client.js in a sandbox with fake
 * browser APIs; each scenario is a fresh page.
 *
 * Run: node tests/js/test_push_arming.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const MODULES = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "modules");
const visibilitySrc = fs.readFileSync(path.join(MODULES, "turn_visibility.js"), "utf8");
const pushSrc = fs.readFileSync(path.join(MODULES, "push_client.js"), "utf8");

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}

function jsonResponse(obj) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(obj) });
}

/** A fresh page: fake Notification, push stack and fetch; the two modules loaded. */
function page({ permission, answer }) {
  const subscribes = [];
  const Notification = function () {};
  Notification.permission = permission;
  Notification.requestPermission = function () {
    Notification.permission = answer;
    return Promise.resolve(answer);
  };
  const registration = {
    pushManager: {
      getSubscription: async () => null,
      subscribe: async () => ({ toJSON: () => ({ endpoint: "https://push.example/sub" }) }),
    },
  };
  const ctx = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    atob: (s) => Buffer.from(s, "base64").toString("binary"),
    Promise,
    Uint8Array,
    Notification,
    PushManager: function () {},
    navigator: {
      serviceWorker: { register: async () => registration, ready: Promise.resolve(registration) },
    },
    document: { title: "Kazma", hidden: false, addEventListener() {} },
    localStorage: { getItem: () => null },
    fetch: (url) => {
      if (url === "/api/notifications/turn-complete") return jsonResponse({ enabled: true });
      if (url === "/api/push/vapid-public-key") {
        return jsonResponse({ available: true, public_key: "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U" });
      }
      return Promise.resolve({ ok: false, json: () => Promise.resolve(null) });
    },
    kazmaSave: (url, opts) => { subscribes.push({ url, opts }); return Promise.resolve({ ok: true }); },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(pushSrc, ctx);
  vm.runInContext(visibilitySrc, ctx);
  return { ctx, subscribes };
}

async function settle() {
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
}

(async () => {
  // 1. The first send asks; the user allows; the page subscribes.
  {
    const { ctx, subscribes } = page({ permission: "default", answer: "granted" });
    ctx.KazmaTurnVisibility.armPermission();
    await settle();
    ok("allow on the first ask subscribes", subscribes.length === 1, JSON.stringify(subscribes));
    ok("the subscription goes to the push route",
      subscribes[0] && subscribes[0].url === "/api/push/subscribe");
  }

  // 2. Already granted (a later visit): the send arms it.
  {
    const { ctx, subscribes } = page({ permission: "granted", answer: "granted" });
    ctx.KazmaTurnVisibility.armPermission();
    ctx.KazmaTurnVisibility.armPermission();
    await settle();
    ok("granted permission subscribes on send, once per page", subscribes.length === 1,
      String(subscribes.length));
  }

  // 3. Negative controls: a refusal or a denied browser subscribes nothing.
  {
    const refused = page({ permission: "default", answer: "denied" });
    refused.ctx.KazmaTurnVisibility.armPermission();
    await settle();
    ok("a refused prompt subscribes nothing", refused.subscribes.length === 0);
    const denied = page({ permission: "denied", answer: "denied" });
    denied.ctx.KazmaTurnVisibility.armPermission();
    await settle();
    ok("a denied browser subscribes nothing", denied.subscribes.length === 0);
  }

  // 4. The old ordering bug: a call before the grant must not use up the page's try.
  {
    const { ctx, subscribes } = page({ permission: "default", answer: "granted" });
    await ctx.KazmaPushClient.ensureSubscribed();  // permission still "default"
    ok("nothing subscribes before permission", subscribes.length === 0);
    ctx.Notification.permission = "granted";
    await ctx.KazmaPushClient.ensureSubscribed();
    await settle();
    ok("a grant after an early call still subscribes", subscribes.length === 1,
      String(subscribes.length));
  }

  if (fail) {
    console.log(fail + " failure(s)");
    process.exit(1);
  }
  console.log("all push arming checks passed");
})().catch((e) => { console.error(e); process.exit(1); });
