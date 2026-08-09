/* Regression test: MCP card buttons must call $parent.<action> because each
   card has its own Alpine x-data scope and cannot see methods on mcpApp(). */

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(
    __dirname,
    '..',
    'kazma-ui',
    'kazma_ui',
    'templates',
    'mcp.html'
);

const html = fs.readFileSync(htmlPath, 'utf8');

const required = [
    '$parent.startServer',
    '$parent.stopServer',
    '$parent.testServer',
];

let failed = false;
for (const marker of required) {
    if (!html.includes(marker)) {
        console.error(`Missing required card action binding: ${marker}`);
        failed = true;
    }
}

if (!failed) {
    console.log('MCP card actions are bound to $parent scope.');
}
process.exitCode = failed ? 1 : 0;
