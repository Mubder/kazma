/** Settings mixin: integrations — mcp, skills, voice, email */
(function (root) {
    "use strict";
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
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
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
            if (!(await window.kazmaConfirm({
                title: 'Remove MCP server',
                message: `Remove MCP server "${name}"? This cannot be undone.`,
                confirmText: 'Remove',
                danger: true,
            }))) return;
            try {
                const resp = await fetch(`/api/settings/mcp/${encodeURIComponent(name)}`, { method: 'DELETE' });
                const body = await resp.json().catch(function() { return {}; });
                if (!resp.ok || body.status === 'error') {
                    showToast(body.message || ('Delete failed (HTTP ' + resp.status + ')'), 'error');
                    return;
                }
                await this.loadMcpServers();
                showToast('Server removed', 'success');
            } catch (e) {
                showToast('Delete failed: ' + e.message, 'error');
            }
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
            if (!(await window.kazmaConfirm({
                title: 'Uninstall skill',
                message: `Uninstall skill "${skillId}"? This cannot be undone.`,
                confirmText: 'Uninstall',
                danger: true,
            }))) return;
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
                showToast('Voice settings saved', 'success');
            } catch (e) {
                showToast('Failed to save voice settings: ' + e.message, 'error');
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
                    if (this.voiceModels.includes(this.voiceForm.tts_voice)) {
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

        onTtsVoiceTypeChange() {
            if (this.ttsVoiceType !== 'custom') {
                this.voiceForm.tts_voice = this.ttsVoiceType;
                this.saveVoiceSettings();
            }
        },

        async loadEmailStatus() {
            this.emailLoading = true;
            try {
                // OAuth callback toast (?email_oauth=ok|error)
                try {
                    const url = new URL(window.location.href);
                    const oauth = url.searchParams.get('email_oauth');
                    if (oauth === 'ok') {
                        const prov = url.searchParams.get('provider') || 'email';
                        const em = url.searchParams.get('email') || '';
                        showToast(
                            (prov === 'gmail' ? 'Gmail' : 'Microsoft') +
                            ' connected' + (em ? ' as ' + em : ''),
                            'success'
                        );
                        url.searchParams.delete('email_oauth');
                        url.searchParams.delete('provider');
                        url.searchParams.delete('email');
                        url.searchParams.delete('msg');
                        history.replaceState(null, '', url.pathname + url.search + url.hash);
                    } else if (oauth === 'error') {
                        showToast('OAuth failed: ' + (url.searchParams.get('msg') || 'unknown'), 'error');
                        url.searchParams.delete('email_oauth');
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
                if (acc && Array.isArray(acc.accounts)) this.emailAccounts = acc.accounts;
            } finally {
                this.emailLoading = false;
            }
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
                showToast(data.message || ('Gmail ' + protocol.toUpperCase() + ' connected'), 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast('Gmail ' + protocol + ' failed: ' + e.message, 'error');
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
                showToast(data.message || ('Microsoft ' + protocol.toUpperCase() + ' connected'), 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast('Microsoft ' + protocol + ' failed: ' + e.message, 'error');
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
                showToast(data.message || 'Google OAuth client saved', 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast('Save failed: ' + e.message, 'error');
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
                    throw new Error(data.error || 'Could not start Google OAuth (is Client ID/secret saved?)');
                }
                window.location.href = data.authorize_url;
            } catch (e) {
                showToast('Gmail OAuth failed: ' + e.message, 'error');
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
                    throw new Error(data.error || 'Could not start Microsoft OAuth');
                }
                window.location.href = data.authorize_url;
            } catch (e) {
                showToast('Microsoft OAuth failed: ' + e.message, 'error');
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
                showToast(data.message || 'Gmail connected', 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast('Gmail connect failed: ' + e.message, 'error');
            } finally {
                this.emailSaving = false;
            }
        },

        async disconnectGmail() {
            if (!(await window.kazmaConfirm({
                title: window.t ? t('settings.email_disconnect') : 'Disconnect',
                message: window.t ? t('settings.email_disconnect_gmail_confirm') : 'Clear Gmail credentials?',
                confirmText: window.t ? t('settings.email_disconnect') : 'Disconnect',
                danger: true,
            }))) return;
            this.emailSaving = true;
            try {
                const resp = await fetch('/api/email/gmail/disconnect', {
                    method: 'POST',
                    credentials: 'same-origin',
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || data.ok === false) throw new Error(data.error || 'Failed');
                this.emailGmail = { address: '', app_password: '' };
                showToast(data.message || 'Gmail disconnected', 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast('Disconnect failed: ' + e.message, 'error');
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
                showToast(data.message || 'Microsoft app saved', 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast('Save failed: ' + e.message, 'error');
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
                showToast('Microsoft connect failed: ' + e.message, 'error');
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
                        showToast(data.message || 'Microsoft connected', 'success');
                        await this.loadEmailStatus();
                        return;
                    }
                    if (data.status === 'failed' || data.status === 'expired') {
                        clearInterval(this.emailMsPollTimer);
                        this.emailMsPollTimer = null;
                        this.emailMsConnecting = false;
                        showToast(data.error || 'Authorization failed', 'error');
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
                message: window.t ? t('settings.email_disconnect_ms_confirm') : 'Clear Microsoft Graph tokens?',
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
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || data.ok === false) throw new Error(data.error || 'Failed');
                this.emailMsDevice = { user_code: '', verification_uri: '', device_code: '', message: '' };
                showToast(data.message || 'Microsoft disconnected', 'success');
                await this.loadEmailStatus();
            } catch (e) {
                showToast('Disconnect failed: ' + e.message, 'error');
            } finally {
                this.emailSaving = false;
            }
        },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
