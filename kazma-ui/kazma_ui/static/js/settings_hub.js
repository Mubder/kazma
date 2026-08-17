/** Settings mixin: hub — providers, models, hub providers/connectors/profiles */
(function (root) {
    "use strict";
    root.KazmaSettingsMixins = root.KazmaSettingsMixins || {};
    root.KazmaSettingsMixins.hub = function () {
        return {
        async loadProviders() {
            this.providers = await ProvidersManager.loadAll();
        },

        openAddProvider() {
            this.newProvider = { name: '', display_name: '', base_url: '', api_key: '', models: '', enabled: true };
            this.showProviderModal = true;
        },

        applyProviderPreset(presetKey) {
            const preset = ProvidersManager.getPreset(presetKey);
            if (preset) {
                this.newProvider.name = presetKey;
                this.newProvider.display_name = preset.name;
                this.newProvider.base_url = preset.base_url;
            }
        },

        async saveProvider() {
            if (!this.newProvider.name || !this.newProvider.base_url) {
                showToast('Name and Base URL are required', 'error');
                return;
            }
            this.saving = true;
            try {
                const data = { ...this.newProvider };
                if (typeof data.models === 'string') {
                    data.models = data.models.split(',').map(m => m.trim()).filter(Boolean);
                }
                await ProvidersManager.add(data);
                this.showProviderModal = false;
                await this.loadProviders();
                showToast('Provider added', 'success');
            } catch (e) {
                showToast('Failed to add provider: ' + e.message, 'error');
            }
            this.saving = false;
        },

        async deleteProvider(name) {
            if (!(await window.kazmaConfirm({
                title: 'Delete provider',
                message: `Delete provider "${name}"? This cannot be undone.`,
                confirmText: 'Delete',
                danger: true,
            }))) return;
            await ProvidersManager.remove(name);
            await this.loadProviders();
            showToast('Provider removed', 'success');
        },

        async toggleProvider(name, enabled) {
            await ProvidersManager.toggle(name, enabled);
            await this.loadProviders();
        },

        async testProvider(name) {
            this.testingProvider = name;
            this.providerTestResult = null;
            const result = await ProvidersManager.test(name);
            this.providerTestResult = { name, ...result };
            this.testingProvider = null;
            // Auto-clear after 8s
            setTimeout(() => { if (this.providerTestResult?.name === name) this.providerTestResult = null; }, 8000);
        },

        async fetchModels() {
            if (!this.currentModel.base_url) { showToast('Enter a base URL first', 'error'); return; }
            this.fetchingModels = true;
            try {
                const data = await ModelsManager.discover(this.modelProvider, this.currentModel.base_url, this.currentModel.api_key);
                if (data.error) {
                    showToast(data.error, 'error');
                } else if (data.models && data.models.length) {
                    this.availableModels = data.models;
                    showToast(data.models.length + ' models found', 'success');
                } else {
                    showToast('No models returned. Check your API key.', 'error');
                }
            } catch (e) {
                showToast('Fetch failed: ' + e.message, 'error');
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
                await fetch('/api/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(updates),
                });
                // Reconfigure the live LLM provider so subsequent chat
                // requests use the new model/base_url/api_key (Bug 3 fix).
                try {
                    await fetch('/api/provider/switch', {
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

                showToast('Model settings saved', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
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
            showToast(`Default for "${taskType}" set to ${this.modelDefaults[taskType]}`, 'success');
        },

        async saveModelProfile() {
            const name = (this.profileName || '').trim();
            if (!name) { showToast('Enter a profile name', 'error'); return; }
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
                showToast('Failed to save profile: ' + e.message, 'error');
            }
        },

        async deleteModelProfile(name) {
            if (!(await window.kazmaConfirm({
                title: 'Delete profile',
                message: `Delete profile "${name}"? This cannot be undone.`,
                confirmText: 'Delete',
                danger: true,
            }))) return;
            try {
                await fetch(`/api/models/saved/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadSavedModels();
                showToast(`Profile "${name}" deleted`, 'success');
            } catch (e) {
                showToast('Failed to delete profile: ' + e.message, 'error');
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
            showToast(`Loaded profile "${name}"`, 'success');
        },

        async runModelComparison() {
            if (!this.comparePrompt || this.compareModels.length === 0) {
                showToast('Enter a prompt and select models', 'error');
                return;
            }
            this.comparing = true;
            try {
                this.compareResults = await ModelsManager.compare(this.comparePrompt, this.compareModels);
            } catch (e) {
                showToast('Comparison failed: ' + e.message, 'error');
            }
            this.comparing = false;
        },

        async saveConnector(platform) {
            this.saving = true;
            try {
                await fetch('/api/settings/connectors', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ platform, settings: this.connectors[platform] || {} }),
                });
                // Auto-refresh gateway adapters so the new connector config
                // takes effect immediately (no manual server restart needed).
                try {
                    const refreshResp = await fetch('/api/gateway/refresh-adapters', { method: 'POST' });
                    const refreshData = await refreshResp.json();
                    showToast(`${platform} settings saved. Gateway refreshed (${refreshData.adapters_count || 0} adapters).`, 'success');
                } catch (refreshErr) {
                    console.warn('[Settings] Gateway refresh failed:', refreshErr);
                    showToast(`${platform} settings saved, but gateway refresh failed. Use "Refresh Gateway" button.`, 'warning');
                }
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        async refreshGateway() {
            this.saving = true;
            try {
                const resp = await fetch('/api/gateway/refresh-adapters', { method: 'POST' });
                const data = await resp.json();
                if (resp.ok) {
                    const names = (data.adapters || []).join(', ') || 'none';
                    showToast(`Gateway refreshed — ${data.adapters_count || 0} adapter(s): ${names}`, 'success');
                } else {
                    showToast('Gateway refresh failed: ' + (data.detail || resp.statusText), 'error');
                }
            } catch (e) {
                showToast('Gateway refresh failed: ' + e.message, 'error');
            }
            this.saving = false;
        },

        async testConnector(platform) {
            this.testingConnector = platform;
            try {
                const resp = await fetch('/api/settings/connectors/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ platform }),
                });
                const result = await resp.json();
                showToast(result.success ? `${platform}: Connected!` : `${platform}: ${result.error}`, result.success ? 'success' : 'error');
            } catch (e) {
                showToast(`Test failed: ${e.message}`, 'error');
            }
            this.testingConnector = null;
        },

        async loadHubProviders() {
            try {
                const raw = await this._fetch('/api/providers');
                if (!raw) throw new Error('providers unavailable');
                // Normalize so Alpine x-for always gets string arrays
                this.hubProviders = (Array.isArray(raw) ? raw : []).map(function (p) {
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
                discord: { guild_id: '' },
                slack: { app_token: '', workspace: '' },
                email: { smtp_host: '', smtp_port: '', username: '', password: '', imap_host: '' },
                webhook: { incoming_url: '', outgoing_url: '', secret: '' },
            };
            return { ...(defaults[name] || {}) };
        },

        openHubProviderModal(name) {
            this.hubProviderTested = false;
            this.hubShowProviderKey = false;
            this.hubTestResult = null;
            if (name) {
                const p = this.hubProviders.find(x => x.name === name);
                if (p) {
                    this.hubEditingProvider = {
                        name: p.name,
                        display_name: p.display_name || '',
                        base_url: p.base_url || '',
                        api_key: p.api_key || '',
                        models: Array.isArray(p.models) ? p.models.join(', ') : (p.models || ''),
                        enabled: p.enabled !== false,
                        google_mode: p.google_mode || (p.project_id ? 'vertex_ai' : 'ai_studio'),
                        project_id: p.project_id || '',
                        location: p.location || 'us-central1',
                        _existing: true,
                    };
                    this.hubProviderTested = true; // editing an existing tested provider is acceptable
                }
            } else {
                this.hubEditingProvider = { name: '', display_name: '', base_url: '', api_key: '', models: '', enabled: true, google_mode: 'ai_studio', project_id: '', location: 'us-central1', _existing: false };
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
                showToast('Name and Base URL are required', 'error');
                return;
            }
            this.saving = true;
            try {
                const data = { ...this.hubEditingProvider };
                if (typeof data.models === 'string') {
                    data.models = data.models.split(',').map(m => m.trim()).filter(Boolean);
                }
                delete data._existing;
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
                    showToast('Provider saved', 'success');
                }
            } catch (e) {
                showToast('Failed to save provider: ' + e.message, 'error');
            }
            this.saving = false;
        },

        async deleteHubProvider(name) {
            if (!(await window.kazmaConfirm({
                title: 'Delete provider',
                message: `Delete provider "${name}"? This cannot be undone.`,
                confirmText: 'Delete',
                danger: true,
            }))) return;
            try {
                await fetch(`/api/providers/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadHubProviders();
                showToast('Provider removed', 'success');
            } catch (e) {
                showToast('Failed to delete provider: ' + e.message, 'error');
            }
        },

        async toggleHubProvider(name, enabled) {
            try {
                await fetch(`/api/providers/${encodeURIComponent(name)}/toggle`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ enabled }),
                });
                await this.loadHubProviders();
            } catch (e) {
                showToast('Toggle failed: ' + e.message, 'error');
            }
        },

        async testHubProvider(name) {
            this.hubTestingProvider = name;
            this.hubTestResult = { type: 'provider' };
            try {
                const resp = await fetch(`/api/providers/${encodeURIComponent(name)}/test`, { method: 'POST' });
                const result = await resp.json();
                let success = !!result.success;
                let errorMsg = result.error || result.detail || (resp.ok ? null : `HTTP ${resp.status}`);
                if (typeof errorMsg === 'object') {
                    errorMsg = JSON.stringify(errorMsg);
                }
                this.hubTestResult = { type: 'provider', success, error: errorMsg, ...result };
            } catch (e) {
                this.hubTestResult = { type: 'provider', success: false, error: e.message };
            }
            this.hubTestingProvider = null;
        },

        async testHubProviderFromModal() {
            const name = this.hubEditingProvider.name;
            if (!name || (!this.hubEditingProvider.base_url && name !== 'google')) {
                showToast('Enter a provider name and base URL first', 'error');
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
                const resp = await fetch(`/api/providers/${encodeURIComponent(name)}/test`, { method: 'POST' });
                const result = await resp.json();
                let success = !!result.success;
                let errorMsg = result.error || result.detail || (resp.ok ? null : `HTTP ${resp.status}`);
                if (typeof errorMsg === 'object') {
                    errorMsg = JSON.stringify(errorMsg);
                }
                this.hubTestResult = { type: 'provider', success, error: errorMsg, ...result };
                this.hubProviderTested = true; // attempt completed: enable save
                if (success) {
                    showToast('Connection test succeeded', 'success');
                } else {
                    showToast(`Test failed: ${errorMsg}`, 'error');
                }
            } catch (e) {
                this.hubTestResult = { type: 'provider', success: false, error: e.message };
                this.hubProviderTested = true; // attempt completed on exception: enable save
                showToast('Test failed: ' + e.message, 'error');
            }
            this.hubTestingProvider = null;
        },

        async discoverHubProvider(name) {
            this.hubDiscoveringProvider = name;
            try {
                const resp = await fetch(`/api/providers/${encodeURIComponent(name)}/discover`, { method: 'POST' });
                const data = await resp.json();
                const count = data.count || 0;
                showToast(`${count} models discovered`, count > 0 ? 'success' : 'warning');
                await this.loadHubProviders();
            } catch (e) {
                showToast('Discover failed: ' + e.message, 'error');
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
                await fetch(`/api/providers/${encodeURIComponent(providerName)}/select-models`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ models: p.selected_models }),
                });
            } catch (e) {
                showToast('Failed to save model selection', 'error');
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
                await fetch(`/api/providers/${encodeURIComponent(providerName)}/select-models`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ models: p.selected_models }),
                });
            } catch (e) {
                showToast('Failed to save model selection', 'error');
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
                    showToast(`${model} is not in the list`, 'info');
                } else {
                    showToast('Failed to remove model', 'error');
                }
                await this.loadHubProviders();
            } catch (e) {
                showToast('Failed to remove model: ' + e.message, 'error');
            }
        },

        async clearHubDiscovered(providerName) {
            if (!(await window.kazmaConfirm({
                title: 'Clear discovered models',
                message: `Clear discovered models for "${providerName}"? Your selected models for this provider will also be cleared.`,
                confirmText: 'Clear',
                danger: true,
            }))) return;
            try {
                const resp = await fetch(`/api/providers/${encodeURIComponent(providerName)}/clear-discovered`, {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                if (resp.ok) {
                    showToast('Cleared discovered models', 'success');
                    await this.loadHubProviders();
                } else {
                    showToast('Failed to clear models', 'error');
                }
            } catch (e) {
                showToast('Failed to clear models: ' + e.message, 'error');
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
                showToast('Connector name is required', 'error');
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
                    try {
                        await fetch('/api/gateway/refresh-adapters', { method: 'POST' });
                    } catch (refreshErr) {
                        console.warn('[Hub] Gateway refresh failed:', refreshErr);
                    }
                    showToast('Connector saved', 'success');
                }
            } catch (e) {
                showToast('Failed to save connector: ' + e.message, 'error');
            }
            this.saving = false;
        },

        async deleteHubConnector(name) {
            if (!(await window.kazmaConfirm({
                title: 'Delete connector',
                message: `Delete connector "${name}"? This cannot be undone.`,
                confirmText: 'Delete',
                danger: true,
            }))) return;
            try {
                await fetch(`/api/connectors/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadHubConnectors();
                showToast('Connector removed', 'success');
            } catch (e) {
                showToast('Failed to delete connector: ' + e.message, 'error');
            }
        },

        async toggleHubConnector(name, enabled) {
            try {
                await fetch(`/api/connectors/${encodeURIComponent(name)}/toggle`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ enabled }),
                });
                await this.loadHubConnectors();
            } catch (e) {
                showToast('Toggle failed: ' + e.message, 'error');
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
                showToast('Select a connector name first', 'error');
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
                await fetch('/api/connectors', {
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
                showToast(result.success ? 'Connection test succeeded' : `Test failed: ${result.error}`, result.success ? 'success' : 'error');
            } catch (e) {
                this.hubTestResult = { type: 'connector', success: false, error: e.message };
                showToast('Test failed: ' + e.message, 'error');
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
            showToast(`Loaded profile "${name}"`, 'success');
        },

        async saveHubProfile() {
            const name = (this.hubEditingProfile.name || '').trim();
            if (!name) { showToast('Profile name is required', 'error'); return; }
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
                showToast('Failed to save profile: ' + e.message, 'error');
            }
            this.saving = false;
        },

        async deleteHubProfile(name) {
            if (!(await window.kazmaConfirm({
                title: 'Delete profile',
                message: `Delete profile "${name}"? This cannot be undone.`,
                confirmText: 'Delete',
                danger: true,
            }))) return;
            try {
                await fetch(`/api/models/profiles/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadHubProfiles();
                showToast(`Profile "${name}" deleted`, 'success');
            } catch (e) {
                showToast('Failed to delete profile: ' + e.message, 'error');
            }
        },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
