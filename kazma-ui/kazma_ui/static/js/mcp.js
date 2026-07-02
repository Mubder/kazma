/* Kazma MCP — Alpine.js app for MCP server management */

function mcpApp() {
    return {
        showAddModal: false,
        newServer: {
            name: '',
            transport: 'stdio',
            commandStr: '',
            url: '',
            working_dir: '',
            env: {},
            authToken: '',
            trust: 'approval_required'
        },

        async addServer() {
            var server = {
                name: this.newServer.name,
                transport: this.newServer.transport,
                command: this.newServer.transport === 'stdio'
                    ? this.newServer.commandStr.split(/\s+/).filter(Boolean)
                    : [],
                url: this.newServer.transport === 'sse' ? this.newServer.url : '',
                working_dir: this.newServer.working_dir || null,
                env: this.newServer.env,
                trust: this.newServer.trust || 'approval_required'
            };
            // Inject bearer token as auth config for SSE servers
            if (this.newServer.transport === 'sse' && this.newServer.authToken) {
                server.auth = { type: 'bearer', token: this.newServer.authToken };
            }

            try {
                var resp = await fetch('/api/mcp/servers', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(server)
                });
                var result = await resp.json();
                if (result.status === 'ok') {
                    showToast('Server added', 'success');
                    this.showAddModal = false;
                    this.resetNewServer();
                    location.reload();
                } else {
                    showToast('Failed: ' + (result.error || ''), 'error');
                }
            } catch (e) {
                showToast('Failed to add server', 'error');
            }
        },

        async startServer(name) {
            try {
                var resp = await fetch('/api/mcp/servers/' + encodeURIComponent(name) + '/start', {
                    method: 'POST'
                });
                var result = await resp.json();
                if (result.status === 'ok') {
                    showToast('Server started with ' + result.tool_count + ' tools', 'success');
                    location.reload();
                } else {
                    showToast('Failed: ' + (result.error || ''), 'error');
                }
            } catch (e) {
                showToast('Failed to start server', 'error');
            }
        },

        async stopServer(name) {
            try {
                var resp = await fetch('/api/mcp/servers/' + encodeURIComponent(name) + '/stop', {
                    method: 'POST'
                });
                var result = await resp.json();
                if (result.status === 'ok') {
                    showToast('Server stopped', 'info');
                    location.reload();
                } else {
                    showToast('Failed: ' + (result.error || ''), 'error');
                }
            } catch (e) {
                showToast('Failed to stop server', 'error');
            }
        },

        async testServer(name) {
            showToast('Testing connection...', 'info');
            try {
                var resp = await fetch('/api/mcp/servers/' + encodeURIComponent(name) + '/test', {
                    method: 'POST'
                });
                var result = await resp.json();
                if (result.success) {
                    showToast('Connected! ' + result.tool_count + ' tools found', 'success');
                } else {
                    showToast('Test failed: ' + result.error, 'error');
                }
            } catch (e) {
                showToast('Test failed', 'error');
            }
        },

        resetNewServer() {
            this.newServer = {
                name: '',
                transport: 'stdio',
                commandStr: '',
                url: '',
                working_dir: '',
                env: {}
            };
        }
    };
}
