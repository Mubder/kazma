/* Kazma MCP — Alpine.js app for MCP server management.
 *
 * Add Server is designed to "just work" for any MCP. Key behaviours:
 *
 *   1. shlex-style command parsing — quoted args with spaces survive
 *      (e.g. ``npx -y foo "path/with spaces"``) instead of being
 *      naively whitespace-split.
 *   2. Auto-rewrite of common install mistakes — many MCP server docs tell
 *      users to run ``npm install -g <pkg>`` (the INSTALL command). That
 *      installs and exits; the MCP handshake never happens; 0 tools.
 *      We detect install commands and rewrite them to the RUN form
 *      (``npx -y <pkg>``), surfacing the rewrite via a toast so the user
 *      knows what we did.
 *   3. Validate-on-add — before persisting the server, we POST to /test
 *      with the parsed config. If the test fails (spawn failure, 0 tools,
 *      handshake timeout), we surface the actual reason and DO NOT save.
 *      No more "saved silently, 0 tools, no idea why".
 */

function notify(message, type) {
    if (typeof window !== 'undefined' && typeof window.showToast === 'function') {
        window.showToast(message, type);
        return;
    }
    if (typeof console !== 'undefined') {
        console[type === 'error' ? 'error' : 'info']('[MCP] ' + message);
    }
}

/* Fallback dialog when the toast store is unavailable — OAuth errors MUST be
   visible or the button looks dead. */
function notifyOAuthError(message) {
    if (typeof window !== 'undefined' && typeof window.kazmaAlert === 'function') {
        window.kazmaAlert({ title: 'OAuth login failed', message: message, variant: 'btn-danger' });
        return;
    }
    notify(message, 'error');
}

/* ── shlex-style command parser (JS port of Python's shlex.split) ────────── */
function parseCommand(str) {
    // Handles: single quotes, double quotes (with \" escapes), backslash
    // escapes, and unquoted runs separated by whitespace. Matches POSIX
    // shell quoting closely enough for MCP command lines.
    var tokens = [];
    var current = '';
    var quote = null;            // null | '"' | "'"
    var hasToken = false;

    for (var i = 0; i < str.length; i++) {
        var ch = str[i];

        if (quote) {
            if (quote === '"' && ch === '\\') {
                // Inside double quotes, backslash escapes the next char.
                var next = str[i + 1];
                if (next !== undefined) {
                    current += next;
                    i++;
                    hasToken = true;
                    continue;
                }
            }
            if (ch === quote) {
                quote = null;     // closing quote
                hasToken = true;  // empty quoted string still counts as a token
            } else {
                current += ch;
                hasToken = true;
            }
            continue;
        }

        if (ch === '\\' ) {
            var next = str[i + 1];
            if (next !== undefined) {
                current += next;
                i++;
                hasToken = true;
            }
            continue;
        }

        if (ch === '"' || ch === "'") {
            quote = ch;
            hasToken = true;       // opening quote begins a token
            continue;
        }

        if (/\s/.test(ch)) {
            if (hasToken) {
                tokens.push(current);
                current = '';
                hasToken = false;
            }
            continue;
        }

        current += ch;
        hasToken = true;
    }
    if (hasToken) tokens.push(current);
    return tokens;
}

/* ── Auto-rewrite common install-command mistakes → run form ───────────────
 * Returns { command: [array], rewritten: bool, notice: string }.
 * If no rewrite is needed, command is the parsed tokens and rewritten=false. */
function autoRewriteCommand(commandStr) {
    var tokens = parseCommand(commandStr || '');
    if (tokens.length === 0) return { command: [], rewritten: false, notice: '' };

    // npm install [-g|--global] <pkg>  →  npx -y <pkg>
    if (tokens[0] === 'npm' && tokens[1] === 'install') {
        var pkg = null;
        for (var i = 2; i < tokens.length; i++) {
            if (tokens[i] === '-g' || tokens[i] === '--global' || tokens[i] === '--save') continue;
            if (tokens[i].startsWith('--')) continue; // skip flags
            pkg = tokens[i];
            break;
        }
        if (pkg) {
            return {
                command: ['npx', '-y', pkg],
                rewritten: true,
                notice: 'Rewrote "npm install ' + pkg + '" to the RUN command "npx -y ' + pkg + '" (npm install only installs the package — it doesn\'t start the MCP server).'
            };
        }
    }

    // pip install <pkg>  →  python -m <pkg-as-module>
    if (tokens[0] === 'pip' && tokens[1] === 'install') {
        var pipPkg = null;
        for (var j = 2; j < tokens.length; j++) {
            if (tokens[j].startsWith('--')) continue;
            if (tokens[j].includes('==') || tokens[j].includes('>=')) {
                pipPkg = tokens[j].split(/[=<>!]/)[0];
                break;
            }
            pipPkg = tokens[j];
            break;
        }
        if (pipPkg) {
            // Python packages and module names usually match (dashes → underscores).
            var mod = pipPkg.replace(/-/g, '_');
            return {
                command: ['python', '-m', mod],
                rewritten: true,
                notice: 'Rewrote "pip install ' + pipPkg + '" to the RUN command "python -m ' + mod + '".'
            };
        }
    }

    // pipx install <pkg>  →  pipx run <pkg>
    if (tokens[0] === 'pipx' && tokens[1] === 'install' && tokens[2]) {
        return {
            command: ['pipx', 'run', tokens[2]],
            rewritten: true,
            notice: 'Rewrote "pipx install ' + tokens[2] + '" to "pipx run ' + tokens[2] + '".'
        };
    }

    return { command: tokens, rewritten: false, notice: '' };
}

function removalError(response, result) {
    if (!response.ok) {
        return (result && result.error) || ('Request failed (' + response.status + ')');
    }
    if (!result || result.status !== 'ok') {
        return (result && result.error) || 'Server removal was not confirmed.';
    }
    return '';
}

async function removeMcpServer(button) {
    var endpoint = button.getAttribute('hx-delete');
    if (!endpoint) return;

    button.disabled = true;
    try {
        var response = await fetch(endpoint, {
            method: 'DELETE',
            headers: { 'Accept': 'application/json' }
        });
        var result = null;
        try {
            result = await response.json();
        } catch (parseError) {
            // Preserve the status-based message below for non-JSON failures.
        }
        var error = removalError(response, result);
        if (error) throw new Error(error);

        var card = button.closest('.mcp-card');
        if (card) card.remove();
        notify('Server removed', 'success');
    } catch (error) {
        notify('Failed to remove server: ' + error.message, 'error');
        button.disabled = false;
    }
}

if (typeof document !== 'undefined') {
    document.addEventListener('click', function(event) {
        var target = event.target;
        var button = target && target.closest
            ? target.closest('button[hx-delete^="/api/mcp/servers/"]')
            : null;
        if (!button) return;

        // The template's legacy htmx handler always toasts success after any
        // response. Take ownership before htmx dispatches so failures remain visible.
        event.preventDefault();
        event.stopImmediatePropagation();
        removeMcpServer(button);
    }, true);
}

function mcpApp() {
    return {
        showAddModal: false,
        adding: false,            // disable button during validate-then-save
        actionPending: '',        // start:<name> | stop:<name> | test:<name>
        addError: '',             // inline error during Add Server
        rewriteNotice: '',        // auto-rewrite explanation shown to user
        selectedPreset: '',       // preset dropdown selection (server id or '')
        presetCategories: [],     // [{name, presets}] from GET /api/mcp/presets
        newServer: {
            name: '',
            transport: 'stdio',
            commandStr: '',
            url: '',
            working_dir: '',
            env: {},               // {KEY: VALUE} entries edited inline
            authToken: '',
            trust: 'approval_required'
        },

        /* React to command edits: parse + auto-rewrite, surface any rewrite. */
        onCommandInput() {
            var r = autoRewriteCommand(this.newServer.commandStr);
            this.rewriteNotice = r.notice;
        },

        /* Fetch the preset catalog (grouped by category) from the backend.
         * Called once when the Add Server modal opens (x-init). The catalog
         * is cached server-side so repeated opens don't re-read the YAML. */
        async loadPresets() {
            if (this.presetCategories.length > 0) return;  // already loaded
            try {
                var resp = await fetch('/api/mcp/presets');
                var data = await resp.json();
                if (data.ok && data.categories) {
                    this.presetCategories = data.categories;
                }
            } catch (e) {
                // Presets are a convenience — if they fail to load, the
                // manual form still works fine.
                console.debug('[MCP] preset load failed:', e);
            }
        },

        /* When the user picks a preset from the dropdown, auto-fill all the
         * fields below. When they pick "— Custom —" (empty), clear them. */
        applyPreset() {
            if (!this.selectedPreset) {
                // Reset to manual/custom mode — clear all fields.
                this.resetNewServer();
                return;
            }
            // Find the preset across all categories.
            var preset = null;
            for (var i = 0; i < this.presetCategories.length; i++) {
                for (var j = 0; j < this.presetCategories[i].presets.length; j++) {
                    if (this.presetCategories[i].presets[j].id === this.selectedPreset) {
                        preset = this.presetCategories[i].presets[j];
                        break;
                    }
                }
                if (preset) break;
            }
            if (!preset) return;

            // Fill the form fields. command[] is joined into commandStr
            // (the field the user sees); addServer() re-parses it via shlex.
            this.newServer.name = preset.id;
            this.newServer.transport = preset.transport || 'stdio';
            this.newServer.commandStr = (preset.command || []).join(' ');
            this.newServer.url = preset.url || '';
            this.newServer.working_dir = '';
            this.newServer.authToken = '';
            this.newServer.trust = 'approval_required';

            // Pre-populate env var rows with the keys the preset needs
            // (values left empty for the user to fill — e.g. FIRECRAWL_API_KEY).
            var envKeys = preset.env_keys || [];
            this.newServer._envRows = envKeys.map(function(k) {
                return { key: k, value: '' };
            });

            // Clear any stale rewrite notice from a prior entry.
            this.rewriteNotice = '';
            this.addError = '';
        },

        /* Add Server: validate-then-save. The server is only persisted if
         * the test connection succeeds. This kills the "0 tools, no idea
         * why" failure mode — bad commands get caught before save, with a
         * specific error message. */
        async addServer() {
            this.addError = '';
            this.adding = true;
            try {
                var rewrite = autoRewriteCommand(this.newServer.commandStr);
                var server = {
                    name: (this.newServer.name || '').trim(),
                    transport: this.newServer.transport,
                    command: this.newServer.transport === 'stdio' ? rewrite.command : [],
                    url: (this.newServer.transport === 'sse' || this.newServer.transport === 'streamable_http') ? this.newServer.url : '',
                    working_dir: this.newServer.working_dir || null,
                    env: this._collectEnv(),
                    trust: this.newServer.trust || 'approval_required'
                };
                if ((this.newServer.transport === 'sse' || this.newServer.transport === 'streamable_http') && this.newServer.authToken) {
                    server.auth = { type: 'bearer', token: this.newServer.authToken };
                }

                // Basic client-side validation.
                if (!server.name) {
                    this.addError = 'Server name is required.';
                    return;
                }
                if (server.transport === 'stdio' && server.command.length === 0) {
                    this.addError = 'Command is required for stdio transport.';
                    return;
                }
                if ((server.transport === 'sse' || server.transport === 'streamable_http') && !server.url) {
                    this.addError = 'URL is required for HTTP-based transport.';
                    return;
                }

                // Surface the rewrite to the user before validating — they
                // should know we changed their input.
                if (rewrite.rewritten) {
                    notify(rewrite.notice, 'info');
                }

                // Validate-on-add: test the connection BEFORE persisting.
                // This catches install-command mistakes (which the rewrite
                // handles), missing binaries (ENOENT), bad API keys, and
                // 0-tools servers — all before the broken entry is saved.
                try {
                    var testResp = await fetch('/api/mcp/test-config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(server)
                    });
                    var testResult = await testResp.json();
                    if (!testResult.success) {
                        this.addError = 'Connection test failed: ' + (testResult.error || 'no error detail');
                        if (testResult.stderr) {
                            this.addError += '\nServer stderr:\n' + testResult.stderr.slice(0, 500);
                        }
                        notify('Test failed — server not saved. See inline error.', 'error');
                        return;
                    }
                    if (testResult.tool_count === 0) {
                        this.addError = 'Server connected but exposed 0 tools. This usually means the package name is wrong or the server failed to initialise. Not saving.';
                        notify('0 tools — server not saved', 'warning');
                        return;
                    }
                } catch (testErr) {
                    // Test endpoint shouldn't fail (it returns 200 with
                    // {success: false}), but be defensive.
                    this.addError = 'Could not validate server: ' + testErr.message;
                    return;
                }

                // Test passed — persist.
                try {
                    var resp = await fetch('/api/mcp/servers', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(server)
                    });
                    var result = await resp.json();
                    if (result.status === 'ok') {
                        notify('Server added — ' + (testResult ? testResult.tool_count : '?') + ' tools', 'success');
                        this.showAddModal = false;
                        this.resetNewServer();
                        location.reload();
                    } else {
                        this.addError = result.error || 'Save failed (unknown reason)';
                    }
                } catch (saveErr) {
                    this.addError = 'Save failed: ' + saveErr.message;
                }
            } finally {
                this.adding = false;
            }
        },

        /* Collect env var entries from the inline editor into a dict.
         * The UI binds rows to this.newServer._envRows = [{key, value}, ...];
         * falls back to the plain env dict if not in use. */
        _collectEnv() {
            if (Array.isArray(this.newServer._envRows)) {
                var env = {};
                for (var i = 0; i < this.newServer._envRows.length; i++) {
                    var row = this.newServer._envRows[i];
                    if (row.key && row.key.trim()) {
                        env[row.key.trim()] = row.value || '';
                    }
                }
                return env;
            }
            return this.newServer.env || {};
        },

        /* Inline env editor helpers. */
        addEnvRow() {
            if (!Array.isArray(this.newServer._envRows)) this.newServer._envRows = [];
            this.newServer._envRows.push({ key: '', value: '' });
        },
        removeEnvRow(idx) {
            if (Array.isArray(this.newServer._envRows)) this.newServer._envRows.splice(idx, 1);
        },

        async startServer(name) {
            if (this.actionPending) return;
            this.actionPending = 'start:' + name;
            try {
                var resp = await fetch('/api/mcp/servers/' + encodeURIComponent(name) + '/start', {
                    method: 'POST'
                });
                var result = await resp.json();
                if (result.status === 'ok') {
                    notify('Server started with ' + result.tool_count + ' tools', 'success');
                    location.reload();
                } else {
                    notify('Failed: ' + (result.error || 'Unable to start server'), 'error');
                }
            } catch (e) {
                notify('Failed to start server: ' + e.message, 'error');
            } finally {
                this.actionPending = '';
            }
        },

        async stopServer(name) {
            if (this.actionPending) return;
            this.actionPending = 'stop:' + name;
            try {
                var resp = await fetch('/api/mcp/servers/' + encodeURIComponent(name) + '/stop', {
                    method: 'POST'
                });
                var result = await resp.json();
                if (result.status === 'ok') {
                    notify('Server stopped', 'info');
                    location.reload();
                } else {
                    notify('Failed: ' + (result.error || 'Unable to stop server'), 'error');
                }
            } catch (e) {
                notify('Failed to stop server: ' + e.message, 'error');
            } finally {
                this.actionPending = '';
            }
        },

        async testServer(name) {
            if (this.actionPending) return;
            this.actionPending = 'test:' + name;
            notify('Testing connection...', 'info');
            try {
                var resp = await fetch('/api/mcp/servers/' + encodeURIComponent(name) + '/test', {
                    method: 'POST'
                });
                var result = await resp.json();
                if (result.success) {
                    notify('Connected! ' + result.tool_count + ' tools found', 'success');
                } else {
                    var msg = 'Test failed: ' + (result.error || 'no detail');
                    if (result.stderr) msg += '\n' + String(result.stderr).slice(0, 400);
                    notify(msg, 'error');
                }
            } catch (e) {
                notify('Test failed: ' + e.message, 'error');
            } finally {
                this.actionPending = '';
            }
        },

        async oauthLogin(name) {
            if (this.actionPending) return;
            this.actionPending = 'oauth:' + name;
            notify('Starting OAuth login — complete the sign-in in your browser…', 'info');
            try {
                var resp = await fetch('/api/mcp/servers/' + encodeURIComponent(name) + '/oauth/start', {
                    method: 'POST'
                });
                var result = await resp.json();
                if (result.status === 'ok' && result.authorization_url) {
                    // Open the provider's login page in a NEW TAB so the flow
                    // is visibly started even if the backend couldn't open a
                    // browser itself (headless / remote server).
                    window.open(result.authorization_url, '_blank', 'noopener');
                    notify('Browser login opened. Return here after signing in, then press Start.', 'success');
                } else if (result.status === 'ok') {
                    notify('Browser login opened. Return here after signing in, then press Start.', 'success');
                } else {
                    notifyOAuthError(result.error || 'unknown error');
                }
            } catch (e) {
                notifyOAuthError(e.message || String(e));
            } finally {
                this.actionPending = '';
            }
        },

        resetNewServer() {
            this.newServer = {
                name: '',
                transport: 'stdio',
                commandStr: '',
                url: '',
                working_dir: '',
                env: {},
                _envRows: [],
                authToken: '',
                trust: 'approval_required'
            };
            this.addError = '';
            this.rewriteNotice = '';
            this.selectedPreset = '';
        }
    };
}
if (typeof window !== "undefined") window.mcpApp = mcpApp;

// Expose the parser/rewriter for unit testing (kazma-cli tests import via Node).
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        parseCommand: parseCommand,
        autoRewriteCommand: autoRewriteCommand,
        removalError: removalError,
        mcpApp: mcpApp,
        notify: notify
    };
}
