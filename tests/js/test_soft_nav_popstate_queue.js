// A Back/Forward soft navigation joins the same queue as a click (nav.js).
//
// Found by the Settings agent of the UI refresh, 2026-11-09: the popstate
// handler started its swap on its own, so a click right after Back started a
// second swap at once and the superseded page's scripts ran on the new page's
// DOM (Memory's console threw on Time Travel; tests/e2e/test_bell_opens_the_page.py
// failed about 3 runs in 10). The click path chains on `navInFlight`; the
// popstate path must too. The browser proof is that e2e test, repeated.
'use strict';
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'kazma-ui', 'kazma_ui', 'static', 'js', 'modules', 'nav.js'),
  'utf8',
).replace(/\r\n/g, '\n');

function popstateBody(src) {
  const start = src.indexOf("window.addEventListener('popstate'");
  if (start < 0) throw new Error('no popstate handler in nav.js');
  let depth = 0;
  let i = src.indexOf('{', start);
  const open = i;
  for (; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') { depth -= 1; if (depth === 0) break; }
  }
  return src.slice(open, i + 1);
}

function joinsTheQueue(body) {
  return /navInFlight\s*=\s*\(navInFlight\s*\|\|\s*Promise\.resolve\(\)\)\.then\(/.test(body)
    && /softNav\(/.test(body);
}

let failed = 0;
function check(name, ok) {
  console.log((ok ? 'ok   ' : 'FAIL ') + name);
  if (!ok) failed += 1;
}

check('the popstate swap chains on navInFlight like a click', joinsTheQueue(popstateBody(SRC)));

const OLD = `window.addEventListener('popstate', async (event) => {
        softNav(location.pathname + location.search, { pushHistory: false })
            .then(updateActiveNav)
            .catch(() => window.location.reload());
    });`;
check('negative control: the old handler (a swap on its own) is caught', !joinsTheQueue(popstateBody(OLD)));

if (failed) { console.error(failed + ' failed'); process.exit(1); }
console.log('\nall popstate queue checks passed');
