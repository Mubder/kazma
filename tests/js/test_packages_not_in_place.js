/* Settings -> Packages is not green over a part that did not install (2026-11-19).
 *
 * The owner's fresh Full install had no Docker, SearXNG, Neo4j or document
 * worker, and every row of the page was green: it listed Python extras only,
 * and Neo4j's Python driver was installed. The route now marks an extra
 * whose part is not in place (``part_missing``) and names those parts
 * (``install.not_in_place``); the page says so. This drives the page's own
 * methods, with the old badge expression as the negative control.
 */
"use strict";
const assert = require("assert");
const path = require("path");
global.window = globalThis;
require(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/settings_ops.js"));

const page = Object.assign({
  pkgInstall: { mode: "full", at: 1, chosen: 23, total: 23, new_in_express: [],
                not_in_place: ["Docker", "Neo4j memory graph"] },
}, global.KazmaSettingsMixins.ops());

assert.strictEqual(page.pkgNotInPlace(), "Docker, Neo4j memory graph");
page.pkgInstall = { mode: null, at: null, chosen: 0, total: 0 };
assert.strictEqual(page.pkgNotInPlace(), "", "an install with no record names nothing");

const neo4j = { name: "neo4j", installed: true, chosen: true, part_missing: "Neo4j memory graph" };
assert.strictEqual(page.pkgExtraBadge(neo4j), "part_missing");
assert.strictEqual(page.pkgBadgeClass(neo4j), "badge-warning");
const cases = [
  [{ installed: true, part_missing: "" }, "installed", "badge-success"],
  [{ installed: false, chosen: false }, "not_chosen", "badge-secondary"],
  [{ installed: false, partial: true }, "partial", "badge-warning"],
  [{ installed: false }, "missing", "badge-danger"],
];
for (const [extra, state, cls] of cases) {
  assert.strictEqual(page.pkgExtraBadge(extra), state);
  assert.strictEqual(page.pkgBadgeClass(extra), cls);
}
// The page words each state from the catalog (the part-not-in-place one first).
const html = require("fs").readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/templates/settings.html"), "utf8");
assert.ok(html.includes("pkgExtraBadge(extra) === 'part_missing'") && html.includes("t('packages.part_not_in_place_badge')"));

// Negative control: the badge before the fix was green for the Neo4j row.
function oldBadge(extra) {
  return extra.installed ? "badge-success"
    : (extra.chosen === false ? "badge-secondary" : (extra.partial ? "badge-warning" : "badge-danger"));
}
assert.strictEqual(oldBadge(neo4j), "badge-success");

console.log("Packages names the parts not in place, and a failed part's extra is not green.");
