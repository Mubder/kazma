/* Regression test for MCP Start/Test action wiring without a toast global. */

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

let requestedUrl = '';
const originalFetch = global.fetch;
const originalWindow = global.window;

delete global.window;
global.fetch = async (url) => {
    requestedUrl = url;
    return {
        json: async () => ({ success: true, tool_count: 2 }),
    };
};

(async () => {
    const app = mcpApp();
    await app.testServer('example-server');

    if (requestedUrl !== '/api/mcp/servers/example-server/test') {
        console.error(`Expected test endpoint request, received: ${requestedUrl}`);
        process.exitCode = 1;
    } else if (app.actionPending !== '') {
        console.error('MCP action state was not cleared after test completion');
        process.exitCode = 1;
    } else {
        console.log('MCP test action requests the endpoint without window.showToast.');
    }

    global.fetch = originalFetch;
    if (originalWindow !== undefined) {
        global.window = originalWindow;
    }
})();
