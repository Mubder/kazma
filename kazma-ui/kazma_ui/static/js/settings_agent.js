/** Settings mixin: agent — agent, safety, memory backends, embedder, time travel */
(function (root) {
    "use strict";
    // Text built here: the catalog's text in the page's language, else the
    // English given; {name} placeholders filled from vars.
    function _k(key, en, vars) {
        var f = root && root.kazmaT;
        if (typeof f === "function") return f(key, en, vars);
        var s = en;
        if (vars) for (var v in vars) s = s.split("{" + v + "}").join(String(vars[v]));
        return s;
    }
    root.KazmaSettingsMixins = root.KazmaSettingsMixins || {};
    root.KazmaSettingsMixins.agent = function () {
        return {
        async saveAgent() {
            this.saving = true;
            try {
                await window.kazmaSave('/api/settings/agent', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.agent),
                });
                showToast(window.t ? window.t('settings.agent_saved') : 'Agent settings saved', 'success');
            } catch (e) {
                showToast(window.t ? window.t('settings.save_failed') : 'Save failed', 'error');
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
                await window.kazmaSave('/api/settings/agent/safety', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.safety),
                });
                showToast(window.t ? window.t('settings.safety_saved') : 'Safety settings saved', 'success');
                await this.loadSoulPending();
            } catch (e) {
                showToast(window.t ? window.t('settings.save_failed') : 'Save failed', 'error');
            }
            this.saving = false;
        },

        async loadSoulPending() {
            try {
                const resp = await fetch('/api/commitment/soul/pending', {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json().catch(function () { return {}; });
                this.soulPending = (data && data.pending) || [];
            } catch (e) {
                this.soulPending = [];
            }
        },

        async confirmSoul(cid) {
            try {
                const resp = await fetch('/api/commitment/soul/' + encodeURIComponent(cid) + '/confirm', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json().catch(function () { return {}; });
                if (resp.ok && data.confirmed) {
                    showToast(window.t ? window.t('settings.soul_confirmed') : 'Soul delta confirmed', 'success');
                } else {
                    showToast(data.error || (window.t ? window.t('settings.save_failed') : 'Save failed'), 'error');
                }
                await this.loadSoulPending();
            } catch (e) {
                showToast(window.t ? window.t('settings.save_failed') : 'Save failed', 'error');
            }
        },

        async rejectSoul(cid) {
            try {
                const resp = await fetch('/api/commitment/soul/' + encodeURIComponent(cid) + '/reject', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json().catch(function () { return {}; });
                if (resp.ok && data.rejected) {
                    showToast(window.t ? window.t('settings.soul_rejected') : 'Soul delta rejected', 'success');
                } else {
                    showToast(data.error || (window.t ? window.t('settings.save_failed') : 'Save failed'), 'error');
                }
                await this.loadSoulPending();
            } catch (e) {
                showToast(window.t ? window.t('settings.save_failed') : 'Save failed', 'error');
            }
        },

        async saveContext() {
            this.saving = true;
            try {
                await window.kazmaSave('/api/settings/agent/context', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.context),
                });
                showToast(window.t ? window.t('settings.context_saved') : 'Context settings saved', 'success');
            } catch (e) {
                showToast(window.t ? window.t('settings.save_failed') : 'Save failed', 'error');
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
                    showToast(_k('settings.agentjs.save_failed', 'Save failed: ') + (err.detail || resp.status), 'error');
                    this.saving = false;
                    return;
                }
                showToast(_k('settings.agentjs.non_stop_settings_saved_applies', 'Non-stop settings saved — applies live'), 'success');
            } catch (e) {
                showToast(_k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
            }
            this.saving = false;
        },

        async saveTenantMode() {
            this.saving = true;
            try {
                await window.kazmaSave('/api/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify([{ key: 'memory.tenant_mode', value: this.memoryTenantMode, category: 'memory' }]),
                });
                showToast(_k('settings.agentjs.memory_isolation_mode_saved_takes', 'Memory isolation mode saved — takes effect next turn'), 'success');
            } catch (e) {
                showToast(_k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
            }
            this.saving = false;
        },

        async loadAboutMe() {
            try {
                const r = await window.kazmaSave('/api/memory/v2/profile');
                this.aboutMe = this.aboutMeSaved = (r && r.about) || '';
                if (r && r.max_chars) this.aboutMeMax = r.max_chars;
                this.aboutMeError = '';
            } catch (e) {
                this.aboutMeError = (e && e.message) || 'Could not load About me';
            }
        },

        async saveAboutMe() {
            this.aboutMeSaving = true;
            this.aboutMeError = '';
            try {
                const r = await window.kazmaSave('/api/memory/v2/profile', {
                    method: 'PUT',
                    body: { about: this.aboutMe || '' },
                });
                if (!r || !r.ok) throw new Error((r && r.error) || _k('settings.agentjs.save_failed_2', 'Save failed'));
                this.aboutMe = this.aboutMeSaved = r.about || '';
                showToast(this.t ? this.t('settings.about_me_saved') : 'Saved', 'success');
            } catch (e) {
                this.aboutMeError = (e && e.message) || 'Save failed';
            } finally {
                this.aboutMeSaving = false;
            }
        },

        async saveMemoryKbMerge() {
            try {
                await window.kazmaSave('/api/settings/memory/merge-kb', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({
                        merge_knowledge_into_chat: !!this.memoryMergeKb,
                        promote_kb_to_episodes: !!this.memoryPromoteKb,
                        smart_search: !!this.memorySmartSearch,
                        explain_recall: !!this.memoryExplainRecall,
                    }),
                });
                showToast(_k('settings.agentjs.memory_knowledge_settings_saved', 'Memory / Knowledge settings saved'), 'success');
            } catch (e) {
                showToast(_k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
            }
        },

        async saveMemoryBackends() {
            this.memoryBackendsSaving = true;
            this.memoryBackendsStatus = _k('settings.agentjs.st_saving', 'Saving…');
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
                    this.memoryBackendsStatus = _k('settings.agentjs.st_saved_next', 'Saved. Next: Test Neo4j, then Sync beliefs → Neo4j.');
                    showToast(_k('settings.agentjs.memory_backends_saved', 'Memory backends saved'), 'success');
                } else {
                    this.memoryBackendsStatus = data.error || _k('settings.agentjs.st_save_failed', 'Save failed');
                    showToast(_k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
                }
            } catch (e) {
                this.memoryBackendsStatus = _k('settings.agentjs.st_save_failed', 'Save failed');
                showToast(_k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
            }
            this.memoryBackendsSaving = false;
        },

        async testMemoryNeo4j() {
            this.memoryNeo4jStatus = _k('settings.agentjs.st_testing_neo4j', 'Testing Neo4j…');
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
                    this.memoryNeo4jStatus = _k('settings.agentjs.st_neo4j_connected', 'Connected · {ms}ms — {detail}', { ms: data.latency_ms || 0, detail: data.detail || '' });
                    showToast(_k('settings.agentjs.neo4j_connected', 'Neo4j connected'), 'success');
                } else {
                    this.memoryNeo4jStatus = (data.error || _k('settings.agentjs.status_failed', 'Failed')) + (data.hint ? (' — ' + data.hint) : '');
                    showToast(_k('settings.agentjs.neo4j_test_failed', 'Neo4j test failed'), 'error');
                }
            } catch (e) {
                this.memoryNeo4jStatus = _k('settings.agentjs.st_test_error', 'Test error: {error}', { error: e });
            }
        },

        async syncMemoryNeo4j() {
            this.memoryNeo4jStatus = _k('settings.agentjs.st_syncing_neo4j', 'Syncing beliefs to Neo4j…');
            this.memoryNeo4jOk = false;
            try {
                const resp = await fetch('/api/settings/memory/backends/sync-neo4j', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                if (data.ok) {
                    this.memoryNeo4jOk = true;
                    this.memoryNeo4jStatus = data.detail || _k('settings.agentjs.st_synced_beliefs', 'Synced {n} beliefs', { n: data.synced || 0 });
                    showToast(_k('settings.agentjs.synced_neo4j', 'Synced {n} beliefs to Neo4j', { n: data.synced || 0 }), 'success');
                } else {
                    this.memoryNeo4jStatus = data.error || _k('settings.agentjs.st_sync_failed', 'Sync failed');
                    showToast(_k('settings.agentjs.neo4j_sync_failed', 'Neo4j sync failed'), 'error');
                }
            } catch (e) {
                this.memoryNeo4jStatus = _k('settings.agentjs.st_sync_error', 'Sync error: {error}', { error: e });
            }
        },

        async syncMemoryState() {
            this.memoryStateSyncStatus = _k('settings.agentjs.st_syncing_postgres', 'Syncing beliefs + episodes to Postgres…');
            this.memoryStateSyncOk = false;
            try {
                const resp = await fetch('/api/settings/memory/backends/sync-postgres', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                if (data.ok) {
                    this.memoryStateSyncOk = true;
                    this.memoryStateSyncStatus = data.detail || _k('settings.agentjs.st_synced_rows', 'Synced {n} rows', { n: data.synced || 0 });
                    showToast(data.detail || ('Synced ' + (data.synced || 0) + ' rows to Postgres'), 'success');
                } else {
                    this.memoryStateSyncStatus = data.error || _k('settings.agentjs.st_sync_failed', 'Sync failed');
                    showToast(_k('settings.agentjs.postgres_sync_failed', 'Postgres sync failed'), 'error');
                }
            } catch (e) {
                this.memoryStateSyncStatus = _k('settings.agentjs.st_sync_error', 'Sync error: {error}', { error: e });
            }
        },

        async testMemoryEmbed() {
            this.memoryBackendsStatus = _k('settings.agentjs.st_testing_embedder', 'Testing embedder…');
            try {
                const resp = await fetch('/api/settings/memory/backends/test-embed', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                this.memoryBackendsStatus = data.ok
                    ? _k('settings.agentjs.st_embed_ok', 'Embed OK · {ms}ms · dim {dim}', { ms: data.latency_ms || 0, dim: data.dim || '?' })
                    : _k('settings.agentjs.st_embed_failed', 'Embed failed: {error}', { error: data.error || _k('settings.agentjs.st_unknown', 'unknown') });
            } catch (e) {
                this.memoryBackendsStatus = _k('settings.agentjs.st_embed_test_error', 'Embed test error');
            }
        },

        async testMemoryVector() {
            this.memoryBackendsStatus = _k('settings.agentjs.st_testing_vector', 'Testing vector backend…');
            try {
                const resp = await fetch('/api/settings/memory/backends/test-vector', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                // A note rides on an OK: the store that serves memory passed,
                // and the one Kazma picked automatically is not in use.
                this.memoryBackendsStatus = data.ok
                    ? (_k('settings.agentjs.st_vector_ok', 'Vector OK · {provider} · {ms}ms', { provider: data.provider || '', ms: data.latency_ms || 0 })
                        + (data.note ? ' — ' + data.note : ''))
                    : _k('settings.agentjs.st_vector_failed', 'Vector failed: {error}', { error: data.error || _k('settings.agentjs.st_unknown', 'unknown') });
                // A remote test re-probes the store; the banner reads that probe.
                if (data.capability) {
                    this.memoryBackendsCapability = Object.assign(
                        {}, this.memoryBackendsCapability, data.capability
                    );
                }
            } catch (e) {
                this.memoryBackendsStatus = _k('settings.agentjs.st_vector_test_error', 'Vector test error');
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
                this.memoryBackendsStatus = _k('settings.agentjs.st_reset_local', 'Reset to local defaults');
                showToast(_k('settings.agentjs.memory_backends_reset_to_local', 'Memory backends reset to local'), 'success');
            } catch (e) {
                showToast(_k('settings.agentjs.reset_failed', 'Reset failed'), 'error');
            }
        },

        async rebuildMemoryEmbeddings() {
            const ok = window.kazmaConfirm
                ? await window.kazmaConfirm({
                    title: _k('settings.agentjs.rebuild_embeddings', 'Rebuild embeddings?'),
                    message: _k('settings.agentjs.re_embed_episodes_beliefs_for', 'Re-embed episodes/beliefs for the current model. May take minutes.'),
                })
                : await window.confirm('Rebuild embeddings?');
            if (!ok) return;
            try {
                const resp = await fetch('/api/settings/memory/backends/rebuild', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                this.memoryBackendsStatus = data.ok ? _k('settings.agentjs.rebuild_started_status', 'Rebuild started (see status on Embedder page)') : (data.error || _k('settings.agentjs.status_failed', 'Failed'));
                showToast(data.ok ? 'Rebuild started' : 'Rebuild failed', data.ok ? 'success' : 'error');
            } catch (e) {
                showToast(_k('settings.agentjs.rebuild_failed', 'Rebuild failed'), 'error');
            }
        },

        async saveLogging() {
            this.saving = true;
            try {
                await window.kazmaSave('/api/settings/system/logging', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.logging),
                });
                showToast(_k('settings.agentjs.logging_settings_saved_restart_for', 'Logging settings saved (restart for rotation changes)'), 'success');
            } catch (e) {
                showToast(_k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
            }
            this.saving = false;
        },

        async saveSwarmRetention() {
            this.swarmRetentionSaving = true;
            try {
                await window.kazmaSave('/api/settings/single', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        key: 'swarm.task_retention_days',
                        value: this.swarmRetention.days,
                        category: 'swarm',
                    }),
                });
                showToast((window.t && window.t('settings.swarm_retention_saved')) || 'Swarm task retention saved', 'success');
            } catch (e) {
                showToast(e.message || _k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
            }
            this.swarmRetentionSaving = false;
        },

        async saveCheckpointRetention() {
            this.checkpointRetentionSaving = true;
            try {
                await window.kazmaSave('/api/settings/single', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        key: 'checkpoints.retention_days',
                        value: this.checkpointRetention.days,
                        category: 'system',
                    }),
                });
                showToast((window.t && window.t('settings.checkpoint_retention_saved')) || 'Step history retention saved', 'success');
            } catch (e) {
                showToast(e.message || _k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
            }
            this.checkpointRetentionSaving = false;
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
                this.documentsStatus = _k('settings.agentjs.st_saved', 'Saved');
                if (window.showToast) window.showToast(_k('settings.agentjs.document_settings_saved', 'Document settings saved'), 'success');
            } catch (e) {
                this.documentsStatus = _k('settings.agentjs.st_save_failed', 'Save failed');
                if (window.showToast) window.showToast(_k('settings.agentjs.document_settings_save_failed', 'Document settings save failed'), 'error');
            } finally {
                this.documentsSaving = false;
            }
        },

        async saveProxy() {
            this.saving = true;
            try {
                await window.kazmaSave('/api/settings/proxy', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.proxy),
                });
                showToast(_k('settings.proxy_saved', 'Proxy settings saved'), 'success');
            } catch (e) {
                showToast(_k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
            }
            this.saving = false;
        },

        async testProxy() {
            this.proxyTesting = true;
            this.proxyTestResult = null;
            try {
                // Save first so the test uses the just-entered credentials.
                await window.kazmaSave('/api/settings/proxy', {
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
                showToast(_k('settings.agentjs.model_is_required', 'Model is required'), 'error');
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
                    showToast(data.error || _k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
                } else {
                    showToast(_k('settings.agentjs.embedder_settings_saved_restart_the', 'Embedder settings saved. Restart the server to apply.'), 'success');
                    this.loadEmbedder();
                }
            } catch (e) {
                showToast(_k('settings.agentjs.save_failed', 'Save failed: ') + e.message, 'error');
            }
            this.embedderSaving = false;
        },

        async rebuildEmbeddings() {
            const ok = await window.kazmaConfirm({
                title: _k('settings.agentjs.rebuild_embeddings', 'Rebuild embeddings?'),
                message: _k('settings.agentjs.all_memory_rows_not_in', 'All memory rows not in the current vector space will be re-encoded with the active model. This runs in the background and can take a while for large stores. A backup is created automatically first.'),
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
                    showToast(_k('settings.agentjs.rebuild_started_in_the_background', 'Rebuild started in the background.'), 'success');
                    this.startEmbedderRebuildPoll();
                } else if (data.status === 'already') {
                    showToast(_k('settings.agentjs.a_rebuild_is_already_running', 'A rebuild is already running.'), 'info');
                    this.startEmbedderRebuildPoll();
                } else {
                    showToast(data.detail || _k('settings.agentjs.failed_to_start_rebuild', 'Failed to start rebuild'), 'error');
                }
            } catch (e) {
                showToast(_k('settings.agentjs.rebuild_failed_to_start', 'Rebuild failed to start: ') + e.message, 'error');
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
                            showToast(_k('settings.agentjs.embedding_rebuild_complete', 'Embedding rebuild complete.'), 'success');
                        } else if (status.state === 'error') {
                            showToast(_k('settings.agentjs.embedding_rebuild_failed', 'Embedding rebuild failed: ') + (status.error || _k('settings.agentjs.unknown_error', 'unknown error')), 'error');
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
                    showToast(data.error || _k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
                } else {
                    try {
                        localStorage.setItem('kazma.notifyOnComplete', enabled ? '1' : '0');
                    } catch (e) { /* ignore */ }
                    showToast(_k('settings.agentjs.notification_preference_saved', 'Notification preference saved.'), 'success');
                }
            } catch (e) {
                showToast(_k('settings.agentjs.save_failed', 'Save failed: ') + e.message, 'error');
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
                showToast(_k('settings.agentjs.snapshots_per_thread_must_be', 'Snapshots per thread must be at least 1'), 'error');
                return;
            }
            const rd = Number(this.timeTravel.retention_days);
            if (!rd || rd < 1) {
                showToast(_k('settings.agentjs.retention_days_must_be_at', 'Retention days must be at least 1'), 'error');
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
                    showToast(data.error || _k('settings.agentjs.save_failed_2', 'Save failed'), 'error');
                } else {
                    showToast(_k('settings.agentjs.time_travel_settings_saved_restart', 'Time travel settings saved. Restart the server to apply.'), 'success');
                }
            } catch (e) {
                showToast(_k('settings.agentjs.save_failed', 'Save failed: ') + e.message, 'error');
            }
            this.timeTravelSaving = false;
        },

        /* What the browser thinks you are in — offered, never imposed. */
        browserTimezone() {
            try {
                return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
            } catch (e) {
                return '';
            }
        },

        useBrowserTimezone() {
            const tz = this.browserTimezone();
            if (tz) {
                this.cronTz.value = tz;
                this.cronTzPreview();
            }
        },

        /* Show the current time in the chosen zone, as it is typed.
         *
         * The zone NAME does not tell you whether you picked the right one;
         * the clock does. It also turns an invalid entry into immediate
         * feedback rather than a 400 discovered on save -- the backend
         * still rejects bad zones, this just stops you getting there. */
        cronTzPreview() {
            const tz = String(this.cronTz.value || '').trim();
            this.cronTzNow = '';
            this.cronTzOffset = '';
            this.cronTzError = false;
            if (!tz) return;
            try {
                const now = new Date();
                this.cronTzNow = new Intl.DateTimeFormat(
                    document.documentElement.lang || 'en',
                    { timeZone: tz, dateStyle: 'medium', timeStyle: 'short' },
                ).format(now);
                const parts = new Intl.DateTimeFormat('en', {
                    timeZone: tz, timeZoneName: 'shortOffset',
                }).formatToParts(now);
                const off = parts.find(p => p.type === 'timeZoneName');
                this.cronTzOffset = off ? off.value : '';
            } catch (e) {
                this.cronTzError = true;
            }
        },

        async loadCronTimezone() {
            try {
                const data = await this._fetch('/api/settings/cron-timezone');
                if (!data) return;
                this.cronTz = {
                    value: String(data.timezone || 'UTC'),
                    source: String(data.source || 'default'),
                };
                this.cronTzPreview();
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
                showToast(window.t('settings.cron_tz_saved'), 'success');
            } catch (e) {
                showToast(window.t('settings.cron_tz_save_failed') + ': ' + e.message, 'error');
            }
            this.cronTzSaving = false;
        },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
