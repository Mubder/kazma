/** Settings mixin: hub — providers, models, hub providers/connectors/profiles */
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
    root.KazmaSettingsMixins.hub = function () {
        return {
        // NOTE: a second, complete provider CRUD path used to live here —
        // loadProviders / openAddProvider / applyProviderPreset / saveProvider
        // / deleteProvider / toggleProvider / testProvider, all against
        // /api/settings/providers. No template bound to any of it; the page
        // runs entirely on the hub* functions below, which call
        // /api/providers. It was found while tracing which route the Test
        // button reaches — the same duplication that had already cost this
        // refactor a phase shipped into the wrong endpoint. Deleted rather
        // than left as a second thing to keep in step.

        async fetchModels() {
            if (!this.currentModel.base_url) { showToast(_k('settings.hub.enter_a_base_url_first', 'Enter a base URL first'), 'error'); return; }
            this.fetchingModels = true;
            try {
                const data = await ModelsManager.discover(this.modelProvider, this.currentModel.base_url, this.currentModel.api_key);
                if (data.error) {
                    showToast(data.error, 'error');
                } else if (data.models && data.models.length) {
                    this.availableModels = data.models;
                    showToast(data.models.length + ' models found', 'success');
                } else {
                    showToast(_k('settings.hub.no_models_returned_check_your', 'No models returned. Check your API key.'), 'error');
                }
            } catch (e) {
                showToast(_k('settings.hub.fetch_failed', 'Fetch failed: ') + e.message, 'error');
            }
            this.fetchingModels = false;
        },

        onProviderChange() {
            const preset = ProvidersManager.getPreset(this.modelProvider);
            if (preset) this.currentModel.base_url = preset.base_url;
            this.availableModels = [];
        },

        async saveModel() {
            this.saving = true;
            const updates = Object.entries(this.currentModel).map(([k, v]) => ({
                key: 'llm.' + k, value: v, category: 'model'
            }));
            try {
                await window.kazmaSave('/api/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(updates),
                });
                // Reconfigure the live LLM provider so subsequent chat
                // requests use the new model/base_url/api_key (Bug 3 fix).
                try {
                    await window.kazmaSave('/api/provider/switch', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                        body: JSON.stringify({
                            provider: this.modelProvider || 'custom',
                            base_url: this.currentModel.base_url,
                            model: this.currentModel.model,
                            api_key: this.currentModel.api_key,
                        }),
                    });
                } catch (switchErr) {
                    console.warn('[Settings] provider/switch failed:', switchErr);
                }

                // If a profile name was entered, save as a named profile
                if (this.profileName && this.profileName.trim()) {
                    await this.saveModelProfile();
                }

                showToast(_k('settings.hub.model_settings_saved', 'Model settings saved'), 'success');
            } catch (e) {
                showToast(_k('settings.hub.save_failed', 'Save failed'), 'error');
            }
            this.saving = false;
        },

        async testModel() {
            this.testing = true;
            this.testResult = null;
            if (!this.currentModel.model) {
                this.testResult = { success: false, error: 'Enter a model name first' };
                this.testing = false;
                return;
            }
            try {
                const resp = await fetch('/api/settings/test-model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.currentModel),
                });
                this.testResult = await resp.json();
            } catch (e) {
                this.testResult = { success: false, error: e.message };
            }
            this.testing = false;
        },

        async saveModelDefault(taskType) {
            await ModelsManager.setDefault(taskType, this.modelDefaults[taskType]);
            showToast(_k('settings.hub.default_set', 'Default for "{task}" set to {model}', { task: taskType, model: this.modelDefaults[taskType] }), 'success');
        },

        async saveModelProfile() {
            const name = (this.profileName || '').trim();
            if (!name) { showToast(_k('settings.hub.enter_a_profile_name', 'Enter a profile name'), 'error'); return; }
            try {
                const resp = await fetch('/api/models/saved', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({
                        name: name,
                        base_url: this.currentModel.base_url || '',
                        api_key: this.currentModel.api_key || '',
                        model: this.currentModel.model || '',
                        provider: this.modelProvider || 'custom',
                    }),
                });
                const result = await resp.json();
                if (result.error) {
                    showToast(result.error, 'error');
                    return;
                }
                this.profileName = '';
                await this.loadSavedModels();
                showToast(`Profile "${name}" saved`, 'success');
            } catch (e) {
                showToast(_k('settings.hub.failed_to_save_profile', 'Failed to save profile: ') + e.message, 'error');
            }
        },

        async deleteModelProfile(name) {
            if (!(await window.kazmaConfirm({
                title: _k('settings.hub.delete_profile', 'Delete profile'),
                message: _k('settings.hub.delete_profile_message', 'Delete profile "{name}"? This cannot be undone.', { name: name }),
                confirmText: _k('settings.hub.delete', 'Delete'),
                danger: true,
            }))) return;
            try {
                await window.kazmaSave(`/api/models/saved/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadSavedModels();
                showToast(`Profile "${name}" deleted`, 'success');
            } catch (e) {
                showToast(_k('settings.hub.failed_to_delete_profile', 'Failed to delete profile: ') + e.message, 'error');
            }
        },

        async loadSavedModels() {
            const saved = await this._fetch('/api/models/saved');
            if (Array.isArray(saved)) this.savedModels = saved;
        },

        loadModelProfile(name) {
            const profile = this.savedModels.find(p => p.name === name);
            if (!profile) return;
            if (profile.base_url) this.currentModel.base_url = profile.base_url;
            if (profile.model) this.currentModel.model = profile.model;
            if (profile.provider) {
                this.modelProvider = profile.provider;
            }
            // api_key is masked (***), so only overwrite if it's a real key
            if (profile.api_key && profile.api_key !== '***') {
                this.currentModel.api_key = profile.api_key;
            }
            showToast(_k('settings.hub.loaded_profile', 'Loaded profile "{name}"', { name: name }), 'success');
        },

        async runModelComparison() {
            if (!this.comparePrompt || this.compareModels.length === 0) {
                showToast(_k('settings.hub.enter_a_prompt_and_select', 'Enter a prompt and select models'), 'error');
                return;
            }
            this.comparing = true;
            try {
                this.compareResults = await ModelsManager.compare(this.comparePrompt, this.compareModels);
            } catch (e) {
                showToast(_k('settings.hub.comparison_failed', 'Comparison failed: ') + e.message, 'error');
            }
            this.comparing = false;
        },

        async refreshGateway() {
            this.saving = true;
            try {
                const resp = await fetch('/api/gateway/refresh-adapters', { method: 'POST' });
                const data = await resp.json();
                if (resp.ok) {
                    const names = (data.adapters || []).join(', ') || 'none';
                    showToast(_k('settings.hub.gateway_refreshed', 'Gateway refreshed — {n} adapter(s): {names}', { n: data.adapters_count || 0, names: names }), 'success');
                } else {
                    showToast(_k('settings.hub.gateway_refresh_failed', 'Gateway refresh failed: ') + (data.detail || resp.statusText), 'error');
                }
            } catch (e) {
                showToast(_k('settings.hub.gateway_refresh_failed', 'Gateway refresh failed: ') + e.message, 'error');
            }
            this.saving = false;
        },

        async loadHubProviders() {
            try {
                const raw = await this._fetch('/api/providers');
                if (!raw) throw new Error('providers unavailable');
                // Carry test results across the reload that Toggle/Discover
                // trigger — otherwise pressing any other button on the card
                // erases the answer the operator just asked for.
                const previous = {};
                (this.hubProviders || []).forEach(function (p) {
                    if (p && p._test) previous[p.name] = p._test;
                });
                // Normalize so Alpine x-for always gets string arrays
                this.hubProviders = (Array.isArray(raw) ? raw : []).map(function (p) {
                    if (previous[p.name]) p._test = previous[p.name];
                    var disc = p.discovered_models;
                    if (!Array.isArray(disc)) disc = [];
                    p.discovered_models = disc.map(function (m) {
                        if (m && typeof m === 'object') return String(m.id || m.name || m);
                        return String(m);
                    }).filter(Boolean);
                    if (!Array.isArray(p.selected_models)) p.selected_models = [];
                    if (p._modelQuery === undefined) p._modelQuery = '';
                    return p;
                });
            } catch (e) {
                console.error('[Hub] Failed to load providers:', e);
                this.hubProviders = [];
            }
        },

        async loadHubConnectors() {
            try {
                const data = await this._fetch('/api/connectors');
                if (!data) throw new Error('connectors unavailable');
                this.hubConnectors = data;
            } catch (e) {
                console.error('[Hub] Failed to load connectors:', e);
                this.hubConnectors = [];
            }
        },

        async loadAdapterRouting() {
            // Delivery & Routing card v4 (2026-09-03): the connector state
            // loads from /api/connectors — the SAME source the old
            // per-platform cards used (token masked ****XXXX, extras +
            // enabled included) — so the card shows exactly what the old
            // dialogs showed. Destinations + selectors come from the
            // settings snapshot; the group route from the output target.
            try {
                const [data, connectors] = await Promise.all([
                    this._fetch('/api/settings'),
                    this._fetch('/api/connectors'),
                ]);
                if (!data) return;
                const byName = {};
                for (const c of (Array.isArray(connectors) ? connectors : [])) {
                    byName[c.name] = c;
                }
                const conn = data.connectors || {};
                const notif = data.notifications || {};
                // get_all() groups FULL dotted keys under the category
                // (data.notifications['notifications.ops.channels']); read
                // that shape first, with a prefix-stripped fallback. v2–v4
                // read the stripped shape only, so every routing checkbox
                // and chat-id field silently reset after each save.
                const grouped = (section, prefix, key) => {
                    if (section[key] !== undefined) return section[key];
                    const short = key.startsWith(prefix + '.')
                        ? key.slice(prefix.length + 1) : key;
                    return section[short];
                };
                let alerts = grouped(notif, 'notifications', 'notifications.ops.channels');
                if (typeof alerts === 'string') {
                    alerts = alerts.split(',').map(x => x.trim()).filter(Boolean);
                }
                let swarmRoutes = grouped(notif, 'notifications', 'notifications.swarm.routes');
                if (typeof swarmRoutes === 'string') {
                    swarmRoutes = swarmRoutes.split(',').map(x => x.trim()).filter(Boolean);
                }
                // Server status messages: unset means Kazma's default (one
                // start card, and a failed start); [] means all off.
                let lifecycle = grouped(notif, 'notifications', 'notifications.lifecycle.events');
                if (typeof lifecycle === 'string') {
                    lifecycle = lifecycle.split(',').map(x => x.trim()).filter(Boolean);
                }
                const r = this.adapterRouting;
                const ex = (name, key) => String(((byName[name] || {}).extras || {})[key] || '');
                r.tgToken = String((byName.telegram || {}).token || '');
                r.tgEnabled = (byName.telegram || {}).enabled !== false;
                r.tgAllowed = ex('telegram', 'allowed_users');
                r.discordToken = String((byName.discord || {}).token || '');
                r.discordEnabled = (byName.discord || {}).enabled !== false;
                r.discordGuild = ex('discord', 'guild_id');
                r.discordAllowed = ex('discord', 'allowed_users');
                r.slackToken = String((byName.slack || {}).token || '');
                r.slackAppToken = ex('slack', 'app_token');
                r.slackWorkspace = ex('slack', 'workspace');
                r.slackAllowed = ex('slack', 'allowed_users');
                r.slackEnabled = (byName.slack || {}).enabled !== false;
                r.tgMainChat = String(grouped(conn, 'connectors', 'connectors.telegram.swarm_chat_id') || '');
                r.discordChannel = String(grouped(conn, 'connectors', 'connectors.discord.swarm_channel_id') || '');
                r.slackChannel = String(grouped(conn, 'connectors', 'connectors.slack.swarm_channel_id') || '');
                r.alertRoutes = Array.isArray(alerts) ? alerts : [];
                r.swarmRoutes = Array.isArray(swarmRoutes) ? swarmRoutes : [];
                r.lifecycleEvents = Array.isArray(lifecycle)
                    ? lifecycle.filter(ev => this.lifecycleEventNames.includes(ev))
                    : ['started', 'startup_failed'];
                // Group route lives in swarm.output_target (masked token).
                r.tgGroupChat = '';
                r.tgGroupToken = '';
                r.tgGroupEnabled = false;
                try {
                    const t = await this._fetch('/api/swarm/output-target');
                    const ot = (t && t.output_target) || {};
                    r.tgGroupChat = ot.chat_id != null ? String(ot.chat_id) : '';
                    r.tgGroupToken = ot.bot_token === '***' ? '***' : (ot.bot_token || '');
                    r.tgGroupEnabled = !!ot.enabled && !!ot.chat_id;
                } catch (eGrp) { /* group route not configured */ }
                // Snapshot for dirty-checking: Save only writes what changed
                // (and only restarts the platform adapters when credentials
                // actually changed — refresh takes seconds and used to run
                // on EVERY save, even routing-only ones).
                this.adapterRoutingSnapshot = JSON.stringify(this._routingDiffable());
            } catch (e) {
                console.error('[Hub] Failed to load adapter routing:', e);
            }
        },

        _routingDiffable() {
            // The comparable state of the card: everything Save would write.
            const r = this.adapterRouting;
            return {
                tgToken: String(r.tgToken || ''),
                tgEnabled: !!r.tgEnabled,
                tgAllowed: String(r.tgAllowed || '').trim(),
                tgMainChat: String(r.tgMainChat || '').trim(),
                tgGroupEnabled: !!(r.tgGroupEnabled && String(r.tgGroupChat || '').trim()),
                tgGroupChat: String(r.tgGroupChat || '').trim(),
                tgGroupToken: String(r.tgGroupToken || ''),
                discordToken: String(r.discordToken || ''),
                discordEnabled: !!r.discordEnabled,
                discordGuild: String(r.discordGuild || '').trim(),
                discordAllowed: String(r.discordAllowed || '').trim(),
                discordChannel: String(r.discordChannel || '').trim(),
                slackToken: String(r.slackToken || ''),
                slackAppToken: String(r.slackAppToken || ''),
                slackEnabled: !!r.slackEnabled,
                slackWorkspace: String(r.slackWorkspace || '').trim(),
                slackAllowed: String(r.slackAllowed || '').trim(),
                slackChannel: String(r.slackChannel || '').trim(),
                alertRoutes: [...(r.alertRoutes || [])].sort(),
                swarmRoutes: [...(r.swarmRoutes || [])].sort(),
                lifecycleEvents: this.lifecycleEventNames.filter(ev => (r.lifecycleEvents || []).includes(ev)),
            };
        },

        _routingChanged(prev, curr, key) {
            return JSON.stringify(prev[key]) !== JSON.stringify(curr[key]);
        },

        toggleRoutingList(list, item, checked) {
            if (!Array.isArray(list)) return;
            const idx = list.indexOf(item);
            if (checked && idx === -1) list.push(item);
            if (!checked && idx !== -1) list.splice(idx, 1);
        },

        _connectorPayloadFor(name) {
            // Build the {name, token, enabled, extras} body the old
            // per-platform dialogs POSTed — masked values are sent back
            // as-is and the server preserves the stored secret.
            const r = this.adapterRouting;
            if (name === 'telegram') {
                return {
                    name, token: String(r.tgToken || ''), enabled: !!r.tgEnabled,
                    extras: { allowed_users: String(r.tgAllowed || '').trim() },
                };
            }
            if (name === 'discord') {
                return {
                    name, token: String(r.discordToken || ''), enabled: !!r.discordEnabled,
                    extras: {
                        guild_id: String(r.discordGuild || '').trim(),
                        allowed_users: String(r.discordAllowed || '').trim(),
                    },
                };
            }
            if (name === 'slack') {
                return {
                    name, token: String(r.slackToken || ''), enabled: !!r.slackEnabled,
                    extras: {
                        app_token: String(r.slackAppToken || ''),
                        workspace: String(r.slackWorkspace || '').trim(),
                        allowed_users: String(r.slackAllowed || '').trim(),
                    },
                };
            }
            return null;
        },

        async saveAdapterRouting() {
            // Diff-driven save (2026-09-04): only the CHANGED values are
            // written. The server rebuilds a platform's adapter when its
            // token or switch changed, in the background, so the Save button
            // never sits grayed while a platform reconnects.
            this.adapterRoutingSaving = true;
            try {
                const r = this.adapterRouting;
                const prev = {};
                try { Object.assign(prev, JSON.parse(this.adapterRoutingSnapshot || '{}')); } catch (eSnap) { /* empty */ }
                const curr = this._routingDiffable();
                const changed = (k) => this._routingChanged(prev, curr, k);
                const puts = [];
                const single = (key, value, category) => puts.push(fetch('/api/settings/single', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ key, value, category }),
                }));
                const connectorPut = (payload) => puts.push(fetch('/api/connectors', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(payload),
                }));
                // Credential fields: the masked value round-trips unchanged —
                // the server preserves the stored secret; a typed value replaces.
                const secretChanged = (field) => {
                    const v = String(curr[field] || '');
                    return v !== '' && !v.startsWith('****') && v !== String(prev[field] || '');
                };

                // Platform connectors (POST /api/connectors — the endpoint the
                // old dialogs used: normalizes tokens, preserves masks, applies
                // allowlists live). Only platforms with actual changes.
                const platforms = [
                    {
                        name: 'telegram',
                        dirty: changed('tgToken') || changed('tgEnabled') || changed('tgAllowed'),
                        payload: () => ({
                            name: 'telegram',
                            token: secretChanged('tgToken') ? String(r.tgToken).trim() : String(r.tgToken || ''),
                            enabled: !!r.tgEnabled,
                            extras: { allowed_users: String(r.tgAllowed || '').trim() },
                        }),
                    },
                    {
                        name: 'discord',
                        dirty: changed('discordToken') || changed('discordEnabled') || changed('discordGuild') || changed('discordAllowed'),
                        payload: () => ({
                            name: 'discord',
                            token: String(r.discordToken || ''),
                            enabled: !!r.discordEnabled,
                            extras: {
                                guild_id: String(r.discordGuild || '').trim(),
                                allowed_users: String(r.discordAllowed || '').trim(),
                            },
                        }),
                    },
                    {
                        name: 'slack',
                        dirty: changed('slackToken') || changed('slackAppToken') || changed('slackEnabled') || changed('slackWorkspace') || changed('slackAllowed'),
                        payload: () => ({
                            name: 'slack',
                            token: String(r.slackToken || ''),
                            enabled: !!r.slackEnabled,
                            extras: {
                                app_token: String(r.slackAppToken || ''),
                                workspace: String(r.slackWorkspace || '').trim(),
                                allowed_users: String(r.slackAllowed || '').trim(),
                            },
                        }),
                    },
                ];
                for (const p of platforms) {
                    if (!p.dirty) continue;
                    connectorPut(p.payload());
                }

                // Destinations + routing selectors.
                if (changed('tgMainChat')) single('connectors.telegram.swarm_chat_id', curr.tgMainChat, 'connectors');
                if (changed('discordChannel')) single('connectors.discord.swarm_channel_id', curr.discordChannel, 'connectors');
                if (changed('slackChannel')) single('connectors.slack.swarm_channel_id', curr.slackChannel, 'connectors');
                if (changed('alertRoutes')) single('notifications.ops.channels', (r.alertRoutes || []).join(','), 'notifications');
                if (changed('swarmRoutes')) single('notifications.swarm.routes', (r.swarmRoutes || []).join(','), 'notifications');
                if (changed('lifecycleEvents')) single('notifications.lifecycle.events', curr.lifecycleEvents, 'notifications');

                // Group route (swarm.output_target) — only when it changed.
                if (changed('tgGroupEnabled') || changed('tgGroupChat') || changed('tgGroupToken')) {
                    if (r.tgGroupEnabled && String(r.tgGroupChat || '').trim()) {
                        const payload = {
                            platform: 'telegram',
                            chat_id: String(r.tgGroupChat).trim(),
                            enabled: true,
                        };
                        const tok = String(r.tgGroupToken || '');
                        if (tok && tok !== '***' && !tok.startsWith('***')) {
                            payload.bot_token = tok.trim();
                        }
                        puts.push(fetch('/api/swarm/output-target', {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                            body: JSON.stringify(payload),
                        }));
                    } else {
                        puts.push(fetch('/api/swarm/output-target', {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                            body: JSON.stringify({ clear: true }),
                        }));
                    }
                }

                if (puts.length === 0) {
                    showToast(_k('settings.hub.nothing_to_save_no_changes', 'Nothing to save — no changes.'), 'info');
                    this.adapterRoutingSaving = false;
                    return;
                }
                const results = await Promise.all(puts);
                const bad = results.find(x => !x.ok);
                if (bad) throw new Error('HTTP ' + bad.status);
                showToast(_k('settings.hub.saved', 'Saved.'), 'success');
                this.adapterRoutingSnapshot = JSON.stringify(curr);

                // No adapter refresh from here: the server applies a changed
                // token, switch or channel to the running adapter itself
                // (kazma_gateway.chat_adapters, 2026-10-01).
                this.loadAdapterRouting();
            } catch (e) {
                showToast(_k('settings.hub.save_failed_2', 'Save failed: ') + e.message, 'error');
            }
            this.adapterRoutingSaving = false;
        },

        async testRoute(name) {
            // Mirrors the old modal's test: save this platform's card state
            // first (the test runs against SAVED credentials), then run the
            // platform health check and show the result inline.
            if (!['telegram', 'discord', 'slack'].includes(name)) return;
            this.adapterRoutingTesting = name;
            this.adapterRoutingTest = null;
            try {
                const payload = this._connectorPayloadFor(name);
                if (payload) {
                    const saveResp = await fetch('/api/connectors', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                        body: JSON.stringify(payload),
                    });
                    if (!saveResp.ok) throw new Error('save failed (HTTP ' + saveResp.status + ')');
                }
                const resp = await fetch('/api/connectors/' + encodeURIComponent(name) + '/test', { method: 'POST' });
                const result = await resp.json();
                this.adapterRoutingTest = { name, ...result };
                showToast(result.success
                    ? _k('settings.hub.connection_test_ok', 'Connection test succeeded')
                    : _k('settings.hub.test_failed_error', 'Test failed: {error}', { error: result.error || '?' }),
                result.success ? 'success' : 'error');
            } catch (e) {
                this.adapterRoutingTest = { name, success: false, error: e.message };
                showToast(_k('settings.hub.test_failed', 'Test failed: ') + e.message, 'error');
            }
            this.adapterRoutingTesting = '';
        },

        connectorCheckTitle(key) {
            // The title of one check a platform Test made (its detail is the
            // server's own words, shown as written).
            const titles = {
                token: _k('settings.hub.check_token', 'Bot token'),
                message_text: _k('settings.hub.check_message_text', 'Message text'),
                servers: _k('settings.hub.check_servers', 'Servers'),
                channel: _k('settings.hub.check_channel', 'Delivery channel'),
                latest: _k('settings.hub.check_latest', 'Your latest message'),
                direct_message: _k('settings.hub.check_direct_message', 'Your direct messages'),
                receiving: _k('settings.hub.check_receiving', 'How messages arrive'),
                groups: _k('settings.hub.check_groups', 'Groups'),
                chat: _k('settings.hub.check_chat', 'Delivery chat'),
                group: _k('settings.hub.check_group', 'Group route'),
                app_token: _k('settings.hub.check_app_token', 'Socket Mode token'),
                scopes: _k('settings.hub.check_scopes', 'Permissions'),
                allowed: _k('settings.hub.check_allowed', 'Allowed users'),
                listening: _k('settings.hub.check_listening', 'Kazma is listening'),
            };
            return titles[key] || key;
        },

        connectorLinkOk(link) {
            // A check's link opens the platform's own app or site, nothing else.
            const s = String(link || '');
            return s.startsWith('https://discord.com/') || s.startsWith('https://slack.com/');
        },

        connectorLinkLabel(platform) {
            return platform === 'slack'
                ? _k('settings.hub.open_in_slack', 'Open this conversation in Slack')
                : _k('settings.hub.open_in_discord', 'Open this conversation in Discord');
        },

        async loadHubProfiles() {
            try {
                const data = await this._fetch('/api/models/profiles');
                if (!data) throw new Error('profiles unavailable');
                this.hubProfiles = data;
            } catch (e) {
                console.error('[Hub] Failed to load profiles:', e);
                this.hubProfiles = [];
            }
        },

        _defaultConnectorExtras(name) {
            const defaults = {
                telegram: { allowed_users: '' },
                discord: { guild_id: '', allowed_users: '' },
                slack: { app_token: '', workspace: '', allowed_users: '' },
                email: { smtp_host: '', smtp_port: '', username: '', password: '', imap_host: '' },
                webhook: { incoming_url: '', outgoing_url: '', secret: '' },
            };
            return { ...(defaults[name] || {}) };
        },

        _isMaskedApiKey(value) {
            const s = String(value || '').trim();
            return !s || s === '***' || s === '****' || s.includes('****');
        },

        openHubProviderModal(name) {
            this.hubProviderTested = false;
            this.hubShowProviderKey = false;
            this.hubTestResult = null;
            if (name) {
                const p = this.hubProviders.find(x => x.name === name);
                if (p) {
                    const stored = String(p.api_key || '');
                    this.hubEditingProvider = {
                        name: p.name,
                        display_name: p.display_name || '',
                        base_url: p.base_url || '',
                        // Never put the masked **** value in the input — Test
                        // would send dots instead of a key. Blank = keep stored.
                        api_key: '',
                        models: Array.isArray(p.models) ? p.models.join(', ') : (p.models || ''),
                        enabled: p.enabled !== false,
                        google_mode: p.google_mode || (p.project_id ? 'vertex_ai' : 'ai_studio'),
                        project_id: p.project_id || '',
                        location: p.location || 'us-central1',
                        _existing: true,
                        _has_stored_key: !!(stored && stored !== '—' && stored !== '***'),
                    };
                    this.hubProviderTested = true; // editing an existing tested provider is acceptable
                }
            } else {
                this.hubEditingProvider = { name: '', display_name: '', base_url: '', api_key: '', models: '', enabled: true, google_mode: 'ai_studio', project_id: '', location: 'us-central1', _existing: false, _has_stored_key: false };
            }
            this.hubProviderModal = true;
        },

        editHubProvider(name) {
            this.openHubProviderModal(name);
        },

        applyHubProviderPreset(presetKey) {
            const preset = ProvidersManager.getPreset(presetKey);
            if (preset) {
                this.hubEditingProvider.name = presetKey;
                this.hubEditingProvider.display_name = preset.name;
                this.hubEditingProvider.base_url = preset.base_url;
            }
        },

        async saveHubProvider() {
            if (!this.hubEditingProvider.name || (!this.hubEditingProvider.base_url && this.hubEditingProvider.name !== 'google')) {
                showToast(_k('settings.hub.name_and_base_url_are', 'Name and Base URL are required'), 'error');
                return;
            }
            this.saving = true;
            try {
                const data = { ...this.hubEditingProvider };
                if (typeof data.models === 'string') {
                    data.models = data.models.split(',').map(m => m.trim()).filter(Boolean);
                }
                delete data._existing;
                delete data._has_stored_key;
                const resp = await fetch('/api/providers', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(data),
                });
                const result = await resp.json();
                if (result.error) {
                    showToast(result.error, 'error');
                } else {
                    this.hubProviderModal = false;
                    await this.loadHubProviders();
                    showToast(_k('settings.hub.provider_saved', 'Provider saved'), 'success');
                }
            } catch (e) {
                showToast(_k('settings.hub.failed_to_save_provider', 'Failed to save provider: ') + e.message, 'error');
            }
            this.saving = false;
        },

        async deleteHubProvider(name) {
            if (!(await window.kazmaConfirm({
                title: _k('settings.hub.delete_provider', 'Delete provider'),
                message: _k('settings.hub.delete_provider_message', 'Delete provider "{name}"? This cannot be undone.', { name: name }),
                confirmText: _k('settings.hub.delete', 'Delete'),
                danger: true,
            }))) return;
            try {
                await window.kazmaSave(`/api/providers/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadHubProviders();
                showToast(_k('settings.hub.provider_removed', 'Provider removed'), 'success');
            } catch (e) {
                showToast(_k('settings.hub.failed_to_delete_provider', 'Failed to delete provider: ') + e.message, 'error');
            }
        },

        async toggleHubProvider(name, enabled) {
            try {
                await window.kazmaSave(`/api/providers/${encodeURIComponent(name)}/toggle`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ enabled }),
                });
                await this.loadHubProviders();
            } catch (e) {
                showToast(_k('settings.hub.toggle_failed', 'Toggle failed: ') + e.message, 'error');
            }
        },

        async testHubProvider(name) {
            this.hubTestingProvider = name;
            this.hubTestResult = { type: 'provider' };
            let outcome;
            try {
                const resp = await fetch(`/api/providers/${encodeURIComponent(name)}/test`, { method: 'POST' });
                const result = await resp.json();
                let success = !!result.success;
                let errorMsg = result.error || result.detail || (resp.ok ? null : `HTTP ${resp.status}`);
                if (typeof errorMsg === 'object') {
                    errorMsg = JSON.stringify(errorMsg);
                }
                outcome = { type: 'provider', source: 'card', success, error: errorMsg, ...result };
            } catch (e) {
                outcome = { type: 'provider', source: 'card', success: false, error: e.message };
            }
            this.hubTestResult = outcome;
            // Also pin it to the card it belongs to. One shared result line at
            // the bottom of the list cannot say *which* of six providers just
            // failed, and the answer is the whole point of pressing Test.
            const card = this.hubProviders.find(x => x.name === name);
            if (card) card._test = outcome;
            this.hubTestingProvider = null;
        },

        /** @returns {'working'|'chat_failing'|'unreachable'|'untested'} */
        providerState(p) {
            return ProvidersManager.cardState(p, p && p._test);
        },

        providerStateLabel(p) {
            return ProvidersManager.stateLabel(this.providerState(p));
        },

        /** The one line under the pill: what the last test actually found. */
        providerStateDetail(p) {
            if (!p || !p._test) return '';
            return ProvidersManager.describe(p._test).detail || '';
        },

        providerCapabilities(p) {
            return ProvidersManager.capabilityBadges(p);
        },

        providerWireFacts(p) {
            return ProvidersManager.wireFacts(p);
        },

        // ── control-plane view ──────────────────────────────────────────
        // The page is master-detail: a list of providers with their state,
        // and one open at a time. Selection is by name rather than index so
        // it survives the reload that Toggle and Discover trigger.

        selectHubProvider(name) {
            this.hubSelectedProvider = name;
        },

        /** The open provider, defaulting to the first one in the list. */
        selectedHubProvider() {
            const list = this.hubProviders || [];
            if (!list.length) return null;
            const found = list.find(p => p.name === this.hubSelectedProvider);
            return found || list[0];
        },

        providerStateCounts() {
            return ProvidersManager.stateCounts(this.hubProviders || []);
        },

        providerApiVersion(p) {
            return ProvidersManager.apiVersionOf(p);
        },

        providerChecks(p) {
            return ProvidersManager.checksFor(p && p._test);
        },

        /** One short line for the list row: what the last check measured. */
        providerRowMeta(p) {
            const state = this.providerState(p);
            if (state === 'untested') return '—';
            const result = p && p._test;
            if (!result) return '';
            if (result.success && result.chat_ms != null) return result.chat_ms + ' ms';
            if (result.reachable && result.chat_ok === false) {
                return ProvidersManager.stateLabel('models_ok_chat_failing');
            }
            return result.latency_ms != null ? result.latency_ms + ' ms' : '';
        },

        async testHubProviderFromModal() {
            const name = this.hubEditingProvider.name;
            if (!name || (!this.hubEditingProvider.base_url && name !== 'google')) {
                showToast(_k('settings.hub.enter_a_provider_name_and', 'Enter a provider name and base URL first'), 'error');
                return;
            }
            this.hubTestingProvider = 'modal';
            this.hubTestResult = null;
            try {
                // Upsert a temporary provider so the test can run against the modal values.
                const temp = { ...this.hubEditingProvider };
                if (typeof temp.models === 'string') {
                    temp.models = temp.models.split(',').map(m => m.trim()).filter(Boolean);
                }
                delete temp._existing;
                delete temp._has_stored_key;
                const upsertResp = await fetch('/api/providers', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(temp),
                });
                if (!upsertResp.ok) {
                    const upsertData = await upsertResp.json();
                    let errMsg = upsertData.error || upsertData.detail || `HTTP ${upsertResp.status}`;
                    if (typeof errMsg === 'object') errMsg = JSON.stringify(errMsg);
                    throw new Error(errMsg);
                }
                const typed = String(temp.api_key || '').trim();
                const testBody = {};
                if (typed && !this._isMaskedApiKey(typed)) {
                    testBody.api_key = typed;
                }
                const resp = await fetch(`/api/providers/${encodeURIComponent(name)}/test`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(testBody),
                });
                const result = await resp.json();
                let success = !!result.success;
                let errorMsg = result.error || result.detail || (resp.ok ? null : `HTTP ${resp.status}`);
                if (typeof errorMsg === 'object') {
                    errorMsg = JSON.stringify(errorMsg);
                }
                this.hubTestResult = { type: 'provider', source: 'modal', success, error: errorMsg, ...result };
                this.hubProviderTested = true; // attempt completed: enable save
                if (success) {
                    showToast(_k('settings.hub.connection_test_succeeded', 'Connection test succeeded'), 'success');
                } else {
                    showToast(_k('settings.hub.test_failed_error', 'Test failed: {error}', { error: errorMsg }), 'error');
                }
            } catch (e) {
                this.hubTestResult = { type: 'provider', source: 'modal', success: false, error: e.message };
                this.hubProviderTested = true; // attempt completed on exception: enable save
                showToast(_k('settings.hub.test_failed', 'Test failed: ') + e.message, 'error');
            }
            this.hubTestingProvider = null;
        },

        async discoverHubProvider(name) {
            this.hubDiscoveringProvider = name;
            try {
                const resp = await fetch(`/api/providers/${encodeURIComponent(name)}/discover`, { method: 'POST' });
                const data = await resp.json();
                const count = data.count || 0;
                const speech = data.speech_omitted || 0;
                var msg = count + ' chat models discovered';
                if (speech) {
                    msg += ' (' + speech + ' speech models hidden — STT is Settings → Voice)';
                }
                showToast(msg, count > 0 ? 'success' : 'warning');
                await this.loadHubProviders();
            } catch (e) {
                showToast(_k('settings.hub.discover_failed', 'Discover failed: ') + e.message, 'error');
            }
            this.hubDiscoveringProvider = null;
        },

        filteredDiscoveredModels(provider) {
            if (!provider) return [];
            var list = provider.discovered_models;
            if (!Array.isArray(list)) return [];
            // Coerce entries to strings (API should return strings)
            var models = list.map(function (m) {
                if (m && typeof m === 'object') return String(m.id || m.name || m);
                return String(m);
            }).filter(Boolean);
            var q = String(provider._modelQuery || '').trim().toLowerCase();
            if (!q) return models;
            return models.filter(function (m) {
                return m.toLowerCase().indexOf(q) !== -1;
            });
        },

        async toggleModelSelection(providerName, model, checked) {
            const p = this.hubProviders.find(x => x.name === providerName);
            if (!p) return;
            if (!p.selected_models) p.selected_models = [];
            if (checked) {
                if (!p.selected_models.includes(model)) p.selected_models.push(model);
            } else {
                p.selected_models = p.selected_models.filter(m => m !== model);
            }
            try {
                await window.kazmaSave(`/api/providers/${encodeURIComponent(providerName)}/select-models`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ models: p.selected_models }),
                });
            } catch (e) {
                showToast(_k('settings.hub.failed_to_save_model_selection', 'Failed to save model selection'), 'error');
            }
        },

        async toggleAllModels(providerName) {
            const p = this.hubProviders.find(x => x.name === providerName);
            if (!p || !p.discovered_models) return;
            // Toggle only the currently filtered set when searching
            const visible = this.filteredDiscoveredModels(p);
            const allVisibleSelected = visible.length > 0 && visible.every(
                m => (p.selected_models || []).includes(m)
            );
            if (allVisibleSelected) {
                p.selected_models = (p.selected_models || []).filter(m => !visible.includes(m));
            } else {
                const set = new Set(p.selected_models || []);
                visible.forEach(m => set.add(m));
                p.selected_models = Array.from(set);
            }
            try {
                await window.kazmaSave(`/api/providers/${encodeURIComponent(providerName)}/select-models`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ models: p.selected_models }),
                });
            } catch (e) {
                showToast(_k('settings.hub.failed_to_save_model_selection', 'Failed to save model selection'), 'error');
            }
        },

        async deleteProviderDiscoveredModel(providerName, model) {
            try {
                const resp = await fetch(`/api/providers/${encodeURIComponent(providerName)}/models/${encodeURIComponent(model)}`, {
                    method: 'DELETE',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const result = resp.ok ? await resp.json() : null;
                if (result && result.status === 'ok') {
                    showToast(`Removed ${model}`, 'success');
                } else if (result && result.status === 'not_found') {
                    showToast(_k('settings.hub.model_not_in_list', '{model} is not in the list', { model: model }), 'info');
                } else {
                    showToast(_k('settings.hub.failed_to_remove_model', 'Failed to remove model'), 'error');
                }
                await this.loadHubProviders();
            } catch (e) {
                showToast(_k('settings.hub.failed_to_remove_model_2', 'Failed to remove model: ') + e.message, 'error');
            }
        },

        async clearHubDiscovered(providerName) {
            if (!(await window.kazmaConfirm({
                title: _k('settings.hub.clear_discovered_models', 'Clear discovered models'),
                message: _k('settings.hub.clear_models_message', 'Clear discovered models for "{name}"? Your selected models for this provider will also be cleared.', { name: providerName }),
                confirmText: _k('settings.hub.clear', 'Clear'),
                danger: true,
            }))) return;
            try {
                const resp = await fetch(`/api/providers/${encodeURIComponent(providerName)}/clear-discovered`, {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                if (resp.ok) {
                    showToast(_k('settings.hub.cleared_discovered_models', 'Cleared discovered models'), 'success');
                    await this.loadHubProviders();
                } else {
                    showToast(_k('settings.hub.failed_to_clear_models', 'Failed to clear models'), 'error');
                }
            } catch (e) {
                showToast(_k('settings.hub.failed_to_clear_models_2', 'Failed to clear models: ') + e.message, 'error');
            }
        },

        openHubConnectorModal(name) {
            this.hubConnectorTested = false;
            this.hubShowConnectorToken = false;
            this.hubTestResult = null;
            if (name) {
                const c = this.hubConnectors.find(x => x.name === name);
                if (c) {
                    this.hubEditingConnector = {
                        name: c.name,
                        token: c.token || '',
                        enabled: c.enabled !== false,
                        extras: { ...(c.extras || {}), _existing: true },
                        _existing: true,
                    };
                    this.hubConnectorTested = true; // existing connectors can be saved without re-test
                }
            } else {
                this.hubEditingConnector = { name: '', token: '', enabled: true, extras: {}, _existing: false };
            }
            this.hubConnectorModal = true;
        },

        editHubConnector(name) {
            this.openHubConnectorModal(name);
        },

        onHubConnectorPlatformChange() {
            const name = this.hubEditingConnector.name;
            this.hubEditingConnector.extras = this._defaultConnectorExtras(name);
            this.hubConnectorTested = false;
        },

        async saveHubConnector() {
            if (!this.hubEditingConnector.name) {
                showToast(_k('settings.hub.connector_name_is_required', 'Connector name is required'), 'error');
                return;
            }
            this.saving = true;
            try {
                const data = { ...this.hubEditingConnector };
                const extras = { ...(data.extras || {}) };
                delete extras._existing;
                data.extras = extras;
                delete data._existing;
                const resp = await fetch('/api/connectors', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(data),
                });
                const result = await resp.json();
                if (result.error) {
                    showToast(result.error, 'error');
                } else {
                    this.hubConnectorModal = false;
                    await this.loadHubConnectors();
                    showToast(_k('settings.hub.connector_saved', 'Connector saved'), 'success');
                }
            } catch (e) {
                showToast(_k('settings.hub.failed_to_save_connector', 'Failed to save connector: ') + e.message, 'error');
            }
            this.saving = false;
        },

        async deleteHubConnector(name) {
            if (!(await window.kazmaConfirm({
                title: _k('settings.hub.delete_connector', 'Delete connector'),
                message: _k('settings.hub.delete_connector_message', 'Delete connector "{name}"? This cannot be undone.', { name: name }),
                confirmText: _k('settings.hub.delete', 'Delete'),
                danger: true,
            }))) return;
            try {
                await window.kazmaSave(`/api/connectors/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadHubConnectors();
                showToast(_k('settings.hub.connector_removed', 'Connector removed'), 'success');
            } catch (e) {
                showToast(_k('settings.hub.failed_to_delete_connector', 'Failed to delete connector: ') + e.message, 'error');
            }
        },

        async toggleHubConnector(name, enabled) {
            try {
                await window.kazmaSave(`/api/connectors/${encodeURIComponent(name)}/toggle`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ enabled }),
                });
                await this.loadHubConnectors();
            } catch (e) {
                showToast(_k('settings.hub.toggle_failed', 'Toggle failed: ') + e.message, 'error');
            }
        },

        async testHubConnector(name) {
            this.hubTestingConnector = name;
            this.hubTestResult = { type: 'connector' };
            try {
                const resp = await fetch(`/api/connectors/${encodeURIComponent(name)}/test`, { method: 'POST' });
                this.hubTestResult = { type: 'connector', ...await resp.json() };
            } catch (e) {
                this.hubTestResult = { type: 'connector', success: false, error: e.message };
            }
            this.hubTestingConnector = null;
        },

        async testHubConnectorFromModal() {
            const name = this.hubEditingConnector.name;
            if (!name) {
                showToast(_k('settings.hub.select_a_connector_name_first', 'Select a connector name first'), 'error');
                return;
            }
            this.hubTestingConnector = 'modal';
            this.hubTestResult = null;
            try {
                // Save a temporary connector so the test can run against the modal values.
                const data = { ...this.hubEditingConnector };
                const extras = { ...(data.extras || {}) };
                delete extras._existing;
                data.extras = extras;
                delete data._existing;
                await window.kazmaSave('/api/connectors', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(data),
                });
                const resp = await fetch(`/api/connectors/${encodeURIComponent(name)}/test`, { method: 'POST' });
                const result = await resp.json();
                this.hubTestResult = { type: 'connector', ...result };
                if (result.success) {
                    this.hubConnectorTested = true;
                }
                showToast(result.success ? _k('settings.hub.connection_test_ok', 'Connection test succeeded') : _k('settings.hub.test_failed_error', 'Test failed: {error}', { error: result.error }), result.success ? 'success' : 'error');
            } catch (e) {
                this.hubTestResult = { type: 'connector', success: false, error: e.message };
                showToast(_k('settings.hub.test_failed', 'Test failed: ') + e.message, 'error');
            }
            this.hubTestingConnector = null;
        },

        openHubProfileModal(name) {
            this.hubShowProfileKey = false;
            if (name) {
                const p = this.hubProfiles.find(x => x.name === name);
                if (p) {
                    this.hubEditingProfile = {
                        name: p.name,
                        provider: p.provider || '',
                        base_url: p.base_url || '',
                        api_key: p.api_key || '',
                        model: p.model || '',
                        _existing: true,
                    };
                }
            } else {
                this.hubEditingProfile = { name: '', provider: '', base_url: '', api_key: '', model: '', _existing: false };
            }
            this.hubProfileModal = true;
        },

        editHubProfile(name) {
            this.openHubProfileModal(name);
        },

        loadHubProfile(name) {
            const p = this.hubProfiles.find(x => x.name === name);
            if (!p) return;
            this.currentModel.base_url = p.base_url || '';
            this.currentModel.model = p.model || '';
            this.currentModel.api_key = (p.api_key && p.api_key !== '***') ? p.api_key : this.currentModel.api_key;
            this.modelProvider = p.provider || '';
            showToast(_k('settings.hub.loaded_profile', 'Loaded profile "{name}"', { name: name }), 'success');
        },

        async saveHubProfile() {
            const name = (this.hubEditingProfile.name || '').trim();
            if (!name) { showToast(_k('settings.hub.profile_name_is_required', 'Profile name is required'), 'error'); return; }
            this.saving = true;
            try {
                const resp = await fetch('/api/models/profiles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.hubEditingProfile),
                });
                const result = await resp.json();
                if (result.error) {
                    showToast(result.error, 'error');
                } else {
                    this.hubProfileModal = false;
                    await this.loadHubProfiles();
                    showToast(`Profile "${name}" saved`, 'success');
                }
            } catch (e) {
                showToast(_k('settings.hub.failed_to_save_profile', 'Failed to save profile: ') + e.message, 'error');
            }
            this.saving = false;
        },

        async deleteHubProfile(name) {
            if (!(await window.kazmaConfirm({
                title: _k('settings.hub.delete_profile', 'Delete profile'),
                message: _k('settings.hub.delete_profile_message', 'Delete profile "{name}"? This cannot be undone.', { name: name }),
                confirmText: _k('settings.hub.delete', 'Delete'),
                danger: true,
            }))) return;
            try {
                await window.kazmaSave(`/api/models/profiles/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadHubProfiles();
                showToast(`Profile "${name}" deleted`, 'success');
            } catch (e) {
                showToast(_k('settings.hub.failed_to_delete_profile', 'Failed to delete profile: ') + e.message, 'error');
            }
        },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
