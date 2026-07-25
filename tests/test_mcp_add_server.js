/**
 * Tests for mcp.js command-parsing + auto-rewrite (Phase 1A).
 *
 * Run with: node tests/test_mcp_add_server.js
 * Exits non-zero on any failure.
 */
const path = require('path');
const fs = require('fs');

// Load mcp.js as a module — it self-exports parseCommand/autoRewriteCommand.
const mcpSrc = fs.readFileSync(
    path.join(__dirname, '..', 'kazma-ui', 'kazma_ui', 'static', 'js', 'mcp.js'),
    'utf8',
);
// Strip the Alpine `mcpApp()` def + browser-only guards before evaluating.
const moduleApi = (() => {
    const sandbox = { module: { exports: {} }, exports: {} };
    // The file's bottom block does the export when run under Node's require,
    // but since we're evaluating the source, do it manually.
    const wrapped = `(function(module, exports){ ${mcpSrc}\n return module.exports; })`;
    // eslint-disable-next-line no-eval
    return eval(wrapped)(sandbox.module, sandbox.exports);
})();

const { parseCommand, autoRewriteCommand } = moduleApi;

let failures = 0;

function assertEqual(actual, expected, msg) {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a !== e) {
        console.error(`✗ ${msg}\n  expected ${e}\n  got      ${a}`);
        failures++;
    } else {
        console.log(`✓ ${msg}`);
    }
}

// ── parseCommand: shlex-style ──────────────────────────────────────────────

assertEqual(parseCommand(''), [], 'empty string → empty array');
assertEqual(parseCommand('npx -y firecrawl-mcp'), ['npx', '-y', 'firecrawl-mcp'], 'simple tokens');
assertEqual(
    parseCommand('npx -y server "/path with spaces"'),
    ['npx', '-y', 'server', '/path with spaces'],
    'double-quoted arg with spaces preserved',
);
assertEqual(
    parseCommand("npx -y server '/path with spaces'"),
    ['npx', '-y', 'server', '/path with spaces'],
    'single-quoted arg with spaces preserved',
);
assertEqual(
    parseCommand('npx -y server "arg with \\"escaped\\" quotes"'),
    ['npx', '-y', 'server', 'arg with "escaped" quotes'],
    'escaped double-quotes inside double-quoted string',
);
assertEqual(parseCommand('   leading whitespace'), ['leading', 'whitespace'], 'leading whitespace trimmed');
assertEqual(parseCommand('trailing whitespace   '), ['trailing', 'whitespace'], 'trailing whitespace trimmed');
assertEqual(parseCommand('a   b    c'), ['a', 'b', 'c'], 'multiple spaces collapsed');
assertEqual(parseCommand('""'), [''], 'empty quoted string → single empty token');

// ── autoRewriteCommand: install → run ──────────────────────────────────────

const r1 = autoRewriteCommand('npm install -g firecrawl-mcp');
assertEqual(r1.command, ['npx', '-y', 'firecrawl-mcp'], 'npm install -g → npx -y');
assertEqual(r1.rewritten, true, 'npm install marked as rewritten');

const r2 = autoRewriteCommand('npm install @modelcontextprotocol/server-filesystem');
assertEqual(r2.command, ['npx', '-y', '@modelcontextprotocol/server-filesystem'], 'npm install (scoped pkg) → npx -y');
assertEqual(r2.rewritten, true, 'npm install scoped marked rewritten');

const r3 = autoRewriteCommand('pip install mcp-server-time');
assertEqual(r3.command, ['python', '-m', 'mcp_server_time'], 'pip install → python -m (dash→underscore)');
assertEqual(r3.rewritten, true, 'pip install marked rewritten');

const r4 = autoRewriteCommand('pipx install some-mcp');
assertEqual(r4.command, ['pipx', 'run', 'some-mcp'], 'pipx install → pipx run');

// No rewrite when already a run command
const r5 = autoRewriteCommand('npx -y firecrawl-mcp');
assertEqual(r5.command, ['npx', '-y', 'firecrawl-mcp'], 'already-run command unchanged');
assertEqual(r5.rewritten, false, 'already-run not flagged rewritten');

// No rewrite for unknown
const r6 = autoRewriteCommand('docker run -it some-mcp');
assertEqual(r6.command, ['docker', 'run', '-it', 'some-mcp'], 'docker run unchanged');
assertEqual(r6.rewritten, false, 'docker run not rewritten');

console.log('');
if (failures === 0) {
    console.log('All mcp.js Phase 1A tests passed.');
    process.exit(0);
} else {
    console.error(`${failures} test(s) failed.`);
    process.exit(1);
}
