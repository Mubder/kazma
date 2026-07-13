/**
 * Settings.js — Alpine.js state management for the 12-tab Kazma Settings panel.
 * Each tab section is a separate method/object for clean separation.
 */

/* ══════════════════════════════════════════════════════════════════════════
   Alpine.js App: settingsApp()
   ══════════════════════════════════════════════════════════════════════════ */

function settingsApp() {
    return {
        // ── Global State ──
        tab: 'providers_connectors',
        loading: false,
        saving: false,

        // ── Providers Tab ──
        providers: [],
        providerPresets: [],
        newProvider: { name: '', display_name: '', base_url: '', api_key: '', models: '', enabled: true },
        showProviderModal: false,
        testingProvider: null,
        providerTestResult: null,

        // ── Models Tab ──
        modelRegistry: [],
        modelDefaults: { chat: '', code: '', summarize: '', translate: '' },
        modelUsage: {},
        availableModels: [],
        modelProvider: '',
        modelBaseUrl: '',
        modelApiKey: '',
        fetchingModels: false,
        comparePrompt: 'Hello, how are you?',
        compareModels: [],
        compareResults: [],
        comparing: false,
        currentModel: { base_url: '', api_key: '', model: '', max_tokens: 4096, temperature: 0.7, timeout: 60 },
        showKey: false,
        testing: false,
        testResult: null,

        // ── Saved Model Profiles ──
        savedModels: [],
        profileName: '',

        // ── Agent Tab ──
        agent: { name: 'kazma', language: 'ar', system_prompt: '', personality: 'default' },
        personalities: [],
        safety: { hitl_enabled: true, require_approval_for: [], approval_timeout: 60, auto_deny_on_timeout: true },
        context: { max_context_tokens: 128000, context_strategy: 'sliding_window', summarization_threshold: 0.8 },

        // ── Connectors Tab ──
        connectors: { telegram: {}, discord: {}, slack: {}, email: {}, webhook: {} },
        connectorStatuses: {},
        testingConnector: null,
        showTelegramToken: false,

        // ── Unified Providers & Connectors Hub ──
        hubSubtab: 'providers',
        hubProviders: [],
        hubConnectors: [],
        hubProfiles: [],
        hubProviderModal: false,
        hubConnectorModal: false,
        hubProfileModal: false,
        hubEditingProvider: { name: '', display_name: '', base_url: '', api_key: '', models: '', enabled: true, _existing: false },
        hubEditingConnector: { name: '', token: '', enabled: true, extras: {} },
        hubEditingProfile: { name: '', provider: '', base_url: '', api_key: '', model: '', _existing: false },
        hubShowProviderKey: false,
        hubShowConnectorToken: false,
        hubShowProfileKey: false,
        hubProviderTested: false,
        hubConnectorTested: false,
        hubTestingProvider: null,
        hubTestingConnector: null,
        hubDiscoveringProvider: null,
        hubTestResult: null,

        // ── MCP Tab ──
        mcpServers: [],
        showMcpModal: false,
        newMcpServer: { name: '', transport: 'stdio', command: '', url: '', env: '' },
        testingMcp: null,

        // ── Skills Tab ──
        skills: [],
        skillFilter: '',

        // ── Appearance Tab ──
        appearance: { theme: 'dark', accent_color: '#5e6ad2', font_size: 14, sidebar_position: 'left', custom_css: '' },

        // ── Shortcuts Tab ──
        shortcuts: {},
        shortcutConflicts: [],
        capturingAction: null,

        // ── Account Tab ──
        account: { username: 'admin', created_at: '' },
        apiTokens: [],
        sessions: [],
        passwordForm: { old_password: '', new_password: '', confirm_password: '' },
        tokenName: '',

        // ── Tools Tab ──
        tools: [],
        toolSearch: '',
        selectedTool: null,
        toolTestArgs: '{}',
        toolTestResult: null,

        // ── System Tab ──
        logs: [],
        logLines: 100,
        diagnostics: {},
        updateInfo: null,
        vaultStatus: { enabled: false, secret_count: 0 },

        // ── Import/Export Tab ──
        exportFormat: 'yaml',
        importData: '',
        importFormat: 'yaml',
        importSelective: false,
        importSections: [],
        availableSections: ['model', 'agent', 'connectors', 'mcp', 'skills', 'appearance', 'shortcuts', 'tools', 'safety'],

        /* ══════════════════════════════════════════════════════════════════
           INITIALIZATION
           ══════════════════════════════════════════════════════════════════ */

        async init() {
            this.loading = true;
            try {
                // Load all settings in parallel
                const [settings, providers, personalities, shortcuts] = await Promise.all([
                    this._fetch('/api/settings'),
                    this._fetch('/api/settings/providers'),
                    this._fetch('/api/settings/agent/personalities'),
                    this._fetch('/api/settings/shortcuts'),
                ]);

                if (settings) {
                    if (settings.model) Object.assign(this.currentModel, settings.model);
                    if (settings.agent) Object.assign(this.agent, settings.agent);
                    if (settings.connectors) Object.assign(this.connectors, settings.connectors);
                    if (settings.appearance) {
                        Object.assign(this.appearance, settings.appearance);
                        if (settings.appearance.font_size) {
                            const root = Alpine.$data(document.querySelector('[x-data]'));
                            if (root) root.fontSize = settings.appearance.font_size;
                        }
                    }
                    if (settings.safety) Object.assign(this.safety, settings.safety);
                    if (settings.context) Object.assign(this.context, settings.context);
                }
                if (Array.isArray(providers)) this.providers = providers;
                if (Array.isArray(personalities)) this.personalities = personalities;
                if (shortcuts && typeof shortcuts === 'object') this.shortcuts = shortcuts;

                // Load model defaults
                const defaults = await this._fetch('/api/settings/models/defaults');
                if (defaults) Object.assign(this.modelDefaults, defaults);

                // Load saved model profiles
                const saved = await this._fetch('/api/models/saved');
                if (Array.isArray(saved)) this.savedModels = saved;

                // Load provider presets
                this.providerPresets = ProvidersManager.getPresetKeys();

                // Load unified hub data
                await Promise.all([
                    this.loadHubProviders(),
                    this.loadHubConnectors(),
                    this.loadHubProfiles(),
                ]);
            } catch (e) {
                console.error('[Settings] Init failed:', e);
            }
            this.loading = false;
        },

        /* ══════════════════════════════════════════════════════════════════
           PROVIDERS TAB
           ══════════════════════════════════════════════════════════════════ */

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
            if (!confirm(`Delete provider "${name}"?`)) return;
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

        /* ══════════════════════════════════════════════════════════════════
           MODELS TAB
           ══════════════════════════════════════════════════════════════════ */

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
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updates),
                });
                // Reconfigure the live LLM provider so subsequent chat
                // requests use the new model/base_url/api_key (Bug 3 fix).
                try {
                    await fetch('/api/provider/switch', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
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
                    headers: { 'Content-Type': 'application/json' },
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

        /* ══════════════════════════════════════════════════════════════════
           SAVED MODEL PROFILES
           ══════════════════════════════════════════════════════════════════ */

        async saveModelProfile() {
            const name = (this.profileName || '').trim();
            if (!name) { showToast('Enter a profile name', 'error'); return; }
            try {
                const resp = await fetch('/api/models/saved', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
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
            if (!confirm(`Delete profile "${name}"?`)) return;
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

        /** Load a saved profile's fields into the current model form. */
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

        /* ══════════════════════════════════════════════════════════════════
           AGENT TAB
           ══════════════════════════════════════════════════════════════════ */

        async saveAgent() {
            this.saving = true;
            try {
                await fetch('/api/settings/agent', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
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
                    headers: { 'Content-Type': 'application/json' },
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
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.context),
                });
                showToast('Context settings saved', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        /* ══════════════════════════════════════════════════════════════════
           CONNECTORS TAB
           ══════════════════════════════════════════════════════════════════ */

        async saveConnector(platform) {
            this.saving = true;
            try {
                await fetch('/api/settings/connectors', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
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

        /**
         * Manually trigger a gateway adapter refresh via the
         * /api/gateway/refresh-adapters endpoint. Re-reads connector tokens
         * from the config store and hot-reloads all adapters without
         * restarting the server.
         */
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
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ platform }),
                });
                const result = await resp.json();
                showToast(result.success ? `${platform}: Connected!` : `${platform}: ${result.error}`, result.success ? 'success' : 'error');
            } catch (e) {
                showToast(`Test failed: ${e.message}`, 'error');
            }
            this.testingConnector = null;
        },

        /* ══════════════════════════════════════════════════════════════════
           UNIFIED PROVIDERS & CONNECTORS HUB
           ══════════════════════════════════════════════════════════════════ */

        async loadHubProviders() {
            try {
                const resp = await fetch('/api/providers');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                this.hubProviders = await resp.json();
            } catch (e) {
                console.error('[Hub] Failed to load providers:', e);
                this.hubProviders = [];
            }
        },

        async loadHubConnectors() {
            try {
                const resp = await fetch('/api/connectors');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                this.hubConnectors = await resp.json();
            } catch (e) {
                console.error('[Hub] Failed to load connectors:', e);
                this.hubConnectors = [];
            }
        },

        async loadHubProfiles() {
            try {
                const resp = await fetch('/api/models/profiles');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                this.hubProfiles = await resp.json();
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
                        _existing: true,
                    };
                    this.hubProviderTested = true; // editing an existing tested provider is acceptable
                }
            } else {
                this.hubEditingProvider = { name: '', display_name: '', base_url: '', api_key: '', models: '', enabled: true, _existing: false };
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
            if (!this.hubEditingProvider.name || !this.hubEditingProvider.base_url) {
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
                    headers: { 'Content-Type': 'application/json' },
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
            if (!confirm(`Delete provider "${name}"?`)) return;
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
                    headers: { 'Content-Type': 'application/json' },
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
            if (!name || !this.hubEditingProvider.base_url) {
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
                    headers: { 'Content-Type': 'application/json' },
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
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ models: p.selected_models }),
                });
            } catch (e) {
                showToast('Failed to save model selection', 'error');
            }
        },

        async toggleAllModels(providerName) {
            const p = this.hubProviders.find(x => x.name === providerName);
            if (!p || !p.discovered_models) return;
            const allSelected = (p.selected_models || []).length === p.discovered_models.length;
            p.selected_models = allSelected ? [] : [...p.discovered_models];
            try {
                await fetch(`/api/providers/${encodeURIComponent(providerName)}/select-models`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ models: p.selected_models }),
                });
            } catch (e) {
                showToast('Failed to save model selection', 'error');
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
                    headers: { 'Content-Type': 'application/json' },
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
            if (!confirm(`Delete connector "${name}"?`)) return;
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
                    headers: { 'Content-Type': 'application/json' },
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
                    headers: { 'Content-Type': 'application/json' },
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
                    headers: { 'Content-Type': 'application/json' },
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
            if (!confirm(`Delete profile "${name}"?`)) return;
            try {
                await fetch(`/api/models/profiles/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadHubProfiles();
                showToast(`Profile "${name}" deleted`, 'success');
            } catch (e) {
                showToast('Failed to delete profile: ' + e.message, 'error');
            }
        },

        /* ══════════════════════════════════════════════════════════════════
           MCP TAB
           ══════════════════════════════════════════════════════════════════ */

        async loadMcpServers() {
            try {
                this.mcpServers = await this._fetch('/api/mcp/servers') || [];
            } catch (e) {
                this.mcpServers = [];
            }
        },

        openAddMcpServer() {
            this.newMcpServer = { name: '', transport: 'stdio', command: '', url: '', env: '' };
            this.showMcpModal = true;
        },

        async saveMcpServer() {
            if (!this.newMcpServer.name) { showToast('Server name is required', 'error'); return; }
            this.saving = true;
            try {
                const data = { ...this.newMcpServer };
                if (data.command && typeof data.command === 'string') data.command = data.command.split(/\s+/);
                if (data.env && typeof data.env === 'string') {
                    try { data.env = JSON.parse(data.env); } catch { data.env = {}; }
                }
                await fetch('/api/settings/mcp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data),
                });
                this.showMcpModal = false;
                await this.loadMcpServers();
                showToast('MCP server added', 'success');
            } catch (e) {
                showToast('Failed to add server: ' + e.message, 'error');
            }
            this.saving = false;
        },

        async deleteMcpServer(name) {
            if (!confirm(`Remove MCP server "${name}"?`)) return;
            await fetch(`/api/settings/mcp/${encodeURIComponent(name)}`, { method: 'DELETE' });
            await this.loadMcpServers();
            showToast('Server removed', 'success');
        },

        async toggleMcpServer(name, enabled) {
            await fetch(`/api/settings/mcp/${encodeURIComponent(name)}/toggle`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled }),
            });
            await this.loadMcpServers();
        },

        async testMcpServer(name) {
            this.testingMcp = name;
            try {
                const resp = await fetch(`/api/settings/mcp/${encodeURIComponent(name)}/test`, { method: 'POST' });
                const result = await resp.json();
                showToast(result.success ? `${name}: ${result.tool_count} tools found` : `${name}: ${result.error}`,
                    result.success ? 'success' : 'error');
            } catch (e) {
                showToast(`Test failed: ${e.message}`, 'error');
            }
            this.testingMcp = null;
        },

        /* ══════════════════════════════════════════════════════════════════
           SKILLS TAB
           ══════════════════════════════════════════════════════════════════ */

        async loadSkills() {
            try {
                this.skills = await this._fetch('/api/skills') || [];
            } catch (e) {
                this.skills = [];
            }
        },

        async toggleSkill(skillId, enabled) {
            await fetch(`/api/settings/skills/${encodeURIComponent(skillId)}/toggle`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled }),
            });
            await this.loadSkills();
        },

        async uninstallSkill(skillId) {
            if (!confirm(`Uninstall skill "${skillId}"?`)) return;
            await fetch(`/api/settings/skills/${encodeURIComponent(skillId)}`, { method: 'DELETE' });
            await this.loadSkills();
            showToast('Skill uninstalled', 'success');
        },

        get filteredSkills() {
            if (!this.skillFilter) return this.skills;
            const q = this.skillFilter.toLowerCase();
            return this.skills.filter(s =>
                (s.name || '').toLowerCase().includes(q) ||
                (s.description || '').toLowerCase().includes(q)
            );
        },

        /* ══════════════════════════════════════════════════════════════════
           APPEARANCE TAB
           ══════════════════════════════════════════════════════════════════ */

        async saveAppearance() {
            this.saving = true;
            try {
                await fetch('/api/settings/appearance', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.appearance),
                });
                // Apply theme immediately
                const root = Alpine.$data(document.querySelector('[x-data]'));
                if (root) root.fontSize = this.appearance.font_size;
                if (this.appearance.theme === 'light') {
                    document.documentElement.setAttribute('data-theme', 'light');
                } else if (this.appearance.theme === 'dark') {
                    document.documentElement.setAttribute('data-theme', 'dark');
                }
                showToast('Appearance saved', 'success');
            } catch (e) {
                showToast('Save failed', 'error');
            }
            this.saving = false;
        },

        previewTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
        },

        applyFontSize(size) {
            this.appearance.font_size = size;
            const root = Alpine.$data(document.querySelector('[x-data]'));
            if (root) root.fontSize = size;
        },

        /* ══════════════════════════════════════════════════════════════════
           SHORTCUTS TAB
           ══════════════════════════════════════════════════════════════════ */

        /**
         * Capture a keyboard shortcut from a keydown event.
         * Prevents default, detects modifier keys, and formats the
         * combination string (e.g. "Ctrl+Shift+K").
         *
         * @param {string} action - The shortcut action name.
         * @param {KeyboardEvent} event - The keydown event.
         */
        captureShortcut(action, event) {
            this.capturingAction = action;

            // Ignore lone modifier presses — wait for the actual key
            const modifierKeys = ['Control', 'Alt', 'Shift', 'Meta'];
            if (modifierKeys.includes(event.key)) {
                return;
            }

            const parts = [];
            if (event.ctrlKey) parts.push('Ctrl');
            if (event.altKey) parts.push('Alt');
            if (event.shiftKey) parts.push('Shift');
            if (event.metaKey) parts.push((typeof navigator !== 'undefined' && navigator.platform.includes('Mac')) ? 'Cmd' : 'Meta');

            // Normalize the key name
            let keyName = event.key;
            // Capitalize single letters
            if (keyName.length === 1) {
                keyName = keyName.toUpperCase();
            }
            // Handle special keys
            const specialMap = {
                ' ': 'Space',
                'ArrowUp': 'Up',
                'ArrowDown': 'Down',
                'ArrowLeft': 'Left',
                'ArrowRight': 'Right',
                'Escape': 'Esc',
                'Delete': 'Del',
                'Backspace': 'Backspace',
                'Enter': 'Enter',
                'Tab': 'Tab',
            };
            keyName = specialMap[keyName] || keyName;

            parts.push(keyName);
            const combo = parts.join('+');

            this.saveShortcut(action, combo);
            this.capturingAction = null;
        },

        /**
         * Clear the capturing hint when the input loses focus.
         */
        clearCaptureHint(action) {
            if (this.capturingAction === action) {
                this.capturingAction = null;
            }
        },

        async saveShortcut(action, keys) {
            this.shortcuts[action] = keys;
            await fetch('/api/settings/shortcuts', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, keys }),
            });
            this.shortcutConflicts = this.detectConflicts();
            showToast(`Shortcut for "${action}" updated`, 'success');
        },

        async resetShortcuts() {
            if (!confirm('Reset all shortcuts to defaults?')) return;
            await fetch('/api/settings/shortcuts/reset', { method: 'POST' });
            this.shortcuts = await this._fetch('/api/settings/shortcuts') || {};
            this.shortcutConflicts = [];
            showToast('Shortcuts reset', 'success');
        },

        detectConflicts() {
            const conflicts = [];
            const values = Object.entries(this.shortcuts);
            for (let i = 0; i < values.length; i++) {
                for (let j = i + 1; j < values.length; j++) {
                    if (values[i][1] && values[j][1] && values[i][1] === values[j][1]) {
                        conflicts.push({ action1: values[i][0], action2: values[j][0], keys: values[i][1] });
                    }
                }
            }
            return conflicts;
        },

        /* ══════════════════════════════════════════════════════════════════
           ACCOUNT TAB
           ══════════════════════════════════════════════════════════════════ */

        async loadAccount() {
            const [tokens, sessions] = await Promise.all([
                this._fetch('/api/settings/account/tokens'),
                this._fetch('/api/settings/account/sessions'),
            ]);
            if (Array.isArray(tokens)) this.apiTokens = tokens;
            if (Array.isArray(sessions)) this.sessions = sessions;
        },

        async changePassword() {
            if (this.passwordForm.new_password !== this.passwordForm.confirm_password) {
                showToast('Passwords do not match', 'error');
                return;
            }
            if (this.passwordForm.new_password.length < 8) {
                showToast('Password must be at least 8 characters', 'error');
                return;
            }
            try {
                const resp = await fetch('/api/settings/account/password', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ old_password: this.passwordForm.old_password, new_password: this.passwordForm.new_password }),
                });
                const data = await resp.json();
                if (resp.ok) {
                    showToast('Password changed', 'success');
                    this.passwordForm = { old_password: '', new_password: '', confirm_password: '' };
                } else {
                    showToast(data.error || 'Failed', 'error');
                }
            } catch (e) {
                showToast('Failed: ' + e.message, 'error');
            }
        },

        async createToken() {
            if (!this.tokenName) { showToast('Token name required', 'error'); return; }
            try {
                const resp = await fetch('/api/settings/account/tokens', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: this.tokenName }),
                });
                const data = await resp.json();
                showToast('Token created: ' + (data.token || '').substring(0, 12) + '...', 'success');
                this.tokenName = '';
                await this.loadAccount();
            } catch (e) {
                showToast('Failed: ' + e.message, 'error');
            }
        },

        async revokeToken(tokenId) {
            if (!confirm('Revoke this token?')) return;
            await fetch(`/api/settings/account/tokens/${tokenId}`, { method: 'DELETE' });
            await this.loadAccount();
            showToast('Token revoked', 'success');
        },

        /* ══════════════════════════════════════════════════════════════════
           TOOLS TAB
           ══════════════════════════════════════════════════════════════════ */

        async loadTools() {
            try {
                this.tools = await this._fetch('/api/settings/tools') || [];
            } catch (e) {
                this.tools = [];
            }
        },

        async toggleTool(toolName, enabled) {
            await fetch(`/api/settings/tools/${encodeURIComponent(toolName)}/toggle`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled }),
            });
            await this.loadTools();
        },

        async testTool(toolName) {
            let args = {};
            try { args = JSON.parse(this.toolTestArgs); } catch { showToast('Invalid JSON arguments', 'error'); return; }
            this.toolTestResult = null;
            try {
                const resp = await fetch(`/api/settings/tools/${encodeURIComponent(toolName)}/test`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ arguments: args }),
                });
                this.toolTestResult = await resp.json();
            } catch (e) {
                this.toolTestResult = { error: e.message };
            }
        },

        get filteredTools() {
            if (!this.toolSearch) return this.tools;
            const q = this.toolSearch.toLowerCase();
            return this.tools.filter(t =>
                (t.name || '').toLowerCase().includes(q) ||
                (t.description || '').toLowerCase().includes(q) ||
                (t.category || '').toLowerCase().includes(q)
            );
        },

        /* ══════════════════════════════════════════════════════════════════
           SYSTEM TAB
           ══════════════════════════════════════════════════════════════════ */

        async loadLogs() {
            try {
                const data = await this._fetch(`/api/settings/system/logs?lines=${this.logLines}`);
                this.logs = data?.lines || [];
            } catch (e) {
                this.logs = [];
            }
        },

        async loadDiagnostics() {
            try {
                this.diagnostics = await this._fetch('/api/settings/system/diagnostics') || {};
            } catch (e) {
                this.diagnostics = { error: e.message };
            }
        },

        async loadVaultStatus() {
            try {
                this.vaultStatus = await this._fetch('/api/settings/vault/status') || { enabled: false, secret_count: 0 };
            } catch (e) {
                this.vaultStatus = { enabled: false, secret_count: 0 };
            }
        },

        async checkUpdates() {
            try {
                this.updateInfo = await this._fetch('/api/settings/system/updates');
            } catch (e) {
                this.updateInfo = { error: e.message };
            }
        },

        async createBackup() {
            try {
                const resp = await fetch('/api/settings/system/backup');
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `kazma-backup-${new Date().toISOString().split('T')[0]}.yaml`;
                a.click();
                URL.revokeObjectURL(url);
                showToast('Backup downloaded', 'success');
            } catch (e) {
                showToast('Backup failed: ' + e.message, 'error');
            }
        },

        async systemReset() {
            if (!confirm('⚠️ This will reset ALL settings to defaults. Are you sure?')) return;
            if (!confirm('Final confirmation: Reset everything?')) return;
            try {
                await fetch('/api/settings/reset', { method: 'POST' });
                showToast('System reset complete. Reloading...', 'success');
                setTimeout(() => location.reload(), 1500);
            } catch (e) {
                showToast('Reset failed: ' + e.message, 'error');
            }
        },

        /* ══════════════════════════════════════════════════════════════════
           IMPORT/EXPORT TAB
           ══════════════════════════════════════════════════════════════════ */

        async exportConfig() {
            try {
                const url = `/api/settings/export?format=${this.exportFormat}`;
                const resp = await fetch(url);
                const blob = await resp.blob();
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = `kazma-config.${this.exportFormat}`;
                a.click();
                showToast('Configuration exported', 'success');
            } catch (e) {
                showToast('Export failed: ' + e.message, 'error');
            }
        },

        async importConfig() {
            if (!this.importData.trim()) { showToast('Paste or upload config data', 'error'); return; }
            this.saving = true;
            try {
                await fetch('/api/settings/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        data: this.importData,
                        format: this.importFormat,
                        selective: this.importSelective,
                        sections: this.importSections,
                    }),
                });
                showToast('Configuration imported. Reloading...', 'success');
                setTimeout(() => location.reload(), 1500);
            } catch (e) {
                showToast('Import failed: ' + e.message, 'error');
            }
            this.saving = false;
        },

        handleFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (e) => {
                this.importData = e.target.result;
                this.importFormat = file.name.endsWith('.json') ? 'json' : 'yaml';
            };
            reader.readAsText(file);
        },

        async resetToDefaults() {
            if (!confirm('Reset ALL settings to defaults?')) return;
            try {
                await fetch('/api/settings/reset', { method: 'POST' });
                showToast('Settings reset. Reloading...', 'success');
                setTimeout(() => location.reload(), 1500);
            } catch (e) {
                showToast('Reset failed: ' + e.message, 'error');
            }
        },

        /* ══════════════════════════════════════════════════════════════════
           HELPERS
           ══════════════════════════════════════════════════════════════════ */

        async _fetch(url) {
            try {
                const resp = await fetch(url);
                if (!resp.ok) return null;
                return await resp.json();
            } catch (e) {
                return null;
            }
        },

        /**
         * Tab change handler — lazy-load data when switching tabs.
         */
        async onTabChange(newTab) {
            this.tab = newTab;
            switch (newTab) {
                case 'providers': await this.loadProviders(); break;
                case 'providers_connectors':
                    await Promise.all([
                        this.loadHubProviders(),
                        this.loadHubConnectors(),
                        this.loadHubProfiles(),
                    ]);
                    break;
                case 'models': break; // Loaded on init
                case 'agent': break;
                case 'connectors': break;
                case 'mcp': await this.loadMcpServers(); break;
                case 'skills': await this.loadSkills(); break;
                case 'appearance': break;
                case 'shortcuts': this.shortcutConflicts = this.detectConflicts(); break;
                case 'account': await this.loadAccount(); break;
                case 'tools': await this.loadTools(); break;
                case 'system': await this.loadDiagnostics(); await this.loadLogs(); await this.loadVaultStatus(); break;
                case 'import': break;
            }
        },
    };
}
