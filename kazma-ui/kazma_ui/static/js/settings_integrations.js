/** Settings mixin: integrations — mcp, skills, voice, email */
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
    root.KazmaSettingsMixins.integrations = function () {
        return {
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
            if (!this.newMcpServer.name) { showToast(_k('settings.int.server_name_is_required', 'Server name is required'), 'error'); return; }
            this.saving = true;
            try {
                const data = { ...this.newMcpServer };
                if (data.command && typeof data.command === 'string') data.command = data.command.split(/\s+/);
                if (data.env && typeof data.env === 'string') {
                    try { data.env = JSON.parse(data.env); } catch { data.env = {}; }
                }
                await window.kazmaSave('/api/settings/mcp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(data),
                });
                this.showMcpModal = false;
                await this.loadMcpServers();
                showToast(_k('settings.int.mcp_server_added', 'MCP server added'), 'success');
            } catch (e) {
                showToast(_k('settings.int.failed_to_add_server', 'Failed to add server: ') + e.message, 'error');
            }
            this.saving = false;
        },

        async deleteMcpServer(name) {
            if (!(await window.kazmaConfirm({
                title: _k('settings.int.remove_mcp_server', 'Remove MCP server'),
                message: _k('settings.int.remove_mcp_message', 'Remove MCP server "{name}"? This cannot be undone.', { name: name }),
                confirmText: _k('settings.int.remove', 'Remove'),
                danger: true,
            }))) return;
            try {
                const resp = await fetch(`/api/settings/mcp/${encodeURIComponent(name)}`, { method: 'DELETE' });
                const body = await resp.json().catch(function() { return {}; });
                if (!resp.ok || body.status === 'error') {
                    showToast(body.message || _k('settings.int.delete_failed_http', 'Delete failed (HTTP {status})', { status: resp.status }), 'error');
                    return;
                }
                await this.loadMcpServers();
                showToast(_k('settings.int.server_removed', 'Server removed'), 'success');
            } catch (e) {
                showToast(_k('settings.int.delete_failed', 'Delete failed: ') + e.message, 'error');
            }
        },

        async toggleMcpServer(name, enabled) {
            try {
                // Saved for the next start and applied now: the answer says
                // whether the server runs, and why not.
                const body = await window.kazmaSave(`/api/settings/mcp/${encodeURIComponent(name)}/toggle`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled }),
                });
                if (body && body.error) {
                    showToast(enabled
                        ? _k('settings.int.mcp_on_not_running', '{name} is on, but it did not start: {error}', { name: name, error: body.error })
                        : _k('settings.int.mcp_off_still_running', '{name} is off, but it is still running: {error}', { name: name, error: body.error }),
                        'warning');
                } else {
                    showToast(enabled
                        ? _k('settings.int.mcp_on_running', '{name} is on and running', { name: name })
                        : _k('settings.int.mcp_off_stopped', '{name} is off and stopped', { name: name }),
                        'success');
                }
            } catch (e) {
                showToast(_k('settings.int.toggle_failed', 'Toggle failed: ') + e.message, 'error');
            }
            // Reload either way: the list shows what the server actually has.
            await this.loadMcpServers();
        },

        /* The MCP page's Test (POST /api/mcp/servers/{name}/test): Settings had
           a copy that started a workspace-bound server on the literal
           ${KAZMA_ACTIVE_WORKSPACE}, so its Test of the filesystem server
           failed (2026-10-02). */
        async testMcpServer(name) {
            this.testingMcp = name;
            try {
                const resp = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/test`, { method: 'POST' });
                const result = await resp.json();
                if (result.success) {
                    showToast(window.kazmaCount('settings.int.mcp_tools_found', result.tool_count || 0, { name: name }), 'success');
                } else {
                    // The server's own last stderr line says why it failed.
                    const why = [result.error, (result.stderr || '').trim().split('\n').pop()].filter(Boolean).join(' — ');
                    showToast(`${name}: ${why || resp.status}`, 'error');
                }
            } catch (e) {
                showToast(_k('settings.int.test_failed', 'Test failed: {error}', { error: e.message }), 'error');
            }
            this.testingMcp = null;
        },

        async loadSkills() {
            try {
                this.skills = await this._fetch('/api/skills') || [];
            } catch (e) {
                this.skills = [];
            }
        },

        // The Skills page's own routes, by the skill's id. This tab used
        // /api/settings/skills/*, which wrote skills.<name>.enabled -- a key
        // nothing reads -- and "uninstalled" a skill by writing it, built-in
        // ones included (2026-10-01).
        async toggleSkill(skill, enabled) {
            try {
                await window.kazmaSave('/api/skills/toggle', {
                    method: 'POST',
                    body: { skill_id: skill.id, enabled: !!enabled },
                });
                showToast(enabled
                    ? _k('skills.ui.enabled', 'Skill enabled')
                    : _k('skills.ui.disabled', 'Skill disabled'), 'success');
            } catch (e) {
                showToast(_k('settings.int.toggle_failed', 'Toggle failed: ') + e.message, 'error');
            }
            await this.loadSkills();
        },

        async uninstallSkill(skill) {
            if (!(await window.kazmaConfirm({
                title: _k('settings.int.uninstall_skill', 'Uninstall skill'),
                message: _k('settings.int.uninstall_skill_message', 'Uninstall skill "{name}"? This cannot be undone.', { name: skill.name }),
                confirmText: _k('settings.int.uninstall', 'Uninstall'),
                danger: true,
            }))) return;
            try {
                const body = await window.kazmaSave('/api/skills/uninstall', {
                    method: 'POST',
                    body: { skill_id: skill.id },
                });
                // Only "ok" removed something ("not_found" removed nothing).
                if (!body || body.status !== 'ok') {
                    showToast(_k('skills.ui.nothing_uninstalled', 'Nothing was uninstalled: {reason}', { reason: (body && (body.error || body.status)) || _k('skills.ui.no_answer', 'no answer') }), 'error');
                } else {
                    showToast(_k('settings.int.skill_uninstalled', 'Skill uninstalled'), 'success');
                }
            } catch (e) {
                showToast(_k('settings.int.uninstall_failed', 'Uninstall failed: ') + e.message, 'error');
            }
            await this.loadSkills();
        },

        get filteredSkills() {
            if (!this.skillFilter) return this.skills;
            const q = this.skillFilter.toLowerCase();
            return this.skills.filter(s =>
                (s.name || '').toLowerCase().includes(q) ||
                (s.description || '').toLowerCase().includes(q)
            );
        },

        async saveVoiceSettings() {
            this.saving = true;
            try {
                const resp = await fetch('/api/settings/voice', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.voiceForm)
                });
                if (!resp.ok) {
                    throw new Error('HTTP ' + resp.status);
                }
                showToast(_k('settings.int.voice_settings_saved', 'Voice settings saved'), 'success');
            } catch (e) {
                showToast(_k('settings.int.failed_to_save_voice_settings', 'Failed to save voice settings: ') + e.message, 'error');
            } finally {
                this.saving = false;
            }
        },

        async loadVoiceModels() {
            try {
                const provider = this.voiceForm.tts_provider || 'edgetts';
                const models = await this._fetch(`/api/voice/voices?provider=${provider}`);
                if (Array.isArray(models)) {
                    this.voiceModels = models;
                    var cur = String(this.voiceForm.tts_voice || '').toLowerCase();
                    if (cur === 'auto') {
                        this.ttsVoiceType = 'auto';
                    } else if (this.voiceModels.includes(this.voiceForm.tts_voice)) {
                        this.ttsVoiceType = this.voiceForm.tts_voice;
                    } else {
                        this.ttsVoiceType = 'custom';
                    }
                }
            } catch (e) {
                console.error('[Settings] Failed to load voice models:', e);
            }
        },

        _normalizeSttModels(raw) {
            var list = Array.isArray(raw) ? raw : [];
            var out = [];
            var seen = {};
            list.forEach(function (m) {
                var id = '';
                var label = '';
                if (m && typeof m === 'object') {
                    id = String(m.id || m.model || m.name || '').trim();
                    label = String(m.label || id).trim();
                } else {
                    id = String(m || '').trim();
                    label = id;
                }
                if (!id || seen[id]) return;
                seen[id] = true;
                out.push({ id: id, label: label });
            });
            return out;
        },

        _sttFallbackModels(provider) {
            var p = (provider || 'openai').toLowerCase();
            var map = {
                openai: [{ id: 'whisper-1', label: 'whisper-1' }],
                groq: [
                    { id: 'whisper-large-v3', label: 'whisper-large-v3' },
                    { id: 'whisper-large-v3-turbo', label: 'whisper-large-v3-turbo' },
                    { id: 'distil-whisper-large-v3-en', label: 'distil-whisper-large-v3-en' },
                ],
                nvidia: [
                    { id: 'openai/whisper-large-v3', label: 'Whisper Large v3 (NIM)' },
                    { id: 'whisper-large-v3', label: 'whisper-large-v3' },
                    { id: 'nvidia/parakeet-ctc-1.1b-en-us', label: 'Parakeet CTC 1.1B (EN)' },
                ],
                'faster-whisper': [
                    { id: 'tiny', label: 'tiny' },
                    { id: 'base', label: 'base' },
                    { id: 'small', label: 'small' },
                    { id: 'medium', label: 'medium' },
                    { id: 'large-v3', label: 'large-v3' },
                ],
                cohere: [
                    { id: 'cohere-transcribe-03-2026', label: 'cohere-transcribe-03-2026' },
                    { id: 'cohere-transcribe-arabic-07-2026', label: 'cohere-transcribe-arabic-07-2026' },
                ],
            };
            return map[p] || [{ id: 'default', label: 'default' }];
        },

        async loadSttModels() {
            var provider = this.voiceForm.stt_provider || 'openai';
            this.sttModelsLoading = true;
            // Show fallback immediately so the dropdown is never empty
            this.sttModelOptions = this._sttFallbackModels(provider);
            try {
                var url = '/api/voice/stt-models?provider=' + encodeURIComponent(provider);
                var resp = await fetch(url, { credentials: 'same-origin' });
                if (resp.ok) {
                    var models = await resp.json();
                    var normalized = this._normalizeSttModels(models);
                    if (normalized.length) {
                        this.sttModelOptions = normalized;
                    }
                } else {
                    console.warn('[Settings] STT models HTTP', resp.status, '— using fallback for', provider);
                }
                var ids = this.sttModelOptions.map(function (m) { return m.id; });
                if (ids.indexOf(this.voiceForm.stt_model) !== -1) {
                    this.sttModelType = this.voiceForm.stt_model;
                } else if (this.voiceForm.stt_model && this.voiceForm.stt_model !== 'default') {
                    this.sttModelType = 'custom';
                } else if (ids.length) {
                    this.sttModelType = ids[0];
                    this.voiceForm.stt_model = ids[0];
                } else {
                    this.sttModelType = 'custom';
                }
            } catch (e) {
                console.error('[Settings] Failed to load STT models:', e);
                this.sttModelOptions = this._sttFallbackModels(provider);
            } finally {
                this.sttModelsLoading = false;
            }
        },

        onSttModelTypeChange() {
            if (this.sttModelType !== 'custom') {
                this.voiceForm.stt_model = this.sttModelType;
                this.saveVoiceSettings();
            }
        },

        _sttLanguageCodes() {
            return [
                'auto', 'ar', 'en', 'fr', 'de', 'es', 'it', 'pt', 'ru',
                'tr', 'zh', 'ja', 'ko', 'hi', 'fa', 'ur', 'nl', 'pl', 'sv',
            ];
        },

        _syncSttLanguageType() {
            var code = String(this.voiceForm.stt_language || 'auto').trim().toLowerCase();
            if (!code) code = 'auto';
            this.voiceForm.stt_language = code;
            this.sttLanguageType = this._sttLanguageCodes().indexOf(code) !== -1 ? code : 'custom';
        },

        onSttLanguageTypeChange() {
            if (this.sttLanguageType !== 'custom') {
                this.voiceForm.stt_language = this.sttLanguageType;
                this.saveVoiceSettings();
            }
        },

        onTtsVoiceTypeChange() {
            if (this.ttsVoiceType !== 'custom') {
                this.voiceForm.tts_voice = this.ttsVoiceType;
                this.saveVoiceSettings();
            }
        },

        /** Voices whose locale tag starts with `prefix` (e.g. 'ar-', 'en-').
         *  edge-tts names every voice `<lang>-<REGION>-<Name>Neural`, which is
         *  what makes a per-language pick possible at all; providers whose
         *  voices are not locale-named return nothing here and the dropdown
         *  falls back to its Default entry. */
        _voicesForLang(prefix) {
            return (this.voiceModels || []).filter(function(v) {
                return typeof v === 'string' && v.toLowerCase().indexOf(prefix) === 0;
            });
        },
        // METHODS, not getters. `settingsApp()` composes its parts with
        // Object.assign, which INVOKES a getter on the source and copies the
        // resulting value — so a getter here is evaluated once, while
        // voiceModels is still [], and freezes as an empty array forever.
        arabicVoices() { return this._voicesForLang('ar-'); },
        latinVoices() { return this._voicesForLang('en-'); },

        async loadEmailStatus() {
            this.emailLoading = true;
            try {
                // OAuth callback toast (?email_oauth=ok|error / ?calendar_oauth=)
                try {
                    const url = new URL(window.location.href);
                    const oauth = url.searchParams.get('email_oauth');
                    const calOauth = url.searchParams.get('calendar_oauth');
                    if (oauth === 'ok') {
                        const prov = url.searchParams.get('provider') || 'email';
                        const em = url.searchParams.get('email') || '';
                        const cal = url.searchParams.get('calendar');
                        const acct = url.searchParams.get('account') || '';
                        let msg = acct
                            ? _k('settings.int.email_account_connected', 'Account “{name}” connected', { name: acct })
                            : prov === 'gmail'
                                ? _k('settings.int.gmail_connected', 'Gmail connected')
                                : _k('settings.int.microsoft_connected', 'Microsoft connected');
                        if (em) msg += _k('settings.int.as_account', ' as {email}', { email: em });
                        if (prov === 'gmail' && cal === '0' && !acct) {
                            msg += _k('settings.int.calendar_not_granted', ' — Calendar was not granted; use Connect Google Calendar');
                        }
                        showToast(msg, 'success');
                        url.searchParams.delete('email_oauth');
                        url.searchParams.delete('provider');
                        url.searchParams.delete('email');
                        url.searchParams.delete('msg');
                        url.searchParams.delete('calendar');
                        url.searchParams.delete('account');
                        history.replaceState(null, '', url.pathname + url.search + url.hash);
                    } else if (oauth === 'error') {
                        showToast(_k('settings.int.oauth_failed', 'OAuth failed: ') + (url.searchParams.get('msg') || 'unknown'), 'error');
                        url.searchParams.delete('email_oauth');
                        url.searchParams.delete('msg');
                        history.replaceState(null, '', url.pathname + url.search + url.hash);
                    }
                    if (calOauth === 'ok') {
                        const em = url.searchParams.get('email') || '';
                        const done = url.searchParams.get('provider') === 'outlook'
                            ? _k('settings.int.outlook_calendar_connected', 'Outlook Calendar connected')
                            : _k('settings.int.gcal_connected', 'Google Calendar connected');
                        showToast(done + (em ? _k('settings.int.as_account', ' as {email}', { email: em }) : ''), 'success');
                        url.searchParams.delete('calendar_oauth');
                        url.searchParams.delete('provider');
                        url.searchParams.delete('email');
                        url.searchParams.delete('msg');
                        history.replaceState(null, '', url.pathname + url.search + url.hash);
                    } else if (calOauth === 'error') {
                        showToast(_k('settings.int.calendar_oauth_failed', 'Calendar OAuth failed: ') + (url.searchParams.get('msg') || 'unknown'), 'error');
                        url.searchParams.delete('calendar_oauth');
                        url.searchParams.delete('msg');
                        history.replaceState(null, '', url.pathname + url.search + url.hash);
                    }
                } catch (e) { /* ignore */ }

                const data = await this._fetch('/api/email/status');
                if (data && !data.error) {
                    Object.assign(this.emailStatus, data);
                    if (data.gmail_address) this.emailGmail.address = data.gmail_address;
                    if (data.microsoft_address) this.emailMsProtocol.address = data.microsoft_address;
                    if (data.ms_tenant_id) this.emailMs.tenant_id = data.ms_tenant_id;
                    // Sync mode tabs to active auth
                    const gm = data.gmail_auth_mode || 'none';
                    if (gm === 'imap' || gm === 'pop' || gm === 'oauth') this.emailGmailMode = gm;
                    else if (gm === 'app_password') this.emailGmailMode = 'imap';
                    const mm = data.microsoft_auth_mode || 'none';
                    if (mm === 'imap' || mm === 'pop' || mm === 'oauth') this.emailMsMode = mm;
                }
                const acc = await this._fetch('/api/email/accounts');
                if (acc && Array.isArray(acc.accounts)) {
                    this.emailAccounts = acc.accounts;
                    this.emailAccountsLoaded = true;
                }
                const cal = await this._fetch('/api/calendar/status');
                if (cal && !cal.error) Object.assign(this.calendarStatus, cal);
            } finally {
                this.emailLoading = false;
            }
        },

        // ── Other accounts (more mailboxes than the main two) ──────────
        // Each signs in on its own, under a short name chat uses ("check my
        // work inbox"); its tokens are kept under that name only.

        otherEmailAccounts() {
            return (this.emailAccounts || []).filter((a) => a.source !== 'main');
        },

        emailAccountKind(type) {
            const names = { gmail: 'Gmail', microsoft: 'Microsoft', imap: 'IMAP', pop: 'POP' };
            return names[type] || type || '—';
        },

        emailAccountAuth(a) {
            if (a.auth === 'oauth') return _k('settings.email_account_signed_in', 'Signed in');
            if (a.auth === 'password') return _k('settings.email_account_app_password', 'Password');
            return _k('settings.email_account_incomplete', 'Sign-in incomplete');
        },

        /* The name a new account is known by in chat. A free default is
           offered ("gmail-2"); letters, digits and hyphens only. */
        async _askAccountName(kind) {
            const taken = new Set((this.emailAccounts || []).map((a) => a.alias));
            const stem = kind === 'microsoft' ? 'outlook' : 'gmail';
            let n = 2;
            while (taken.has(stem + '-' + n)) n += 1;
            const name = await window.kazmaPrompt({
                title: kind === 'microsoft'
                    ? _k('settings.email_account_name_title_microsoft', 'Add a Microsoft account')
                    : _k('settings.email_account_name_title_google', 'Add a Google account'),
                message: _k('settings.email_account_name_prompt', 'A short name for this account, used in chat (for example: work, personal). Letters a–z, digits and hyphens.'),
                defaultValue: stem + '-' + n,
                confirmText: _k('settings.email_account_continue', 'Continue to sign in'),
            });
            if (name == null) return null;
            const clean = String(name).trim().toLowerCase().replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '');
            if (!clean) {
                showToast(_k('settings.int.email_account_bad_name', 'Name the account with letters a–z, digits and hyphens.'), 'error');
                return null;
            }
            return clean;
        },

        async _startAccountSignIn(kind, alias) {
            const base = kind === 'microsoft'
                ? '/api/email/oauth/microsoft/start.json'
                : '/api/email/oauth/gmail/start.json';
            this.emailSaving = true;
            try {
                const resp = await fetch(base + '?account=' + encodeURIComponent(alias), { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok || !data.authorize_url) {
                    throw new Error(data.error || ('HTTP ' + resp.status));
                }
                window.location.href = data.authorize_url;
            } catch (e) {
                showToast(_k('settings.int.email_account_failed', 'Could not add the account: {error}', { error: e.message }), 'error');
                this.emailSaving = false;
            }
        },

        async addGoogleAccount() {
            const alias = await this._askAccountName('gmail');
            if (alias) await this._startAccountSignIn('gmail', alias);
        },

        async addMicrosoftAccount() {
            if ((this.emailMs.client_id || '').trim()) await this.saveMsClient();
            const alias = await this._askAccountName('microsoft');
            if (alias) await this._startAccountSignIn('microsoft', alias);
        },

        async reconnectEmailAccount(a) {
            if (!a || !a.alias) return;
            await this._startAccountSignIn(a.type === 'microsoft' ? 'microsoft' : 'gmail', a.alias);
        },

        /* A Microsoft account by code, for an Azure app registered for the
           device code only (Microsoft refuses the redirect). */
        async addMicrosoftAccountWithCode() {
            const alias = await this._askAccountName('microsoft');
            if (!alias) return;
            this._stopAccountMsPoll();
            try {
                const resp = await fetch('/api/email/oauth/microsoft/device/start', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ account: alias }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok || !data.device_code) throw new Error(data.error || ('HTTP ' + resp.status));
                this.accountMsDevice = {
                    user_code: data.user_code || '',
                    verification_uri: data.verification_uri_complete || data.verification_uri || 'https://microsoft.com/devicelogin',
                    device_code: data.device_code,
                    alias,
                };
            } catch (e) {
                showToast(_k('settings.int.email_account_failed', 'Could not add the account: {error}', { error: e.message }), 'error');
                return;
            }
            this.accountMsPolling = true;
            const device_code = this.accountMsDevice.device_code;
            this.accountMsPollTimer = setInterval(async () => {
                let data = {};
                try {
                    const resp = await fetch('/api/email/oauth/microsoft/device/poll', {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                        body: JSON.stringify({ device_code }),
                    });
                    data = await resp.json().catch(() => ({}));
                } catch (e) {
                    return;  // network hiccup: keep polling
                }
                if (data.ok && data.status === 'authorized') {
                    this._stopAccountMsPoll();
                    showToast(_k('settings.int.email_account_connected', 'Account “{name}” connected', { name: data.account || alias })
                        + (data.email ? _k('settings.int.as_account', ' as {email}', { email: data.email }) : ''), 'success');
                    await this.loadEmailStatus();
                } else if (data.status === 'failed' || data.status === 'expired') {
                    this._stopAccountMsPoll();
                    showToast(data.error || _k('settings.int.authorization_failed', 'Authorization failed'), 'error');
                }
            }, 5000);
        },

        _stopAccountMsPoll() {
            if (this.accountMsPollTimer) clearInterval(this.accountMsPollTimer);
            this.accountMsPollTimer = null;
            this.accountMsPolling = false;
            this.accountMsDevice = { user_code: '', verification_uri: '', device_code: '', alias: '' };
        },

        /* An account that signs in with a password (an app password for
           Gmail). The server tries the login before keeping it. */
        async addPasswordAccount() {
            const f = this.emailAccountForm;
            const body = {
                alias: (f.alias || '').trim(),
                type: f.type,
                address: (f.address || '').trim(),
                password: f.password || '',
            };
            if (f.type === 'imap' && f.host) body.imap_host = f.host.trim();
            if (f.type === 'pop' && f.host) body.pop_host = f.host.trim();
            if ((f.type === 'imap' || f.type === 'pop') && f.smtp_host) body.smtp_host = f.smtp_host.trim();
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/accounts', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(body),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
                this.emailAccountForm = { open: false, alias: '', type: 'gmail', address: '', password: '', host: '', smtp_host: '' };
                showToast(_k('settings.int.email_account_added', 'Account “{name}” added', { name: (data.account && data.account.alias) || body.alias }), 'success');
            } catch (e) {
                // Never keep the password in the page after a failed try.
                this.emailAccountForm.password = '';
                showToast(_k('settings.int.email_account_failed', 'Could not add the account: {error}', { error: e.message }), 'error');
            } finally {
                this.emailSaving = false;
            }
            await this.loadEmailStatus();
        },

        async removeEmailAccount(a) {
            if (!a || !a.removable) return;
            if (!(await window.kazmaConfirm({
                title: _k('settings.email_account_remove_title', 'Remove account'),
                message: _k('settings.email_account_remove_confirm', 'Remove “{name}” ({address})? Kazma forgets its sign-in and stops using it; the mailbox itself is not touched.', { name: a.alias, address: a.address || '—' }),
                confirmText: _k('settings.email_account_remove', 'Remove'),
                danger: true,
            }))) return;
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/accounts/' + encodeURIComponent(a.alias) + '/remove', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
                showToast(_k('settings.int.email_account_removed', 'Account “{name}” removed', { name: a.alias }), 'success');
            } catch (e) {
                showToast(_k('settings.int.disconnect_failed', 'Disconnect failed: ') + e.message, 'error');
            } finally {
                this.emailSaving = false;
            }
            await this.loadEmailStatus();
        },

        async saveGmailProtocol(protocol) {
            const address = (this.emailGmail.address || '').trim();
            const password = (this.emailGmail.app_password || '').trim();
            if (!address || !password) {
                showToast(window.t ? t('settings.email_gmail_required') : 'Email and app password required', 'error');
                return;
            }
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/protocol/connect', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({
                        provider: 'gmail',
                        protocol: protocol === 'pop' ? 'pop' : 'imap',
                        address,
                        password,
                    }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
                this.emailGmail.app_password = '';
                this.emailGmailMode = protocol === 'pop' ? 'pop' : 'imap';
                showToast(data.message || _k('settings.int.gmail_protocol_connected', 'Gmail {protocol} connected', { protocol: protocol.toUpperCase() }), 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast(_k('settings.int.protocol_failed', '{provider} {protocol} failed: {error}', { provider: 'Gmail', protocol: protocol, error: e.message }), 'error');
            } finally {
                this.emailSaving = false;
            }
        },

        async saveMsProtocol(protocol) {
            const address = (this.emailMsProtocol.address || '').trim();
            const password = (this.emailMsProtocol.password || '').trim();
            if (!address || !password) {
                showToast(window.t ? t('settings.email_ms_protocol_required') : 'Email and password required', 'error');
                return;
            }
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/protocol/connect', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({
                        provider: 'microsoft',
                        protocol: protocol === 'pop' ? 'pop' : 'imap',
                        address,
                        password,
                    }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
                this.emailMsProtocol.password = '';
                this.emailMsMode = protocol === 'pop' ? 'pop' : 'imap';
                showToast(data.message || _k('settings.int.microsoft_protocol_connected', 'Microsoft {protocol} connected', { protocol: protocol.toUpperCase() }), 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast(_k('settings.int.protocol_failed', '{provider} {protocol} failed: {error}', { provider: 'Microsoft', protocol: protocol, error: e.message }), 'error');
            } finally {
                this.emailSaving = false;
            }
        },

        async saveGmailOAuthClient() {
            const client_id = (this.emailGmailOAuth.client_id || '').trim();
            const client_secret = (this.emailGmailOAuth.client_secret || '').trim();
            if (!client_id || !client_secret) {
                showToast(window.t ? t('settings.email_gmail_oauth_client_required') : 'Google Client ID and secret required', 'error');
                return;
            }
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/oauth/gmail/client', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ client_id, client_secret }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
                this.emailGmailOAuth.client_secret = '';
                showToast(data.message || _k('settings.int.google_oauth_client_saved', 'Google OAuth client saved'), 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast(_k('settings.int.save_failed', 'Save failed: ') + e.message, 'error');
            } finally {
                this.emailSaving = false;
            }
        },

        async connectGmailOAuth() {
            const formId = (this.emailGmailOAuth.client_id || '').trim();
            const formSecret = (this.emailGmailOAuth.client_secret || '').trim();
            // Always save when both fields present (refresh after restart)
            if (formId && formSecret) {
                await this.saveGmailOAuthClient();
            } else if (!this.emailStatus.gmail_oauth_client_set) {
                showToast(
                    window.t
                        ? t('settings.email_gmail_oauth_client_required')
                        : 'Paste Google OAuth Client ID + secret, click Save OAuth client, then Connect.',
                    'error'
                );
                return;
            } else if (formId && !formSecret) {
                // Client ID typed again but secret blank — need both to re-save
                showToast(
                    window.t
                        ? t('settings.email_gmail_oauth_secret_again')
                        : 'Re-enter Client secret (or leave both fields empty if already saved).',
                    'error'
                );
                return;
            }
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/oauth/gmail/start.json', { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok || !data.authorize_url) {
                    throw new Error(data.error || _k('settings.int.could_not_start_google_oauth', 'Could not start Google OAuth (is Client ID/secret saved?)'));
                }
                window.location.href = data.authorize_url;
            } catch (e) {
                showToast(_k('settings.int.gmail_oauth_failed', 'Gmail OAuth failed: ') + e.message, 'error');
                this.emailSaving = false;
            }
        },

        async connectMicrosoftOAuth() {
            if ((this.emailMs.client_id || '').trim()) {
                await this.saveMsClient();
            }
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/oauth/microsoft/start.json', { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok || !data.authorize_url) {
                    throw new Error(data.error || _k('settings.int.could_not_start_microsoft_oauth', 'Could not start Microsoft OAuth'));
                }
                window.location.href = data.authorize_url;
            } catch (e) {
                showToast(_k('settings.int.microsoft_oauth_failed', 'Microsoft OAuth failed: ') + e.message, 'error');
                this.emailSaving = false;
            }
        },

        async saveGmail() {
            const address = (this.emailGmail.address || '').trim();
            const app_password = (this.emailGmail.app_password || '').trim();
            if (!address || !app_password) {
                showToast(window.t ? t('settings.email_gmail_required') : 'Email and app password required', 'error');
                return;
            }
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/gmail/connect', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ address, app_password }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok) {
                    throw new Error(data.error || ('HTTP ' + resp.status));
                }
                this.emailGmail.app_password = '';
                showToast(data.message || _k('settings.int.gmail_connected', 'Gmail connected'), 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast(_k('settings.int.gmail_connect_failed', 'Gmail connect failed: ') + e.message, 'error');
            } finally {
                this.emailSaving = false;
            }
        },

        async connectCalendarOAuth() {
            if (!this.emailStatus.gmail_oauth_client_set && !this.calendarStatus.google_oauth_client_set) {
                showToast(
                    window.t
                        ? t('settings.email_gmail_oauth_client_required')
                        : 'Paste Google OAuth Client ID + secret on the Gmail card first, then Connect Calendar.',
                    'error'
                );
                return;
            }
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/calendar/oauth/google/start.json', { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok || !data.authorize_url) {
                    throw new Error(data.error || _k('settings.int.could_not_start_google_calendar', 'Could not start Google Calendar OAuth'));
                }
                window.location.href = data.authorize_url;
            } catch (e) {
                showToast(_k('settings.int.calendar_oauth_failed', 'Calendar OAuth failed: ') + e.message, 'error');
                this.emailSaving = false;
            }
        },

        async disconnectGoogleCalendar() {
            await this._disconnectCalendar({
                url: '/api/calendar/oauth/google/disconnect',
                title: _k('settings.calendar_disconnect_google', 'Disconnect Google Calendar'),
                message: _k('settings.calendar_disconnect_confirm', 'Disconnect Google Calendar? Kazma stops reading and changing it until you connect it again here. Gmail stays connected.'),
                done: _k('settings.int.google_calendar_disconnected', 'Google Calendar disconnected'),
            });
        },

        /* The calendar card's own Microsoft sign-in: it keeps the tokens for
           Outlook Calendar only. The mail card's sign-in (connectMicrosoftOAuth)
           would also reconnect a mailbox the owner disconnected. */
        async connectOutlookCalendar() {
            if ((this.emailMs.client_id || '').trim()) {
                await this.saveMsClient();
            }
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/calendar/oauth/microsoft/start.json', { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok || !data.authorize_url) {
                    throw new Error(data.error || _k('settings.int.could_not_start_microsoft_oauth', 'Could not start Microsoft OAuth'));
                }
                window.location.href = data.authorize_url;
            } catch (e) {
                showToast(_k('settings.int.calendar_oauth_failed', 'Calendar OAuth failed: ') + e.message, 'error');
                this.emailSaving = false;
            }
        },

        /* The same calendar-only sign-in with a code, for when Microsoft
           refuses the redirect (the Azure app has no web redirect URI). */
        async connectOutlookCalendarWithCode() {
            if (this.calendarMsPollTimer) {
                clearInterval(this.calendarMsPollTimer);
                this.calendarMsPollTimer = null;
            }
            this.calendarMsDevice = { user_code: '', verification_uri: '', device_code: '' };
            try {
                const resp = await fetch('/api/calendar/oauth/microsoft/device/start', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok || !data.device_code) {
                    throw new Error(data.error || ('HTTP ' + resp.status));
                }
                this.calendarMsDevice = {
                    user_code: data.user_code || '',
                    verification_uri: data.verification_uri_complete || data.verification_uri || 'https://microsoft.com/devicelogin',
                    device_code: data.device_code,
                };
            } catch (e) {
                showToast(_k('settings.int.calendar_oauth_failed', 'Calendar OAuth failed: ') + e.message, 'error');
                return;
            }
            this.calendarMsPolling = true;
            const device_code = this.calendarMsDevice.device_code;
            this.calendarMsPollTimer = setInterval(async () => {
                let data = {};
                try {
                    const resp = await fetch('/api/email/oauth/microsoft/device/poll', {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                        body: JSON.stringify({ device_code }),
                    });
                    data = await resp.json().catch(() => ({}));
                } catch (e) {
                    return;  // network hiccup: keep polling
                }
                if (data.ok && data.status === 'authorized') {
                    this._stopCalendarMsPoll();
                    showToast(_k('settings.int.outlook_calendar_connected', 'Outlook Calendar connected')
                        + (data.email ? _k('settings.int.as_account', ' as {email}', { email: data.email }) : ''), 'success');
                    await this.loadEmailStatus();
                } else if (data.status === 'failed' || data.status === 'expired') {
                    this._stopCalendarMsPoll();
                    showToast(data.error || _k('settings.int.authorization_failed', 'Authorization failed'), 'error');
                }
                // authorization_pending / slow_down: keep polling
            }, 5000);
        },

        _stopCalendarMsPoll() {
            if (this.calendarMsPollTimer) clearInterval(this.calendarMsPollTimer);
            this.calendarMsPollTimer = null;
            this.calendarMsPolling = false;
            this.calendarMsDevice = { user_code: '', verification_uri: '', device_code: '' };
        },

        async disconnectOutlookCalendar() {
            await this._disconnectCalendar({
                url: '/api/calendar/oauth/microsoft/disconnect',
                title: _k('settings.calendar_disconnect_outlook', 'Disconnect Outlook Calendar'),
                message: _k('settings.calendar_disconnect_outlook_confirm', 'Disconnect Outlook Calendar? Kazma stops reading and changing it until you connect it again here. Microsoft mail stays connected.'),
                done: _k('settings.int.outlook_calendar_disconnected', 'Outlook Calendar disconnected'),
            });
        },

        async _disconnectCalendar(opts) {
            if (!(await window.kazmaConfirm({
                title: opts.title,
                message: opts.message,
                confirmText: _k('settings.email_disconnect', 'Disconnect'),
                danger: true,
            }))) return;
            this.emailSaving = true;
            try {
                const resp = await fetch(opts.url, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || data.ok === false) throw new Error(data.error || _k('settings.int.failed', 'Failed'));
                showToast(opts.done, 'success');
            } catch (e) {
                showToast(_k('settings.int.disconnect_failed', 'Disconnect failed: ') + e.message, 'error');
            } finally {
                this.emailSaving = false;
            }
            await this.loadEmailStatus();
        },

        async disconnectGmail() {
            if (!(await window.kazmaConfirm({
                title: window.t ? t('settings.email_disconnect') : 'Disconnect',
                message: _k('settings.email_disconnect_gmail_confirm', 'Disconnect Gmail? Kazma stops reading and sending its mail. Google Calendar has its own switch on the calendar card below.'),
                confirmText: window.t ? t('settings.email_disconnect') : 'Disconnect',
                danger: true,
            }))) return;
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/gmail/disconnect', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || data.ok === false) throw new Error(data.error || _k('settings.int.failed', 'Failed'));
                this.emailGmail = { address: '', app_password: '' };
                showToast(data.message || _k('settings.int.gmail_disconnected', 'Gmail disconnected'), 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast(_k('settings.int.disconnect_failed', 'Disconnect failed: ') + e.message, 'error');
            } finally {
                this.emailSaving = false;
            }
        },

        async saveMsClient() {
            const client_id = (this.emailMs.client_id || '').trim();
            const client_secret = (this.emailMs.client_secret || '').trim();
            const tenant_id = (this.emailMs.tenant_id || 'common').trim() || 'common';
            if (!client_id) {
                showToast(window.t ? t('settings.email_ms_client_required') : 'Azure client ID required', 'error');
                return;
            }
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/oauth/microsoft/client', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ client_id, client_secret, tenant_id }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
                showToast(data.message || _k('settings.int.microsoft_app_saved', 'Microsoft app saved'), 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast(_k('settings.int.save_failed', 'Save failed: ') + e.message, 'error');
            } finally {
                this.emailSaving = false;
            }
        },

        async connectMicrosoft() {
            // Ensure client id is set first if user typed it
            if ((this.emailMs.client_id || '').trim()) {
                await this.saveMsClient();
            }
            this.emailMsConnecting = true;
            this.emailMsDevice = { user_code: '', verification_uri: '', device_code: '', message: '' };
            try {
                const resp = await fetch('/api/email/oauth/microsoft/device/start', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok) {
                    throw new Error(data.error || ('HTTP ' + resp.status));
                }
                this.emailMsDevice = {
                    user_code: data.user_code || '',
                    verification_uri: data.verification_uri_complete || data.verification_uri || 'https://microsoft.com/devicelogin',
                    device_code: data.device_code || '',
                    message: data.message || '',
                };
                showToast(window.t ? t('settings.email_ms_enter_code') : 'Enter the code at Microsoft', 'info');
                this._startMsPoll();
            } catch (e) {
                this.emailMsConnecting = false;
                showToast(_k('settings.int.microsoft_connect_failed', 'Microsoft connect failed: ') + e.message, 'error');
            }
        },

        _startMsPoll() {
            if (this.emailMsPollTimer) {
                clearInterval(this.emailMsPollTimer);
                this.emailMsPollTimer = null;
            }
            const device_code = this.emailMsDevice.device_code;
            if (!device_code) {
                this.emailMsConnecting = false;
                return;
            }
            const intervalMs = 5000;
            this.emailMsPollTimer = setInterval(async () => {
                try {
                    const resp = await fetch('/api/email/oauth/microsoft/device/poll', {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                        body: JSON.stringify({ device_code }),
                    });
                    const data = await resp.json().catch(() => ({}));
                    if (data.ok && data.status === 'authorized') {
                        clearInterval(this.emailMsPollTimer);
                        this.emailMsPollTimer = null;
                        this.emailMsConnecting = false;
                        this.emailMsDevice = { user_code: '', verification_uri: '', device_code: '', message: '' };
                        showToast(data.message || _k('settings.int.microsoft_connected', 'Microsoft connected'), 'success');
                        await this.loadEmailStatus();
                        return;
                    }
                    if (data.status === 'failed' || data.status === 'expired') {
                        clearInterval(this.emailMsPollTimer);
                        this.emailMsPollTimer = null;
                        this.emailMsConnecting = false;
                        showToast(data.error || _k('settings.int.authorization_failed', 'Authorization failed'), 'error');
                    }
                    // authorization_pending / slow_down → keep polling
                } catch (e) {
                    /* keep polling */
                }
            }, intervalMs);
        },

        async disconnectMicrosoft() {
            if (!(await window.kazmaConfirm({
                title: window.t ? t('settings.email_disconnect') : 'Disconnect',
                message: _k('settings.email_disconnect_ms_confirm', 'Disconnect Microsoft mail? Kazma stops reading and sending its mail. Outlook Calendar has its own switch on the calendar card below.'),
                confirmText: window.t ? t('settings.email_disconnect') : 'Disconnect',
                danger: true,
            }))) return;
            if (this.emailMsPollTimer) {
                clearInterval(this.emailMsPollTimer);
                this.emailMsPollTimer = null;
            }
            this.emailMsConnecting = false;
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/oauth/microsoft/disconnect', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || data.ok === false) throw new Error(data.error || _k('settings.int.failed', 'Failed'));
                this.emailMsDevice = { user_code: '', verification_uri: '', device_code: '', message: '' };
                showToast(data.message || _k('settings.int.microsoft_disconnected', 'Microsoft disconnected'), 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast(_k('settings.int.disconnect_failed', 'Disconnect failed: ') + e.message, 'error');
            } finally {
                this.emailSaving = false;
            }
        },

        async loadXStatus() {
            this.xLoading = true;
            try {
                const data = await this._fetch('/api/x/status');
                if (data && !data.error) {
                    Object.assign(this.xStatus, data);
                    if (data.handle) this.xForm.handle = data.handle;
                    if (data.caps) {
                        if (data.caps.max_posts_per_day) this.xForm.max_posts_per_day = data.caps.max_posts_per_day;
                        if (data.caps.max_posts_per_month) this.xForm.max_posts_per_month = data.caps.max_posts_per_month;
                    }
                    this.xForm.enabled = !!data.enabled;
                }
            } catch (e) {
                showToast(_k('settings.int.failed_to_load_x_status', 'Failed to load X status: ') + e.message, 'error');
            } finally {
                this.xLoading = false;
            }
        },

        async saveXCredentials() {
            this.xSaving = true;
            try {
                const resp = await fetch('/api/x/credentials', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        api_key: this.xForm.api_key,
                        api_key_secret: this.xForm.api_key_secret,
                        access_token: this.xForm.access_token,
                        access_token_secret: this.xForm.access_token_secret,
                        handle: this.xForm.handle,
                        enabled: this.xForm.enabled,
                        max_posts_per_day: Number(this.xForm.max_posts_per_day) || 8,
                        max_posts_per_month: Number(this.xForm.max_posts_per_month) || 80,
                    }),
                });
                const data = await resp.json().catch(function () { return {}; });
                if (!resp.ok || data.ok === false) {
                    showToast(data.detail || data.error || _k('settings.int.save_failed_2', 'Save failed'), 'error');
                    return;
                }
                this.xForm.api_key = '';
                this.xForm.api_key_secret = '';
                this.xForm.access_token = '';
                this.xForm.access_token_secret = '';
                Object.assign(this.xStatus, data);
                showToast(_k('settings.int.x_credentials_saved_vaulted_test', 'X credentials saved (vaulted). Test the connection next.'), 'success');
            } catch (e) {
                showToast(_k('settings.int.save_failed', 'Save failed: ') + e.message, 'error');
            } finally {
                this.xSaving = false;
            }
        },

        async testXConnection() {
            this.xSaving = true;
            try {
                const resp = await fetch('/api/x/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                });
                const data = await resp.json().catch(function () { return {}; });
                if (!resp.ok || data.ok === false) {
                    showToast(data.detail || data.error || _k('settings.int.x_test_failed', 'X test failed'), 'error');
                    return;
                }
                Object.assign(this.xStatus, data);
                var who = data.verified_username ? (' @' + data.verified_username) : '';
                showToast(_k('settings.int.x_api_ok', 'X API ok{who}. Read + Write user tokens work.', { who: who }), 'success');
            } catch (e) {
                showToast(_k('settings.int.x_test_failed_2', 'X test failed: ') + e.message, 'error');
            } finally {
                this.xSaving = false;
            }
        },

        async disconnectX() {
            if (!(await window.kazmaConfirm({
                title: _k('settings.int.disconnect_x', 'Disconnect X'),
                message: _k('settings.int.remove_the_four_oauth_keys', 'Remove the four OAuth keys from the vault and disable posting?'),
                confirmText: _k('settings.int.disconnect', 'Disconnect'),
                danger: true,
            }))) return;
            this.xSaving = true;
            try {
                const resp = await fetch('/api/x/disconnect', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                });
                const data = await resp.json().catch(function () { return {}; });
                if (!resp.ok || data.ok === false) {
                    showToast(data.detail || data.error || _k('settings.int.disconnect_failed_2', 'Disconnect failed'), 'error');
                    return;
                }
                Object.assign(this.xStatus, data);
                this.xForm.enabled = false;
                showToast(_k('settings.int.x_connector_disconnected', 'X connector disconnected.'), 'success');
            } catch (e) {
                showToast(_k('settings.int.disconnect_failed', 'Disconnect failed: ') + e.message, 'error');
            } finally {
                this.xSaving = false;
            }
        },

        // ── X auto-reply ─────────────────────────────────────────────

        async loadXReply() {
            this.xReplyLoading = true;
            try {
                const data = await this._fetch('/api/x/reply');
                if (data && data.ok !== false) {
                    Object.assign(this.xReply, data);
                    this.xReplyRoleBindings = {};
                    (data.ai_roles || []).forEach(role => {
                        this.xReplyRoleBindings[role] = Object.assign({ selection: 'inherit', provider: '', model: '' }, ((data.ai || {}).roles || {})[role] || {});
                    });
                    this.xReplySummonersText = (data.summoners || []).join(', ');
                    this.xReplyProblems = [];
                }
            } catch (e) {
                showToast(_k('settings.int.failed_to_load_auto_reply', 'Failed to load auto-reply settings: ') + e.message, 'error');
            } finally {
                this.xReplyLoading = false;
            }
            this._loadXReplyLibraries();
        },

        async _loadXReplyLibraries() {
            try {
                const data = await this._fetch('/api/kb/libraries');
                const libs = (data && data.libraries) || [];
                const saved = (this.xReply.knowledge_library || '').trim();
                if (saved && !libs.some(function (l) { return l.id === saved; })) {
                    libs.unshift({ id: saved, name: saved, chunk_count: 0 });
                }
                this.xReplyLibraries = libs;
            } catch (_e) {
                this.xReplyLibraries = [];
            }
        },

        xReplyAddSubject() {
            this.xReply.subjects.push({
                schema_version: 2, revision: 1, target: '', aliases: [], exclusions: [], scope: '',
                exceptions: [], allowed_moods: [], allow_draft: true, allow_auto: false,
                evidence_policy: 'required', evidence_max_age_days: 30, required_checks: ['context', 'target', 'stance', 'evidence', 'safety'],
                id: '', match: [], view: '', mood: 'dry', side: '', register: '',
                hard_lines: [], examples: [], _matchText: '', _hardText: '', _exText: '',
            });
            this.xReplyOpen = this.xReply.subjects.length - 1;
        },

        xReplyAIModels() {
            const provider = (this.xReply.ai || {}).provider || '';
            const row = (this.xReply.ai_options || []).find(function (p) { return p.provider === provider; });
            return row ? (row.models || []) : [];
        },

        xReplyAIConfigPayload() {
            const settings = Object.assign({}, this.xReply.ai);
            if (this.xReplyRoleBindings && Object.keys(this.xReplyRoleBindings).length) {
                settings.roles = {};
                Object.keys(this.xReplyRoleBindings).forEach(role => {
                    const pair = this.xReplyRoleBindings[role];
                    if (pair.selection !== 'inherit') settings.roles[role] = Object.assign({}, pair);
                });
            }
            return settings;
        },

        xReplyRemoveSubject(i) {
            this.xReply.subjects.splice(i, 1);
            if (this.xReplyOpen === i) this.xReplyOpen = null;
        },

        // Keywords / hard lines / examples are edited as comma- or
        // newline-separated text because a chip editor is more UI than this
        // earns. Split on newline first so a multi-line example survives.
        _xSplit(raw, multiline) {
            if (!raw) return [];
            const parts = multiline ? String(raw).split(/\n+/) : String(raw).split(/[,\n]+/);
            return parts.map(function (s) { return s.trim(); }).filter(Boolean);
        },

        xReplySubjectPayload() {
            const self = this;
            return this.xReply.subjects.map(function (s) {
                const card = {
                    id: (s.id || '').trim(),
                    match: s._matchText !== undefined ? self._xSplit(s._matchText, false) : (s.match || []),
                    view: s.view || '',
                    mood: s.mood || 'dry',
                    side: s.side || '',
                    register: s.register || '',
                    hard_lines: s._hardText !== undefined ? self._xSplit(s._hardText, true) : (s.hard_lines || []),
                    examples: s._exText !== undefined ? self._xSplit(s._exText, true) : (s.examples || []),
                };
                ['schema_version', 'revision', 'target', 'aliases', 'exclusions', 'scope', 'exceptions',
                 'allowed_moods', 'allow_draft', 'allow_auto', 'evidence_policy', 'evidence_max_age_days', 'required_checks',
                 'counterexamples', 'owner', 'change_reason'].forEach(function (key) {
                    if (s[key] !== undefined) card[key] = s[key];
                });
                return card;
            });
        },

        async saveXReply() {
            if (this.xReplySaving || this.xReplyLoading) return;
            this.xReplySaving = true;
            this.xReplyProblems = [];
            try {
                const resp = await fetch('/api/x/reply', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        expected_revision: Number(this.xReply.settings_revision) || 0,
                        enabled: !!this.xReply.enabled,
                        ai: this.xReplyAIConfigPayload(),
                        mode: this.xReply.mode || 'off',
                        summoners: this._xSplit(this.xReplySummonersText, false),
                        trigger: this.xReply.trigger || '',
                        max_replies_per_day: Number(this.xReply.max_replies_per_day) || 0,
                        max_replies_per_target_per_day: Number(this.xReply.max_replies_per_target_per_day) || 0,
                        cooldown_per_thread_s: Number(this.xReply.cooldown_per_thread_s) || 0,
                        min_target_followers: Number(this.xReply.min_target_followers) || 0,
                        poll_interval_s: Number(this.xReply.poll_interval_s) || 600,
                        summoner_policy: this.xReply.summoner_policy || 'allowlist',
                        allow_emoji_mood: !!this.xReply.allow_emoji_mood,
                        stance_check: !!this.xReply.stance_check,
                        unmatched: this.xReply.unmatched || 'skip',
                        use_knowledge: !!this.xReply.use_knowledge,
                        knowledge_library: this.xReply.knowledge_library || '',
                        open_thread_marker: this.xReply.open_thread_marker || '',
                        close_thread_marker: this.xReply.close_thread_marker || '',
                        subjects: this.xReplySubjectPayload(),
                    }),
                });
                const data = await resp.json().catch(function () { return {}; });
                if (!resp.ok || data.ok === false) {
                    this.xReplyProblems = data.problems || [];
                    showToast(data.error || data.detail || _k('settings.int.save_failed_2', 'Save failed'), 'error');
                    return;
                }
                Object.assign(this.xReply, data);
                this.xReplySummonersText = (data.summoners || []).join(', ');
                // Warnings are advisory (e.g. anyone + auto): the save
                // succeeded, the operator should know what they turned on.
                (data.warnings || []).forEach(function (w) { showToast(w, 'warning'); });
                if (data.poller_running) {
                    showToast(_k('settings.int.auto_reply_saved_mentions_poller', 'Auto-reply saved. Mentions poller is live.'), 'success');
                } else if (data.restart_required_for_poller) {
                    showToast(_k('settings.int.saved_restart_kazma_to_start', 'Saved. Restart Kazma to start the mentions poller.'), 'warning');
                } else {
                    showToast(_k('settings.int.auto_reply_settings_saved', 'Auto-reply settings saved.'), 'success');
                }
            } catch (e) {
                showToast(_k('settings.int.save_failed', 'Save failed: ') + e.message, 'error');
            } finally {
                this.xReplySaving = false;
            }
        },

        // The point of the panel: see what a view produces before it ships.
        // Publishes nothing and records nothing, so it can be run as many
        // times as it takes to get the voice right.
        async runXReplyPreview() {
            if (!this.xReplyPreview.text.trim()) {
                showToast(_k('settings.int.paste_the_post_you_want', 'Paste the post you want a reply to.'), 'error');
                return;
            }
            this.xReplyPreview.busy = true;
            this.xReplyPreview.result = null;
            try {
                const resp = await fetch('/api/x/reply/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                    // Send the subject card that is open, so an edit can be
                    // tried before it is saved. Falls back to stored config
                    // when nothing is expanded.
                    body: JSON.stringify({
                        parent_text: this.xReplyPreview.text,
                        ai: this.xReplyAIConfigPayload(),
                        parent_handle: this.xReplyPreview.handle,
                        subject_id: this.xReplyPreview.subject_id,
                        mood: this.xReplyPreview.mood,
                        subject: (this.xReplyOpen !== null && this.xReply.subjects[this.xReplyOpen])
                            ? this.xReplySubjectPayload()[this.xReplyOpen]
                            : null,
                    }),
                });
                const data = await resp.json().catch(function () { return {}; });
                this.xReplyPreview.result = data;
                if (!resp.ok && !data.reason) {
                    showToast(data.error || _k('settings.int.preview_failed', 'Preview failed'), 'error');
                }
            } catch (e) {
                showToast(_k('settings.int.preview_failed_2', 'Preview failed: ') + e.message, 'error');
            } finally {
                this.xReplyPreview.busy = false;
            }
        },

        async loadXReplyRecent() {
            try {
                const data = await this._fetch('/api/x/reply/recent?limit=15');
                this.xReplyRecent = (data && data.rows) || [];
            } catch (e) {
                this.xReplyRecent = [];
            }
        },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
