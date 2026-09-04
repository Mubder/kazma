/** Settings mixin: core — state, init, _fetch, onTabChange, restartServer */
(function (root) {
    "use strict";
    root.KazmaSettingsMixins = root.KazmaSettingsMixins || {};
    root.KazmaSettingsMixins.core = function () {
        return {
        tab: 'providers_connectors',
        loading: true,
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
        agent: {
            name: 'kazma',
            language: 'ar',
            system_prompt: '',
            personality: 'default',
            max_iterations: 15,
            recursion_limit: 100,
            long_task_default_enabled: false,
            long_task_default_preset: 'research',
        },
        personalities: [],
        safety: { hitl_enabled: true, require_approval_for: [], approval_timeout: 300, auto_deny_on_timeout: true, soul_requires_confirm: false, outbound_allowed_targets: [] },
        soulPending: [],
        context: { max_context_tokens: 128000, context_strategy: 'sliding_window', summarization_threshold: 0.8 },
        nonstop: {
            enabled: false,
            stall_threshold_seconds: 180,
            tool_timeout_seconds: 120,
            max_recovery_attempts: 3,
            backoff_base_seconds: 5,
            backoff_max_seconds: 300,
            failover_enabled: false,
            failover_chain: '',
            failover_cooldown_seconds: 300,
            ledger_enabled: true,
        },
        documents: {
            enabled: true,
            shadow: false,
            default_authoritative: false,
            mode: '',
            intake_max_bytes: 52428800,
            intake_max_files: 10,
            ocr_enabled: true,
            worker_timeout_seconds: 300,
            worker_memory_mb: 1024,
            capacity_storage_free_floor_bytes: 536870912,
            security_malware_scan: 'auto',
            security_malware_fail_closed: false,
            gc_enabled: true,
            indexing_enabled: true,
            malware_probe: { available: false },
        },
        documentsSaving: false,
        documentsStatus: '',
        memoryTenantMode: 'shared',
        memoryBackends: {
            mode: 'local',
            embedder: { provider: 'local', model: 'BAAI/bge-m3', base_url: '', api_key: '', dim: 1024 },
            vector: { provider: 'sqlite_vec', url: '', api_key: '', collection: 'kazma_memory', dimension: 1024 },
            graph: { provider: 'sqlite', url: '', user: 'neo4j', password: '', api_key: '' },
            state: { provider: 'sqlite', url: '' },
            failover: { on_remote_error: 'local', timeout_ms: 5000 },
        },
        memoryBackendsSaving: false,
        memoryBackendsStatus: '',
        memoryBackendsCapability: {
            vector_write_ready: true,
            vector_status: 'full',
            vector_status_detail: '',
        },
        memoryBackendsStateCap: { detail: '' },
        memoryBackendsGraphCap: { detail: '' },
        memoryMergeKb: true,
        memoryPromoteKb: true,
        memorySmartSearch: false,
        memoryExplainRecall: true,
        memoryNeo4jStatus: '',
        memoryNeo4jOk: false,
        memoryStateSyncStatus: '',
        memoryStateSyncOk: false,
        logging: { level: 'INFO', format: 'text', retention_days: 7 },
        proxy: { provider: 'none', host: 'portal.anyip.io', port: '1080', username: '', password: '', network: 'mixed', country: '', session_sticky: false },
        proxyTestResult: null,
        proxyTesting: false,

        // ── Embedder Tab ──
        embedder: { provider: 'local', model: 'BAAI/bge-m3', dim: 1024, base_url: '', api_key_env: 'KAZMA_EMBED_API_KEY', _preset: 'BAAI/bge-m3' },
        embedderStatus: { config: {}, active: null, db: { episodes: {}, beliefs: {} } },
        embedderPresets: [
            { model: 'BAAI/bge-m3', dim: 1024, label: 'BAAI/bge-m3 — multilingual (recommended)' },
            { model: 'BAAI/bge-large-en-v1.5', dim: 1024, label: 'BAAI/bge-large-en-v1.5 — English' },
            { model: 'intfloat/multilingual-e5-large', dim: 1024, label: 'intfloat/multilingual-e5-large' },
            { model: 'Snowflake/snowflake-arctic-embed-l', dim: 1024, label: 'Snowflake arctic-embed-l (English)' },
            { model: 'nomic-ai/nomic-embed-text-v1.5', dim: 768, label: 'nomic-embed-text-v1.5' },
            { model: 'sentence-transformers/paraphrase-multilingual-mistral', dim: 768, label: 'paraphrase-multilingual-mistral' },
            { model: 'all-MiniLM-L6-v2', dim: 384, label: 'all-MiniLM-L6-v2 — lightweight (legacy)' },
        ],
        embedderSaving: false,
        embedderRestarting: false,
        embedderRebuilding: false,
        embedderRebuildStatus: { state: 'idle', model: '', total: 0, done: 0, error: null },
        _embedderPollTimer: null,

        // ── Time Travel (replay / fork) ──
        timeTravel: { max_snapshots: 50, retention_days: 30, auto_maintain: true },
        timeTravelEffective: null,
        timeTravelSaving: false,
        timeTravelRestarting: false,

        // ── Scheduled tasks timezone (cron) ──
        cronTz: { value: 'UTC', source: 'default' },
        cronTzSaving: false,
        cronTzNow: '',
        cronTzOffset: '',
        cronTzError: false,

        /* Every IANA zone the browser knows, not a curated dozen.
         *
         * The list was twelve hand-picked zones. It is a datalist, so an
         * operator in Asia/Karachi could always type their own -- but they
         * got no completion, no confirmation, and a typo only surfaced as a
         * 400 on save. Intl.supportedValuesOf gives roughly 400 zones on
         * any current browser; the hand-picked set stays as the fallback
         * for engines that lack it, and leads so the common choices are
         * still one keystroke away. */
        get cronTzCommon() {
            return [
                'UTC', 'Asia/Kuwait', 'Asia/Riyadh', 'Asia/Dubai', 'Asia/Qatar',
                'Africa/Cairo', 'Europe/London', 'Europe/Berlin', 'Europe/Istanbul',
                'America/New_York', 'America/Chicago', 'America/Los_Angeles',
            ];
        },

        get cronTzAll() {
            try {
                const all = Intl.supportedValuesOf('timeZone');
                if (Array.isArray(all) && all.length) return all;
            } catch (e) { /* older engine — the shortlist still works */ }
            return this.cronTzCommon;
        },

        /* Grouped by region for a real <select>.
         *
         * A free-text field with a datalist let you type a zone that does
         * not exist, which the backend then rejected on save -- correct,
         * but the wrong moment to find out. Four hundred options are a lot
         * for one flat list, so they are grouped by region with the common
         * ones first; native selects also support type-to-jump, so the
         * shortcut the datalist gave is not lost. A value that cannot be
         * typed cannot be mistyped. */
        get cronTzGroups() {
            const common = this.cronTzCommon;
            const groups = [{ label: '★', zones: common.slice() }];
            const byRegion = {};
            for (const z of this.cronTzAll) {
                if (common.includes(z)) continue;
                const region = z.includes('/') ? z.split('/')[0] : 'Other';
                (byRegion[region] = byRegion[region] || []).push(z);
            }
            for (const region of Object.keys(byRegion).sort()) {
                groups.push({ label: region, zones: byRegion[region].sort() });
            }
            return groups;
        },

        // ── Connectors Tab ──
        connectors: { telegram: {}, discord: {}, slack: {}, email: {}, webhook: {} },
        connectorStatuses: {},
        testingConnector: null,
        showTelegramToken: false,

        // ── Unified Providers & Connectors Hub ──
        hubSubtab: 'providers',
        hubProviders: [],
        hubConnectors: [],
        // Delivery & Routing (Settings → Providers & Connectors →
        // Platform Connectors): every field the old per-platform cards
        // carried — token, allowed users, guild/workspace, enabled —
        // loaded from /api/connectors (masked ****XXXX round-trip), plus
        // each route's destination and the alert / swarm-output selectors
        // (2026-09-03 v4).
        adapterRouting: {
            tgToken: '', tgEnabled: true, tgAllowed: '', tgMainChat: '',
            tgGroupEnabled: false, tgGroupChat: '', tgGroupToken: '',
            discordToken: '', discordEnabled: true, discordGuild: '', discordAllowed: '', discordChannel: '',
            slackToken: '', slackAppToken: '', slackWorkspace: '', slackAllowed: '', slackEnabled: true, slackChannel: '',
            alertRoutes: [], swarmRoutes: [],
        },
        adapterRoutingSaving: false,
        adapterRoutingApplying: false,
        adapterRoutingSnapshot: '',
        routingShow: { tgToken: false, tgGroupToken: false, discordToken: false, slackToken: false, slackAppToken: false },
        adapterRoutingTesting: '',
        adapterRoutingTest: null,
        hubProfiles: [],
        hubProviderModal: false,
        hubConnectorModal: false,
        hubProfileModal: false,
        hubEditingProvider: { name: '', display_name: '', base_url: '', api_key: '', models: '', enabled: true, google_mode: 'ai_studio', project_id: '', location: 'us-central1', _existing: false },
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
        /** Per-provider filter text for the discovered-models panel */
        modelSearch: {},
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
        appearance: { theme: 'dark', accent_color: '#3b82f6', font_size: 14, sidebar_position: 'left', custom_css: '' },

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
        lastCreatedToken: '',
        // SaaS multi-user
        saasStatus: null,
        platformUsers: [],
        tenants: [],
        newUser: { username: '', password: '', role: 'operator' },
        newTenant: { id: '', name: '' },

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

        // ── Packages Tab ──
        pkgCore: [],
        pkgExtras: [],
        pkgTotal: 0,
        pkgPythonVer: '',
        pkgDbBackend: 'sqlite',
        pkgDbUrlSet: false,
        pkgMemory: { status: '', summary: '', headline: '', layers: {}, issues: [] },
        pkgSearch: '',
        pkgLoading: false,
        pkgInstalling: '',
        pkgInstallMsg: '',
        pkgInstallOk: false,

        // ── Import/Export Tab ──
        exportFormat: 'yaml',
        importData: '',
        importFormat: 'yaml',
        importSelective: false,
        importSections: [],
        availableSections: ['model', 'agent', 'connectors', 'mcp', 'skills', 'appearance', 'shortcuts', 'tools', 'safety'],

        // ── Backup Tab ──
        backupRunning: false,
        backupResult: null,
        backupProgressText: '',
        backupProgressPhase: '',
        backupList: [],
        backupRetention: 7,
        _backupPollId: null,

        // ── Offsite Backup ──
        offsiteConfig: null,
        offsiteProvider: '',
        offsiteEnabled: true,
        offsiteTesting: false,
        offsiteTestResult: null,
        offsiteSaved: false,
        // WebDAV (WD MyCloud / NAS) credentials
        offsiteWebdavUrl: '',
        offsiteWebdavUser: '',
        offsiteWebdavPass: '',
        // FTP (WD MyCloud OS3 / NAS) credentials
        offsiteFtpHost: '',
        offsiteFtpPort: '21',
        offsiteFtpUser: '',
        offsiteFtpPass: '',
        offsiteFtpPath: '',
        // S3 / B2 credentials
        offsiteS3Key: '',
        offsiteS3Secret: '',
        offsiteS3Bucket: '',
        offsiteS3Endpoint: '',
        offsiteS3Region: 'us-east-1',

        // ── Voice Tab ──
        voiceForm: {
            enabled: false,
            tts_reply: true,
            stt_provider: 'openai',
            stt_model: 'default',
            stt_base_url: '',
            tts_provider: 'edgetts',
            tts_voice: 'default',
            stt_language: 'auto',
            tts_output_format: 'mp3',
        },
        voiceProviders: { stt: ['openai', 'groq', 'cohere', 'nvidia', 'faster-whisper'], tts: ['edgetts', 'openai', 'nvidia', 'kokoro', 'coqui'] },
        voiceModels: [],
        /** Normalized STT options: always [{id, label}] for Alpine x-for */
        sttModelOptions: [],
        sttModelsLoading: false,
        sttModelType: 'default',
        ttsVoiceType: 'default',

        // ── Email tab (Connect Gmail / Microsoft — OAuth | IMAP | POP) ──
        emailStatus: {
            active_provider: 'sandbox',
            gmail_configured: false,
            gmail_address: '',
            gmail_auth_mode: 'none',
            gmail_oauth_client_set: false,
            microsoft_configured: false,
            microsoft_address: '',
            microsoft_auth_mode: 'none',
            ms_client_id_set: false,
            ms_tenant_id: 'common',
            imap_configured: false,
            pop_configured: false,
            sandbox_always: true,
            accounts: [],
        },
        emailAccounts: [],
        emailGmailMode: 'oauth',
        emailMsMode: 'oauth',
        emailGmail: { address: '', app_password: '' },
        emailGmailOAuth: { client_id: '', client_secret: '' },
        emailMs: { client_id: '', client_secret: '', tenant_id: 'common' },
        emailMsProtocol: { address: '', password: '' },
        emailMsDevice: { user_code: '', verification_uri: '', device_code: '', message: '' },
        emailMsConnecting: false,
        emailMsPollTimer: null,
        emailLoading: false,
        emailSaving: false,

        // ── X (Twitter) official API ──
        xStatus: {
            configured: false,
            enabled: false,
            kill_switch: false,
            handle: '',
            can_post: false,
            verified_username: '',
            caps: { max_posts_per_day: 8, posts_today: 0, max_posts_per_month: 80, posts_30d: 0 },
            keys_set: { api_key: false, api_key_secret: false, access_token: false, access_token_secret: false },
        },
        xForm: { api_key: '', api_key_secret: '', access_token: '', access_token_secret: '', handle: '', enabled: true, max_posts_per_day: 8, max_posts_per_month: 80 },
        xLoading: false,
        xSaving: false,
        // X audit log (append-only x_audit.db; loaded on demand)
        xShowKeys: false,

        async init() {
            const self = this;
            self.loading = true;
            try {
                // Load all settings in parallel
                const [settings, providers, personalities, shortcuts, agentCfg, contextCfg, safetyCfg, appearanceCfg, nonstopCfg] = await Promise.all([
                    self._fetch('/api/settings'),
                    self._fetch('/api/settings/providers'),
                    self._fetch('/api/settings/agent/personalities'),
                    self._fetch('/api/settings/shortcuts'),
                    self._fetch('/api/settings/agent'),
                    self._fetch('/api/settings/agent/context'),
                    self._fetch('/api/settings/agent/safety'),
                    self._fetch('/api/settings/appearance'),
                    self._fetch('/api/settings/agent/nonstop'),
                ]);

                if (settings) {
                    if (settings.model) Object.assign(self.currentModel, settings.model);
                    if (agentCfg && typeof agentCfg === 'object') {
                        Object.assign(self.agent, agentCfg);
                    } else if (settings.agent) {
                        Object.assign(self.agent, settings.agent);
                    }
                    if (settings.connectors) Object.assign(self.connectors, settings.connectors);
                    if (appearanceCfg) {
                        Object.assign(self.appearance, appearanceCfg);
                        if (typeof self._applyAccentColor === 'function') {
                            self._applyAccentColor(self.appearance.accent_color);
                        }
                        if (appearanceCfg.font_size) {
                            try {
                                const rootEl = document.documentElement;
                                if (rootEl && rootEl._x_dataStack) {
                                    const root = Alpine.$data(rootEl);
                                    if (root) root.fontSize = appearanceCfg.font_size;
                                }
                            } catch (e) { /* ignore font sync */ }
                        }
                    }
                    if (safetyCfg) Object.assign(self.safety, safetyCfg);
                    try {
                        const soul = await self._fetch('/api/commitment/soul/pending');
                        self.soulPending = (soul && soul.pending) || [];
                    } catch (e) {
                        self.soulPending = [];
                    }
                    if (contextCfg) Object.assign(self.context, contextCfg);
                    if (nonstopCfg) Object.assign(self.nonstop, nonstopCfg);
                    const memTenant = settings.memory && settings.memory['memory.tenant_mode'];
                    if (memTenant) self.memoryTenantMode = memTenant;
                }
                if (Array.isArray(providers)) self.providers = providers;
                if (Array.isArray(personalities)) self.personalities = personalities;
                if (shortcuts && typeof shortcuts === 'object') self.shortcuts = shortcuts;

                const defaults = await self._fetch('/api/settings/models/defaults');
                if (defaults) Object.assign(self.modelDefaults, defaults);

                const saved = await self._fetch('/api/models/saved');
                if (Array.isArray(saved)) self.savedModels = saved;

                if (window.ProvidersManager && typeof ProvidersManager.getPresetKeys === 'function') {
                    self.providerPresets = ProvidersManager.getPresetKeys();
                } else {
                    self.providerPresets = [];
                }

                try {
                    const cfg = await self._fetch('/api/settings/memory/merge-kb');
                    if (cfg) {
                        if (cfg.merge_knowledge_into_chat != null) self.memoryMergeKb = !!cfg.merge_knowledge_into_chat;
                        if (cfg.promote_kb_to_episodes != null) self.memoryPromoteKb = !!cfg.promote_kb_to_episodes;
                        if (cfg.smart_search != null) self.memorySmartSearch = !!cfg.smart_search;
                        if (cfg.explain_recall != null) self.memoryExplainRecall = !!cfg.explain_recall;
                    }
                } catch (e) { /* optional */ }

                try {
                    const mb = await self._fetch('/api/settings/memory/backends');
                    if (mb && mb.backends) {
                        const b = mb.backends;
                        self.memoryBackends = {
                            mode: b.mode || 'local',
                            embedder: Object.assign({}, self.memoryBackends.embedder, b.embedder || {}),
                            vector: Object.assign({}, self.memoryBackends.vector, b.vector || {}),
                            graph: Object.assign({}, self.memoryBackends.graph, b.graph || {}),
                            state: Object.assign({}, self.memoryBackends.state, b.state || {}),
                            failover: Object.assign({}, self.memoryBackends.failover, b.failover || {}),
                        };
                    }
                    if (mb && mb.capability) {
                        self.memoryBackendsCapability = Object.assign(
                            {}, self.memoryBackendsCapability, mb.capability
                        );
                    }
                    if (mb && mb.state_capability) {
                        self.memoryBackendsStateCap = mb.state_capability;
                    }
                    if (mb && mb.graph_capability) {
                        self.memoryBackendsGraphCap = mb.graph_capability;
                    }
                } catch (e) { /* optional */ }

                // Load unified hub data (the first tab). Do not await
                // voice/STT provider lists here — those can hang and leave
                // the shell on "Loading settings…".
                if (typeof self.loadHubProviders === 'function') {
                    await Promise.all([
                        self.loadHubProviders(),
                        self.loadHubConnectors(),
                        self.loadHubProfiles(),
                        self.loadAdapterRouting(),
                    ]);
                }
            } catch (e) {
                console.error('[Settings] Init failed:', e);
            } finally {
                self.loading = false;
            }
            var later = self._loadSecondarySettings;
            if (typeof later === 'function') {
                Promise.resolve(later.call(self)).catch(function (err) {
                    console.error('[Settings] Secondary load failed:', err);
                });
            }

            // Deep-link: /settings?tab=packages (or any valid tab id)
            try {
                const params = new URLSearchParams(window.location.search);
                let requested = params.get('tab');
                // OAuth round-trip from Backup → Offsite (flag set by
                // connectGoogleDrive/connectOneDrive): the email callback
                // always returns to ?tab=email, but the user was connecting a
                // backup provider — land on the backup tab so the pending
                // auto-select/save in loadOffsiteConfig runs.
                try {
                    if (localStorage.getItem('kazma_offsite_connect_pending')) {
                        requested = 'backup';
                    }
                } catch (e) { /* storage unavailable */ }
                if (requested && requested !== self.tab) {
                    await self.onTabChange(requested);
                }
            } catch (e) {
                /* ignore bad query strings */
            }
        },

        async restartServer(opts = {}) {
            const restartNeeded = opts.restartNeeded || (() => this.embedderRestartNeeded());
            const setBusy = opts.setBusy || ((v) => { this.embedderRestarting = v; });
            const title = opts.title || 'Restart server?';
            const message = opts.message || 'The server will restart with the saved embedder config. The page will reconnect automatically. Unsaved chat sessions are persisted.';
            if (!restartNeeded()) {
                showToast(opts.noRestartMsg || 'No restart needed — config already matches the running server.', 'info');
                return;
            }
            const ok = await window.kazmaConfirm({ title, message, danger: true });
            if (!ok) return;
            setBusy(true);
            try {
                const resp = await fetch('/api/settings/system/restart', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json();
                if (data.status === 'error') {
                    showToast(data.detail || 'Restart failed', 'error');
                    setBusy(false);
                    return;
                }
                showToast('Restarting server… the page will reload shortly.', 'info', 5000);
                // Poll the health endpoint until it comes back, then reload.
                const start = Date.now();
                const poll = async () => {
                    try {
                        const r = await fetch('/health/live', { method: 'GET', cache: 'no-store' });
                        if (r.ok && Date.now() - start > 3000) {
                            window.location.reload();
                            return;
                        }
                    } catch (e) { /* server down — expected during restart */ }
                    if (Date.now() - start > 60000) {
                        showToast('Server did not come back — check the terminal.', 'error');
                        setBusy(false);
                        return;
                    }
                    setTimeout(poll, 1500);
                };
                setTimeout(poll, 1000);
            } catch (e) {
                showToast('Restart request failed: ' + e.message, 'error');
                setBusy(false);
            }
        },

        async _fetch(url) {
            try {
                var ctrl = typeof AbortController === 'function' ? new AbortController() : null;
                var timer = ctrl ? setTimeout(function () { try { ctrl.abort(); } catch (e) { /* ignore */ } }, 8000) : null;
                const resp = await fetch(url, {
                    credentials: 'same-origin',
                    signal: ctrl ? ctrl.signal : undefined,
                });
                if (timer) clearTimeout(timer);
                // NOTE: the global fetch wrapper (auth-guard.js) now owns the
                // session-expired → /login redirect for 401/403.
                if (resp.status === 401 || resp.status === 403) return null;
                if (!resp.ok) return null;
                return await resp.json();
            } catch (e) {
                return null;
            }
        },

        async _loadSecondarySettings() {
            try {
                const docSettings = await this._fetch('/api/settings/documents');
                if (docSettings && !docSettings.error) {
                    Object.assign(this.documents, docSettings);
                }
            } catch (e) {
                console.error('[Settings] Failed to load document config:', e);
            }
            if (typeof this.loadTurnNotify === 'function') {
                Promise.resolve(this.loadTurnNotify()).catch(function (e) {
                    console.error('[Settings] Failed to load turn notification setting:', e);
                });
            }
            try {
                const voiceSettings = await this._fetch('/api/settings/voice');
                if (voiceSettings) {
                    Object.assign(this.voiceForm, voiceSettings);
                }
                const voiceProvs = await this._fetch('/api/voice/providers');
                if (voiceProvs) {
                    if (Array.isArray(voiceProvs.stt)) this.voiceProviders.stt = voiceProvs.stt;
                    if (Array.isArray(voiceProvs.tts)) this.voiceProviders.tts = voiceProvs.tts;
                }
                if (typeof this.loadVoiceModels === 'function') {
                    await Promise.all([this.loadVoiceModels(), this.loadSttModels()]);
                }
            } catch (e) {
                console.error('[Settings] Failed to load voice config:', e);
            }
        },

        async onTabChange(newTab) {
            // Legacy deep-links → consolidated homes
            var scrollEmbedder = false;
            if (newTab === 'connectors' || newTab === 'provider_connectors') {
                this.hubSubtab = 'connectors';
                newTab = 'providers_connectors';
            } else if (newTab === 'embedder') {
                scrollEmbedder = true;
                newTab = 'memory';
            }
            this.tab = newTab;
            try {
                const url = new URL(window.location.href);
                if (newTab && newTab !== 'providers_connectors') {
                    url.searchParams.set('tab', newTab);
                } else {
                    url.searchParams.delete('tab');
                }
                if (scrollEmbedder) {
                    url.hash = 'memory-embedder-section';
                }
                history.replaceState(null, '', url.pathname + url.search + url.hash);
            } catch (e) {
                /* ignore URL sync errors */
            }
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
                case 'memory':
                    await Promise.all([this.loadEmbedder(), this.loadTimeTravel(), this.loadCronTimezone()]);
                    if (scrollEmbedder || (window.location.hash || '').includes('embedder')) {
                        setTimeout(function() {
                            var el = document.getElementById('memory-embedder-section');
                            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        }, 80);
                    }
                    break;
                case 'mcp': await this.loadMcpServers(); break;
                case 'skills': await this.loadSkills(); break;
                case 'appearance': break;
                case 'shortcuts': this.shortcutConflicts = this.detectConflicts(); break;
                case 'account': await this.loadAccount(); break;
                case 'tools': await this.loadTools(); break;
                case 'system': await this.loadDiagnostics(); await this.loadLogs(); await this.loadVaultStatus(); await this.loadLogging(); await this.loadProxy(); break;
                case 'backup': await Promise.all([this.loadBackupList(), this.loadOffsiteConfig(), this.syncBackupState()]); break;
                case 'packages': await this.loadPackages(); break;
                case 'import': break;
                case 'voice':
                    await Promise.all([this.loadVoiceModels(), this.loadSttModels()]);
                    break;
                case 'email':
                    await this.loadEmailStatus();
                    break;
                case 'x':
                    await this.loadXStatus();
                    break;
            }
        },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
