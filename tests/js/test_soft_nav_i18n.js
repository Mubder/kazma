"use strict";

const assert = require("assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ui = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui");
const base = fs.readFileSync(path.join(ui, "templates", "base.html"), "utf8");
const nav = fs.readFileSync(path.join(ui, "static", "js", "modules", "nav.js"), "utf8");
assert.match(base, /<script id="kazma-i18n-data" type="application\/json">\s*{{ translations_json\|safe }}\s*<\/script>/);
const bootstrap = base.match(/^\s*window\.KAZMA_I18N\s*=.*;$/m)[0];
const start = nav.indexOf("        // Refresh i18n from the new page");
const end = nav.indexOf("        // In the page's own order", start);
assert.ok(start >= 0 && end > start);
const refresh = nav.slice(start, end);
const catalog = {
    sample: { en: 'quotes " and ; braces } newline\n', ar: "نص عربي؛ ٧ × ٨ = ٥٦" },
    hostile: { en: '</script><script>window.executed = true</script>' },
};
const data = JSON.stringify(catalog).replace(/</g, "\\u003c");
const legacyScript = {
    getAttribute: () => null,
    textContent: `window.KAZMA_I18N = ${data};\nwindow.KAZMA_LANG = "ar";\nwindow.executed = true;`,
};

function run(source, payload = data) {
    const warnings = [];
    const window = { KAZMA_I18N: { retained: true } };
    const doc = {
        getElementById: (id) => id === "kazma-i18n-data" && payload !== null
            ? { textContent: payload } : null,
        querySelectorAll: () => [legacyScript],
    };
    vm.runInNewContext(source, { window, doc, document: doc,
        console: { warn: (...args) => warnings.push(args) } });
    return { window, warnings };
}

for (const source of [bootstrap, refresh]) {
    const result = run(source);
    assert.equal(JSON.stringify(result.window.KAZMA_I18N), JSON.stringify(catalog));
    assert.equal(result.warnings.length, 0);
    assert.equal(result.window.executed, undefined);
}
for (const payload of [null, "{invalid"]) {
    const result = run(refresh, payload);
    assert.equal(JSON.stringify(result.window.KAZMA_I18N), '{"retained":true}');
    assert.equal(result.warnings.length, payload === null ? 0 : 1);
}

// Negative control: the former parser consumed the bootstrap's helpers too.
const old = run(`
    const text = doc.querySelectorAll('script')[0].textContent.trim();
    const m = /^\\s*window\\.KAZMA_I18N\\s*=\\s*/.exec(text);
    try { window.KAZMA_I18N = JSON.parse(text.slice(m[0].length).replace(/;\\s*$/, '')); }
    catch (error) { console.warn(error); }
`);
assert.equal(old.warnings.length, 1);
assert.equal(JSON.stringify(old.window.KAZMA_I18N), '{"retained":true}');
console.log("PASS: initial and soft-nav translations parse inert JSON without executing helpers");
