/** Settings mixin: agent — agent, safety, memory backends, embedder, time travel */
(function (root) {
    "use strict";
    root.KazmaSettingsMixins = root.KazmaSettingsMixins || {};
    root.KazmaSettingsMixins.agent = function () {
        return {
        async saveAgent() {
            this.saving = true;
            try {
                await fetch('/api/settings/agent', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.agent),
                });
                showToast('Agent settings saved', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        async setPersonality(name) {
            this.agent.personality = name;
            const p = this.personalities.find(p => p.name === name);
            if (p && p.system_prompt) this.agent.system_prompt = p.system_prompt;
        },

        async saveSafety() {
            this.saving = true;
            try {
                await fetch('/api/settings/agent/safety', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.safety),
                });
                showToast('Safety settings saved', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        async saveContext() {
            this.saving = true;
            try {
                await fetch('/api/settings/agent/context', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.context),
                });
                showToast('Context settings saved', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        async saveNonstop() {
            this.saving = true;
            try {
                const resp = await fetch('/api/settings/agent/nonstop', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.nonstop),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(function() { return {}; });
                    showToast('Save failed: ' + (err.detail || resp.status), 'error');
                    this.saving = false;
                    return;
                }
                showToast('Non-stop settings saved — applies live', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        async saveTenantMode() {
            this.saving = true;
            try {
                await fetch('/api/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify([{ key: 'memory.tenant_mode', value: this.memoryTenantMode, category: 'memory' }]),
                });
                showToast('Memory isolation mode saved — takes effect next turn', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        async saveMemoryKbMerge() {
            try {
                await fetch('/api/settings/memory/merge-kb', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({
                        merge_knowledge_into_chat: !!this.memoryMergeKb,
                        promote_kb_to_episodes: !!this.memoryPromoteKb,
                        smart_search: !!this.memorySmartSearch,
                        explain_recall: !!this.memoryExplainRecall,
                    }),
                });
                showToast('Memory / Knowledge settings saved', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
        },

        async saveMemoryBackends() {
            this.memoryBackendsSaving = true;
            this.memoryBackendsStatus = 'Saving…';
            try {
                const resp = await fetch('/api/settings/memory/backends', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.memoryBackends),
                });
                const data = await resp.json();
                if (data.ok) {
                    if (data.backends) {
                        const b = data.backends;
                        this.memoryBackends.embedder = Object.assign({}, this.memoryBackends.embedder, b.embedder || {});
                        this.memoryBackends.vector = Object.assign({}, this.memoryBackends.vector, b.vector || {});
                        this.memoryBackends.graph = Object.assign({}, this.memoryBackends.graph, b.graph || {});
                        this.memoryBackends.state = Object.assign({}, this.memoryBackends.state, b.state || {});
                    }
                    this.memoryBackendsStatus = 'Saved. Next: Test Neo4j, then Sync beliefs → Neo4j.';
                    showToast('Memory backends saved', 'success');
                } else {
                    this.memoryBackendsStatus = data.error || 'Save failed';
                    showToast('Save failed', 'error');
                }
            } catch (e) {
                this.memoryBackendsStatus = 'Save failed';
                showToast('Save failed', 'error');
            }
            this.memoryBackendsSaving = false;
        },

        async testMemoryNeo4j() {
            this.memoryNeo4jStatus = 'Testing Neo4j…';
            this.memoryNeo4jOk = false;
            try {
                const resp = await fetch('/api/settings/memory/backends/test-neo4j', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ graph: this.memoryBackends.graph || {} }),
                });
                const data = await resp.json();
                if (data.ok) {
                    this.memoryNeo4jOk = true;
                    this.memoryNeo4jStatus = 'Connected · ' + (data.latency_ms || 0) + 'ms — ' + (data.detail || '');
                    showToast('Neo4j connected', 'success');
                } else {
                    this.memoryNeo4jStatus = (data.error || 'Failed') + (data.hint ? (' — ' + data.hint) : '');
                    showToast('Neo4j test failed', 'error');
                }
            } catch (e) {
                this.memoryNeo4jStatus = 'Test error: ' + e;
            }
        },

        async syncMemoryNeo4j() {
            this.memoryNeo4jStatus = 'Syncing beliefs to Neo4j…';
            this.memoryNeo4jOk = false;
            try {
                const resp = await fetch('/api/settings/memory/backends/sync-neo4j', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                if (data.ok) {
                    this.memoryNeo4jOk = true;
                    this.memoryNeo4jStatus = data.detail || ('Synced ' + (data.synced || 0) + ' beliefs');
                    showToast('Synced ' + (data.synced || 0) + ' beliefs to Neo4j', 'success');
                } else {
                    this.memoryNeo4jStatus = data.error || 'Sync failed';
                    showToast('Neo4j sync failed', 'error');
                }
            } catch (e) {
                this.memoryNeo4jStatus = 'Sync error: ' + e;
            }
        },

        async syncMemoryState() {
            this.memoryStateSyncStatus = 'Syncing beliefs + episodes to Postgres…';
            this.memoryStateSyncOk = false;
            try {
                const resp = await fetch('/api/settings/memory/backends/sync-postgres', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                if (data.ok) {
                    this.memoryStateSyncOk = true;
                    this.memoryStateSyncStatus = data.detail || ('Synced ' + (data.synced || 0) + ' rows');
                    showToast(data.detail || ('Synced ' + (data.synced || 0) + ' rows to Postgres'), 'success');
                } else {
                    this.memoryStateSyncStatus = data.error || 'Sync failed';
                    showToast('Postgres sync failed', 'error');
                }
            } catch (e) {
                this.memoryStateSyncStatus = 'Sync error: ' + e;
            }
        },

        async testMemoryEmbed() {
            this.memoryBackendsStatus = 'Testing embedder…';
            try {
                const resp = await fetch('/api/settings/memory/backends/test-embed', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                this.memoryBackendsStatus = data.ok
                    ? ('Embed OK · ' + (data.latency_ms || 0) + 'ms · dim ' + (data.dim || '?'))
                    : ('Embed failed: ' + (data.error || 'unknown'));
            } catch (e) {
                this.memoryBackendsStatus = 'Embed test error';
            }
        },

        async testMemoryVector() {
            this.memoryBackendsStatus = 'Testing vector backend…';
            try {
                const resp = await fetch('/api/settings/memory/backends/test-vector', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                this.memoryBackendsStatus = data.ok
                    ? ('Vector OK · ' + (data.provider || '') + ' · ' + (data.latency_ms || 0) + 'ms')
                    : ('Vector failed: ' + (data.error || 'unknown'));
            } catch (e) {
                this.memoryBackendsStatus = 'Vector test error';
            }
        },

        async resetMemoryBackendsLocal() {
            try {
                const resp = await fetch('/api/settings/memory/backends/reset-local', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                if (data.backends) {
                    const b = data.backends;
                    this.memoryBackends = {
                        mode: b.mode || 'local',
                        embedder: Object.assign({}, this.memoryBackends.embedder, b.embedder || {}),
                        vector: Object.assign({}, this.memoryBackends.vector, b.vector || {}),
                        graph: Object.assign({}, this.memoryBackends.graph, b.graph || {}),
                        state: Object.assign({}, this.memoryBackends.state, b.state || {}),
                        failover: Object.assign({}, this.memoryBackends.failover, b.failover || {}),
                    };
                }
                this.memoryBackendsStatus = 'Reset to local defaults';
                showToast('Memory backends reset to local', 'success');
            } catch (e) {
                showToast('Reset failed', 'error');
            }
        },

        async rebuildMemoryEmbeddings() {
            const ok = window.kazmaConfirm
                ? await window.kazmaConfirm({
                    title: 'Rebuild embeddings?',
                    message: 'Re-embed episodes/beliefs for the current model. May take minutes.',
                })
                : confirm('Rebuild embeddings?');
            if (!ok) return;
            try {
                const resp = await fetch('/api/settings/memory/backends/rebuild', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                this.memoryBackendsStatus = data.ok ? 'Rebuild started (see status on Embedder page)' : (data.error || 'Failed');
                showToast(data.ok ? 'Rebuild started' : 'Rebuild failed', data.ok ? 'success' : 'error');
            } catch (e) {
                showToast('Rebuild failed', 'error');
            }
        },

        async saveLogging() {
            this.saving = true;
            try {
                await fetch('/api/settings/system/logging', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.logging),
                });
                showToast('Logging settings saved (restart for rotation changes)', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        async loadProxy() {
            try {
                const data = await this._fetch('/api/settings/proxy');
                if (data) {
                    this.proxy = {
                        provider: data.provider || 'none',
                        host: data.host || 'portal.anyip.io',
                        port: String(data.port || '1080'),
                        username: data.username || '',
                        password: data.password || '',
                        network: data.network || 'mixed',
                        country: data.country || '',
                        session_sticky: !!data.session_sticky,
                    };
                }
            } catch (e) { /* keep defaults */ }
            this.proxyTestResult = null;
        },

        async saveDocuments() {
            this.documentsSaving = true;
            this.documentsStatus = '';
            try {
                const body = {
                    enabled: !!this.documents.enabled,
                    shadow: !!this.documents.shadow,
                    default_authoritative: !!this.documents.default_authoritative,
                    intake_max_bytes: Number(this.documents.intake_max_bytes),
                    intake_max_files: Number(this.documents.intake_max_files),
                    ocr_enabled: !!this.documents.ocr_enabled,
                    worker_timeout_seconds: Number(this.documents.worker_timeout_seconds),
                    worker_memory_mb: Number(this.documents.worker_memory_mb),
                    capacity_storage_free_floor_bytes: Number(this.documents.capacity_storage_free_floor_bytes),
                    security_malware_scan: this.documents.security_malware_scan || 'auto',
                    security_malware_fail_closed: !!this.documents.security_malware_fail_closed,
                    gc_enabled: !!this.documents.gc_enabled,
                    indexing_enabled: !!this.documents.indexing_enabled,
                };
                const resp = await fetch('/api/settings/documents', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!resp.ok) throw new Error('save failed');
                const refreshed = await this._fetch('/api/settings/documents');
                if (refreshed && !refreshed.error) Object.assign(this.documents, refreshed);
                this.documentsStatus = 'Saved';
                if (window.showToast) window.showToast('Document settings saved', 'success');
            } catch (e) {
                this.documentsStatus = 'Save failed';
                if (window.showToast) window.showToast('Document settings save failed', 'error');
            } finally {
                this.documentsSaving = false;
            }
        },

        async saveProxy() {
            this.saving = true;
            try {
                await fetch('/api/settings/proxy', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.proxy),
                });
                showToast(I18N?.proxy_saved || 'Proxy settings saved', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        async testProxy() {
            this.proxyTesting = true;
            this.proxyTestResult = null;
            try {
                // Save first so the test uses the just-entered credentials.
                await fetch('/api/settings/proxy', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.proxy),
                });
                const resp = await fetch('/api/settings/proxy/test', { method: 'POST' });
                this.proxyTestResult = await resp.json();
            } catch (e) {
                this.proxyTestResult = { success: false, error: e.message };
            }
            this.proxyTesting = false;
        },

        async loadEmbedder() {
            try {
                const data = await this._fetch('/api/settings/embedder');
                if (!data) return;
                this.embedderStatus = {
                    config: data.config || {},
                    active: data.active || null,
                    db: data.db || { episodes: {}, beliefs: {} },
                };
                const store = data.store || {};
                if (store.model) {
                    this.embedder = {
                        provider: store.provider || 'local',
                        model: store.model,
                        dim: store.dim ? Number(store.dim) : 1024,
                        base_url: store.base_url || '',
                        api_key_env: store.api_key_env || 'KAZMA_EMBED_API_KEY',
                        _preset: this.embedderPresets.some(p => p.model === store.model) ? store.model : '__custom__',
                    };
                } else {
                    // Nothing persisted in the store — mirror the effective config.
                    const cfg = data.config || {};
                    this.embedder = {
                        provider: cfg.provider || 'local',
                        model: cfg.model || 'BAAI/bge-m3',
                        dim: cfg.dim || 1024,
                        base_url: cfg.base_url || '',
                        api_key_env: cfg.api_key_env || 'KAZMA_EMBED_API_KEY',
                        _preset: this.embedderPresets.some(p => p.model === (cfg.model || '')) ? cfg.model : '__custom__',
                    };
                }
                if (data.rebuild) this.embedderRebuildStatus = { state: 'idle', model: '', total: 0, done: 0, error: null, ...data.rebuild };
                if (this.embedderRebuildStatus.state === 'running') this.startEmbedderRebuildPoll();
            } catch (e) {
                console.error('[Settings] Failed to load embedder status:', e);
            }
        },

        applyEmbedderPreset() {
            const preset = this.embedderPresets.find(p => p.model === this.embedder._preset);
            if (preset) {
                this.embedder.model = preset.model;
                this.embedder.dim = preset.dim;
            }
        },

        async saveEmbedder() {
            if (!this.embedder.model || !String(this.embedder.model).trim()) {
                showToast('Model is required', 'error');
                return;
            }
            this.embedderSaving = true;
            try {
                const resp = await fetch('/api/settings/embedder', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.embedder),
                });
                const data = await resp.json();
                if (data.status === 'error') {
                    showToast(data.error || 'Save failed', 'error');
                } else {
                    showToast('Embedder settings saved. Restart the server to apply.', 'success');
                    this.loadEmbedder();
                }
            } catch (e) {
                showToast('Save failed: ' + e.message, 'error');
            }
            this.embedderSaving = false;
        },

        async rebuildEmbeddings() {
            const ok = await window.kazmaConfirm({
                title: 'Rebuild embeddings?',
                message: 'All memory rows not in the current vector space will be re-encoded with the active model. This runs in the background and can take a while for large stores. A backup is created automatically first.',
                danger: true,
            });
            if (!ok) return;
            this.embedderRebuilding = true;
            try {
                const resp = await fetch('/api/settings/embedder/rebuild', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                if (data.status === 'ok') {
                    showToast('Rebuild started in the background.', 'success');
                    this.startEmbedderRebuildPoll();
                } else if (data.status === 'already') {
                    showToast('A rebuild is already running.', 'info');
                    this.startEmbedderRebuildPoll();
                } else {
                    showToast(data.detail || 'Failed to start rebuild', 'error');
                }
            } catch (e) {
                showToast('Rebuild failed to start: ' + e.message, 'error');
            }
            this.embedderRebuilding = false;
        },

        startEmbedderRebuildPoll() {
            if (this._embedderPollTimer) clearInterval(this._embedderPollTimer);
            this._embedderPollTimer = setInterval(async () => {
                try {
                    const status = await this._fetch('/api/settings/embedder/rebuild');
                    if (status) this.embedderRebuildStatus = status;
                    if (status && status.state !== 'running') {
                        clearInterval(this._embedderPollTimer);
                        this._embedderPollTimer = null;
                        this.loadEmbedder(); // refresh DB composition
                        if (status.state === 'done') {
                            showToast('Embedding rebuild complete.', 'success');
                        } else if (status.state === 'error') {
                            showToast('Embedding rebuild failed: ' + (status.error || 'unknown error'), 'error');
                        }
                    }
                } catch (e) { /* server still up, keep polling */ }
            }, 3000);
        },

        activeEmbedderClass() {
            if (this.embedderStatus.active && this.embedderStatus.active.class) {
                return this.embedderStatus.active.class.replace('Embedder', '');
            }
            return '—';
        },

        embedderRestartNeeded() {
            const cfg = this.embedderStatus.config || {};
            const active = this.embedderStatus.active;
            if (!active) return true; // singleton not instantiated yet — restart harmless
            const modelMatch = !active.model || active.model === (cfg.model || '');
            const dimMatch = active.dim == cfg.dim;
            return !(modelMatch && dimMatch);
        },

        embedderDbVersions() {
            const db = this.embedderStatus.db || {};
            const versions = new Set([...Object.keys(db.episodes || {}), ...Object.keys(db.beliefs || {})]);
            return [...versions].map(v => ({
                version: v,
                episodes: (db.episodes || {})[v] || 0,
                beliefs: (db.beliefs || {})[v] || 0,
            })).sort((a, b) => (b.episodes + b.beliefs) - (a.episodes + a.beliefs));
        },

        rebuildPercent() {
            const total = this.embedderRebuildStatus.total || 0;
            if (!total) return 0;
            return Math.min(100, Math.round((this.embedderRebuildStatus.done / total) * 100));
        },

        /** Turn-completion desktop notifications (Turn Delivery V2 P4). */
        async loadTurnNotify() {
            try {
                const data = await this._fetch('/api/notifications/turn-complete');
                if (data && typeof data.enabled === 'boolean') {
                    this.turnNotify = { enabled: data.enabled };
                    // Mirror so already-open chat tabs pick up the operator
                    // value without a reload.
                    try {
                        localStorage.setItem('kazma.notifyOnComplete', data.enabled ? '1' : '0');
                    } catch (e) { /* ignore */ }
                }
            } catch (e) {
                console.error('[Settings] Failed to load turn notification setting:', e);
            }
        },

        async saveTurnNotify() {
            this.turnNotifySaving = true;
            try {
                const enabled = !!this.turnNotify.enabled;
                const resp = await fetch('/api/settings/single', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({
                        key: 'notifications.turn_complete',
                        value: enabled ? '1' : '0',
                        category: 'notifications',
                    }),
                });
                const data = await resp.json();
                if (data.status === 'error') {
                    showToast(data.error || 'Save failed', 'error');
                } else {
                    try {
                        localStorage.setItem('kazma.notifyOnComplete', enabled ? '1' : '0');
                    } catch (e) { /* ignore */ }
                    showToast('Notification preference saved.', 'success');
                }
            } catch (e) {
                showToast('Save failed: ' + e.message, 'error');
            }
            this.turnNotifySaving = false;
        },

        async loadTimeTravel() {            try {
                const data = await this._fetch('/api/settings/time_travel');
                if (!data) return;
                const store = data.store || {};
                this.timeTravel = {
                    max_snapshots: store.max_snapshots != null ? Number(store.max_snapshots) : 50,
                    retention_days: store.retention_days != null ? Number(store.retention_days) : 30,
                    auto_maintain: store.auto_maintain != null ? Boolean(store.auto_maintain) : true,
                };
                this.timeTravelEffective = data.effective != null ? Number(data.effective) : 50;
            } catch (e) {
                console.error('[Settings] Failed to load time travel settings:', e);
            }
        },

        timeTravelRestartNeeded() {
            return Number(this.timeTravel.max_snapshots) !== Number(this.timeTravelEffective);
        },

        async saveTimeTravel() {
            const n = Number(this.timeTravel.max_snapshots);
            if (!n || n < 1) {
                showToast('Snapshots per thread must be at least 1', 'error');
                return;
            }
            const rd = Number(this.timeTravel.retention_days);
            if (!rd || rd < 1) {
                showToast('Retention days must be at least 1', 'error');
                return;
            }
            this.timeTravelSaving = true;
            try {
                const resp = await fetch('/api/settings/time_travel', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ max_snapshots: n, retention_days: rd, auto_maintain: !!this.timeTravel.auto_maintain }),
                });
                const data = await resp.json();
                if (data.status === 'error') {
                    showToast(data.error || 'Save failed', 'error');
                } else {
                    showToast('Time travel settings saved. Restart the server to apply.', 'success');
                }
            } catch (e) {
                showToast('Save failed: ' + e.message, 'error');
            }
            this.timeTravelSaving = false;
        },

        async loadCronTimezone() {
            try {
                const data = await this._fetch('/api/settings/cron-timezone');
                if (!data) return;
                this.cronTz = {
                    value: String(data.timezone || 'UTC'),
                    source: String(data.source || 'default'),
                };
            } catch (e) {
                console.error('[Settings] Failed to load schedule timezone:', e);
            }
        },

        async saveCronTimezone() {
            const value = String(this.cronTz.value || '').trim();
            if (!value) return;
            this.cronTzSaving = true;
            try {
                const resp = await fetch('/api/settings/single', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ key: 'cron.timezone', value: value }),
                });
                if (!resp.ok) {
                    let detail = 'Save failed';
                    try {
                        const err = await resp.json();
                        detail = err.detail || detail;
                    } catch (e0) { /* non-JSON body */ }
                    showToast(detail, 'error');
                    return;
                }
                await this.loadCronTimezone();
                showToast('Schedule timezone saved — applies to newly scheduled tasks.', 'success');
            } catch (e) {
                showToast('Save failed: ' + e.message, 'error');
            }
            this.cronTzSaving = false;
        },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
