/**
 * A notification in the bell opens the page it is about (2026-11-08, the
 * owner: "if there is a notification, clicking on that notification takes
 * you to the related page"). The rows were plain text: nothing to click.
 *
 * Runs the REAL store literal from modules/stores.js: href() takes the
 * server's link when it is a path of this site, else a "link:/..." callback,
 * else the Dashboard; never another site's address. The header's rows are
 * links to it.
 *
 * Run: node tests/js/test_bell_links.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui");
const src = fs.readFileSync(path.join(root, "static", "js", "modules", "stores.js"), "utf8").replace(/\r\n/g, "\n");
const open = "Alpine.store('notifications', ";
const at = src.indexOf(open);
const end = src.indexOf("\n        });", at);
let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("the notifications store is extractable", at >= 0 && end > at);
const store = (0, eval)("(" + src.slice(at + open.length, end + "\n        }".length) + ")");

ok("the server's page", store.href({ link: "/memory" }) === "/memory");
ok("a deep link keeps its query", store.href({ link: "/settings?tab=agent" }) === "/settings?tab=agent");
ok("an older alert's link callback", store.href({ callback_id: "link:/research?session=s1" }) === "/research?session=s1");
ok("nothing named: the Dashboard", store.href({ title: "x" }) === "/dashboard");
ok("no item: the Dashboard", store.href(null) === "/dashboard");
for (const bad of ["//evil.example/x", "https://evil.example/", "javascript:alert(1)", "/\\evil.example"]) {
  ok("never another site: " + bad, store.href({ link: bad }) === "/dashboard", store.href({ link: bad }));
  ok("never another site by callback: " + bad, store.href({ callback_id: "link:" + bad }) === "/dashboard");
}

const header = fs.readFileSync(path.join(root, "templates", "components", "header.html"), "utf8");
const rows = header.slice(header.indexOf('x-for="item in $store.notifications.visibleItems()"'));
const row = rows.slice(0, rows.indexOf("</template>"));
ok("each row is a link to its page", /<a class="dropdown-item notif-item" :href="\$store\.notifications\.href\(item\)"/.test(row), row.slice(0, 200));
ok("a click marks it read and closes the bell", row.includes('@click="$store.notifications.openItem(item)"'));
ok("the bell lists what is still to see", header.includes('x-for="item in $store.notifications.visibleItems()"'));

// Read (2026-11-08, the owner: "the notification still there with a bell
// badge showing"): opening one marks it read; the badge counts the unread,
// read news leaves, a read problem stays (dimmed) until it is fixed.
{
  const posted = [];
  const fresh = (0, eval)("(" + src.slice(at + open.length, end + "\n        }".length) + ")");
  globalThis.fetch = (url, init) => { posted.push([url, JSON.parse(init.body), init.keepalive]); return Promise.resolve({ ok: true }); };
  fresh.items = [
    { id: "done", title: "Research completed", severity: "SUCCESS", timestamp: 3 },
    { id: "mem", title: "Memory recall degraded", severity: "WARNING", timestamp: 2 },
    { id: "old", title: "Seen elsewhere", severity: "INFO", timestamp: 1, read: true },
  ];
  fresh.count = fresh.unreadItems().length;
  ok("the badge counts the unread", fresh.count === 2, fresh.count);
  ok("read news is not listed", fresh.visibleItems().map((i) => i.id).join() === "done,mem");
  fresh.openItem(fresh.items[1]);
  ok("opening one marks it read", fresh.count === 1 && fresh.isRead(fresh.items[1]));
  ok("it is recorded on the server, past the page change", posted.length === 1
    && posted[0][0] === "/api/alerts/read" && posted[0][1].ids.join() === "mem" && posted[0][2] === true);
  ok("a read problem stays, after the unread", fresh.visibleItems().map((i) => i.id).join() === "done,mem");
  ok("opening closes the bell", fresh.open === false);
  fresh.markAllRead();
  ok("Mark all as read clears the badge", fresh.count === 0, fresh.count);
  ok("only what was unread is sent", posted.length === 2 && posted[1][1].ids.join() === "done");
  ok("read news has left; the problem stays", fresh.visibleItems().map((i) => i.id).join() === "mem");
  fresh.markAllRead();
  ok("nothing left to mark sends nothing", posted.length === 2);
  // Control: the store before 2026-11-08 counted every alert.
  const OLD_COUNT = (items) => items.length;
  ok("control: the old count kept the badge up", OLD_COUNT(fresh.items) === 3);
}
// Control: the rows before 2026-11-08 were text, with nothing to follow.
const OLD = '<div class="dropdown-item" style="cursor:default;flex-direction:column;align-items:flex-start;gap:2px">';
ok("control: the old row had no link", !/href/.test(OLD));
ok("the old row is gone", !header.includes(OLD));

if (fail) { console.log(fail + " failure(s)"); process.exit(1); }
console.log("all ok");
