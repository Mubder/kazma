/* Regression test: oauthLogin() must visibly act on every outcome —
   opens the authorization URL in a new tab on success, and shows an
   alert-capable error path on failure. */

const path = require('path');

const { mcpApp } = require(path.join(
    __dirname,
    '..',
    'kazma-ui',
    'kazma_ui',
    'static',
    'js',
    'mcp.js',
));

let openedUrl = null;
let alerted = null;
let toastCount = 0;

global.window = {
    open: (url) => { openedUrl = url; },
    showToast: () => { toastCount += 1; },
    kazmaAlert: (opts) => { alerted = opts.message; },
};
global.fetch = async (url) => ({
    json: async () => url.includes('/oauth/start')
        ? { status: 'ok', authorization_url: 'https://www.facebook.com/v26.0/dialog/oauth?client_id=x' }
        : {},
});

(async () => {
    const app = mcpApp();
    await app.oauthLogin('Meta Developer Tools');
    if (openedUrl !== 'https://www.facebook.com/v26.0/dialog/oauth?client_id=x') {
        console.error('FAIL: expected window.open with authorization_url, got:', openedUrl);
        process.exitCode = 1;
        return;
    }

    // Failure path: DCR rejected → error must be visible (alert fallback).
    global.fetch = async () => ({
        json: async () => ({ status: 'error', error: 'DCR closed by provider' }),
    });
    await app.oauthLogin('Meta Developer Tools');
    if (alerted !== 'DCR closed by provider') {
        console.error('FAIL: expected kazmaAlert with provider error, got:', alerted);
        process.exitCode = 1;
        return;
    }

    console.log('oauthLogin opens authorization URL and surfaces provider errors.');
})();
