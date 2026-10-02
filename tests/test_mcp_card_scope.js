/* The MCP card's buttons reach mcpApp()'s methods (2026-10-02).

   Each card has its own x-data ({ expanded: false }) inside mcpApp(). In
   Alpine 3 an expression is evaluated against the element's data stack --
   the card's data, then its parent's -- so a card button calls
   startServer(...) by name. This file used to REQUIRE "$parent.startServer"
   etc., on the belief that the card could not see the parent's methods;
   Alpine 3 has no $parent, so Start, Stop, Test and OAuth login threw
   "ReferenceError: $parent is not defined" on every click (live, found
   switching the sequential-thinking server).

   The test takes each button's real @click expression from mcp.html, with
   the real mcpApp() from mcp.js, and evaluates it the way Alpine does: an
   async function over `with (scope)`, where scope looks a name up in the
   card's data first, then in mcpApp(). The shipped $parent form is the
   negative control: it must throw the same ReferenceError.
   tests/test_alpine_templates.py holds the class (only Alpine 3's magics). */

const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', 'kazma-ui', 'kazma_ui');
const html = fs.readFileSync(path.join(root, 'templates', 'mcp.html'), 'utf8');
const { mcpApp } = require(path.join(root, 'static', 'js', 'mcp.js'));

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

// Alpine 3's mergeProxies: the closest data first, then its parents'.
function dataStack(...layers) {
    return new Proxy({}, {
        has: (_t, key) => layers.some((l) => key in l),
        get: (_t, key) => {
            const owner = layers.find((l) => key in l);
            if (!owner) return undefined;
            const value = owner[key];
            return typeof value === 'function' ? value.bind(owner) : value;
        },
        set: (_t, key, value) => {
            (layers.find((l) => key in l) || layers[0])[key] = value;
            return true;
        },
    });
}

function evaluate(expression, scope) {
    return new AsyncFunction('scope', 'with (scope) { await (' + expression + '); }')(scope);
}

// The card buttons' @click expressions, Jinja filled in for a server "demo".
const clicks = {};
for (const m of html.matchAll(/@click='([^']*)'/g)) {
    const expr = m[1].replace(/\{\{\s*server\.name\|tojson\s*\}\}/g, '"demo"');
    const action = (expr.match(/(\w+)\(/) || [])[1];
    if (action) clicks[action] = expr;
}

const expected = {
    startServer: '/api/mcp/servers/demo/start',
    stopServer: '/api/mcp/servers/demo/stop',
    testServer: '/api/mcp/servers/demo/test',
    oauthLogin: '/api/mcp/servers/demo/oauth/start',
};

let requested = [];
global.fetch = async (url) => {
    requested.push(url);
    return { json: async () => ({ status: 'error', error: 'stub', success: false }) };
};
global.location = { reload() {} };

(async () => {
    let failed = false;
    const fail = (msg) => { console.error(msg); failed = true; };

    for (const [action, url] of Object.entries(expected)) {
        const expr = clicks[action];
        if (!expr) { fail(`mcp.html has no card button calling ${action}`); continue; }
        requested = [];
        try {
            await evaluate(expr, dataStack({ expanded: false }, mcpApp()));
        } catch (e) {
            fail(`${action}: "${expr}" threw ${e.name}: ${e.message}`);
            continue;
        }
        if (requested[0] !== url) fail(`${action}: requested ${requested[0]}, expected ${url}`);
    }

    // Negative control: the shipped binding throws, as it did on the page.
    try {
        await evaluate('$parent.startServer("demo")', dataStack({ expanded: false }, mcpApp()));
        fail('negative control: $parent.startServer(...) did not throw');
    } catch (e) {
        if (!(e instanceof ReferenceError)) fail(`negative control threw ${e.name}, not ReferenceError`);
    }

    if (!failed) console.log('MCP card buttons reach mcpApp() through the data stack; $parent throws.');
    process.exitCode = failed ? 1 : 0;
})();
